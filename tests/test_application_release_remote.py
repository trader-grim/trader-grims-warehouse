import base64
import copy
import hashlib
from unittest.mock import Mock

import pytest

from tgw.application_release_remote import (
    ApplicationReleaseRemoteError,
    MIGRATION_PATHS,
    SCHEMA,
    _hash,
    _reconcile,
    validate_request,
)


def _request():
    archive = b"exact archive"
    runtime = b'{"schema":"tgw-production-operational-config/v1"}\n'
    sha = lambda raw: "sha256:" + hashlib.sha256(raw).hexdigest()
    h = lambda digit: "sha256:" + digit * 64
    parameters = {
        "generation": "release-b", "candidate_commit": "a" * 40,
        "candidate_tree": "b" * 40, "archive_sha256": sha(archive),
        "artifact_ref": "artifact:candidate", "root_id": "production-releases",
        "expected_current": "release-a", "operation_id": "w09-operation",
        "review_receipt": h("1"), "controller_receipt": h("2"),
        "migration_receipts": [
            {"migration_path": path, "migration_sha256": h(str(index + 3)), "receipt_hash": h(str(index + 5))}
            for index, path in enumerate(MIGRATION_PATHS)
        ],
        "projection": {"release_path": "agent-services/plan-runtime/projection.json", "content_sha256": h("7")},
        "runtime_config": {
            "artifact_ref": "config:candidate", "generation_path": "config/tgw-api-config.json",
            "content_sha256": sha(runtime), "overlay_manifest_sha256": h("8"),
            "config_schema": "tgw-production-operational-config/v1",
            "executor_principal": "executor:release", "operator_principals": ["operator:api"],
            "executor_credential_env": "TGW_AUTHORITY_TOKEN", "credential_reference": "credential:release",
            "trusted_root": "/opt/TGW/releases/release-b", "trusted_uid": 0,
            "forbidden_paths": ["/opt/TGW/src", "/run/tgw/no-local-plan"],
        },
        "services": ["tgw-api.service"], "health_probes": ["http://127.0.0.1:7373/health"],
        "nix_system_path": "/nix/store/0123456789abcdfghijklmnpqrsvwxyz-system",
        "predecessor_observation_ref": "observation:a", "predecessor_observation_hash": h("9"),
        "provider_observation_ref": "observation:w09-provider", "provider_observation_hash": h("0"),
        "immutable_generation_path": "/opt/TGW/releases/release-b",
        "predecessor": {
            "generation": "release-a", "selector_target": "/opt/TGW/releases/release-a",
            "commit": "c" * 40, "tree": "d" * 40, "archive_sha256": h("a"),
            "release_manifest_hash": h("b"), "content_manifest_sha256": h("c"),
            "projection_sha256": None, "runtime_config_sha256": h("e"),
        },
    }
    unsigned = {
        "schema": SCHEMA, "action": "install", "parameters": parameters,
        "archive_b64": base64.b64encode(archive).decode(),
        "config_b64": base64.b64encode(runtime).decode(),
    }
    return {**unsigned, "request_hash": _hash(unsigned)}


def _config():
    return {
        "root_id": "production-releases", "services": ["tgw-api.service"],
        "health_probes": ["http://127.0.0.1:7373/health"],
        "max_archive_bytes": 1024, "max_config_bytes": 1024,
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


def test_reconcile_restores_database_after_pre_dispatch_marker_even_without_applied_stage(tmp_path):
    root = tmp_path / "root"
    (root / "receipts").mkdir(parents=True)
    receipt_root = tmp_path / "receipts"; receipt_root.mkdir(mode=0o700)
    backup_root = tmp_path / "backups"; backup_root.mkdir(mode=0o700)
    dump = backup_root / "w09-operation.dump"; dump.write_bytes(b"database backup")
    runtime = Mock()
    result = _reconcile(
        {"operation_id": "w09-operation", "generation": "release-b", "expected_current": "release-a"},
        {
            "release_root": str(root), "receipt_root": str(receipt_root),
            "backup_root": str(backup_root), "active_config_path": str(tmp_path / "active.json"),
            "services": ["tgw-api.service"],
        },
        runtime,
        {
            "operation_id": "w09-operation",
            "stages": ["migration-restore-required"], "evidence": [],
        },
    )
    assert result["status"] == "RESTORED"
    runtime.restore.assert_called_once_with(dump)
