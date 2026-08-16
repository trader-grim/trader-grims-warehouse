"""Reproducible manifests for closed, reviewed integrated candidates."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tgw.plan_luet import LUET_REVISION, LUET_VERSION, PROVIDER_ID


class CandidateManifestError(ValueError):
    pass


TEST_RECEIPT_SCHEMA = "tgw-candidate-test-receipt/v1"
RELEASE_MANIFEST_SCHEMA = "tgw-release-manifest-v1"
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


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


def _canonical_hash(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _is_pytest_command(command: Sequence[str]) -> bool:
    """Keep a candidate receipt from claiming that a no-op is a test suite."""
    names = [Path(item).name for item in command]
    return any(name in {"pytest", "py.test"} for name in names) or any(
        command[index:index + 2] == ["-m", "pytest"]
        for index in range(len(command) - 1)
    )


def create_test_receipt(
    *,
    scope: str,
    command: Sequence[str],
    source_commit: str,
    source_tree: str,
    returncode: int,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> dict[str, Any]:
    """Create an exact-candidate test receipt from an executed command.

    The output bodies intentionally stay outside the compact manifest; their
    hashes make the recorded outcome tamper evident while an evidence sink can
    retain the full log.
    """
    if scope not in {"focused", "full"}:
        raise CandidateManifestError("test receipt scope is invalid")
    if not command or not all(isinstance(item, str) and item for item in command):
        raise CandidateManifestError("test receipt command is invalid")
    if not _is_pytest_command(command):
        raise CandidateManifestError("candidate test receipt must run pytest")
    if not isinstance(returncode, int):
        raise CandidateManifestError("test receipt return code is invalid")
    receipt = {
        "schema": TEST_RECEIPT_SCHEMA,
        "scope": scope,
        "command": list(command),
        "source_commit": source_commit,
        "source_tree": source_tree,
        "returncode": returncode,
        "status": "PASS" if returncode == 0 else "FAIL",
        "stdout_sha256": "sha256:" + hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": "sha256:" + hashlib.sha256(stderr).hexdigest(),
    }
    receipt["receipt_hash"] = _canonical_hash(receipt)
    return receipt


def verify_test_receipt(
    receipt: Mapping[str, Any], *, scope: str, source_commit: str, source_tree: str,
) -> dict[str, Any]:
    """Accept only a hash-bound successful test run for this candidate."""
    required = {
        "schema", "scope", "command", "source_commit", "source_tree", "returncode",
        "status", "stdout_sha256", "stderr_sha256", "receipt_hash",
    }
    if set(receipt) != required or receipt.get("schema") != TEST_RECEIPT_SCHEMA:
        raise CandidateManifestError("test receipt schema is invalid")
    if receipt.get("scope") != scope:
        raise CandidateManifestError("test receipt scope binding mismatch")
    if receipt.get("source_commit") != source_commit or receipt.get("source_tree") != source_tree:
        raise CandidateManifestError("test receipt source binding mismatch")
    if receipt.get("status") != "PASS" or receipt.get("returncode") != 0:
        raise CandidateManifestError("candidate test receipt is not passing")
    command = receipt.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise CandidateManifestError("test receipt command is invalid")
    if not _is_pytest_command(command):
        raise CandidateManifestError("candidate test receipt must run pytest")
    for field in ("stdout_sha256", "stderr_sha256", "receipt_hash"):
        value = receipt.get(field)
        if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
            raise CandidateManifestError("test receipt hash is invalid")
    unsigned = dict(receipt)
    claimed = unsigned.pop("receipt_hash")
    if claimed != _canonical_hash(unsigned):
        raise CandidateManifestError("test receipt hash mismatch")
    return dict(receipt)


def verify_predecessor_release(
    predecessor: Mapping[str, Any], *, base_commit: str, base_tree: str,
) -> dict[str, str]:
    """Bind migration comparison to an immutable selected release manifest.

    A caller cannot suppress SQL migration checks by simply choosing the
    candidate itself (or an arbitrary newer commit) as ``base_commit``.  The
    baseline must instead be the source identity declared by the release
    manifest of the currently selected predecessor generation.
    """
    if predecessor.get("schema") != RELEASE_MANIFEST_SCHEMA:
        raise CandidateManifestError("predecessor release manifest schema is invalid")
    commit = predecessor.get("commit")
    tree = predecessor.get("git_tree")
    archive = predecessor.get("archive_sha256")
    if not all(isinstance(value, str) for value in (commit, tree, archive)):
        raise CandidateManifestError("predecessor release source identity is invalid")
    if not _GIT_OBJECT.fullmatch(commit) or not _GIT_OBJECT.fullmatch(tree):
        raise CandidateManifestError("predecessor release Git identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{64}", archive):
        raise CandidateManifestError("predecessor release archive identity is invalid")
    if commit != base_commit or tree != base_tree:
        raise CandidateManifestError("base commit/tree does not match predecessor release")
    return {
        "generation": str(predecessor.get("generation", "")),
        "commit": commit,
        "tree": tree,
        "archive_sha256": "sha256:" + archive,
        "release_manifest_hash": _canonical_hash(predecessor),
    }


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
    predecessor_release: Mapping[str, Any],
    plan_commit: str,
    solution_hash: str,
    closure_hash: str,
    focused_receipt: Mapping[str, Any],
    full_suite_receipt: Mapping[str, Any],
    graph: Mapping[str, Any] | None = None,
    conformance_receipt: Mapping[str, Any] | None = None,
    migration_receipt: MigrationSafetyReceipt | None = None,
) -> dict[str, Any]:
    """Describe only committed Git objects; mutable worktree state is ignored."""

    exact_commit = str(_git(repo, "rev-parse", f"{commit}^{{commit}}")).strip()
    tree = str(_git(repo, "rev-parse", f"{exact_commit}^{{tree}}")).strip()
    base = str(_git(repo, "rev-parse", f"{base_commit}^{{commit}}")).strip()
    base_tree = str(_git(repo, "rev-parse", f"{base}^{{tree}}")).strip()
    if base == exact_commit:
        raise CandidateManifestError("candidate predecessor cannot be the candidate itself")
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", base, exact_commit], cwd=repo,
    )
    if ancestry.returncode != 0:
        raise CandidateManifestError("predecessor release is not an ancestor of candidate")
    predecessor = verify_predecessor_release(
        predecessor_release, base_commit=base, base_tree=base_tree,
    )
    archive = _git(repo, "archive", "--format=tar", exact_commit, text=False)
    changed = tuple(sorted(line for line in str(_git(repo, "diff", "--name-only", base, exact_commit)).splitlines() if line))
    migrations = tuple(path for path in changed if path.endswith(".sql") or "/migrations/" in path)
    if migrations and (migration_receipt is None or not migration_receipt.verified):
        raise CandidateManifestError("candidate changes database schema without a verified backup/restore receipt")
    focused = verify_test_receipt(
        focused_receipt, scope="focused", source_commit=exact_commit, source_tree=tree,
    )
    full_suite = verify_test_receipt(
        full_suite_receipt, scope="full", source_commit=exact_commit, source_tree=tree,
    )
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
        "predecessor_release": predecessor,
        "plan": {"commit": plan_commit, "solution_hash": solution_hash, "closure_hash": closure_hash},
        "conformance": conformance,
        "tests": {
            "focused": focused,
            "full_suite": full_suite,
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
