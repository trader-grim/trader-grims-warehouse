import hashlib

import pytest

from tgw.nix_observer_render_evaluation import OUTPUTS, SCHEMA, RenderEvaluationError, canonical, validate_request


def request():
    value = {
        "schema": SCHEMA,
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "artifact_ref": "artifact:sha256:" + "d" * 64,
        "archive_sha256": "sha256:" + "d" * 64,
        "flake_lock_sha256": "sha256:" + "e" * 64,
        "flake_sha256": "sha256:" + "f" * 64,
        "module_sha256": "sha256:" + "1" * 64,
        "launcher_source_sha256": "sha256:" + "2" * 64,
        "observer_source_sha256": "sha256:" + "3" * 64,
        "provider_sha256": "sha256:" + "4" * 64,
        "host_identity_receipt_sha256": "sha256:" + "5" * 64,
        "systemd_analyze_sha256": "sha256:" + "6" * 64,
        "target": "nix-input-observer-rendered-artifacts",
        "system": "x86_64-linux",
        "network_policy": "offline-no-substituters",
        "allow_ifd": False,
        "activate": False,
        "profile_write": False,
        "home_db_write": False,
        "expected_outputs": list(OUTPUTS),
        "expected_metadata_status": "NON_DEPLOYABLE_RENDER_FIXTURE",
        "input_closure_manifest": [
            {
                "node": "nixpkgs",
                "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                "store_path": "/nix/store/11111111111111111111111111111111-source",
                "nar_sha256": "sha256:" + "7" * 64,
            }
        ],
        "input_closure_path_count": 1,
        "systemd_analyze_version": "systemd 257 (257.10)",
        "max_duration_seconds": 900,
        "max_output_bytes": 16 * 1024 * 1024,
    }
    value["input_closure_manifest_sha256"] = "sha256:" + hashlib.sha256(canonical(value["input_closure_manifest"])).hexdigest()
    value["request_sha256"] = "sha256:" + hashlib.sha256(canonical(value)).hexdigest()
    return value


def test_closed_non_deployable_render_request():
    assert validate_request(request())["expected_outputs"] == list(OUTPUTS)


@pytest.mark.parametrize("field,value", [("allow_ifd", True), ("activate", True), ("expected_metadata_status", "DEPLOYABLE"), ("target", "review-egress-systemd-units")])
def test_render_request_mutations_fail_closed(field, value):
    item = request()
    item[field] = value
    unsigned = dict(item)
    unsigned.pop("request_sha256")
    item["request_sha256"] = "sha256:" + hashlib.sha256(canonical(unsigned)).hexdigest()
    with pytest.raises(RenderEvaluationError):
        validate_request(item)
