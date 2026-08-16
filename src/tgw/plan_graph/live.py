"""Bind the independently reviewed Plan Graph core to the standalone Plan."""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .core import SourcePreconditionError, brief, build, coverage, query

DEFAULT_PLAN_ROOT = Path("/opt/TGW/library/plans")
_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SOLUTION_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
RECEIVER_PROFILES = {
    "codex": "Retrieve cited Plan sources before implementation; run tests and return receipts.",
    "claude": "Retrieve cited Plan sources; independently review evidence and return a verdict receipt.",
    "aider": "Retrieve cited Plan sources before editing; stay in the named worktree and return the exact diff.",
    "hermes": "Retrieve cited Plan sources and evidence; preserve authority boundaries and return a bounded receipt.",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(root: Path, *args: str, git_path: str = "git") -> str:
    proc = subprocess.run(
        [git_path, "-c", f"safe.directory={root}", "-C", str(root), *args],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode:
        raise SourcePreconditionError("envelope_invalid", str(root))
    return proc.stdout.strip()


def _selected_paths(root: Path) -> list[str]:
    paths: set[str] = set()
    for relative_root in ("plan", "pp", "reference"):
        base = root / relative_root
        if base.is_dir():
            paths.update(
                path.relative_to(root).as_posix()
                for path in base.rglob("*.md")
                if path.is_file() and not path.is_symlink()
            )
    if not paths:
        raise SourcePreconditionError("source_absent", str(root))
    return sorted(paths)


def approved_plan_binding(
    plan_root: Path | str, *, approved_plan_commit: str | None,
    approved_solution_hash: str | None, git_path: str = "git",
) -> dict[str, str]:
    """Fail closed unless a consumer names one clean approved Plan snapshot.

    A clean repository HEAD is not an approval.  This small, shared binding is
    deliberately used by graph navigation as well as dispatch-facing callers,
    so an MCP/launcher cannot silently substitute whichever Plan revision is
    currently checked out.
    """
    if not isinstance(approved_plan_commit, str) or not _FULL_COMMIT.fullmatch(approved_plan_commit):
        raise SourcePreconditionError("approved_plan_commit_required", "exact approved Plan commit required")
    if not isinstance(approved_solution_hash, str) or not _SOLUTION_HASH.fullmatch(approved_solution_hash):
        raise SourcePreconditionError("approved_solution_required", "exact approved Plan solution required")
    root = Path(plan_root).resolve(strict=True)
    if Path(_git(root, "rev-parse", "--show-toplevel", git_path=git_path)).resolve() != root:
        raise SourcePreconditionError("plan_root_mismatch", str(root))
    head = _git(root, "rev-parse", "HEAD^{commit}", git_path=git_path)
    if head != approved_plan_commit:
        raise SourcePreconditionError("approved_plan_mismatch", str(root))
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all", git_path=git_path):
        raise SourcePreconditionError("source_changed", str(root))
    return {
        "plan_root": str(root), "plan_commit": approved_plan_commit,
        "solution_hash": approved_solution_hash,
    }


def source_envelope(root: Path, allowlist: Path, *, git_path: str = "git") -> dict[str, Any]:
    root = root.resolve(strict=True)
    head = _git(root, "rev-parse", "HEAD", git_path=git_path)
    tree = _git(root, "rev-parse", "HEAD^{tree}", git_path=git_path)
    status_text = _git(root, "status", "--porcelain=v1", "--untracked-files=all", git_path=git_path)
    if status_text:
        raise SourcePreconditionError("source_changed", str(root))
    allow_raw = allowlist.read_bytes()
    records: list[dict[str, Any]] = []
    for rel in [line for line in allow_raw.decode().splitlines() if line]:
        path = root / rel
        observed = os.lstat(path)
        raw = path.read_bytes() if stat.S_ISREG(observed.st_mode) else b""
        source_type = (
            "regular" if stat.S_ISREG(observed.st_mode)
            else "symlink" if stat.S_ISLNK(observed.st_mode)
            else "fifo" if stat.S_ISFIFO(observed.st_mode) else "other"
        )
        records.append({
            "path": rel, "type": source_type,
            "mode": f"{stat.S_IMODE(observed.st_mode):04o}",
            "bytes": len(raw), "sha256": _sha(raw),
        })
    envelope: dict[str, Any] = {
        "schema": "tgw-plan-source-envelope-v1",
        "authority_role": "standalone-plan-vault",
        "authority_locator": "TGW_PLAN_VAULT_STANDALONE",
        "observed_host": socket.gethostname(), "observed_root": str(root),
        "head": head, "tree": tree, "status_bytes": 0,
        "status_sha256": _sha(b""), "allowlist_bytes": len(allow_raw),
        "allowlist_sha256": _sha(allow_raw),
        "records_sha256": _sha(_canonical(records)), "record_count": len(records),
        # Stable for an exact clean commit so repeated retrieval uses one
        # content-addressed artifact set rather than rebuilding per request.
        "observed_at": _git(root, "show", "-s", "--format=%cI", "HEAD", git_path=git_path),
        "errors": [],
        "exclusions": ["inbox, archive, research, and runtime state are not Plan intent"],
        "records": records,
    }
    envelope["envelope_sha256"] = _sha(_canonical(envelope))
    return envelope


def live_plan_graph(
    plan_root: Path | str = DEFAULT_PLAN_ROOT, task: str = "", *,
    receiver: str = "codex", operation: str = "brief", limit: int = 12,
    git_path: str = "git", runtime_root: Path | str | None = None,
    approved_plan_commit: str | None = None,
    approved_solution_hash: str | None = None,
) -> dict[str, Any]:
    """Build and query one exact, clean standalone-Plan snapshot."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    receiver = receiver.strip().lower()
    if receiver not in RECEIVER_PROFILES:
        raise ValueError(f"unknown receiver: {receiver}")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    operation = operation.strip().lower()
    if operation not in {"brief", "query", "coverage"}:
        raise ValueError(f"unknown operation: {operation}")

    binding = approved_plan_binding(
        plan_root, approved_plan_commit=approved_plan_commit,
        approved_solution_hash=approved_solution_hash, git_path=git_path,
    )
    root = Path(binding["plan_root"])
    runtime_base = (
        Path(runtime_root)
        if runtime_root is not None
        else Path(os.environ.get("TGW_PLAN_GRAPH_RUNTIME", tempfile.gettempdir()))
    )
    runtime = runtime_base / "tgw-plan-graph"
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    paths = _selected_paths(root)
    allowlist = runtime / f"allowlist-{_sha(str(root).encode())}.txt"
    allowlist.write_text("".join(f"{path}\n" for path in paths), encoding="utf-8")
    envelope = source_envelope(root, allowlist, git_path=git_path)
    output = runtime / envelope["envelope_sha256"]
    if not output.exists():
        build(root, allowlist, output, source_envelope=envelope)

    if operation == "brief":
        result = brief(root, output, task, limit, source_envelope=envelope, allowlist=allowlist)
    elif operation == "query":
        result = query(root, output, task, limit, source_envelope=envelope, allowlist=allowlist)
    else:
        result = coverage(root, allowlist, output, source_envelope=envelope)
    result.update({
        "ok": True, "plan_root": str(root), "plan_commit": envelope["head"],
        "plan_tree": envelope["tree"], "source_envelope": envelope["envelope_sha256"],
        "approved_solution_hash": binding["solution_hash"],
        "receiver": receiver, "receiver_profile": RECEIVER_PROFILES[receiver],
        "canonical_authority": (
            "Standalone Plan Markdown at the bound commit remains canonical; "
            "this derived graph grants no effect authority."
        ),
    })
    return result
