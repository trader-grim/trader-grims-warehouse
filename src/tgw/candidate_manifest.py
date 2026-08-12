"""Reproducible manifests for closed, reviewed integrated candidates."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class CandidateManifestError(ValueError):
    pass


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=text)
    return result.stdout


@dataclass(frozen=True)
class MigrationSafetyReceipt:
    schema: str
    source_schema_hash: str
    backup_hash: str
    restored_hash: str
    verified: bool


def verify_backup_restore(
    schema: bytes,
    *,
    backup: Callable[[bytes], bytes],
    restore: Callable[[bytes], bytes],
) -> MigrationSafetyReceipt:
    backup_body = backup(schema)
    restored = restore(backup_body)
    def digest(value: bytes) -> str:
        return "sha256:" + hashlib.sha256(value).hexdigest()
    return MigrationSafetyReceipt(
        "tgw-migration-backup-restore-receipt/v1",
        digest(schema),
        digest(backup_body),
        digest(restored),
        restored == schema,
    )


def build_candidate_manifest(
    repo: Path,
    *,
    commit: str,
    base_commit: str,
    plan_commit: str,
    solution_hash: str,
    closure_hash: str,
    focused_receipt: Mapping[str, Any],
    full_suite: Sequence[str],
    migration_receipt: MigrationSafetyReceipt | None = None,
) -> dict[str, Any]:
    """Describe only committed Git objects; mutable worktree state is ignored."""

    exact_commit = str(_git(repo, "rev-parse", f"{commit}^{{commit}}")).strip()
    tree = str(_git(repo, "rev-parse", f"{exact_commit}^{{tree}}")).strip()
    base = str(_git(repo, "rev-parse", f"{base_commit}^{{commit}}")).strip()
    archive = _git(repo, "archive", "--format=tar", exact_commit, text=False)
    changed = tuple(sorted(line for line in str(_git(repo, "diff", "--name-only", base, exact_commit)).splitlines() if line))
    migrations = tuple(path for path in changed if path.endswith(".sql") or "/migrations/" in path)
    if migrations and (migration_receipt is None or not migration_receipt.verified):
        raise CandidateManifestError("candidate changes database schema without a verified backup/restore receipt")
    manifest = {
        "schema": "tgw-integrated-candidate-manifest/v1",
        "source": {
            "commit": exact_commit,
            "tree": tree,
            "archive_sha256": "sha256:" + hashlib.sha256(archive).hexdigest(),
            "base_commit": base,
            "changed_paths": list(changed),
        },
        "plan": {"commit": plan_commit, "solution_hash": solution_hash, "closure_hash": closure_hash},
        "tests": {
            "focused": dict(focused_receipt),
            "full_suite": {"command": list(full_suite), "status": "DEFINED_NOT_RUN"},
        },
        "database": {
            "migration_paths": list(migrations),
            "backup_restore": asdict(migration_receipt) if migration_receipt else None,
        },
        "candidate_closed": True,
        "installed": False,
    }
    unsigned = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_hash"] = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    return manifest
