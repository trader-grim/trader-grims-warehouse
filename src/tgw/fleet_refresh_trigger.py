"""Durable, coalescing W18 watched-input to fleet-request trigger."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping

from tgw.config import DEFAULT_CONFIG, load_operational_config

_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REVISIONS = {
    "plan", "solution", "source", "catalog", "bootstrap", "broker_policy",
    "admission",
}


class FleetRefreshTriggerError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _directory(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise FleetRefreshTriggerError(f"{label} is invalid")
    path = Path(value)
    if (
        not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents
        or not path.is_dir() or path.is_symlink()
    ):
        raise FleetRefreshTriggerError(f"{label} must be a durable directory outside /tmp")
    return path


def _regular(value: Any, label: str) -> Path:
    if not isinstance(value, str):
        raise FleetRefreshTriggerError(f"{label} is invalid")
    path = Path(value)
    if (
        not path.is_absolute() or path == Path("/tmp") or Path("/tmp") in path.parents
        or not path.is_file() or path.is_symlink()
    ):
        raise FleetRefreshTriggerError(f"{label} must be a durable regular file outside /tmp")
    _require_root_owned(path, label)
    return path


def _require_root_owned(path: Path, label: str) -> None:
    metadata = path.stat()
    if metadata.st_uid != 0 or metadata.st_mode & 0o022:
        raise FleetRefreshTriggerError(
            f"{label} must be root-owned and not group/other writable"
        )


def _read_bound_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(descriptor)
        raw = os.read(descriptor, 1024 * 1024 + 1)
        named = os.stat(path, follow_symlinks=False)
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise FleetRefreshTriggerError(f"{label} is invalid") from exc
    finally:
        os.close(descriptor)
    if (
        len(raw) > 1024 * 1024
        or (before.st_dev, before.st_ino, before.st_size)
        != (named.st_dev, named.st_ino, named.st_size)
        or len(raw) != before.st_size
    ):
        raise FleetRefreshTriggerError(f"{label} changed during observation")
    if not isinstance(value, dict):
        raise FleetRefreshTriggerError(f"{label} is invalid")
    return value, raw


def _atomic_replace(path: Path, value: Mapping[str, Any]) -> None:
    stage = path.with_name(f".{path.name}.{os.getpid()}.next")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_canonical(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(stage, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if stage.exists() and not stage.is_symlink():
            stage.unlink()


def trigger_configured_fleet_refresh(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = config.get("fleet_refresh_trigger")
    required = {"schema", "input_path", "request_root", "state_root", "actors"}
    if (
        not isinstance(raw, Mapping) or set(raw) != required
        or raw.get("schema") != "tgw-fleet-refresh-trigger/v1"
    ):
        raise FleetRefreshTriggerError("fleet refresh trigger configuration is invalid")
    input_path = _regular(raw["input_path"], "fleet watched input")
    request_root = _directory(raw["request_root"], "fleet request root")
    state_root = _directory(raw["state_root"], "fleet trigger state root")
    actors = raw["actors"]
    if (
        not isinstance(actors, list) or not actors or actors != sorted(set(actors))
        or not all(isinstance(actor, str) and actor for actor in actors)
    ):
        raise FleetRefreshTriggerError("fleet trigger actors are invalid")

    watched, watched_raw = _read_bound_json(input_path, "fleet watched input")
    if set(watched) != {"schema", "predecessor_generation", "revisions"} or watched.get(
        "schema"
    ) != "tgw-w18-fleet-watched-input/v1":
        raise FleetRefreshTriggerError("fleet watched input fields are not exact")
    predecessor = watched["predecessor_generation"]
    revisions = watched["revisions"]
    if not isinstance(predecessor, str) or _HASH.fullmatch(predecessor) is None:
        raise FleetRefreshTriggerError("fleet predecessor generation is invalid")
    if not isinstance(revisions, Mapping) or set(revisions) != _REVISIONS:
        raise FleetRefreshTriggerError("fleet watched revisions are incomplete")
    if _COMMIT.fullmatch(str(revisions["plan"])) is None or _COMMIT.fullmatch(
        str(revisions["source"])
    ) is None:
        raise FleetRefreshTriggerError("fleet watched Git revisions are invalid")
    for name in _REVISIONS - {"plan", "source"}:
        if not isinstance(revisions[name], str) or _HASH.fullmatch(revisions[name]) is None:
            raise FleetRefreshTriggerError(f"fleet watched {name} revision is invalid")

    desired = {"revisions": dict(revisions), "actors": list(actors)}
    successor = _hash(desired)
    suffix = successor.removeprefix("sha256:")[:24]
    request_id = f"refresh-{suffix}"
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1",
        "transaction_id": request_id,
        "idempotency_key": f"revisions-{suffix}",
        "predecessor_generation": predecessor,
        "successor_generation": successor,
        "revisions": dict(revisions),
        "actors": list(actors),
    }
    request_hash = _hash(request)
    request_path = request_root / f"{request_id}.json"
    state_path = state_root / "pending-refresh.json"
    lock_path = state_root / "trigger.lock"
    lock = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        status = "TRIGGERED"
        if request_path.exists() or request_path.is_symlink():
            if request_path.is_symlink() or not request_path.is_file():
                raise FleetRefreshTriggerError("fleet refresh request path is unsafe")
            if request_path.read_bytes() != _canonical(request) + b"\n":
                raise FleetRefreshTriggerError("fleet refresh request identity collision")
            status = "COALESCED"
        else:
            _atomic_replace(request_path, request)
        state = {
            "schema": "tgw-w18-fleet-refresh-trigger-state/v1",
            "status": "PENDING",
            "request_id": request_id,
            "request_hash": request_hash,
            "successor_generation": successor,
            "input_sha256": "sha256:" + hashlib.sha256(watched_raw).hexdigest(),
        }
        if state_path.is_symlink():
            raise FleetRefreshTriggerError("fleet trigger state path is unsafe")
        _atomic_replace(state_path, state)
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        os.close(lock)
    unsigned = {
        "schema": "tgw-w18-fleet-refresh-trigger-receipt/v1",
        "status": status,
        "request_id": request_id,
        "request_hash": request_hash,
        "successor_generation": successor,
    }
    return {**unsigned, "receipt_hash": _hash(unsigned)}


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-fleet-refresh-trigger")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        result = trigger_configured_fleet_refresh(load_operational_config(args.config))
    except (OSError, FleetRefreshTriggerError, ValueError) as exc:
        print(json.dumps({"status": "HOLD", "reason": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
