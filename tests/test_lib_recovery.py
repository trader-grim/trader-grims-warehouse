from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.lib_recovery import REQUIRED_TIERS, ManifestError, canonical_bytes, inventory, object_record, seal_generation, verify_receipt


def generation(root: Path) -> dict:
    tiers = {}
    for name in REQUIRED_TIERS:
        path = root / name / "evidence"
        path.parent.mkdir()
        path.write_text(name)
        tiers[name] = {"state": "complete", "objects": [object_record(path, root)]}
    tiers["encrypted_secrets"].update({"encryption": "age", "key_custody": "operator-held-offline", "plaintext_excluded": True})
    captured = "2026-08-29T18:00:30Z"
    digest = "sha256:" + "a" * 64
    manifest = {
        "schema": "tgw-lib-recovery-generation/v2",
        "generation": "20260829T180000Z-test",
        "state": "complete",
        "started_at": "2026-08-29T18:00:00Z",
        "completed_at": "2026-08-29T18:01:00Z",
        "retention_class": "daily",
        "tools": {"git": "2.45", "postgresql": "16"},
        "barriers": {
            "git_refs": {name: {"commit": char * 40, "tree": char * 40, "refs": {"refs/heads/main": char * 40}, "captured_at": captured} for name, char in (("source", "a"), ("plan", "b"))},
            "postgresql": {"start_lsn": "0/123", "stop_lsn": "0/456", "timeline": 1, "wal_contiguous": True, "schema_sha256": digest, "migration_identity": "alembic:123", "captured_at": captured},
            "filesystems": {"library": {"method": "bounded-walk", "barrier_id": "walk-1", "manifest_sha256": digest, "captured_at": captured}},
        },
        "replicas": {
            "local_fast": {"state": "verified", "readback_verified": True, "failure_domain": "host-disk-2", "manifest_sha256": digest},
            "off_host_encrypted": {
                "state": "verified",
                "readback_verified": True,
                "failure_domain": "offline-site-b",
                "manifest_sha256": digest,
                "encryption": "age",
                "key_custody": "operator-held-offline",
            },
        },
        "receipt": {"storage": "worm", "immutability_verified": True},
        "tiers": tiers,
    }
    object_digest = "sha256:" + hashlib.sha256(canonical_bytes({"generation": manifest["generation"], "barriers": manifest["barriers"], "tiers": manifest["tiers"]})).hexdigest()
    manifest["object_manifest_sha256"] = object_digest
    manifest["replicas"]["local_fast"]["manifest_sha256"] = object_digest
    manifest["replicas"]["off_host_encrypted"]["manifest_sha256"] = object_digest
    return manifest


@pytest.fixture
def signing_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_seal_and_clean_readback(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    receipt = seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")
    verify_receipt(receipt, staging, signing_key.public_key(), "operator-2026")
    assert json.loads(receipt.read_text())["state"] == "complete"


def test_partial_generation_never_seals(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    manifest["tiers"]["postgresql"]["state"] = "failed"
    with pytest.raises(ManifestError, match="postgresql"):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")
    assert not (tmp_path / "receipts").exists()


def test_missing_tier_never_seals(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    del manifest["tiers"]["encrypted_secrets"]
    with pytest.raises(ManifestError, match="encrypted_secrets"):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")


def test_tampered_object_fails_readback(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    receipt = seal_generation(staging, generation(staging), tmp_path / "receipts", signing_key, "operator-2026")
    (staging / "library" / "evidence").write_text("tampered")
    with pytest.raises(ManifestError, match="size differs|hash differs"):
        verify_receipt(receipt, staging, signing_key.public_key(), "operator-2026")


def test_tampered_receipt_fails_before_objects(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    receipt = seal_generation(staging, generation(staging), tmp_path / "receipts", signing_key, "operator-2026")
    payload = json.loads(receipt.read_text())
    payload["retention_class"] = "forever"
    receipt.chmod(0o640)
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ManifestError, match="manifest hash"):
        verify_receipt(receipt, staging, signing_key.public_key(), "operator-2026")


def test_receipt_is_immutable_and_cannot_be_reused(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    receipt = seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")
    assert receipt.stat().st_mode & 0o222 == 0
    with pytest.raises(ManifestError, match="already exists"):
        seal_generation(staging, copy.deepcopy(manifest), tmp_path / "receipts", signing_key, "operator-2026")


def test_generation_path_traversal_is_rejected(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    manifest["generation"] = "../../outside"
    with pytest.raises(ManifestError, match="safe basename"):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")


@pytest.mark.parametrize(
    "mutation, match",
    [
        (lambda value: value.pop("replicas"), "replica"),
        (lambda value: value["tiers"]["encrypted_secrets"].pop("encryption"), "encrypted_secrets"),
        (lambda value: value.__setitem__("receipt", {"storage": "chmod", "immutability_verified": True}), "immutable"),
        (lambda value: value["barriers"].__setitem__("postgresql", {"lsn": "0/123"}), "PostgreSQL"),
    ],
)
def test_security_evidence_is_mandatory(tmp_path: Path, signing_key: Ed25519PrivateKey, mutation, match: str) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    mutation(manifest)
    with pytest.raises(ManifestError, match=match):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")


def test_object_cannot_escape_generation(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ManifestError, match="escapes"):
        object_record(outside, root)


def test_replica_must_bind_exact_object_manifest(tmp_path: Path, signing_key: Ed25519PrivateKey) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    manifest["replicas"]["off_host_encrypted"]["manifest_sha256"] = "sha256:" + "f" * 64
    with pytest.raises(ManifestError, match="replica"):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")


@pytest.mark.parametrize("refs", [True, ["refs/heads/main"], {"main": "a" * 40}, {"refs/heads/main": "c" * 40}])
def test_git_refs_are_a_related_ref_oid_map(tmp_path: Path, signing_key: Ed25519PrivateKey, refs) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    manifest["barriers"]["git_refs"]["source"]["refs"] = refs
    manifest["object_manifest_sha256"] = "sha256:" + hashlib.sha256(canonical_bytes({"generation": manifest["generation"], "barriers": manifest["barriers"], "tiers": manifest["tiers"]})).hexdigest()
    for replica in manifest["replicas"].values():
        replica["manifest_sha256"] = manifest["object_manifest_sha256"]
    with pytest.raises(ManifestError, match="git ref map"):
        seal_generation(staging, manifest, tmp_path / "receipts", signing_key, "operator-2026")


def test_metadata_collection_failure_is_fatal(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "object"
    path.write_text("x")
    monkeypatch.setattr("tgw.lib_recovery.shutil.which", lambda _name: None)
    with pytest.raises(ManifestError, match="getfacl"):
        object_record(path, tmp_path)


def test_inventory_names_every_bounded_recovery_surface(tmp_path: Path) -> None:
    names = {surface["name"] for surface in inventory([tmp_path])["surfaces"]}
    assert names == {
        "standalone_plan_git", "canonical_source_git", "todo_branches_worktrees",
        "postgresql_todo_queue_history", "library_plan_materializations_runbooks_archive",
        "tgw_lib_context_doctor_coding_queue", "master_itemdata_media_history_annex",
        "unix_users_groups_ownership", "encrypted_secrets_operator_custody",
        "backup_health_receipts_alerts", "reproducible_projections",
    }
