import json
from datetime import datetime, timedelta, timezone

import pytest

from tgw.bootstrap_authority import BootstrapGrant, BootstrapSessionAuthority
from tgw.platform_bootstrap import (
    ATTESTATION_KEY_REF,
    MANIFEST_SCHEMA,
    PLAN_COMMIT,
    RETIREMENT_CONDITION,
    SOLUTION_HASH,
    SSH_KEY_REF,
    digest,
    platform_bootstrap_effect_parameters,
)


def _parameters():
    checksum = "sha256:" + "d" * 64
    artifact = {"artifact_ref": "artifact:" + checksum, "sha256": checksum}
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "plan_commit": PLAN_COMMIT,
        "solution_hash": SOLUTION_HASH,
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "flake_commit": "b" * 40,
        "flake_tree": "c" * 40,
        "expected_current_system": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixos-system-tgw-prod-old",
        "successor_system": "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-nixos-system-tgw-prod-new",
        "prior_system": "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixos-system-tgw-prod-old",
        "artifacts": {
            name: dict(artifact)
            for name in (
                "native_wrapper",
                "remote_bootstrap",
                "helper",
                "wrapper_config",
                "composition",
                "prerequisite_receipt",
                "attestation_public_key",
                "ssh_authorized_public_key",
                "nix_module",
                "package",
            )
        },
        "credential_bindings": {
            "attestation_signing": {"ref": ATTESTATION_KEY_REF, "sha256": checksum},
            "ssh_identity": {"ref": SSH_KEY_REF, "sha256": checksum},
        },
        "operation_id": "bootstrap:a3-platform-1",
        "candidate_receipt": "candidate:sha256:" + "0" * 64,
        "review_receipt": "review:sha256:" + "1" * 64,
        "controller_receipt": "controller:sha256:" + "2" * 64,
        "activation_provider_receipt": "activation-provider:sha256:" + "5" * 64,
        "health_receipt": "health:sha256:" + "3" * 64,
        "probe_receipt": "probe:sha256:" + "4" * 64,
        "retirement_condition": RETIREMENT_CONDITION,
        "live_flake_gate": "EXTERNAL_TGW_PROD_FLAKE_IMPORT_BUILD_REQUIRED",
    }
    manifest["manifest_sha256"] = digest(manifest)
    return platform_bootstrap_effect_parameters(manifest)


def _grant(**changes):
    effect = {
        "kind": "approval-platform-bootstrap-deployment",
        "generation": "platform-bb5c67d",
        "parameters": _parameters(),
    }
    value = {
        "plan_commit": PLAN_COMMIT,
        "solution_hash": SOLUTION_HASH,
        "target_host": "tgw-prod",
        "root_id": "production-releases",
        "candidate_commit": "b" * 40,
        "effect": effect,
        "expires_at": "2030-01-01T00:00:00Z",
        "deployment_uses": 1,
        "retirement_condition": "W10:canonical-gate-operational",
    }
    value.update(changes)
    return BootstrapGrant.parse(value)


def test_exact_grant_is_consumed_once_to_immutable_bound_receipt(tmp_path):
    grant = _grant()
    path = tmp_path / "bootstrap-consumed.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit)
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)

    receipt = authority.consume(grant.grant_id, effect_hash=grant.effect.effect_hash, generation=grant.effect.generation, now=now)

    assert receipt == json.loads(path.read_text())
    assert receipt["target_host"] == "tgw-prod"
    assert receipt["candidate_commit"] == "b" * 40
    assert receipt["receipt_id"].startswith("bootstrap-consumption:sha256:")
    with pytest.raises(ValueError, match="already consumed"):
        authority.consume(grant.grant_id, effect_hash=grant.effect.effect_hash, generation=grant.effect.generation, now=now + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("request_id", "wrong", "request identity"),
        ("effect_hash", "effect:sha256:" + "0" * 64, "effect identity"),
        ("generation", "other", "effect identity"),
    ],
)
def test_mismatch_does_not_spend_grant(tmp_path, field, value, message):
    grant = _grant()
    path = tmp_path / "receipt.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit)
    arguments = {"request_id": grant.grant_id, "effect_hash": grant.effect.effect_hash, "generation": grant.effect.generation}
    arguments[field] = value
    with pytest.raises(ValueError, match=message):
        authority.consume(**arguments, now=datetime(2029, 1, 1, tzinfo=timezone.utc))
    assert not path.exists()


def test_stale_plan_and_expired_or_broadened_grants_fail_closed(tmp_path):
    grant = _grant()
    with pytest.raises(ValueError, match="different Plan commit"):
        BootstrapSessionAuthority(grant, receipt_path=tmp_path / "receipt", current_plan_commit="0" * 40)
    authority = BootstrapSessionAuthority(grant, receipt_path=tmp_path / "receipt", current_plan_commit=grant.plan_commit)
    with pytest.raises(ValueError, match="expired"):
        authority.consume(grant.grant_id, effect_hash=grant.effect.effect_hash, generation=grant.effect.generation, now=datetime(2031, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="exactly one"):
        _grant(deployment_uses=2)


def test_bootstrap_grant_rejects_legacy_coding_release_even_if_target_looks_related():
    legacy = {
        "kind": "coding-release", "generation": "g",
        "parameters": {"root_id": "production-releases", "candidate_commit": "b" * 40},
    }
    with pytest.raises(ValueError, match="exact platform bootstrap"):
        _grant(effect=legacy)
