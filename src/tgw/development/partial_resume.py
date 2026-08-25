"""Append-only, byte-exact evidence for local Codex implementation attempts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SCHEMA = "tgw-coding-implementation-attempt/v3"
MANIFEST_SCHEMA = "tgw-coding-preservation-manifest/v2"
HISTORY = ".tgw-coding-history/implementation"
PRESERVATION = ".tgw-coding-preservation"
LEGACY_1747 = Path("/opt/TGW/var/worktrees/todo-1747-plan-52b355efbebde5d607a2b055")


class PartialResumeError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(("git", *args), cwd=root, check=False, capture_output=True)
    if result.returncode:
        raise PartialResumeError(result.stderr.decode(errors="replace")[-500:])
    return result.stdout


def _safe_path(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="surrogateescape")
    path = PurePosixPath(text)
    if not text or path.is_absolute() or ".." in path.parts:
        raise PartialResumeError("unsafe Git path")
    return text


def _status_entries(data: bytes) -> tuple[list[dict[str, str]], list[str]]:
    fields, entries, paths, index = data.split(b"\0"), [], [], 0
    while index < len(fields) and fields[index]:
        value = fields[index]
        if len(value) < 4 or value[2:3] != b" ":
            raise PartialResumeError("malformed NUL Git status")
        xy, path = value[:2].decode("ascii"), _safe_path(value[3:])
        entry = {"xy": xy, "path": path}
        paths.append(path)
        index += 1
        if "R" in xy or "C" in xy:
            if index >= len(fields) or not fields[index]:
                raise PartialResumeError("truncated rename/copy status")
            original = _safe_path(fields[index])
            entry["original_path"] = original
            paths.append(original)
            index += 1
        entries.append(entry)
    return entries, sorted(set(paths), key=os.fsencode)


def _node(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"path": relative, "type": "deleted"}
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        target = os.readlink(path)
        content, kind = os.fsencode(target), "symlink"
    elif stat.S_ISREG(info.st_mode):
        content, kind = path.read_bytes(), "file"
    else:
        content, kind = b"", "other"
    return {
        "path": relative,
        "type": kind,
        "mode": f"{mode:04o}",
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        **({"target": target} if kind == "symlink" else {}),
    }


def source_fingerprint(worktree: Path) -> dict[str, Any]:
    exclusions = (":(exclude)implementation-receipt.json", f":(exclude){HISTORY}", f":(exclude){PRESERVATION}")
    raw = _git(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", *exclusions)
    entries, paths = _status_entries(raw)
    index_delta = _git(worktree, "diff", "--cached", "--binary", "HEAD", "--", ".", *exclusions)
    worktree_delta = _git(worktree, "diff", "--binary", "--", ".", *exclusions)
    body = {
        "head": _git(worktree, "rev-parse", "HEAD").decode().strip(),
        "tree": _git(worktree, "rev-parse", "HEAD^{tree}").decode().strip(),
        "status_nul_b64": base64.b64encode(raw).decode("ascii"),
        "status_entries": entries,
        "changed_paths": paths,
        "index_delta_b64": base64.b64encode(index_delta).decode("ascii"),
        "worktree_binary_delta_b64": base64.b64encode(worktree_delta).decode("ascii"),
        "nodes": [_node(worktree, item) for item in paths],
    }
    return {**body, "fingerprint": "sha256:" + hashlib.sha256(_canonical(body)).hexdigest()}


_BINDINGS = ("job_id", "attempt_count", "todo_id", "plan_commit", "solution_hash", "source_commit", "source_tree", "actor", "worktree", "treatment_id", "treatment_version")


def make_attempt(binding: Mapping[str, Any], worktree: Path, *, outcome: str, predecessor: str | None = None, artifacts: list[Any] | None = None) -> dict[str, Any]:
    missing = [key for key in _BINDINGS if binding.get(key) in (None, "")]
    if missing or binding.get("treatment_id") != "codex-implement":
        raise PartialResumeError("incomplete attempt binding: " + ",".join(missing))
    fingerprint = source_fingerprint(worktree)
    unsigned = {"schema": SCHEMA, **{key: binding[key] for key in _BINDINGS}, "outcome": outcome, "predecessor": predecessor, "stage": "implementation", "artifacts": artifacts or [], **fingerprint}
    return {**unsigned, "attempt_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}


def append_attempt(worktree: Path, attempt: Mapping[str, Any]) -> Path:
    root = worktree / HISTORY
    root.mkdir(parents=True, exist_ok=True)
    sequence = len(tuple(root.glob("[0-9]*-*.json"))) + 1
    digest = str(attempt.get("attempt_hash", "")).removeprefix("sha256:")
    path = root / f"{sequence:06d}-{digest}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(attempt, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path


def history(worktree: Path, expected: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    result, predecessor = [], None
    paths = sorted((worktree / HISTORY).glob("*.json")) if (worktree / HISTORY).is_dir() else ()
    for sequence, path in enumerate(paths, 1):
        value = json.loads(path.read_text(encoding="utf-8"))
        unsigned = dict(value)
        claimed = unsigned.pop("attempt_hash", None)
        actual = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
        if (
            not path.name.startswith(f"{sequence:06d}-")
            or value.get("schema") != SCHEMA
            or claimed != actual
            or value.get("predecessor") != predecessor
            or not path.name.endswith(actual.removeprefix("sha256:") + ".json")
        ):
            raise PartialResumeError("attempt lineage is invalid")
        if expected and any(expected.get(key) is not None and value.get(key) != expected.get(key) for key in _BINDINGS):
            raise PartialResumeError("attempt lineage binding mismatch")
        predecessor = claimed
        result.append(value)
    return result


def classify(worktree: Path, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
    current = source_fingerprint(worktree)
    try:
        attempts = history(worktree, expected)
    except (OSError, ValueError, json.JSONDecodeError, PartialResumeError) as exc:
        return {"state": "STALE_RECEIPT", "resumable": False, "error": str(exc), "history": []}
    if not attempts:
        state = "UNSAFE_DIRTY" if current["changed_paths"] else "ABANDONED_CLEAN"
        return {"state": state, "resumable": False, "source": current, "history": []}
    latest = attempts[-1]
    resumable = next((item for item in reversed(attempts) if item["outcome"] == "partial" and item["fingerprint"] == current["fingerprint"]), None)
    if latest["outcome"] == "satisfied":
        state = "CLOSED_CANDIDATE" if current["head"] != latest["source_commit"] else "STALE_RECEIPT"
    elif resumable is not None and all(item["fingerprint"] == current["fingerprint"] and item["outcome"] in {"partial", "failed"} for item in attempts[attempts.index(resumable) :]):
        state = "RESUMABLE_PARTIAL"
    else:
        state = "UNSAFE_DIRTY"
    return {
        "state": state,
        "resumable": state == "RESUMABLE_PARTIAL",
        "source": current,
        "history": attempts,
        "resume_of": (resumable or latest)["attempt_hash"],
        "predecessor": latest["attempt_hash"],
        "fingerprint": current["fingerprint"],
    }


def preservation_manifest(worktree: Path, classification: Mapping[str, Any], binding: Mapping[str, Any]) -> Path:
    root = worktree / PRESERVATION
    root.mkdir(exist_ok=True)
    unsigned = {"schema": MANIFEST_SCHEMA, "binding": dict(binding), "classification": classification.get("state"), "source": source_fingerprint(worktree)}
    value = {**unsigned, "manifest_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}
    path = root / (value["manifest_hash"].removeprefix("sha256:") + ".json")
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return path


def migrate_todo_1747(worktree: Path, binding: Mapping[str, Any], jobs: list[Mapping[str, Any]]) -> Path:
    """Write and verify the exact one-time 1747 manifest before attempt history."""
    if worktree.resolve() != LEGACY_1747 or binding.get("todo_id") != 1747:
        raise PartialResumeError("migration is restricted to exact Todo 1747")
    expected_jobs = {
        "dfdfd643-312e-46ef-a33c-1542340e9b9c": "partial",
        "2b1f9f04-a09f-489e-aade-f21ab1e4aaa9": "failed",
    }
    normalized = []
    for job in jobs:
        job_id, outcome = str(job.get("job_id")), job.get("outcome")
        if expected_jobs.get(job_id) != outcome or not isinstance(job.get("payload"), Mapping):
            raise PartialResumeError("Todo 1747 durable job identity, outcome, or payload mismatch")
        payload = job["payload"]
        plan = payload.get("plan_binding")
        if (
            not isinstance(plan, Mapping)
            or payload.get("todo_id") != 1747
            or payload.get("todo_agent") != binding.get("actor")
            or payload.get("worktree") != binding.get("worktree")
            or payload.get("treatment_id") != "codex-implement"
            or any(plan.get(key) != binding.get(key) for key in ("plan_commit", "solution_hash", "source_commit"))
        ):
            raise PartialResumeError("Todo 1747 durable payload binding mismatch")
        normalized.append({"job_id": job_id, "outcome": outcome, "payload": dict(payload)})
    if {item["job_id"] for item in normalized} != set(expected_jobs):
        raise PartialResumeError("Todo 1747 requires both exact durable jobs")
    state = source_fingerprint(worktree)
    if state["changed_paths"] != ["src/tgw/coding_cli.py", "src/tgw/pp_workflow_reconcile.py", "tests/test_pp_workflow_reconcile.py"]:
        raise PartialResumeError("Todo 1747 exact three-path binding mismatch")
    receipt = worktree / "implementation-receipt.json"
    if not receipt.is_file() or history(worktree):
        raise PartialResumeError("Todo 1747 receipt is missing or migration already ran")
    unsigned = {
        "schema": "tgw-coding-1747-migration/v1",
        "binding": dict(binding),
        "source": state,
        "legacy_receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
        "jobs": sorted(normalized, key=lambda item: item["job_id"]),
    }
    value = {**unsigned, "manifest_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}
    root = worktree / PRESERVATION
    root.mkdir(exist_ok=True)
    path = root / "todo-1747-migration.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    if json.loads(path.read_text(encoding="utf-8")) != value:
        raise PartialResumeError("Todo 1747 immutable migration manifest verification failed")
    predecessor = None
    for job in sorted(normalized, key=lambda item: item["outcome"] != "partial"):
        payload = job["payload"]
        attempt_binding = {**binding, "job_id": job["job_id"], "attempt_count": payload.get("attempt_count", 1)}
        attempt = make_attempt(attempt_binding, worktree, outcome=job["outcome"], predecessor=predecessor)
        append_attempt(worktree, attempt)
        predecessor = attempt["attempt_hash"]
    return path
