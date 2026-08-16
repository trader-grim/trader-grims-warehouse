import hashlib
import io
import json
import os
import tarfile
from pathlib import Path
from unittest.mock import Mock

from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect
from tgw.release_controller import MountedReleaseController
from tgw.release_installer import install_runtime_files, materialize, runtime_manifest_identity

COMMIT_A, COMMIT_B, TREE = "a" * 40, "b" * 40, "c" * 40
EXECUTOR = "executor:release-runner"
MIGRATION = b"SELECT 1;\n"
PROJECTION = b'{"projection":"exact"}\n'
RUNTIME_CONFIG = b'{"schema":"test-runtime"}\n'


def _hash_object(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _migration_receipt():
    unsigned = {
        "schema": "tgw-database-migration-receipt/v2", "candidate_commit": COMMIT_B,
        "candidate_tree": TREE, "base_commit": COMMIT_A, "base_tree": TREE,
        "migration_path": "src/tgw/migration.sql", "migration_sha256": "sha256:" + hashlib.sha256(MIGRATION).hexdigest(),
        "schema_snapshot_path": None, "schema_snapshot_sha256": None, "postgres_version": "PostgreSQL 17.6",
        "backup_sha256": "sha256:" + "1" * 64, "source_schema_sha256": "sha256:" + "2" * 64,
        "restored_schema_sha256": "sha256:" + "2" * 64, "source_data_sha256": "sha256:" + "3" * 64,
        "restored_data_sha256": "sha256:" + "3" * 64, "migrated_schema_sha256": "sha256:" + "4" * 64,
        "migrated_data_sha256": "sha256:" + "3" * 64, "verified": True,
    }
    return {**unsigned, "receipt_hash": _hash_object(unsigned)}


def _authority(receipt_id="authority:approved"):
    store = Mock()
    store.begin_execution.return_value = {"receipt_id": receipt_id}
    store.complete_execution.return_value = {}
    return store


def _archive(path: Path, commit: str, body: bytes) -> str:
    with tarfile.open(path, "w:gz", pax_headers={"comment": commit}) as archive:
        files = {"src/tgw/app.py": body}
        if commit == COMMIT_B:
            files.update({"src/tgw/migration.sql": MIGRATION, "projection.json": PROJECTION})
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path, health_status="HEALTHY"):
    root = tmp_path / "mounted-root"
    old_archive = tmp_path / "old.tar.gz"
    old_digest = _archive(old_archive, COMMIT_A, b"old")
    materialize(root, old_archive, generation="release-a", commit=COMMIT_A, tree=TREE, archive_sha256=old_digest)
    os.symlink("releases/release-a", root / "current")
    candidate_archive = tmp_path / "candidate.tar.gz"
    candidate_digest = _archive(candidate_archive, COMMIT_B, b"new")
    calls = []

    def provider(name, result):
        def invoke(*args):
            calls.append(name)
            return dict(result)
        return Mock(side_effect=invoke)

    predecessor_hash = "sha256:" + "9" * 64
    providers = {
        "observe_predecessor": provider("observe", {
            "status": "MATCH", "receipt": "preflight:fresh", "bound_observation": predecessor_hash,
            "generation": "release-a", "nix_system_path": "/nix/store/system-a",
            "services": ["tgw-api.service"], "health_probes": ["api"],
        }),
        "quiesce_services": provider("quiesce", {"status": "QUIESCED", "receipt": "quiesce:a"}),
        "backup": provider("backup", {"status": "BACKED_UP", "receipt": "backup:a"}),
        "migrate": provider("migrate", {"status": "APPLIED", "receipt": "migration:b", "applied_paths": ["src/tgw/migration.sql"]}),
        "stage_runtime": None,
        "activate_generation": provider("activate", {"status": "ACTIVATED", "receipt": "activate:b", "prior_generation": "release-a", "generation": "release-b"}),
        "restart_services": provider("restart", {"status": "RESTARTED", "receipt": "restart:b"}),
        "health": provider("health", {"status": health_status, "receipt": "health:b"}),
        "verify_unrelated_state": provider("invariants", {"status": "UNCHANGED", "receipt": "invariants:b"}),
        "record_stage": provider("journal", {"status": "RECORDED", "receipt": "journal:stage"}),
        "reconcile_predecessor": provider("reconcile", {"status": "RESTORED", "receipt": "restore:a", "generation": "release-a", "predecessor_healthy": True}),
    }
    parameters = {
        "generation": "release-b",
        "candidate_commit": COMMIT_B, "candidate_tree": TREE,
        "archive_sha256": candidate_digest, "artifact_ref": "artifact:candidate",
        "root_id": "tgw-staging", "expected_current": "release-a", "operation_id": "install-b",
        "review_receipt": "review:passed", "controller_receipt": "controller:passed",
        "migration_receipts": [_migration_receipt()], "projection": {
            "release_path": "projection.json", "content_sha256": "sha256:" + hashlib.sha256(PROJECTION).hexdigest(),
        },
        "runtime_config": {
            "artifact_ref": "config:b",
            "content_sha256": "sha256:" + hashlib.sha256(RUNTIME_CONFIG).hexdigest(),
            "overlay_manifest_sha256": "sha256:" + runtime_manifest_identity(
                "release-b", {"config/tgw-api-config.json": hashlib.sha256(RUNTIME_CONFIG).hexdigest()},
            )["manifest_sha256"],
        },
        "services": ["tgw-api.service"], "health_probes": ["api"],
        "immutable_generation_path": "/opt/TGW/releases/release-b",
        "predecessor_observation_hash": predecessor_hash,
        "nix_system_path": "/nix/store/system-a",
    }
    def stage_runtime(_root_id, generation, _release, _projection, _config, _operation):
        calls.append("stage")
        installed = install_runtime_files(
            root, generation, {"config/tgw-api-config.json": RUNTIME_CONFIG},
        )
        return {
            "status": "STAGED", "receipt": "runtime:b",
            "generation_path": "/opt/TGW/releases/release-b",
            "runtime_manifest_sha256": installed["runtime_manifest_sha256"],
        }
    providers["stage_runtime"] = Mock(side_effect=stage_runtime)
    controller = MountedReleaseController(
        roots={"tgw-staging": root}, artifacts={"artifact:candidate": candidate_archive}, **providers,
    )
    effect = TypedEffect.parse({"kind": "coding-release", "generation": "release-b", "parameters": {
        key: parameters[key] for key in (
            "candidate_commit", "candidate_tree", "archive_sha256", "artifact_ref", "root_id",
            "expected_current", "operation_id", "review_receipt", "controller_receipt", "migration_receipts",
        )
    }})
    registry = TypedEffectHandlerRegistry(
        release_install=lambda _ignored: controller.install(parameters),
        release_rollback=lambda _ignored: controller.rollback(parameters),
        flake_push=Mock(), flake_switch_record=Mock(), dependency_resubmit=Mock(),
    )
    return root, controller, registry, effect, providers, calls, parameters


