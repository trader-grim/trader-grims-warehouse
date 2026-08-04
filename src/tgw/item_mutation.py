"""Private Phase 1 JSON-authoritative item mutation transaction boundary.

The journal is append-only JSONL.  It is deliberately local to the configured
non-production fixture and contains enough bytes to reconcile after process
death without restoring an older document over a newer generation.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator

from .config import location_dir, sku_dir, sku_json

ABSENT_GENERATION = "absent"
_TERMINAL = frozenset({"COMMITTED", "REPAIR_REQUIRED", "ABORTED", "CONFLICT"})


def generation_for_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def generation_for_path(path: Path) -> str:
    try:
        return generation_for_bytes(path.read_bytes())
    except FileNotFoundError:
        return ABSENT_GENERATION


def _root(cfg: Dict[str, Any]) -> Path:
    return Path(cfg.get("item_mutation_root", Path(cfg["itemdata_root"]).parent / ".item-mutations"))


def _canonical(value: Any) -> str:
    # allow_nan=False is also recursive JSON-native validation.  Compact and
    # sorted representation binds operation identity without Python equality
    # collapsing bool/int/float.
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _append(cfg: Dict[str, Any], event: Dict[str, Any]) -> None:
    root = _root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "receipts.jsonl"
    raw = (_canonical(event) + "\n").encode("utf-8")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o660)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("receipt journal is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short receipt journal write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _events(cfg: Dict[str, Any]) -> list[Dict[str, Any]]:
    path = _root(cfg) / "receipts.jsonl"
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("receipt journal is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, os.fstat(fd).st_size)
        if raw and not raw.endswith(b"\n"):
            cut = raw.rfind(b"\n") + 1
            os.ftruncate(fd, cut)
            os.fsync(fd)
            raw = raw[:cut]
        return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]
    finally:
        os.close(fd)


@contextlib.contextmanager
def item_locks(cfg: Dict[str, Any], *skus: str) -> Iterator[None]:
    lock_root = _root(cfg) / "locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for sku in sorted(set(skus)):
            # SKU path validation happens through sku_json too; keep lock names flat.
            name = hashlib.sha256(sku.encode("utf-8")).hexdigest() + ".lock"
            handle = open(lock_root / name, "a+b")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handles.append(handle)
        yield
    finally:
        for handle in reversed(handles):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


@contextlib.contextmanager
def operation_lock(cfg: Dict[str, Any], operation_id: str) -> Iterator[None]:
    lock_root = _root(cfg) / "operation-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    path = lock_root / (hashlib.sha256(operation_id.encode()).hexdigest() + ".lock")
    with open(path, "a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _crash(boundary: str) -> None:
    if os.environ.get("TGW_ITEM_MUTATION_CRASH_AFTER") == boundary:
        os._exit(86)


def _serialize(cfg: Dict[str, Any], doc: Dict[str, Any]) -> bytes:
    indent = 2 if cfg.get("pretty", True) else None
    text = json.dumps(doc, ensure_ascii=False, indent=indent, allow_nan=False) + "\n"
    return text.encode("utf-8")


def _publish(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (path.stat().st_mode & 0o777) if path.exists() else 0o660
    import tempfile
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=path.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _envelope(doc: Dict[str, Any], key: str) -> Dict[str, Any]:
    return {"present": True, "value": doc[key]} if key in doc else {"present": False}


def _transform(sku: str, kind: str, payload: Dict[str, Any], before: Dict[str, Any] | None):
    if kind == "create":
        if before is not None:
            raise ValueError("create requires absent item")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("create data must be an object")
        if "sku" in data and data["sku"] != sku:
            raise ValueError("payload sku does not match operation sku")
        after = {"sku": sku, **data}
        keys = set(after)
    elif kind in {"set", "merge"}:
        if before is None:
            raise ValueError(f"{kind} requires existing item")
        fields = payload.get("fields")
        if (not isinstance(fields, dict) or
                not all(isinstance(key, str) for key in fields)):
            raise ValueError("fields must be an object")
        after = dict(before or {})
        after.update(fields)
        delete_fields = payload.get("delete_fields", [])
        if not isinstance(delete_fields, list) or not all(isinstance(k, str) for k in delete_fields):
            raise ValueError("delete_fields must be a string list")
        for key in delete_fields:
            after.pop(key, None)
        keys = set(fields) | set(delete_fields)
    elif kind == "delete":
        if before is None:
            raise ValueError("delete requires existing item")
        fields = payload.get("fields")
        if not isinstance(fields, list) or not all(isinstance(k, str) for k in fields):
            raise ValueError("delete fields must be a string list")
        after = dict(before or {})
        for key in fields:
            after.pop(key, None)
        keys = set(fields)
    elif kind == "append":
        if before is None:
            raise ValueError("append requires existing item")
        field, event = payload.get("field"), payload.get("event")
        if not isinstance(field, str):
            raise ValueError("append field must be a string")
        after = dict(before or {})
        history = list(after.get(field, []))
        identity = _canonical(event)
        if not any(_canonical(old) == identity for old in history):
            history.append(event)
        after[field] = history
        keys = {field}
    else:
        raise ValueError(f"unsupported operation kind: {kind}")
    _canonical(after)
    changes = {key: {"before": _envelope(before or {}, key), "after": _envelope(after, key)}
               for key in sorted(keys)}
    return after, changes


def _project_sqlite(cfg: Dict[str, Any], doc: Dict[str, Any]) -> None:
    if "sqlite_catalog_path" in cfg:
        from .sqlite_catalog import upsert_catalog_row
        result = upsert_catalog_row(cfg, doc)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "SQLite projection failed"))


def _project_location(cfg: Dict[str, Any], sku: str,
                      old_location: Any, new_location: Any) -> None:
    if "location_tree_root" not in cfg:
        return
    old = str(old_location or "").strip()
    new = str(new_location or "").strip()
    if old and old != new:
        link = location_dir(cfg, old) / sku
        if link.exists() or link.is_symlink():
            link.unlink()
    _crash("location_remove")
    if new:
        directory = location_dir(cfg, new)
        directory.mkdir(parents=True, exist_ok=True)
        link = directory / sku
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(sku_dir(cfg, sku), link)
    _crash("location_add")


def _binding(op_id: str, sku: str, kind: str, expected: str,
             payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"operation_id": op_id, "sku": sku, "kind": kind,
            "expected_generation": expected, "payload": payload}


def _prior(cfg: Dict[str, Any], op_id: str):
    return [e for e in _events(cfg) if e.get("operation_id") == op_id]


def _archive_once(cfg: Dict[str, Any], intent: Dict[str, Any], path: Path) -> None:
    archive_root = Path(cfg["archive_root"])
    archive_root.mkdir(parents=True, exist_ok=True)
    zpath = archive_root / f"{path.stem}.zip"
    identity = hashlib.sha256(intent["binding"].encode()).hexdigest()
    name = f"{path.name}.operation-{identity}"
    with zipfile.ZipFile(zpath, "a", compression=zipfile.ZIP_DEFLATED) as zf:
        if name in zf.namelist():
            if zf.read(name) != path.read_bytes():
                raise RuntimeError("archive evidence contradicts canonical before bytes")
            return
        zf.writestr(name, path.read_bytes())


def mutate_item(cfg: Dict[str, Any], operation_id: str, sku: str, kind: str,
                expected_generation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one stable-identity mutation and return its truthful receipt."""
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("operation_id must be a non-empty string")
    # Validate paths and payload before recording an intent or touching stores.
    path = sku_json(cfg, sku)
    binding = _binding(operation_id, sku, kind, expected_generation, payload)
    binding_key = _canonical(binding)
    with operation_lock(cfg, operation_id), item_locks(cfg, sku):
        prior = _prior(cfg, operation_id)
        if prior:
            if prior[0].get("binding") != binding_key:
                result = {**binding, "status": "CONFLICT", "reason": "operation_id_mismatch"}
                _append(cfg, {"event": "terminal", "binding": binding_key,
                              "operation_id": operation_id, "result": result})
                return result
            terminals = [e["result"] for e in prior
                         if e.get("event") in {"terminal", "resolution"}
                         and e.get("binding") == binding_key]
            if terminals:
                return terminals[-1]
            intent = next((e for e in prior if e.get("event") == "intent"), None)
            if intent is not None:
                return _reconcile_intent(cfg, intent, prior)
        actual = generation_for_path(path)
        if actual != expected_generation:
            result = {**binding, "status": "CONFLICT", "reason": "stale_generation",
                      "actual_generation": actual}
            _append(cfg, {"event": "terminal", "binding": binding_key, "result": result,
                          "operation_id": operation_id})
            return result
        before = json.loads(path.read_bytes()) if actual != ABSENT_GENERATION else None
        try:
            after, changes = _transform(sku, kind, payload, before)
            after_bytes = _serialize(cfg, after)
        except Exception as exc:
            result = {**binding, "status": "ABORTED", "reason": str(exc)}
            _append(cfg, {"event": "terminal", "binding": binding_key, "result": result,
                          "operation_id": operation_id})
            return result
        committed = generation_for_bytes(after_bytes)
        intent = {"event": "intent", "operation_id": operation_id, "binding": binding_key,
                  "binding_record": binding, "before_generation": actual,
                  "committed_generation": committed,
                  "after_b64": base64.b64encode(after_bytes).decode("ascii"),
                  "old_location": (before or {}).get("location"), "changes": changes}
        _append(cfg, intent)
        _crash("intent")
        try:
            no_op = before is not None and path.read_bytes() == after_bytes
            if not no_op and before is not None and cfg.get("archive_root") is not None:
                _archive_once(cfg, intent, path)
            _append(cfg, {"event": "attempt", "boundary": "noop" if no_op else "archive",
                          "operation_id": operation_id, "binding": binding_key})
            if not no_op:
                _crash("archive")
                _publish(path, after_bytes)
                _append(cfg, {"event": "attempt", "boundary": "canonical",
                              "operation_id": operation_id, "binding": binding_key})
        except Exception as exc:
            published = generation_for_path(path) == committed
            result = {**binding, "status": "REPAIR_REQUIRED" if published else "ABORTED",
                      "reason": str(exc), "changes": changes,
                      "committed_generation": committed}
            _append(cfg, {"event": "terminal", "operation_id": operation_id,
                          "binding": binding_key, "result": result})
            return result
        _crash("canonical")
        result = _finish(cfg, intent, after)
        _append(cfg, {"event": "terminal", "operation_id": operation_id,
                      "binding": binding_key, "result": result})
        return result


