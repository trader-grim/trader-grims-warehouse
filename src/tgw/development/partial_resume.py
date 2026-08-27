"""Append-only, byte-exact evidence for local Codex implementation attempts."""

from __future__ import annotations

import base64
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SCHEMA = "tgw-coding-implementation-attempt/v3"
MANIFEST_SCHEMA = "tgw-coding-preservation-manifest/v2"
HISTORY = ".tgw-coding-history/implementation"
PRESERVATION = ".tgw-coding-preservation"
LEGACY_1747 = Path("/opt/TGW/var/worktrees/todo-1747-plan-52b355efbebde5d607a2b055")
LEGACY_1747_FINGERPRINT = "sha256:1682aca6df1d147169a7d7aa9bce000a270546d57cf765c1002aecaa9071d733"
LEGACY_1747_RECEIPT_SHA256 = "0d58f8e21f3b89a89c3e242c7a96827534242989dd3ec751aa0e46c97b57742c"
LEGACY_1747_SOURCE_COMMIT = "14753ce93bfc5d29253611719377a717112db750"
LEGACY_1747_SOURCE_TREE = "25ce0a73657dfe8d89569149178113e0d1affa32"
LEGACY_1747_PLAN_COMMIT = "058e2f980201cc78245358e4901cf007063f2c29"
LEGACY_1747_SOLUTION_HASH = "sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f"
RECEIPT_FILES = frozenset(
    {
        "implementation-receipt.json",
        "review-receipt.json",
        "controller-harness-receipt.json",
        "stitch-receipt.json",
        "deployment-receipt.json",
        "operator-admit-pending.json",
    }
)


class PartialResumeError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _canonical_worktree(worktree: Path) -> Path:
    """Return the exact existing request-bound worktree root."""
    try:
        root = worktree.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PartialResumeError("request-bound worktree is unavailable") from exc
    if not root.is_dir():
        raise PartialResumeError("request-bound worktree is not a directory")
    return root


def _git(root: Path, *args: str) -> bytes:
    root = _canonical_worktree(root)
    result = subprocess.run(
        ("git", "-c", f"safe.directory={root}", *args),
        cwd=root,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise PartialResumeError(result.stderr.decode(errors="replace")[-500:])
    return result.stdout


def source_tree(worktree: Path, source_commit: str) -> str:
    """Resolve the exact baseline tree through the request-bound repository."""
    if not isinstance(source_commit, str) or len(source_commit) != 40:
        raise PartialResumeError("source commit is malformed")
    return _git(worktree, "rev-parse", f"{source_commit}^{{tree}}").decode().strip()


def candidate_changed_paths(worktree: Path, base_commit: str, candidate_commit: str) -> list[str]:
    """Return the one canonical base-to-candidate path set.

    NUL framing is required so new files and unusual (but UTF-8) Git names are
    evidence rather than presentation-dependent ``--name-only`` text.
    """
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) for value in (base_commit, candidate_commit)):
        raise PartialResumeError("candidate commit identity is malformed")
    raw = _git(
        worktree,
        "diff",
        "--name-only",
        "-z",
        "--no-renames",
        f"{base_commit}..{candidate_commit}",
        "--",
        ".",
    )
    values = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            values.append(_safe_path(item))
        except UnicodeEncodeError as exc:  # pragma: no cover - defensive
            raise PartialResumeError("candidate path is not canonical UTF-8") from exc
    try:
        for value in values:
            value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PartialResumeError("candidate path is not canonical UTF-8") from exc
    return sorted(set(values), key=os.fsencode)


def validate_closed_candidate(
    worktree: Path,
    artifact: Mapping[str, Any],
    *,
    base_commit: str,
    candidate_commit: str,
    candidate_tree: str,
) -> None:
    """Reject incomplete, contradictory, or noncanonical implementation evidence."""
    if set(artifact) - {"kind", "commit", "tree", "base_commit", "changed_paths", "detail"}:
        raise PartialResumeError("closed candidate has unknown evidence fields")
    expected_paths = candidate_changed_paths(worktree, base_commit, candidate_commit)
    if (
        artifact.get("kind") != "closed_candidate"
        or artifact.get("commit") != candidate_commit
        or artifact.get("tree") != candidate_tree
        or artifact.get("base_commit") != base_commit
        or artifact.get("changed_paths") != expected_paths
        or not expected_paths
    ):
        raise PartialResumeError("closed candidate evidence is absent, contradictory, or noncanonical")


