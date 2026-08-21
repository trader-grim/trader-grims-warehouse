import base64
import copy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import tgw.application_release_remote as remote_module
from tgw.application_release_remote import (
    MIGRATION_PATHS,
    SCHEMA,
    ApplicationReleaseRemoteError,
    _hash,
    _reconcile,
    _successor_identity,
    execute,
    validate_request,
)


def _request():
    archive = b"exact archive"
    runtime = b'{"schema":"tgw-production-operational-config/v1"}\n'

    def sha(raw):
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    def h(digit):
        return "sha256:" + digit * 64

    parameters = {
        "generation": "release-b",
        "candidate_commit": "a" * 40,
        "candidate_tree": "b" * 40,
        "archive_sha256": sha(archive),
        "artifact_ref": "artifact:candidate",
        "root_id": "production-releases",
        "expected_current": "release-a",
        "operation_id": "w09-operation",
        "review_receipt": h("1"),
        "controller_receipt": h("2"),
        "migration_receipts": [{"migration_path": path, "migration_sha256": h(str(index + 3)), "receipt_hash": h(str(index + 5))} for index, path in enumerate(MIGRATION_PATHS)],
        "projection": {"release_path": "agent-services/plan-runtime/projection.json", "content_sha256": h("7")},
        "runtime_config": {
            "artifact_ref": "config:candidate",
            "generation_path": "config/tgw-api-config.json",
            "content_sha256": sha(runtime),
            "overlay_manifest_sha256": h("8"),
            "config_schema": "tgw-production-operational-config/v1",
            "executor_principal": "executor:release",
            "operator_principals": ["operator:api"],
            "executor_credential_env": "TGW_AUTHORITY_TOKEN",
            "credential_reference": "credential:release",
            "trusted_root": "/opt/TGW/releases/release-b",
            "trusted_uid": 0,
            "forbidden_paths": ["/opt/TGW/src", "/run/tgw/no-local-plan"],
        },
        "services": ["tgw-api.service"],
        "health_probes": ["http://127.0.0.1:7373/health"],
        "nix_system_path": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-system",
        "predecessor_observation_ref": "observation:a",
        "predecessor_observation_hash": h("9"),
        "provider_observation_ref": "observation:w09-provider",
        "provider_observation_hash": h("0"),
        "immutable_generation_path": "/opt/TGW/releases/release-b",
        "predecessor": {
            "generation": "release-a",
            "selector_target": "/opt/TGW/releases/release-a",
            "commit": "c" * 40,
            "tree": "d" * 40,
            "archive_sha256": h("a"),
            "release_manifest_hash": h("b"),
            "content_manifest_sha256": h("c"),
            "projection_sha256": None,
            "runtime_config_sha256": h("e"),
            "database_identity_sha256": h("f"),
            "runtime_config_uid": 0,
            "runtime_config_gid": 995,
            "runtime_config_mode": 0o640,
            "runtime_config_size": len(runtime),
        },
    }
    unsigned = {
        "schema": SCHEMA,
        "action": "install",
        "parameters": parameters,
        "archive_b64": base64.b64encode(archive).decode(),
        "config_b64": base64.b64encode(runtime).decode(),
    }
    return {**unsigned, "request_hash": _hash(unsigned)}


def _config():
    return {
        "root_id": "production-releases",
        "services": ["tgw-api.service"],
        "health_probes": ["http://127.0.0.1:7373/health"],
        "max_archive_bytes": 1024,
        "max_config_bytes": 1024,
    }


def test_request_rejects_migration_traversal_or_reordering_before_host_action():
    request = _request()
    assert validate_request(request, _config())["parameters"]["migration_receipts"][0]["migration_path"] == MIGRATION_PATHS[0]
    bad = copy.deepcopy(request)
    bad["parameters"]["migration_receipts"][0]["migration_path"] = "../live_schema.sql"
    unsigned = {key: value for key, value in bad.items() if key != "request_hash"}
    bad["request_hash"] = _hash(unsigned)
    with pytest.raises(ApplicationReleaseRemoteError, match="migration receipt"):
        validate_request(bad, _config())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("generation", "current"),
        ("generation", "releases"),
        ("generation", "operations"),
        ("generation", "receipts"),
        ("generation", "refusals"),
        ("generation", ".stage-candidate"),
        ("expected_current", ".current-candidate"),
    ],
)
def test_request_rejects_reserved_generation_names_before_host_action(field, value):
    bad = _request()
    bad["parameters"][field] = value
    if field == "generation":
        bad["parameters"]["immutable_generation_path"] = f"/opt/TGW/releases/{value}"
    unsigned = {key: item for key, item in bad.items() if key != "request_hash"}
    bad["request_hash"] = _hash(unsigned)
    with pytest.raises(ApplicationReleaseRemoteError, match="unsafe generation name"):
        validate_request(bad, _config())


def test_w09_install_is_typed_hold_before_any_host_mutation(tmp_path):
    config = {
        **_config(),
        "helper_sha256": "sha256:" + "a" * 64,
        "config_sha256": "sha256:" + "b" * 64,
        "receipt_root": str(tmp_path / "receipts"),
        "release_root": str(tmp_path / "releases"),
    }
    runtime = Mock()

    response = execute(_request(), config, runtime)

    assert response["status"] == "HOLD"
    assert response["reason"] == "W16_ADMISSION_REQUIRED"
    assert response["evidence"] == [
        "w09-install:retired-before-mutation",
        "required-provider:app-release-install/v1",
    ]
    assert runtime.mock_calls == []
    assert not (tmp_path / "receipts").exists()
    assert not (tmp_path / "releases").exists()