def _finish(cfg: Dict[str, Any], intent: Dict[str, Any], doc: Dict[str, Any]):
    binding = intent["binding_record"]
    result = {**binding, "status": "COMMITTED",
              "before_generation": intent["before_generation"],
              "committed_generation": intent["committed_generation"],
              "changes": intent["changes"], "projections": {}}
    failures = []
    try:
        _project_sqlite(cfg, doc)
        if "sqlite_catalog_path" in cfg:
            con = sqlite3.connect(cfg["sqlite_catalog_path"])
            try:
                row = con.execute("select data from catalog where sku=?", (binding["sku"],)).fetchone()
            finally:
                con.close()
            if row is None or _canonical(json.loads(row[0])) != _canonical(doc):
                raise RuntimeError("SQLite persisted content mismatch")
        result["projections"]["sqlite"] = {
            "ok": True, "generation": intent["committed_generation"],
            "content_sha256": hashlib.sha256(_canonical(doc).encode("utf-8")).hexdigest(),
        }
    except Exception as exc:
        failures.append({"projection": "sqlite", "error": str(exc)})
        result["projections"]["sqlite"] = {"ok": False, "error": str(exc)}
    _crash("sqlite")
    try:
        _project_location(cfg, binding["sku"], intent.get("old_location"), doc.get("location"))
        if "location_tree_root" in cfg:
            location = str(doc.get("location") or "").strip()
            if location:
                link = location_dir(cfg, location) / binding["sku"]
                expected = sku_dir(cfg, binding["sku"])
                if not link.is_symlink() or link.resolve() != expected.resolve():
                    raise RuntimeError("location persisted link mismatch")
            else:
                old_location = str(intent.get("old_location") or "").strip()
                if old_location:
                    old_link = location_dir(cfg, old_location) / binding["sku"]
                    if old_link.exists() or old_link.is_symlink():
                        raise RuntimeError("location persisted stale old link")
        result["projections"]["location"] = {
            "ok": True, "generation": intent["committed_generation"],
            "location": _envelope(doc, "location"),
            "target": str(sku_dir(cfg, binding["sku"])) if doc.get("location") else None,
        }
    except Exception as exc:
        failures.append({"projection": "location", "error": str(exc)})
        result["projections"]["location"] = {"ok": False, "error": str(exc)}
    if failures:
        result["status"] = "REPAIR_REQUIRED"
        result["failures"] = failures
    return result