def validate_implementation_lineage(
    worktree: Path,
    *,
    base_commit: str,
    candidate_commit: str,
    candidate_tree: str,
    receipt: Mapping[str, Any] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact latest successful implementation attempt.

    Git ancestry is necessary but never sufficient: the latest append-only
    attempt and, when supplied, the published implementation receipt must both
    close the same candidate with the canonical base and path set.
    """
    attempts = history(worktree)
    if not attempts:
        raise PartialResumeError("implementation attempt lineage is absent")
    latest = attempts[-1]
    required = (
        "todo_id", "plan_commit", "solution_hash", "source_commit", "source_tree",
        "actor", "worktree", "treatment_id", "treatment_version",
    )
    if expected is None or any(expected.get(key) in (None, "") for key in required):
        raise PartialResumeError("complete expected implementation binding is absent")
    canonical_root = str(_canonical_worktree(worktree))
    normalized_expected = dict(expected)
    if normalized_expected.get("worktree") != canonical_root:
        raise PartialResumeError("expected implementation worktree binding mismatch")
    if any(latest.get(key) != normalized_expected.get(key) for key in required):
        raise PartialResumeError("latest implementation attempt binding mismatch")
    closed = [
        item for item in latest.get("artifacts", [])
        if isinstance(item, Mapping) and item.get("kind") == "closed_candidate"
    ]
    if latest.get("outcome") != "satisfied" or len(closed) != 1:
        raise PartialResumeError("latest implementation attempt does not exactly satisfy one candidate")
    validate_closed_candidate(
        worktree, closed[0], base_commit=base_commit,
        candidate_commit=candidate_commit, candidate_tree=candidate_tree,
    )
    if latest.get("source_commit") != base_commit or latest.get("head") != candidate_commit or latest.get("tree") != candidate_tree:
        raise PartialResumeError("latest implementation attempt source lineage is stale")
    reconciliations = [
        item for item in latest.get("artifacts", [])
        if isinstance(item, Mapping) and item.get("kind") == "implementation_reconciliation"
    ]
    if reconciliations:
        if len(reconciliations) != 1:
            raise PartialResumeError("implementation reconciliation lineage is ambiguous")
        reconciliation = reconciliations[0]
        try:
            prior_bytes = base64.b64decode(reconciliation["prior_receipt_b64"], validate=True)
        except (KeyError, TypeError, ValueError) as exc:
            raise PartialResumeError("implementation reconciliation prior receipt bytes are absent") from exc
        prior_sha256 = "sha256:" + hashlib.sha256(prior_bytes).hexdigest()
        if (
            reconciliation.get("prior_attempt_hash") != latest.get("predecessor")
            or reconciliation.get("prior_receipt_sha256") != prior_sha256
        ):
            raise PartialResumeError("implementation reconciliation semantic link is invalid")
    if receipt is not None:
        receipt_closed = [
            item for item in receipt.get("artifacts", [])
            if isinstance(item, Mapping) and item.get("kind") == "closed_candidate"
        ]
        if (
            receipt.get("status") != "PASS"
            or receipt.get("outcome") != "satisfied"
            or receipt.get("treatment_id") != "codex-implement"
            or receipt.get("object_id") != canonical_root
            or len(receipt_closed) != 1
            or dict(receipt_closed[0]) != dict(closed[0])
        ):
            raise PartialResumeError("implementation receipt is stale, substituted, or contradictory")
        plan = receipt.get("plan_binding")
        plan_required = ("plan_commit", "solution_hash", "source_commit", "worktree")
        if (
            not isinstance(plan, Mapping)
            or any(plan.get(key) != normalized_expected.get(key) for key in plan_required)
        ):
            raise PartialResumeError("implementation receipt Plan binding is stale or absent")
    return latest


def recover_implementation_receipt_projection(
    worktree: Path, *, base_commit: str, candidate_commit: str, candidate_tree: str,
    expected: Mapping[str, Any],
) -> bool:
    """Regenerate a stale top-level projection from canonical reconciled history."""
    root = _canonical_worktree(worktree)
    path = root / "implementation-receipt.json"
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise PartialResumeError("implementation receipt recovery source is unavailable") from exc
    if not isinstance(receipt, dict):
        raise PartialResumeError("implementation receipt recovery source is malformed")
    try:
        validate_implementation_lineage(
            root, base_commit=base_commit, candidate_commit=candidate_commit,
            candidate_tree=candidate_tree, receipt=receipt, expected=expected,
        )
        return False
    except PartialResumeError:
        pass
    latest = validate_implementation_lineage(
        root, base_commit=base_commit, candidate_commit=candidate_commit,
        candidate_tree=candidate_tree, expected=expected,
    )
    reconciliations = [
        item for item in latest.get("artifacts", [])
        if isinstance(item, Mapping) and item.get("kind") == "implementation_reconciliation"
    ]
    plan = receipt.get("plan_binding")
    if (
        len(reconciliations) != 1
        or receipt.get("status") != "PASS"
        or receipt.get("outcome") != "satisfied"
        or receipt.get("treatment_id") != "codex-implement"
        or receipt.get("object_id") != str(root)
        or not isinstance(plan, Mapping)
        or plan.get("source_commit") != base_commit
        or plan.get("worktree") != str(root)
    ):
        raise PartialResumeError("implementation receipt recovery binding is contradictory")
    recovered = {**receipt, "artifacts": list(latest["artifacts"])}
    value = json.dumps(recovered, indent=2, sort_keys=True).encode() + b"\n"
    if value == raw:
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_text = tempfile.mkstemp(prefix=path.name + ".", dir=root)
    temporary = Path(temporary_text)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(root)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    worktree = _canonical_worktree(worktree)
    exclusions = (
        *(f":(exclude){name}" for name in sorted(RECEIPT_FILES)),
        f":(exclude){HISTORY}",
        f":(exclude){PRESERVATION}",
    )
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
    existing = history(worktree)
    unsigned = dict(attempt)
    claimed = unsigned.pop("attempt_hash", None)
    actual = "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()
    if attempt.get("schema") != SCHEMA or claimed != actual or attempt.get("predecessor") != (existing[-1]["attempt_hash"] if existing else None):
        raise PartialResumeError("attempt append is not the next exact lineage entry")
    sequence = len(existing) + 1
    digest = str(attempt.get("attempt_hash", "")).removeprefix("sha256:")
    path = root / f"{sequence:06d}-{digest}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(attempt, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(root)
    _fsync_directory(root.parent)
    _fsync_directory(worktree)
    return path


def history(worktree: Path, expected: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if expected is not None:
        required = tuple(key for key in _BINDINGS if key not in {"job_id", "attempt_count"})
        missing = [key for key in required if expected.get(key) in (None, "")]
        if missing:
            raise PartialResumeError("incomplete expected attempt binding: " + ",".join(missing))
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
    worktree = _canonical_worktree(worktree)
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
        closed = [item for item in latest.get("artifacts", []) if isinstance(item, Mapping) and item.get("kind") == "closed_candidate"]
        exact_closed = (
            len(closed) == 1
            and not current["changed_paths"]
            and current["fingerprint"] == latest.get("fingerprint")
            and current["head"] == latest.get("head") == closed[0].get("commit")
            and current["tree"] == latest.get("tree") == closed[0].get("tree")
            and current["head"] != latest.get("source_commit")
        )
        if exact_closed:
            try:
                validate_closed_candidate(
                    worktree,
                    closed[0],
                    base_commit=latest["source_commit"],
                    candidate_commit=current["head"],
                    candidate_tree=current["tree"],
                )
            except PartialResumeError:
                exact_closed = False
        if exact_closed:
            try:
                _git(
                    worktree,
                    "merge-base",
                    "--is-ancestor",
                    latest["source_commit"],
                    current["head"],
                )
            except PartialResumeError:
                exact_closed = False
        state = "CLOSED_CANDIDATE" if exact_closed else "UNSAFE_DIRTY" if current["changed_paths"] else "STALE_RECEIPT"
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
        _fsync_directory(root)
        _fsync_directory(worktree)
    return path


def migrate_todo_1747(worktree: Path, binding: Mapping[str, Any], jobs: list[Mapping[str, Any]]) -> Path:
    """Write and verify the exact one-time 1747 manifest before attempt history."""
    if (
        worktree.resolve() != LEGACY_1747
        or binding.get("todo_id") != 1747
        or binding.get("worktree") != str(worktree.resolve())
        or binding.get("actor") != "codex"
        or binding.get("treatment_id") != "codex-implement"
        or binding.get("treatment_version") != "1"
        or binding.get("source_commit") != LEGACY_1747_SOURCE_COMMIT
        or binding.get("source_tree") != LEGACY_1747_SOURCE_TREE
        or binding.get("plan_commit") != LEGACY_1747_PLAN_COMMIT
        or binding.get("solution_hash") != LEGACY_1747_SOLUTION_HASH
    ):
        raise PartialResumeError("migration is restricted to exact Todo 1747")
    expected_jobs = {
        "dfdfd643-312e-46ef-a33c-1542340e9b9c": ("partial", 1, "HardFailure('coding treatment reported partial')"),
        "2b1f9f04-a09f-489e-aade-f21ab1e4aaa9": ("failed", 1, "HardFailure('coding treatment reported failed')"),
    }
    if len(jobs) != len(expected_jobs):
        raise PartialResumeError("Todo 1747 requires exactly two durable jobs")
    if binding.get("source_tree") != source_tree(worktree, str(binding.get("source_commit", ""))):
        raise PartialResumeError("Todo 1747 source commit/tree binding mismatch")
    normalized = []
    for job in jobs:
        job_id, outcome = str(job.get("job_id")), job.get("outcome")
        expected = expected_jobs.get(job_id)
        if (
            expected is None
            or expected != (outcome, job.get("attempt_count"), job.get("error_detail"))
            or job.get("state") != "dead_letter"
            or job.get("error_code") != "HARD_FAILURE"
            or not isinstance(job.get("payload"), Mapping)
        ):
            raise PartialResumeError("Todo 1747 durable job identity, outcome, or payload mismatch")
        payload = job["payload"]
        plan = payload.get("plan_binding")
        identity = plan.get("worktree_identity") if isinstance(plan, Mapping) else None
        if (
            not isinstance(plan, Mapping)
            or not isinstance(identity, Mapping)
            or payload.get("todo_id") != 1747
            or payload.get("todo_agent") != binding.get("actor")
            or payload.get("worktree") != binding.get("worktree")
            or payload.get("object_id") != binding.get("worktree")
            or payload.get("treatment_id") != "codex-implement"
            or any(plan.get(key) != binding.get(key) for key in ("plan_commit", "solution_hash", "source_commit"))
            or plan.get("worktree") != binding.get("worktree")
            or identity.get("worktree") != binding.get("worktree")
            or identity.get("actor") != binding.get("actor")
            or identity.get("head") != binding.get("source_commit")
        ):
            raise PartialResumeError("Todo 1747 durable payload binding mismatch")
        normalized.append(
            {
                "job_id": job_id,
                "outcome": outcome,
                "attempt_count": job["attempt_count"],
                "state": job["state"],
                "error_code": job["error_code"],
                "error_detail": job["error_detail"],
                "payload": dict(payload),
            }
        )
    if len({item["job_id"] for item in normalized}) != len(expected_jobs):
        raise PartialResumeError("Todo 1747 requires both exact durable jobs")
    root = worktree / PRESERVATION
    path = root / "todo-1747-migration.json"
    if path.exists():
        try:
            installed_bytes = path.read_bytes()
            installed = json.loads(installed_bytes)
        except (OSError, json.JSONDecodeError) as exc:
            raise PartialResumeError("Todo 1747 migration manifest is unreadable") from exc
        expected_manifest_keys = {
            "schema",
            "binding",
            "source",
            "legacy_receipt_sha256",
            "jobs",
            "manifest_hash",
        }
        if not isinstance(installed, Mapping) or set(installed) != expected_manifest_keys:
            raise PartialResumeError("Todo 1747 migration manifest differs")
        unsigned_installed = dict(installed)
        claimed_hash = unsigned_installed.pop("manifest_hash", None)
        installed_source = installed.get("source")
        source_body = dict(installed_source) if isinstance(installed_source, Mapping) else {}
        source_claim = source_body.pop("fingerprint", None)
        source_hash = "sha256:" + hashlib.sha256(_canonical(source_body)).hexdigest()
        if (
            installed.get("schema") != "tgw-coding-1747-migration/v1"
            or claimed_hash != "sha256:" + hashlib.sha256(_canonical(unsigned_installed)).hexdigest()
            or installed_bytes != (json.dumps(installed, sort_keys=True) + "\n").encode()
            or installed.get("binding") != dict(binding)
            or installed.get("jobs") != sorted(normalized, key=lambda item: item["job_id"])
            or installed.get("legacy_receipt_sha256") != LEGACY_1747_RECEIPT_SHA256
            or source_claim != LEGACY_1747_FINGERPRINT
            or source_hash != LEGACY_1747_FINGERPRINT
            or installed_source.get("head") != LEGACY_1747_SOURCE_COMMIT
            or installed_source.get("tree") != binding.get("source_tree")
            or installed_source.get("changed_paths")
            != [
                "src/tgw/coding_cli.py",
                "src/tgw/pp_workflow_reconcile.py",
                "tests/test_pp_workflow_reconcile.py",
            ]
        ):
            raise PartialResumeError("Todo 1747 migration manifest differs")
        attempts = history(worktree, {**binding, "job_id": None, "attempt_count": None})
        if len(attempts) < 2:
            raise PartialResumeError("Todo 1747 historical attempt lineage is incomplete")
        for attempt, job in zip(attempts[:2], sorted(normalized, key=lambda item: item["outcome"] != "partial"), strict=True):
            if (
                attempt.get("job_id") != job["job_id"]
                or attempt.get("outcome") != job["outcome"]
                or attempt.get("attempt_count") != job["attempt_count"]
                or attempt.get("fingerprint") != LEGACY_1747_FINGERPRINT
            ):
                raise PartialResumeError("Todo 1747 historical attempt lineage differs")
        current = classify(worktree, {**binding, "job_id": None, "attempt_count": None})
        if current.get("state") in {"RESUMABLE_PARTIAL", "CLOSED_CANDIDATE"}:
            return path
        raise PartialResumeError("Todo 1747 installed migration is not bound to the current worktree")
    state = source_fingerprint(worktree)
    if state["fingerprint"] != LEGACY_1747_FINGERPRINT or state["changed_paths"] != [
        "src/tgw/coding_cli.py",
        "src/tgw/pp_workflow_reconcile.py",
        "tests/test_pp_workflow_reconcile.py",
    ]:
        raise PartialResumeError("Todo 1747 exact three-path binding mismatch")
    receipt = worktree / "implementation-receipt.json"
    receipt_hash = hashlib.sha256(receipt.read_bytes()).hexdigest() if receipt.is_file() else None
    if receipt_hash != LEGACY_1747_RECEIPT_SHA256:
        raise PartialResumeError("Todo 1747 exact legacy receipt is missing or differs")
    unsigned = {
        "schema": "tgw-coding-1747-migration/v1",
        "binding": dict(binding),
        "source": state,
        "legacy_receipt_sha256": receipt_hash,
        "jobs": sorted(normalized, key=lambda item: item["job_id"]),
    }
    value = {**unsigned, "manifest_hash": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}
    predecessor = None
    expected_attempts = []
    for job in sorted(normalized, key=lambda item: item["outcome"] != "partial"):
        attempt_binding = {
            **binding,
            "job_id": job["job_id"],
            "attempt_count": job["attempt_count"],
        }
        attempt = make_attempt(
            attempt_binding,
            worktree,
            outcome=job["outcome"],
            predecessor=predecessor,
        )
        expected_attempts.append(attempt)
        predecessor = attempt["attempt_hash"]
    existing = history(worktree, {**binding, "job_id": None, "attempt_count": None})
    if existing != expected_attempts[: len(existing)]:
        raise PartialResumeError("Todo 1747 existing attempt history is not an exact migration prefix")
    root.mkdir(exist_ok=True)
    if path.exists():
        try:
            installed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PartialResumeError("Todo 1747 migration manifest is unreadable") from exc
        if installed != value:
            raise PartialResumeError("Todo 1747 migration manifest differs")
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(root)
        _fsync_directory(worktree)
    if json.loads(path.read_text(encoding="utf-8")) != value:
        raise PartialResumeError("Todo 1747 immutable migration manifest verification failed")
    for attempt in expected_attempts[len(existing) :]:
        append_attempt(worktree, attempt)
    return path
def _protected_archive_root(path: Path, worktree: Path) -> tuple[Path, os.stat_result]:
    """Require an operator-provisioned tgw-coders archive on this filesystem."""
    try:
        group = __import__("grp").getgrnam("tgw-coders")
        raw = path.lstat()
        resolved = path.resolve(strict=True)
        worktree_device = worktree.stat(follow_symlinks=False).st_dev
    except (KeyError, OSError, RuntimeError) as exc:
        raise PartialResumeError("preservation archive is not provisioned") from exc
    if (
        stat.S_ISLNK(raw.st_mode)
        or not stat.S_ISDIR(raw.st_mode)
        or resolved != path.absolute()
        or raw.st_gid != group.gr_gid
        or raw.st_mode & 0o007
        or raw.st_mode & 0o2000 == 0
        or raw.st_dev != worktree_device
    ):
        raise PartialResumeError("preservation archive is not a protected same-filesystem tgw-coders directory")
    try:
        owner = __import__("pwd").getpwuid(raw.st_uid)
        owner_ok = raw.st_uid == 0 or owner.pw_gid == group.gr_gid or owner.pw_name in group.gr_mem
    except KeyError:
        owner_ok = False
    if not owner_ok:
        raise PartialResumeError("preservation archive owner is not trusted")
    return resolved, raw


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino, left.st_mode, left.st_uid, left.st_gid) == (
        right.st_dev, right.st_ino, right.st_mode, right.st_uid, right.st_gid)


def _open_bound_directory(path: Path, expected: os.stat_result) -> int:
    """Open an absolute directory component-by-component and bind its identity."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        for component in path.relative_to(path.anchor).parts:
            if component in {"", ".", ".."}:
                raise PartialResumeError("preservation path has an unsafe component")
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not _same_identity(os.fstat(descriptor), expected):
            raise PartialResumeError("preservation directory changed after validation")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_bound_directory(path: Path, descriptor: int) -> None:
    """Require the visible absolute path to retain the pinned directory inode."""
    try:
        visible = path.stat(follow_symlinks=False)
        bound = os.fstat(descriptor)
    except OSError as exc:
        raise PartialResumeError("preservation directory binding is unavailable") from exc
    if not stat.S_ISDIR(visible.st_mode) or not _same_identity(visible, bound):
        raise PartialResumeError("preservation directory binding changed")


def _publish_retirement_receipt(directory_fd: int, expected: bytes) -> None:
    """Convergently publish a complete nlink-1 receipt by no-replace rename."""
    final = "retirement-receipt.json"
    legacy = ".retirement-receipt.json.tmp"
    prefix = legacy + "."
    try:
        final_fd = os.open(final, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    except FileNotFoundError:
        final_fd = -1
    if final_fd >= 0:
        with os.fdopen(final_fd, "rb") as stream:
            state = os.fstat(stream.fileno())
            raw = stream.read()
        if not stat.S_ISREG(state.st_mode) or raw != expected:
            raise PartialResumeError("preservation retirement receipt differs")
        if state.st_nlink == 2:
            try:
                legacy_fd = os.open(legacy, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            except FileNotFoundError:
                raise PartialResumeError("preservation retirement receipt differs")
            with os.fdopen(legacy_fd, "rb") as stream:
                legacy_state = os.fstat(stream.fileno())
                legacy_raw = stream.read()
            if (
                (legacy_state.st_dev, legacy_state.st_ino) != (state.st_dev, state.st_ino)
                or legacy_raw != expected
            ):
                raise PartialResumeError("preservation retirement receipt names are ambiguous")
            os.unlink(legacy, dir_fd=directory_fd)
            os.fsync(directory_fd)
        elif state.st_nlink != 1:
            raise PartialResumeError("preservation retirement receipt has a foreign hardlink")
        if any(name == legacy or name.startswith(prefix) for name in os.listdir(directory_fd)):
            raise PartialResumeError("preservation retirement receipt names are ambiguous")
        return

    temporaries = [
        name for name in os.listdir(directory_fd)
        if name == legacy or name.startswith(prefix)
    ]
    if len(temporaries) > 1:
        raise PartialResumeError("preservation retirement receipt names are ambiguous")
    temporary = temporaries[0] if temporaries else prefix + secrets.token_hex(16)
    created = not temporaries
    if created:
        temporary_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o640,
            dir_fd=directory_fd,
        )
        with os.fdopen(temporary_fd, "wb") as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temporary_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
        with os.fdopen(temporary_fd, "rb") as stream:
            state = os.fstat(stream.fileno())
            if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or stream.read() != expected:
                raise PartialResumeError("preservation retirement temporary receipt differs")
    temporary_fd = os.open(temporary, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        pinned = os.fstat(temporary_fd)
        _retirement_receipt_checkpoint("before-publish")
        try:
            _rename_noreplace(directory_fd, temporary, directory_fd, final)
            published = True
        except PartialResumeError as exc:
            source_missing = (
                isinstance(exc.__cause__, OSError)
                and exc.__cause__.errno == errno.ENOENT
            )
            if "already exists" not in str(exc) and not source_missing:
                raise
            published = False
        _retirement_receipt_checkpoint("after-publish")
        if not published:
            try:
                visible = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                visible = None
            if visible is not None:
                if (visible.st_dev, visible.st_ino) != (pinned.st_dev, pinned.st_ino):
                    raise PartialResumeError("preservation retirement temporary receipt was replaced")
                os.unlink(temporary, dir_fd=directory_fd)
    finally:
        os.close(temporary_fd)
    os.fsync(directory_fd)
    final_fd = os.open(final, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    with os.fdopen(final_fd, "rb") as stream:
        state = os.fstat(stream.fileno())
        if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1 or stream.read() != expected:
            raise PartialResumeError("preservation retirement receipt differs")
    os.fsync(directory_fd)


def _retirement_receipt_checkpoint(_phase: str) -> None:
    """Deterministic test seam for receipt publication races."""


def _open_archive_child(archive_fd: int, name: str, archive_stat: os.stat_result) -> tuple[int, os.stat_result]:
    """Bind one direct archive child and reject aliases or changed metadata."""
    if not name or name in {".", ".."} or "/" in name:
        raise PartialResumeError("preservation archive child name is unsafe")
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=archive_fd)
        observed = os.fstat(descriptor)
    except OSError as exc:
        raise PartialResumeError("preservation archive destination is not a direct directory") from exc
    if (
        not stat.S_ISDIR(observed.st_mode)
        or observed.st_dev != archive_stat.st_dev
        or observed.st_uid != archive_stat.st_uid
        or observed.st_gid != archive_stat.st_gid
        or stat.S_IMODE(observed.st_mode) != 0o2750
    ):
        os.close(descriptor)
        raise PartialResumeError("preservation archive destination protection differs")
    return descriptor, observed


def _verify_archive_child_binding(archive_fd: int, name: str, descriptor: int) -> None:
    """Require the archive name to retain the pinned destination inode."""
    try:
        visible = os.stat(name, dir_fd=archive_fd, follow_symlinks=False)
        bound = os.fstat(descriptor)
    except OSError as exc:
        raise PartialResumeError("preservation archive destination binding is unavailable") from exc
    if not stat.S_ISDIR(visible.st_mode) or not _same_identity(visible, bound):
        raise PartialResumeError("preservation archive destination binding changed")


def _rename_noreplace(
    source_fd: int, source: str, destination_fd: int, destination: str,
) -> None:
    """Atomically move one descriptor-relative name without replacement."""
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise PartialResumeError("atomic no-replace archive retirement is unavailable")
    if renameat2(
        source_fd, os.fsencode(source), destination_fd, os.fsencode(destination), 1,
    ) != 0:  # RENAME_NOREPLACE
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise PartialResumeError("preservation archive destination already exists")
        raise PartialResumeError("atomic no-replace archive retirement failed") from OSError(
            error, os.strerror(error), destination,
        )


def _archived_evidence(directory_fd: int) -> tuple[list[dict[str, Any]], str]:
    """Hash evidence only through stable, non-linked descriptor bindings."""
    rows: list[dict[str, Any]] = []
    for name in sorted(os.listdir(directory_fd), key=os.fsencode):
        if name == "retirement-receipt.json":
            continue
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o007:
                raise PartialResumeError("archived preservation evidence has an unsafe descendant")
            with os.fdopen(descriptor, "rb") as stream:
                raw = stream.read()
                after = os.fstat(stream.fileno())
            if (before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid,
                    before.st_nlink, before.st_size, before.st_mtime_ns) != (
                    after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid,
                    after.st_nlink, after.st_size, after.st_mtime_ns):
                raise PartialResumeError("archived preservation evidence changed while reading")
        except OSError as exc:
            raise PartialResumeError("archived preservation evidence is unreadable") from exc
        rows.append({"name": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return rows, hashlib.sha256(_canonical(rows)).hexdigest()


def retire_preservation(
    worktree: Path, *, todo_id: int, candidate_commit: str,
    archive_root: Path | None = None,
) -> dict[str, Any] | None:
    """Replayably retire active evidence after the exact candidate is closed."""
    root = _canonical_worktree(worktree)
    source = root / PRESERVATION
    configured_raw = os.environ.get("TGW_CODING_PRESERVATION_ARCHIVE_ROOT")
    configured = archive_root or (Path(configured_raw) if configured_raw else None)
    if configured is None:
        if source.exists():
            raise PartialResumeError("preservation archive is not configured")
        return None
    archive, archive_stat = _protected_archive_root(configured, root)
    prefix = f"todo-{todo_id}-{candidate_commit}-"
    archive_fd: int | None = None
    root_fd: int | None = None
    source_fd: int | None = None
    destination_fd: int | None = None
    try:
        archive_fd = _open_bound_directory(archive, archive_stat)
        candidates = sorted(name for name in os.listdir(archive_fd) if name.startswith(prefix))
        root_stat = root.stat(follow_symlinks=False)
        root_fd = _open_bound_directory(root, root_stat)
        try:
            source_stat = os.stat(PRESERVATION, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            source_stat = None
        if source_stat is not None:
            if not stat.S_ISDIR(source_stat.st_mode) or source_stat.st_dev != archive_stat.st_dev:
                raise PartialResumeError("active preservation evidence is unsafe")
            source_fd = os.open(PRESERVATION, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=root_fd)
            try:
                if not _same_identity(os.fstat(source_fd), source_stat):
                    raise PartialResumeError("active preservation evidence changed")
                rows, digest = _archived_evidence(source_fd)
            finally:
                os.close(source_fd)
                source_fd = None
            destination_name = prefix + digest
            if candidates:
                raise PartialResumeError("preservation archive destination exists while evidence remains active")
            source_fd = os.open(PRESERVATION, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=root_fd)
            if not _same_identity(os.fstat(source_fd), source_stat):
                raise PartialResumeError("active preservation evidence changed before retirement")
            if not _same_identity(os.fstat(archive_fd), archive_stat):
                raise PartialResumeError("preservation archive root changed before retirement")
            os.fchown(source_fd, archive_stat.st_uid, archive_stat.st_gid)
            os.fchmod(source_fd, 0o2750)
            os.fsync(source_fd)
            _verify_bound_directory(root, root_fd)
            _verify_bound_directory(archive, archive_fd)
            _rename_noreplace(root_fd, PRESERVATION, archive_fd, destination_name)
            destination_fd, moved_stat = _open_archive_child(
                archive_fd, destination_name, archive_stat,
            )
            if not _same_identity(os.fstat(source_fd), moved_stat):
                raise PartialResumeError("retired preservation destination identity differs")
            try:
                os.stat(PRESERVATION, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise PartialResumeError("active preservation name remains after retirement")
            _verify_bound_directory(root, root_fd)
            _verify_bound_directory(archive, archive_fd)
            os.fsync(root_fd)
            os.fsync(archive_fd)
        elif len(candidates) == 1:
            destination_name = candidates[0]
        elif not candidates:
            return None
        else:
            raise PartialResumeError("preservation retirement replay is ambiguous")
        if destination_fd is None:
            destination_fd, destination_stat = _open_archive_child(
                archive_fd, destination_name, archive_stat,
            )
        else:
            destination_stat = os.fstat(destination_fd)
        try:
            _verify_archive_child_binding(archive_fd, destination_name, destination_fd)
            rows, digest = _archived_evidence(destination_fd)
            if destination_name != prefix + digest:
                raise PartialResumeError("preservation archive destination digest suffix differs")
            revalidated = os.fstat(destination_fd)
            if any(getattr(revalidated, field) != getattr(destination_stat, field) for field in (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
            )):
                raise PartialResumeError("preservation archive destination changed")
            destination = archive / destination_name
            unsigned = {"schema": "tgw-coding-preservation-retirement/v1", "todo_id": todo_id,
                "candidate_commit": candidate_commit, "worktree": str(root),
                "archive": str(destination), "evidence": rows}
            receipt = {**unsigned, "receipt_sha256": "sha256:" + hashlib.sha256(_canonical(unsigned)).hexdigest()}
            expected = json.dumps(receipt, sort_keys=True).encode() + b"\n"
            _verify_archive_child_binding(archive_fd, destination_name, destination_fd)
            _publish_retirement_receipt(destination_fd, expected)
            _verify_archive_child_binding(archive_fd, destination_name, destination_fd)
            os.fsync(archive_fd)
            return receipt
        finally:
            os.close(destination_fd)
            destination_fd = None
    finally:
        for descriptor in (destination_fd, source_fd, root_fd, archive_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