def test_reconcile_restore_marker_without_exact_predecessor_state_is_ambiguous(tmp_path):
    root = tmp_path / "root"
    (root / "receipts").mkdir(parents=True)
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    dump = backup_root / "w09-operation.dump"
    dump.write_bytes(b"database backup")
    runtime = Mock()
    result = _reconcile(
        {"operation_id": "w09-operation", "generation": "release-b", "expected_current": "release-a"},
        {
            "release_root": str(root),
            "receipt_root": str(receipt_root),
            "backup_root": str(backup_root),
            "active_config_path": str(tmp_path / "active.json"),
            "services": ["tgw-api.service"],
        },
        runtime,
        {
            "operation_id": "w09-operation",
            "stages": ["migration-restore-required"],
            "evidence": [],
        },
    )
    assert result["status"] == "AMBIGUOUS"
    runtime.restore.assert_not_called()


def test_reconcile_never_claims_restored_while_selector_names_neighbor_generation(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    (root / "receipts").mkdir(parents=True)
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    runtime = Mock()
    monkeypatch.setattr(remote_module, "current_generation", lambda _root: "release-c")
    result = _reconcile(
        {
            "operation_id": "w09-operation",
            "generation": "release-b",
            "expected_current": "release-a",
            "predecessor": {},
        },
        {
            "release_root": str(root),
            "receipt_root": str(receipt_root),
            "backup_root": str(backup_root),
            "active_config_path": str(tmp_path / "active.json"),
            "services": ["tgw-api.service"],
            "unrelated_paths": [],
        },
        runtime,
        {"operation_id": "w09-operation", "stages": [], "evidence": []},
    )
    assert result["status"] == "AMBIGUOUS"
    assert result["generation"] == "release-c"
    runtime.health.assert_not_called()


def test_reconcile_rechecks_exact_predecessor_after_service_restart(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    (root / "receipts").mkdir(parents=True)
    receipt_root = tmp_path / "receipts"
    receipt_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    runtime = Mock()
    monkeypatch.setattr(remote_module, "current_generation", lambda _root: "release-a")
    observations = Mock(
        side_effect=[
            {"generation": "release-a"},
            ApplicationReleaseRemoteError("post-restart predecessor changed"),
        ]
    )
    monkeypatch.setattr(remote_module, "_predecessor_identity", observations)

    result = _reconcile(
        {
            "operation_id": "w09-operation",
            "generation": "release-b",
            "expected_current": "release-a",
            "predecessor": {"generation": "release-a"},
        },
        {
            "release_root": str(root),
            "receipt_root": str(receipt_root),
            "backup_root": str(backup_root),
            "active_config_path": str(tmp_path / "active.json"),
            "services": ["tgw-api.service"],
            "unrelated_paths": [str(tmp_path / "unrelated")],
        },
        runtime,
        {"operation_id": "w09-operation", "stages": [], "evidence": []},
    )

    assert result["status"] == "AMBIGUOUS"
    assert observations.call_count == 2
    runtime.systemctl.assert_called_once_with("restart", ["tgw-api.service"])
    runtime.health.assert_called_once_with()


def test_successor_postrestart_observation_rejects_database_mutation(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "root"
    release = root / "releases" / "release-b"
    release.mkdir(parents=True)
    selector = root / "current"
    selector.symlink_to(Path("releases") / "release-b")
    projection = release / "agent-services/plan-runtime/projection.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(b"projection")
    manifest = {
        "commit": "a" * 40,
        "git_tree": "b" * 40,
        "archive_sha256": "1" * 64,
        "content_manifest_sha256": "2" * 64,
    }
    (release / ".release-manifest.json").write_text(json.dumps(manifest))
    active = tmp_path / "active.json"
    active.write_bytes(b"runtime config")
    active.chmod(0o640)
    runtime = Mock()
    runtime.database_identity.side_effect = ["sha256:" + "3" * 64, "sha256:" + "4" * 64]
    monkeypatch.setattr(remote_module, "current_generation", lambda _root: "release-b")
    monkeypatch.setattr(remote_module, "verify", lambda _root, _generation: {"status": "PASS"})
    parameters = {
        "generation": "release-b",
        "immutable_generation_path": str(release),
        "candidate_commit": "a" * 40,
        "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "1" * 64,
        "projection": {
            "release_path": "agent-services/plan-runtime/projection.json",
            "content_sha256": remote_module._hash_bytes(b"projection"),
        },
        "runtime_config": {"content_sha256": remote_module._hash_bytes(b"runtime config")},
    }
    config = {
        "release_root": str(root),
        "current_selector": str(selector),
        "active_config_path": str(active),
        "active_config_metadata": {
            "uid": active.stat().st_uid,
            "gid": active.stat().st_gid,
            "mode": 0o640,
        },
    }
    before = _successor_identity(
        parameters,
        config,
        runtime,
        runtime_config_size=len(b"runtime config"),
    )
    with pytest.raises(ApplicationReleaseRemoteError, match="successor identity"):
        _successor_identity(
            parameters,
            config,
            runtime,
            runtime_config_size=len(b"runtime config"),
            expected=before,
        )