def test_exact_stage_order_quiesces_before_backup_and_migrates_before_activation(tmp_path):
    _, _, registry, effect, providers, calls, _ = _fixture(tmp_path)
    receipt = AuthorityEffectController(registry, _authority()).execute(
        request_id="request:release-b", effect=effect, executor_principal=EXECUTOR,
    )
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert calls.index("quiesce") < calls.index("backup") < calls.index("migrate") < calls.index("activate") < calls.index("restart") < calls.index("health")
    assert "migration:b" in receipt.evidence
    providers["migrate"].assert_called_once()


def test_health_failure_uses_stage_aware_predecessor_reconciliation(tmp_path):
    _, _, registry, effect, providers, _, _ = _fixture(tmp_path, health_status="UNHEALTHY")
    receipt = AuthorityEffectController(registry, _authority()).execute(
        request_id="request:release-b", effect=effect, executor_principal=EXECUTOR,
    )
    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt == "restore:a"
    providers["reconcile_predecessor"].assert_called_once()


def test_preselection_failure_still_reconciles_without_assuming_selector_receipt(tmp_path):
    _, _, registry, effect, providers, _, _ = _fixture(tmp_path)
    providers["migrate"].side_effect = RuntimeError("migration failed")
    receipt = AuthorityEffectController(registry, _authority()).execute(
        request_id="migration-failure", effect=effect, executor_principal=EXECUTOR,
    )
    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    providers["activate_generation"].assert_not_called()
    providers["reconcile_predecessor"].assert_called_once()


def test_runtime_stage_cannot_mutate_sealed_candidate_before_activation(tmp_path):
    root, _, registry, effect, providers, _, _ = _fixture(tmp_path)
    legitimate_stage = providers["stage_runtime"].side_effect

    def mutate(*args):
        result = legitimate_stage(*args)
        source = root / "releases/release-b/src/tgw/app.py"
        source.chmod(0o600)
        source.write_bytes(b"neighboring code")
        source.chmod(0o400)
        return result

    providers["stage_runtime"].side_effect = mutate
    receipt = AuthorityEffectController(registry, _authority()).execute(
        request_id="runtime-mutation", effect=effect, executor_principal=EXECUTOR,
    )
    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    providers["activate_generation"].assert_not_called()
    providers["reconcile_predecessor"].assert_called_once()


def test_reconciliation_without_predecessor_health_is_ambiguous(tmp_path):
    _, _, registry, effect, providers, _, _ = _fixture(tmp_path, health_status="UNHEALTHY")
    providers["reconcile_predecessor"].return_value = {
        "status": "RESTORED", "receipt": "restore:a", "generation": "release-a",
        "predecessor_healthy": False, "evidence": ["restore:ambiguous"],
    }
    providers["reconcile_predecessor"].side_effect = None
    receipt = AuthorityEffectController(registry, _authority()).execute(
        request_id="ambiguous", effect=effect, executor_principal=EXECUTOR,
    )
    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert "restore:ambiguous" in receipt.evidence


def test_unknown_mount_fails_without_calling_host_providers(tmp_path):
    _, controller, _, _, providers, _, parameters = _fixture(tmp_path)
    try:
        controller.install({**parameters, "root_id": "not-mounted"})
    except ValueError as exc:
        assert "unknown mounted release root" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unknown mount was accepted")
    providers["quiesce_services"].assert_not_called()
