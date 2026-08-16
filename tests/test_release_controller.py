import hashlib
import io
import os
import tarfile
from pathlib import Path
from unittest.mock import Mock

from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect
from tgw.release_controller import MountedReleaseController
from tgw.release_installer import materialize

COMMIT_A, COMMIT_B, TREE = "a" * 40, "b" * 40, "c" * 40
EXECUTOR = "executor:release-runner"


def _authority(receipt_id="authority:approved"):
    store = Mock()
    store.begin_execution.return_value = {"receipt_id": receipt_id}
    store.complete_execution.return_value = {}
    return store


def _archive(path: Path, commit: str, body: bytes) -> str:
    with tarfile.open(path, "w:gz", pax_headers={"comment": commit}) as archive:
        info = tarfile.TarInfo("src/tgw/app.py")
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path, health_status="healthy"):
    root = tmp_path / "mounted-root"
    old_archive = tmp_path / "old.tar.gz"
    old_digest = _archive(old_archive, COMMIT_A, b"old")
    materialize(root, old_archive, generation="release-a", commit=COMMIT_A, tree=TREE, archive_sha256=old_digest)
    os.symlink("releases/release-a", root / "current")
    candidate_archive = tmp_path / "candidate.tar.gz"
    candidate_digest = _archive(candidate_archive, COMMIT_B, b"new")
    backup = Mock(return_value={"receipt": "backup:release-a"})
    health = Mock(return_value={"status": health_status, "receipt": "health:release-b"})
    controller = MountedReleaseController(roots={"tgw-staging": root}, artifacts={"artifact:candidate": candidate_archive}, backup=backup, health=health)
    parameters = {
        "candidate_commit": COMMIT_B,
        "candidate_tree": TREE,
        "archive_sha256": candidate_digest,
        "artifact_ref": "artifact:candidate",
        "root_id": "tgw-staging",
        "expected_current": "release-a",
        "operation_id": "install-b",
        "review_receipt": "review:passed",
        "controller_receipt": "controller:passed",
    }
    effect = TypedEffect.parse({"kind": "coding-release", "generation": "release-b", "parameters": parameters})
    registry = TypedEffectHandlerRegistry(release_install=controller.install, release_rollback=controller.rollback, flake_push=Mock(), flake_switch_record=Mock(), dependency_resubmit=Mock())
    return root, controller, registry, effect, backup, health


def test_reviewed_candidate_installs_by_exact_hash_with_backup_selection_and_health_receipts(tmp_path):
    root, _, registry, effect, backup, health = _fixture(tmp_path)
    authority = _authority()

    receipt = AuthorityEffectController(registry, authority).execute(request_id="request:release-b", effect=effect, executor_principal=EXECUTOR)

    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert "backup:release-a" in receipt.evidence
    assert "review:passed" in receipt.evidence
    assert "controller:passed" in receipt.evidence
    assert "health:release-b" in receipt.evidence
    assert os.readlink(root / "current") == "releases/release-b"
    backup.assert_called_once_with("tgw-staging", "release-a")
    health.assert_called_once_with("tgw-staging", "release-b")


def test_failed_generation_health_rolls_back_using_exact_selection_receipt(tmp_path):
    root, _, registry, effect, _, _ = _fixture(tmp_path, health_status="unhealthy")

    receipt = AuthorityEffectController(registry, _authority()).execute(request_id="request:release-b", effect=effect, executor_principal=EXECUTOR)

    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "rollback:install-b-rollback"
    assert os.readlink(root / "current") == "releases/release-a"


def test_unknown_mount_fails_without_touching_registered_root(tmp_path):
    root, _, registry, effect, _, _ = _fixture(tmp_path)
    bad = TypedEffect.parse({"kind": "coding-release", "generation": effect.generation, "parameters": {**effect.parameters, "root_id": "production-not-mounted"}})

    receipt = AuthorityEffectController(registry, _authority()).execute(request_id="r", effect=bad, executor_principal=EXECUTOR)

    assert receipt.outcome is EffectOutcome.FAILED
    assert os.readlink(root / "current") == "releases/release-a"
