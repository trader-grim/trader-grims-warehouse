from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect
from tgw.platform_bootstrap import (
    ATTESTATION_KEY_REF,
    MANIFEST_SCHEMA,
    PLAN_COMMIT,
    RETIREMENT_CONDITION,
    SOLUTION_HASH,
    SSH_KEY_REF,
    A3PlatformBootstrapProvider,
    ExternalPrivateKey,
    ImmutableBootstrapReceiptStore,
    digest,
    platform_bootstrap_effect_parameters,
    validate_platform_bootstrap_effect,
    validate_platform_bootstrap_manifest,
)

HEX = "d" * 64
OLD = "/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-nixos-system-tgw-prod-old"
NEW = "/nix/store/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-nixos-system-tgw-prod-new"


def _manifest() -> dict:
    artifact = {"artifact_ref": "artifact:sha256:" + HEX, "sha256": "sha256:" + HEX}
    value = {
        "schema": MANIFEST_SCHEMA,
        "plan_commit": PLAN_COMMIT,
        "solution_hash": SOLUTION_HASH,
        "target_host": "tgw-prod",
        "flake_repository_id": "tgw-flake",
        "flake_commit": "a" * 40,
        "flake_tree": "b" * 40,
        "expected_current_system": OLD,
        "successor_system": NEW,
        "prior_system": OLD,
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
            "attestation_signing": {"ref": ATTESTATION_KEY_REF, "sha256": "sha256:" + HEX},
            "ssh_identity": {"ref": SSH_KEY_REF, "sha256": "sha256:" + HEX},
        },
        "operation_id": "bootstrap:a3-platform-1",
        "review_receipt": "review:sha256:" + "1" * 64,
        "controller_receipt": "controller:sha256:" + "2" * 64,
        "health_receipt": "health:sha256:" + "3" * 64,
        "probe_receipt": "probe:sha256:" + "4" * 64,
        "retirement_condition": RETIREMENT_CONDITION,
    }
    value["manifest_sha256"] = digest(value)
    return value


def _rehash(value: dict) -> dict:
    value.pop("manifest_sha256", None)
    value["manifest_sha256"] = digest(value)
    return value


def _external_key(tmp_path: Path, name: str, ref: str) -> tuple[ExternalPrivateKey, bytes]:
    content = ("test-only-private-material-" + name).encode()
    path = tmp_path / name
    path.write_bytes(content)
    path.chmod(0o400)
    checksum = "sha256:" + __import__("hashlib").sha256(content).hexdigest()
    return ExternalPrivateKey(ref, path, checksum), content


def _provider(tmp_path: Path, *, current=OLD, health=None, probe=None, activate=None, rollback=None):
    tmp_path.mkdir(exist_ok=True)
    manifest = _manifest()
    attestation, attestation_bytes = _external_key(tmp_path, "attestation.key", ATTESTATION_KEY_REF)
    ssh, ssh_bytes = _external_key(tmp_path, "ssh.key", SSH_KEY_REF)
    manifest["credential_bindings"]["attestation_signing"]["sha256"] = attestation.sha256
    manifest["credential_bindings"]["ssh_identity"]["sha256"] = ssh.sha256
    _rehash(manifest)
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    provider = A3PlatformBootstrapProvider(
        manifest,
        attestation_key=attestation,
        ssh_key=ssh,
        receipts=ImmutableBootstrapReceiptStore(receipts, trusted_uid=os.geteuid()),
        current_system=lambda: current,
        activate_successor=activate or Mock(return_value={"status": "activated", "system": NEW}),
        verify_health=health or Mock(return_value={"status": "healthy", "receipt": manifest["health_receipt"]}),
        verify_probe=probe or Mock(return_value={"status": "passed", "receipt": manifest["probe_receipt"]}),
        activate_prior=rollback or Mock(return_value={"status": "activated", "system": OLD}),
        trusted_key_uid=os.geteuid(),
    )
    return provider, (attestation_bytes, ssh_bytes)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(target_host="other"),
        lambda value: value.update(successor_system=value["expected_current_system"]),
        lambda value: value["artifacts"].update(command={"artifact_ref": "artifact:sha256:" + HEX, "sha256": "sha256:" + HEX}),
        lambda value: value["artifacts"]["native_wrapper"].update(artifact_ref="/tmp/wrapper"),
        lambda value: value["credential_bindings"]["ssh_identity"].update(ref="credential:tgw-review:codex"),
        lambda value: value.update(flake_tree="0" * 39),
    ],
)
def test_manifest_rejects_host_closure_artifact_key_and_flake_broadening(mutate):
    value = copy.deepcopy(_manifest())
    mutate(value)
    _rehash(value)
    with pytest.raises(ValueError):
        validate_platform_bootstrap_manifest(value)


