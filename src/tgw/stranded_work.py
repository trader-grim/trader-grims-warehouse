"""Read-only discovery of implemented or executed work stranded outside Git admission.

The inventory deliberately keeps design, implementation, execution, admission, and
deployment as independent evidence dimensions.  A dirty worktree is evidence to
preserve and reconcile; it is never cleanup authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
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


def _file_record(path: Path, relative: str) -> dict[str, Any]:
    try:
        return {"path": relative, "sha256": _sha(path), "bytes": path.stat().st_size}
    except OSError as exc:
        return {"path": relative, "unreadable": type(exc).__name__}


def _signals(worktree: Path, changed: list[str]) -> dict[str, list[dict[str, Any]]]:
    evidence: dict[str, list[dict[str, Any]]] = {
        "designed": [], "implemented": [], "executed": [], "admitted": [], "deployed": [],
    }
    for relative in changed:
        path = worktree / relative
        name = relative.lower()
        if not path.is_file():
            continue
        record = _file_record(path, relative)
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
    stranded = bool(records) and bool(evidence["implemented"])
    evidence_residue = bool(records) and not stranded and bool(evidence["executed"])
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
        "classification": (
            "STRANDED-WORK" if stranded
            else "EVIDENCE-RESIDUE" if evidence_residue
            else "OBSERVED-WORKTREE"
        ),
        "cleanup_authorized": False,
    }


def inspect_repository(repository: Path) -> dict[str, Any]:
    repository = repository.resolve()
    head = _git(repository, "rev-parse", "HEAD").strip()
    common_dir = _git(repository, "rev-parse", "--git-common-dir").strip()
    common_path = (repository / common_dir).resolve()
    remotes = []
    for line in _git(repository, "remote", "-v").splitlines():
        fields = line.split()
        if len(fields) >= 2 and not any(item["name"] == fields[0] for item in remotes):
            remotes.append({"name": fields[0], "url": fields[1]})
    fsck = subprocess.run(
        [
            "git", "-c", f"safe.directory={repository}", "-C", str(repository),
            "fsck", "--full", "--no-reflogs", "--unreachable",
        ],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    unreachable = [line for line in fsck.stdout.splitlines() if line.startswith("unreachable ")]
    return {
        "schema": "tgw-repository-evidence/v1",
        "path": str(repository),
        "git_common_dir": str(common_path),
        "head": head,
        "branch": _git(repository, "branch", "--show-current").strip() or None,
        "remotes": remotes,
        "unreachable_object_count": len(unreachable),
        "unreachable_object_sample": unreachable[:25],
        "fsck_error": fsck.stderr.strip() if fsck.returncode else None,
    }


def discover_repositories(roots: Iterable[Path]) -> list[Path]:
    repositories: set[Path] = set()
    for root in roots:
        root = root.resolve()
        if not root.exists():
            continue
        candidates = [root] if (root / ".git").exists() else []
        try:
            candidates.extend(path.parent for path in root.rglob(".git"))
        except OSError:
            pass
        for candidate in candidates:
            probe = subprocess.run(
                ["git", "-c", f"safe.directory={candidate}", "-C", str(candidate),
                 "rev-parse", "--show-toplevel"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            if probe.returncode == 0:
                repositories.add(Path(probe.stdout.strip()).resolve())
    return sorted(repositories)


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
    worktrees = []
    for path in sorted(paths):
        try:
            exists = path.exists()
        except OSError as exc:
            worktrees.append({
                "schema": "tgw-worktree-evidence/v1",
                "path": str(path),
                "classification": "INACCESSIBLE-WORKTREE",
                "error": type(exc).__name__,
                "cleanup_authorized": False,
            })
            continue
        if exists:
            try:
                worktrees.append(inspect_worktree(path))
            except (OSError, StrandedWorkError) as exc:
                worktrees.append({
                    "schema": "tgw-worktree-evidence/v1",
                    "path": str(path),
                    "classification": "INACCESSIBLE-WORKTREE",
                    "error": type(exc).__name__,
                    "cleanup_authorized": False,
                })
    payload = {"schema": "tgw-stranded-work-inventory/v1", "worktrees": worktrees}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["inventory_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def inventory_environment(roots: Iterable[Path]) -> dict[str, Any]:
    repositories = discover_repositories(roots)
    worktree_inventory = inventory_worktrees(repositories)
    payload = {
        "schema": "tgw-stranded-work-environment/v1",
        "roots": [str(path.resolve()) for path in roots],
        "repositories": [inspect_repository(path) for path in repositories],
        "worktrees": worktree_inventory["worktrees"],
        "summary": {
            "repository_count": len(repositories),
            "worktree_count": len(worktree_inventory["worktrees"]),
            "stranded_work_count": sum(
                item["classification"] == "STRANDED-WORK"
                for item in worktree_inventory["worktrees"]
            ),
            "inaccessible_worktree_count": sum(
                item["classification"] == "INACCESSIBLE-WORKTREE"
                for item in worktree_inventory["worktrees"]
            ),
            "evidence_residue_count": sum(
                item["classification"] == "EVIDENCE-RESIDUE"
                for item in worktree_inventory["worktrees"]
            ),
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["inventory_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inventory TGW stranded work read-only")
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = inventory_environment(args.roots)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
