"""Reproducible manifests for closed, reviewed integrated candidates."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.plan_luet import LUET_REVISION, LUET_VERSION, PROVIDER_ID


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


def graph_hash(graph: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def create_luet_conformance_receipt(
    result: Mapping[str, Any],
    *,
    graph: Mapping[str, Any],
    plan_commit: str,
    source_commit: str,
    source_tree: str,
    binary_sha256: str,
) -> dict[str, Any]:
    """Turn one successful live pinned adapter result into an immutable receipt."""
    if result.get("provider_id") != PROVIDER_ID or result.get("status") != "AGREEMENT" or result.get("available") is not True:
        raise CandidateManifestError("live Luet result does not prove agreement")
    if not isinstance(binary_sha256, str) or len(binary_sha256) != 71 or not binary_sha256.startswith("sha256:"):
        raise CandidateManifestError("live Luet binary sha256 is required")
    receipt = {
        "schema": "tgw-luet-conformance-receipt/v1",
        "provider_id": PROVIDER_ID,
        "luet_version": LUET_VERSION,
        "luet_revision": LUET_REVISION,
        "binary_sha256": binary_sha256,
        "plan_commit": plan_commit,
        "graph_hash": graph_hash(graph),
        "closure_hash": result["closure_hash"],
        "source_commit": source_commit,
        "source_tree": source_tree,
        "status": "AGREEMENT",
        "selected_providers": result.get("selected_providers", []),
    }
    receipt["receipt_hash"] = "sha256:" + hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return receipt


def verify_luet_conformance_receipt(receipt: Mapping[str, Any], *, graph: Mapping[str, Any], plan_commit: str, closure_hash: str, source_commit: str, source_tree: str) -> dict[str, Any]:
    """Verify persisted conformance without invoking an ambient binary."""
    if receipt.get("schema") != "tgw-luet-conformance-receipt/v1":
        raise CandidateManifestError("Luet conformance receipt schema is invalid")
    expected = {
        "provider_id": PROVIDER_ID,
        "luet_version": LUET_VERSION,
        "luet_revision": LUET_REVISION,
        "plan_commit": plan_commit,
        "graph_hash": graph_hash(graph),
        "closure_hash": closure_hash,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "status": "AGREEMENT",
    }
    if not isinstance(receipt.get("binary_sha256"), str) or len(receipt["binary_sha256"]) != 71 or not receipt["binary_sha256"].startswith("sha256:"):
        raise CandidateManifestError("Luet conformance receipt binary pin is invalid")
    mismatched = [key for key, value in expected.items() if receipt.get(key) != value]
    if mismatched:
        raise CandidateManifestError(f"Luet conformance receipt binding mismatch: {', '.join(mismatched)}")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash", None)
    actual = "sha256:" + hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if claimed != actual:
        raise CandidateManifestError("Luet conformance receipt hash mismatch")
    return dict(receipt)


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
    graph: Mapping[str, Any] | None = None,
    conformance_receipt: Mapping[str, Any] | None = None,
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
    if conformance_receipt is not None:
        if graph is None:
            raise CandidateManifestError("graph is required with a Luet conformance receipt")
        verified = verify_luet_conformance_receipt(conformance_receipt, graph=graph, plan_commit=plan_commit, closure_hash=closure_hash, source_commit=exact_commit, source_tree=tree)
        conformance = {"status": "VERIFIED", "receipt_hash": verified["receipt_hash"]}
    else:
        conformance = {"status": "MISSING", "receipt_hash": None}
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
        "conformance": conformance,
        "tests": {
            "focused": dict(focused_receipt),
            "full_suite": {"command": list(full_suite), "status": "DEFINED_NOT_RUN"},
        },
        "database": {
            "migration_paths": list(migrations),
            "backup_restore": asdict(migration_receipt) if migration_receipt else None,
        },
        "candidate_closed": True,
        "dispatchable": conformance["status"] == "VERIFIED",
        "installed": False,
    }
    unsigned = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["manifest_hash"] = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    return manifest