def test_effect_rejects_semantically_old_review_egress_schema_and_noncanonical_json():
    with pytest.raises(ValueError, match="not exact"):
        validate_platform_bootstrap_effect(
            {
                "target_host": "tgw-prod",
                "broker_source_sha256": HEX,
                "egress_contract_sha256": HEX,
            }
        )
    parameters = platform_bootstrap_effect_parameters(_manifest())
    parameters["manifest_json"] = json.dumps(json.loads(parameters["manifest_json"]), indent=2)
    with pytest.raises(ValueError, match="canonical"):
        validate_platform_bootstrap_effect(parameters)


def test_provider_enforces_current_cas_and_one_attempt_replay(tmp_path):
    wrong, _ = _provider(tmp_path / "wrong", current=NEW)
    parameters = platform_bootstrap_effect_parameters(wrong.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    with pytest.raises(ValueError, match="expected-current CAS"):
        wrong.install(parameters)

    good, _ = _provider(tmp_path / "good")
    parameters = platform_bootstrap_effect_parameters(good.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    assert len(good.install(parameters)["evidence"]) == 4
    with pytest.raises(ValueError, match="already recorded"):
        good.install(parameters)


def test_partial_apply_failure_rolls_back_exact_prior_closure_through_authority(tmp_path):
    health = Mock(side_effect=RuntimeError("health unavailable after activation"))
    rollback = Mock(return_value={"status": "activated", "system": OLD})
    provider, _ = _provider(tmp_path, health=health, rollback=rollback)
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=provider.install,
        bootstrap_rollback=provider.rollback,
    )
    effect = TypedEffect.parse(
        {
            "kind": "approval-platform-bootstrap-deployment",
            "generation": "a3-platform-bootstrap-1",
            "parameters": platform_bootstrap_effect_parameters(provider.manifest),
        }
    )

    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "bootstrap:consumed"})).execute(
        request_id="bootstrap:a3", effect=effect
    )

    assert receipt.outcome is EffectOutcome.ROLLED_BACK
    assert receipt.rollback_receipt.startswith("platform-bootstrap-receipt:sha256:")
    rollback.assert_called_once_with(provider.manifest)


def test_private_key_bytes_never_enter_manifest_effect_receipts_or_evidence(tmp_path):
    provider, secret_values = _provider(tmp_path)
    parameters = platform_bootstrap_effect_parameters(provider.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    evidence = provider.install(parameters)["evidence"]
    exposed = parameters["manifest_json"].encode() + b"\n".join(path.read_bytes() for path in (tmp_path / "receipts").iterdir())
    exposed += json.dumps(evidence).encode()
    for secret in secret_values:
        assert secret not in exposed
    assert b"PRIVATE KEY" not in exposed


def test_external_private_keys_reject_wrong_mode_digest_ref_and_nix_store_path(tmp_path):
    key, _ = _external_key(tmp_path, "private.key", ATTESTATION_KEY_REF)
    expected = {"ref": ATTESTATION_KEY_REF, "sha256": key.sha256}
    key.path.chmod(0o600)
    with pytest.raises(ValueError, match="0400"):
        key.validate(expected, trusted_uid=os.geteuid())
    key.path.chmod(0o400)
    with pytest.raises(ValueError, match="reference or digest"):
        key.validate({**expected, "ref": SSH_KEY_REF}, trusted_uid=os.geteuid())
    with pytest.raises(ValueError, match="reference or digest"):
        ExternalPrivateKey(key.ref, key.path, "sha256:" + "0" * 64).validate(expected, trusted_uid=os.geteuid())
    with pytest.raises(ValueError, match="non-Nix"):
        ExternalPrivateKey(key.ref, Path("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-secret"), key.sha256).validate(
            expected, trusted_uid=os.geteuid()
        )


def test_nix_leaf_is_disabled_by_default_and_contains_only_fixed_public_material():
    module = Path("nix/a3-platform-bootstrap.nix").read_text()
    package = Path("nix/a3-platform-bootstrap-package.nix").read_text()
    assert "mkEnableOption" in module
    assert "authorizedKeys.keys = [ cfg.sshAuthorizedPublicKey ]" in module
    assert 'NOPASSWD: ${wrapper} ""' in module
    assert "attestationPublicKey" in module
    assert "PRIVATE KEY" not in package
    assert "cleanSource" not in package
    assert "nix_observer_render_remote.py" in package
    assert "nix_observer_render_helper.py" in package
    assert "tgw_nix_observer_render_transport.c" in package
