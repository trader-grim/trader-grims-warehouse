"""Read-only discovery of implemented or executed work stranded outside Git admission.

The inventory deliberately keeps design, implementation, execution, admission, and
deployment as independent evidence dimensions.  A dirty worktree is evidence to
preserve and reconcile; it is never cleanup authority.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


class StrandedWorkError(RuntimeError):
    """The requested inventory could not be produced safely."""


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={path}", "-C", str(path), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise StrandedWorkError(f"git {' '.join(args)} failed for {path}: {result.stderr.strip()}")
    return result.stdout


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signals(worktree: Path, changed: list[str]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {
        "designed": [], "implemented": [], "executed": [], "admitted": [], "deployed": [],
    }
    for relative in changed:
        path = worktree / relative
        name = relative.lower()
        if not path.is_file():
            continue
        record = {"path": relative, "sha256": _sha(path), "bytes": path.stat().st_size}
        if name.endswith((".py", ".rs", ".go", ".js", ".ts")) and not name.startswith("tests/"):
            evidence["implemented"].append(record)
        if name.startswith("tests/") or "/tests/" in name:
            evidence["implemented"].append(record)
        if any(token in name for token in ("result", "receipt", "run-manifest", "controller")):
            evidence["executed"].append(record)
        if name.endswith(("plan-graph.json", "code-graph.json", "coverage-ledger.json")):
            evidence["executed"].append(record)
        if name.endswith(".md") and any(token in name for token in ("plan", "spec", "design", "packet")):
            evidence["designed"].append(record)
    return evidence


def inspect_worktree(worktree: Path) -> dict[str, Any]:
    worktree = worktree.resolve()
    head = _git(worktree, "rev-parse", "HEAD").strip()
    status = _git(worktree, "status", "--porcelain=v1", "--untracked-files=all", "-z")
    records = [value for value in status.split("\0") if value]
    changed = sorted({value[3:] for value in records if len(value) > 3})
    evidence = _signals(worktree, changed)
    stranded = bool(records) and bool(evidence["implemented"] or evidence["executed"])
    return {
        "schema": "tgw-worktree-evidence/v1",
        "path": str(worktree),
        "head": head,
        "dirty": bool(records),
        "status_records": records,
        "evidence": evidence,
        "states": {
            "designed": bool(evidence["designed"]),
            "implemented": bool(evidence["implemented"]),
            "executed": bool(evidence["executed"]),
            "admitted": False if stranded else None,
            "deployed": None,
        },
        "classification": "STRANDED-WORK" if stranded else "OBSERVED-WORKTREE",
        "cleanup_authorized": False,
    }


def inventory_worktrees(repositories: Iterable[Path]) -> dict[str, Any]:
    paths: set[Path] = set()
    for repository in repositories:
        repository = repository.resolve()
        output = _git(repository, "worktree", "list", "--porcelain")
        paths.update(
            Path(line.removeprefix("worktree "))
            for line in output.splitlines()
            if line.startswith("worktree ")
        )
    worktrees = [inspect_worktree(path) for path in sorted(paths) if path.exists()]
    payload = {"schema": "tgw-stranded-work-inventory/v1", "worktrees": worktrees}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["inventory_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload
