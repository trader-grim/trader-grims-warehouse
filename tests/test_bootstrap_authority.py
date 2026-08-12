import json
from datetime import datetime, timedelta, timezone

import pytest

from tgw.bootstrap_authority import BootstrapGrant, BootstrapSessionAuthority


def _grant(**changes):
    effect = {
        "kind": "approval-platform-bootstrap-deployment",
        "generation": "platform-bb5c67d",
        "parameters": {
            "target_host": "tgw-prod", "flake_repository_id": "tgw-flake", "flake_commit": "b" * 40, "flake_tree": "c" * 40,
            "expected_current_system": "/nix/store/aaaaaaaa-nixos-system-tgw-prod-old", "successor_system": "/nix/store/bbbbbbbb-nixos-system-tgw-prod-new",
            "credential_ref": "credential:tgw-review:codex", "credential_sha256": "d" * 64, "broker_source_sha256": "d" * 64,
            "namespace_source_sha256": "d" * 64, "nix_module_sha256": "d" * 64, "egress_contract_sha256": "d" * 64,
            "install_contract_sha256": "d" * 64, "review_receipt": "review:passed", "controller_receipt": "controller:passed",
            "network_attestation_receipt": "network:passed", "probe_receipt": "probes:passed", "operation_id": "bootstrap:review-transport-1",
        },
    }
    value = {
        "plan_commit": "f" * 40,
        "solution_hash": "sha256:" + "a" * 64,
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
