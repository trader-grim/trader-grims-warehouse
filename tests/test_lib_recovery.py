from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tgw.lib_recovery import REQUIRED_TIERS, ManifestError, object_record, seal_generation, verify_receipt


def generation(root: Path) -> dict:
    tiers = {}
    for name in REQUIRED_TIERS:
        path = root / name / "evidence"
        path.parent.mkdir()
        path.write_text(name)
        tiers[name] = {"state": "complete", "objects": [object_record(path, root)]}
    return {
        "schema": "tgw-lib-recovery-generation/v1",
        "generation": "20260829T180000Z-test",
        "state": "complete",
        "started_at": "2026-08-29T18:00:00Z",
        "completed_at": "2026-08-29T18:01:00Z",
        "retention_class": "daily",
        "tools": {"git": "2.45", "postgresql": "16"},
        "barriers": {"git_refs": {"source": "abc", "plan": "def"}, "postgresql": {"lsn": "0/123"}, "filesystems": {"library": "manifest:aaa"}},
        "tiers": tiers,
    }


def test_seal_and_clean_readback(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    receipt = seal_generation(staging, manifest, tmp_path / "receipts")
    verify_receipt(receipt, staging)
    assert json.loads(receipt.read_text())["state"] == "complete"


def test_partial_generation_never_seals(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    manifest["tiers"]["postgresql"]["state"] = "failed"
    with pytest.raises(ManifestError, match="postgresql"):
        seal_generation(staging, manifest, tmp_path / "receipts")
    assert not (tmp_path / "receipts").exists()


def test_missing_tier_never_seals(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    del manifest["tiers"]["encrypted_secrets"]
    with pytest.raises(ManifestError, match="encrypted_secrets"):
        seal_generation(staging, manifest, tmp_path / "receipts")


def test_tampered_object_fails_readback(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    receipt = seal_generation(staging, generation(staging), tmp_path / "receipts")
    (staging / "library" / "evidence").write_text("tampered")
    with pytest.raises(ManifestError, match="size differs|hash differs"):
        verify_receipt(receipt, staging)


def test_tampered_receipt_fails_before_objects(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    receipt = seal_generation(staging, generation(staging), tmp_path / "receipts")
    payload = json.loads(receipt.read_text())
    payload["retention_class"] = "forever"
    receipt.chmod(0o640)
    receipt.write_text(json.dumps(payload))
    with pytest.raises(ManifestError, match="manifest hash"):
        verify_receipt(receipt, staging)


def test_receipt_is_immutable_and_cannot_be_reused(tmp_path: Path) -> None:
    staging = tmp_path / "objects"
    staging.mkdir()
    manifest = generation(staging)
    receipt = seal_generation(staging, manifest, tmp_path / "receipts")
    assert receipt.stat().st_mode & 0o222 == 0
    with pytest.raises(ManifestError, match="already exists"):
        seal_generation(staging, copy.deepcopy(manifest), tmp_path / "receipts")


def test_object_cannot_escape_generation(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("x")
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ManifestError, match="escapes"):
        object_record(outside, root)