def reconcile_pending(cfg: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Reconcile unfinished/repair-required operations from current bytes."""
    events = _events(cfg)
    intents = {e["operation_id"]: e for e in events if e.get("event") == "intent"}
    latest = {}
    for event in events:
        if (event.get("event") in {"terminal", "resolution"} and "result" in event and
                event.get("binding") == intents.get(event.get("operation_id"), {}).get("binding")):
            latest[event["operation_id"]] = event["result"]
    repaired = []
    for op_id, intent in intents.items():
        if latest.get(op_id, {}).get("status") in {"COMMITTED", "ABORTED", "CONFLICT"}:
            continue
        sku = intent["binding_record"]["sku"]
        with item_locks(cfg, sku):
            result = _reconcile_intent(cfg, intent, events)
            repaired.append(result)
    return repaired


def _reconcile_intent(cfg: Dict[str, Any], intent: Dict[str, Any], events):
    op_id = intent["operation_id"]
    path = sku_json(cfg, intent["binding_record"]["sku"])
    current = generation_for_path(path)
    wanted = intent["committed_generation"]
    if current == intent["before_generation"]:
        if current != ABSENT_GENERATION and cfg.get("archive_root") is not None:
            _archive_once(cfg, intent, path)
            _append(cfg, {"event": "attempt", "boundary": "archive",
                          "operation_id": op_id, "binding": intent["binding"]})
        _publish(path, base64.b64decode(intent["after_b64"]))
        current = generation_for_path(path)
    if current != wanted:
        result = {**intent["binding_record"], "status": "CONFLICT",
                  "reason": "newer_canonical_generation", "actual_generation": current,
                  "committed_generation": wanted}
    else:
        result = _finish(cfg, intent, json.loads(path.read_bytes()))
    _append(cfg, {"event": "resolution", "operation_id": op_id,
                  "binding": intent["binding"], "result": result})
    return result


def legacy_mutate(cfg: Dict[str, Any], sku: str, kind: str,
                  payload: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility seam for the predeclared direct wrapper allowlist."""
    path = sku_json(cfg, sku)
    return mutate_item(cfg, "legacy-" + uuid.uuid4().hex, sku, kind,
                       generation_for_path(path), payload)
