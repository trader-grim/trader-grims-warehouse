from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import tgw.platform_bootstrap as bootstrap_module
from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.plan_authority import TypedEffect
from tgw.platform_bootstrap import (
    ATTESTATION_KEY_REF,
    AUTHORIZED_KEY_PREFIX,
    MANIFEST_SCHEMA,
    PLAN_COMMIT,
    RETIREMENT_CONDITION,
    SOLUTION_HASH,
    SSH_KEY_REF,
    A3PlatformBootstrapProvider,
    BootstrapStateAmbiguous,
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
        "candidate_receipt": "candidate:sha256:" + "0" * 64,
        "review_receipt": "review:sha256:" + "1" * 64,
        "controller_receipt": "controller:sha256:" + "2" * 64,
        "activation_provider_receipt": "activation-provider:sha256:" + "5" * 64,
        "health_receipt": "health:sha256:" + "3" * 64,
        "probe_receipt": "probe:sha256:" + "4" * 64,
        "retirement_condition": RETIREMENT_CONDITION,
        "live_flake_gate": "EXTERNAL_TGW_PROD_FLAKE_IMPORT_BUILD_REQUIRED",
    }
    value["manifest_sha256"] = digest(value)
    return value


def _rehash(value: dict) -> dict:
    value.pop("manifest_sha256", None)
    value["manifest_sha256"] = digest(value)
    return value


def _external_key(
    tmp_path: Path, name: str, ref: str, private: Ed25519PrivateKey, *, openssh: bool
) -> tuple[ExternalPrivateKey, bytes]:
    content = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH if openssh else serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / name
    path.write_bytes(content)
    path.chmod(0o400)
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    return ExternalPrivateKey(ref, path, checksum), content


def _record(tmp_path: Path, name: str, value: dict) -> dict:
    path = tmp_path / (name + ".json")
    value["record_path"] = str(path)
    value["receipt_sha256"] = digest(value)
    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    path.chmod(0o400)
    return value


def _materialize(tmp_path: Path, manifest: dict, name: str, content: bytes, mode: int = 0o444) -> dict:
    path = tmp_path / (name + ".artifact")
    path.write_bytes(content)
    path.chmod(mode)
    checksum = "sha256:" + hashlib.sha256(content).hexdigest()
    manifest["artifacts"][name] = {"artifact_ref": "artifact:" + checksum, "sha256": checksum}
    return {
        "schema": "tgw-a3-platform-bootstrap-artifact/v1",
        "name": name,
        "artifact_ref": "artifact:" + checksum,
        "path": str(path),
        "sha256": checksum,
        "size": len(content),
        "owner_uid": os.geteuid(),
        "mode": f"{mode:04o}",
    }


def _provider(tmp_path: Path, *, current=OLD, health=None, probe=None, activate=None, rollback=None):
    tmp_path.mkdir(exist_ok=True)
    manifest = _manifest()
    attestation_private = Ed25519PrivateKey.from_private_bytes(b"\x01" * 32)
    ssh_private = Ed25519PrivateKey.from_private_bytes(b"\x02" * 32)
    attestation, attestation_bytes = _external_key(
        tmp_path, "attestation.key", ATTESTATION_KEY_REF, attestation_private, openssh=False
    )
    ssh, ssh_bytes = _external_key(tmp_path, "ssh.key", SSH_KEY_REF, ssh_private, openssh=True)
    manifest["credential_bindings"]["attestation_signing"]["sha256"] = attestation.sha256
    manifest["credential_bindings"]["ssh_identity"]["sha256"] = ssh.sha256
    attestation_public = attestation_private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    ssh_public = ssh_private.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode("ascii")
    authorized = (AUTHORIZED_KEY_PREFIX + ssh_public.removeprefix("ssh-ed25519 ")).encode()
    artifact_records = {}
    for name in manifest["artifacts"]:
        content = (
            attestation_public
            if name == "attestation_public_key"
            else authorized
            if name == "ssh_authorized_public_key"
            else ("test-public-artifact-" + name).encode()
        )
        artifact_records[name] = _materialize(tmp_path, manifest, name, content)
    candidate = _record(
        tmp_path,
        "candidate-record",
        {
            "schema": "tgw-a3-platform-bootstrap-candidate/v1",
            "status": "REVIEWED_IMMUTABLE",
            "flake_commit": manifest["flake_commit"],
            "flake_tree": manifest["flake_tree"],
            "successor_system": manifest["successor_system"],
            "artifacts": copy.deepcopy(manifest["artifacts"]),
        }
    )
    manifest["candidate_receipt"] = "candidate:" + candidate["receipt_sha256"]
    review = _record(
        tmp_path,
        "review-record",
        {
            "schema": "tgw-a3-platform-bootstrap-review/v1",
            "status": "PASS",
            "candidate_receipt": manifest["candidate_receipt"],
        }
    )
    manifest["review_receipt"] = "review:" + review["receipt_sha256"]
    controller = _record(
        tmp_path,
        "controller-record",
        {
            "schema": "tgw-a3-platform-bootstrap-controller/v1",
            "status": "PASS",
            "candidate_receipt": manifest["candidate_receipt"],
            "review_receipt": manifest["review_receipt"],
        }
    )
    manifest["controller_receipt"] = "controller:" + controller["receipt_sha256"]
    closure = _record(
        tmp_path,
        "closure-record",
        {
            "schema": "tgw-a3-platform-bootstrap-closure-membership/v1",
            "status": "EXACT_MEMBER",
            "successor_system": manifest["successor_system"],
            "candidate_receipt": manifest["candidate_receipt"],
            "flake_commit": manifest["flake_commit"],
            "flake_tree": manifest["flake_tree"],
            "artifacts": copy.deepcopy(manifest["artifacts"]),
        }
    )
    manifest["activation_provider_receipt"] = "activation-provider:" + closure["receipt_sha256"]
    _rehash(manifest)
    records = {
        **{record["artifact_ref"]: record for record in artifact_records.values()},
        manifest["candidate_receipt"]: candidate,
        manifest["review_receipt"]: review,
        manifest["controller_receipt"]: controller,
    }
    state = [current]

    def default_activate(value):
        state[0] = value["successor_system"]
        return {
            "status": "activated",
            "from_system": value["prior_system"],
            "to_system": value["successor_system"],
            "provider_receipt": value["activation_provider_receipt"],
            "receipt": "activation:sha256:" + "6" * 64,
        }

    def default_rollback(value):
        state[0] = value["prior_system"]
        return {
            "status": "activated",
            "from_system": value["successor_system"],
            "to_system": value["prior_system"],
            "receipt": "activation:sha256:" + "7" * 64,
        }
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    provider = A3PlatformBootstrapProvider(
        manifest,
        attestation_key=attestation,
        ssh_key=ssh,
        receipts=ImmutableBootstrapReceiptStore(receipts, trusted_uid=os.geteuid()),
        current_system=lambda: state[0],
        activate_successor=activate or default_activate,
        verify_health=health or Mock(return_value={"status": "healthy", "receipt": manifest["health_receipt"]}),
        verify_probe=probe or Mock(return_value={"status": "passed", "receipt": manifest["probe_receipt"]}),
        activate_prior=rollback or default_rollback,
        resolve_record=lambda reference: records[reference],
        resolve_closure=lambda system: closure if system == NEW else {},
        trusted_key_uid=os.geteuid(),
    )
    return provider, (attestation_bytes, ssh_bytes), state, records, closure


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
    wrong, _, _, _, _ = _provider(tmp_path / "wrong", current=NEW)
    parameters = platform_bootstrap_effect_parameters(wrong.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    with pytest.raises(BootstrapStateAmbiguous, match="expected-current CAS"):
        wrong.install(parameters)

    good, _, _, _, _ = _provider(tmp_path / "good")
    parameters = platform_bootstrap_effect_parameters(good.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    assert len(good.install(parameters)["evidence"]) == 8
    with pytest.raises(BootstrapStateAmbiguous, match="attempt persistence"):
        good.install(parameters)


def test_install_cas_is_immediately_before_activation_and_successor_readback_precedes_health(tmp_path):
    provider, _, state, _, _ = _provider(tmp_path / "cas")
    parameters = {**platform_bootstrap_effect_parameters(provider.manifest), "generation": "a3-platform-bootstrap-1"}
    persist = provider.receipts.persist

    def drift_after_attempt(value, *, phase):
        reference = persist(value, phase=phase)
        if phase == "attempt":
            state[0] = "/nix/store/cccccccccccccccccccccccccccccccc-nixos-system-tgw-prod-third"
        return reference

    provider.receipts.persist = drift_after_attempt
    provider.activate_successor = Mock()
    with pytest.raises(BootstrapStateAmbiguous, match="immediately before activation"):
        provider.install(parameters)
    provider.activate_successor.assert_not_called()

    provider, _, _, _, _ = _provider(tmp_path / "readback")
    provider.activate_successor = Mock(
        return_value={
            "status": "activated",
            "from_system": OLD,
            "to_system": NEW,
            "provider_receipt": provider.manifest["activation_provider_receipt"],
            "receipt": "activation:sha256:" + "6" * 64,
        }
    )
    provider.verify_health = Mock()
    parameters = {**platform_bootstrap_effect_parameters(provider.manifest), "generation": "a3-platform-bootstrap-1"}
    with pytest.raises(BootstrapStateAmbiguous, match="successor readback"):
        provider.install(parameters)
    provider.verify_health.assert_not_called()


def test_rollback_cas_prior_noop_successor_restore_and_third_state_ambiguity(tmp_path):
    prior, _, _, _, _ = _provider(tmp_path / "prior")
    parameters = {**platform_bootstrap_effect_parameters(prior.manifest), "generation": "a3-platform-bootstrap-1"}
    result = prior.rollback(parameters)
    assert result["receipt"].startswith("platform-bootstrap-receipt:sha256:")
    receipt = json.loads(next((tmp_path / "prior" / "receipts").iterdir()).read_text())
    assert receipt["outcome"] == "NOOP_ALREADY_PRIOR"

    successor, _, state, _, _ = _provider(tmp_path / "successor", current=NEW)
    parameters = {**platform_bootstrap_effect_parameters(successor.manifest), "generation": "a3-platform-bootstrap-1"}
    assert successor.rollback(parameters)["receipt"].startswith("platform-bootstrap-receipt:sha256:")
    assert state[0] == OLD

    third = "/nix/store/cccccccccccccccccccccccccccccccc-nixos-system-tgw-prod-third"
    ambiguous, _, _, _, _ = _provider(tmp_path / "third", current=third)
    parameters = {**platform_bootstrap_effect_parameters(ambiguous.manifest), "generation": "a3-platform-bootstrap-1"}
    with pytest.raises(BootstrapStateAmbiguous, match="neither prior nor successor") as raised:
        ambiguous.rollback(parameters)
    assert raised.value.evidence


def test_authority_receipt_classifies_third_state_rollback_as_ambiguous(tmp_path):
    provider, _, state, _, _ = _provider(tmp_path)
    third = "/nix/store/cccccccccccccccccccccccccccccccc-nixos-system-tgw-prod-third"

    def partial_activation(value):
        state[0] = third
        return {
            "status": "activated",
            "from_system": value["prior_system"],
            "to_system": value["successor_system"],
            "provider_receipt": value["activation_provider_receipt"],
            "receipt": "activation:sha256:" + "6" * 64,
        }

    provider.activate_successor = partial_activation
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=provider.install,
        bootstrap_rollback=provider.rollback,
        bootstrap_validate=provider.preflight,
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
    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert receipt.evidence


def test_partial_apply_failure_rolls_back_exact_prior_closure_through_authority(tmp_path):
    health = Mock(side_effect=RuntimeError("health unavailable after activation"))
    provider, _, state, _, _ = _provider(tmp_path, health=health)

    def rollback(value):
        state[0] = OLD
        return {
            "status": "activated",
            "from_system": value["successor_system"],
            "to_system": value["prior_system"],
            "receipt": "activation:sha256:" + "7" * 64,
        }

    provider.activate_prior = Mock(side_effect=rollback)
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=provider.install,
        bootstrap_rollback=provider.rollback,
        bootstrap_validate=provider.preflight,
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
    provider.activate_prior.assert_called_once_with(provider.manifest)


@pytest.mark.parametrize("attacked", ["candidate", "review", "controller", "artifact", "closure"])
def test_resolved_immutable_inputs_and_closure_membership_fail_before_authority_consumption(tmp_path, attacked):
    provider, _, _, records, closure = _provider(tmp_path)
    if attacked == "candidate":
        records[provider.manifest["candidate_receipt"]]["flake_commit"] = "0" * 40
    elif attacked == "review":
        records[provider.manifest["review_receipt"]]["status"] = "FAIL"
    elif attacked == "controller":
        records[provider.manifest["controller_receipt"]]["review_receipt"] = "review:sha256:" + "0" * 64
    elif attacked == "artifact":
        artifact = records[provider.manifest["artifacts"]["native_wrapper"]["artifact_ref"]]
        Path(artifact["path"]).chmod(0o644)
    else:
        closure["successor_system"] = OLD
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=provider.install,
        bootstrap_rollback=provider.rollback,
        bootstrap_validate=provider.preflight,
    )
    effect = TypedEffect.parse(
        {
            "kind": "approval-platform-bootstrap-deployment",
            "generation": "a3-platform-bootstrap-1",
            "parameters": platform_bootstrap_effect_parameters(provider.manifest),
        }
    )
    consume = Mock()
    with pytest.raises(ValueError):
        AuthorityEffectController(registry, consume).execute(request_id="bootstrap:a3", effect=effect)
    consume.assert_not_called()


def test_success_receipt_binds_review_controller_activation_and_provider_receipts(tmp_path):
    provider, _, _, _, _ = _provider(tmp_path)
    parameters = {**platform_bootstrap_effect_parameters(provider.manifest), "generation": "a3-platform-bootstrap-1"}
    result = provider.install(parameters)
    success_path = tmp_path / "receipts" / "bootstrap_a3-platform-1-success.json"
    success = json.loads(success_path.read_text())
    assert provider.manifest["review_receipt"] in result["evidence"]
    assert provider.manifest["controller_receipt"] in result["evidence"]
    assert provider.manifest["activation_provider_receipt"] in result["evidence"]
    assert success["review_receipt"] == provider.manifest["review_receipt"]
    assert success["controller_receipt"] == provider.manifest["controller_receipt"]
    assert success["activation_receipt"] in result["evidence"]


def test_private_key_bytes_never_enter_manifest_effect_receipts_or_evidence(tmp_path):
    provider, secret_values, _, _, _ = _provider(tmp_path)
    parameters = platform_bootstrap_effect_parameters(provider.manifest)
    parameters["generation"] = "a3-platform-bootstrap-1"
    evidence = provider.install(parameters)["evidence"]
    exposed = parameters["manifest_json"].encode() + b"\n".join(path.read_bytes() for path in (tmp_path / "receipts").iterdir())
    exposed += json.dumps(evidence).encode()
    for secret in secret_values:
        assert secret not in exposed
    assert b"PRIVATE KEY" not in exposed


def test_external_private_keys_reject_wrong_mode_digest_ref_and_nix_store_path(tmp_path):
    private = Ed25519PrivateKey.from_private_bytes(b"\x03" * 32)
    key, _ = _external_key(tmp_path, "private.key", ATTESTATION_KEY_REF, private, openssh=False)
    expected = {"ref": ATTESTATION_KEY_REF, "sha256": key.sha256}
    key.path.chmod(0o600)
    with pytest.raises(ValueError, match="0400"):
        key.validate(expected, key_kind="attestation-ed25519", public_verifier=b"\x00" * 32, trusted_uid=os.geteuid())
    key.path.chmod(0o400)
    with pytest.raises(ValueError, match="reference or digest"):
        key.validate(
            {**expected, "ref": SSH_KEY_REF},
            key_kind="attestation-ed25519",
            public_verifier=b"\x00" * 32,
            trusted_uid=os.geteuid(),
        )
    with pytest.raises(ValueError, match="reference or digest"):
        ExternalPrivateKey(key.ref, key.path, "sha256:" + "0" * 64).validate(
            expected, key_kind="attestation-ed25519", public_verifier=b"\x00" * 32, trusted_uid=os.geteuid()
        )
    with pytest.raises(ValueError, match="non-Nix"):
        ExternalPrivateKey(key.ref, Path("/nix/store/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-secret"), key.sha256).validate(
            expected, key_kind="attestation-ed25519", public_verifier=b"\x00" * 32, trusted_uid=os.geteuid()
        )


def test_actual_ed25519_private_keys_must_match_exact_public_verifier_artifacts(tmp_path):
    attestation = Ed25519PrivateKey.from_private_bytes(b"\x04" * 32)
    key, _ = _external_key(tmp_path, "attestation-match.key", ATTESTATION_KEY_REF, attestation, openssh=False)
    expected = {"ref": ATTESTATION_KEY_REF, "sha256": key.sha256}
    public = attestation.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key.validate(expected, key_kind="attestation-ed25519", public_verifier=public, trusted_uid=os.geteuid())
    with pytest.raises(ValueError, match="raw public verifier"):
        key.validate(expected, key_kind="attestation-ed25519", public_verifier=b"\x09" * 32, trusted_uid=os.geteuid())

    ssh = Ed25519PrivateKey.from_private_bytes(b"\x05" * 32)
    ssh_key, _ = _external_key(tmp_path, "ssh-match.key", SSH_KEY_REF, ssh, openssh=True)
    ssh_expected = {"ref": SSH_KEY_REF, "sha256": ssh_key.sha256}
    public_line = ssh.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    authorized = (AUTHORIZED_KEY_PREFIX + public_line.removeprefix("ssh-ed25519 ")).encode()
    ssh_key.validate(ssh_expected, key_kind="ssh-ed25519", public_verifier=authorized, trusted_uid=os.geteuid())
    for attacked in (
        authorized + b"\n",
        authorized + b"\n" + authorized,
        authorized.replace(b"restrict,", b"no-port-forwarding,"),
        authorized.replace(b" -n -- ", b" -- "),
    ):
        with pytest.raises(ValueError, match="strict forced authorized key"):
            ssh_key.validate(ssh_expected, key_kind="ssh-ed25519", public_verifier=attacked, trusted_uid=os.geteuid())


def test_receipt_store_loops_short_writes_and_returns_no_ref_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "receipts"
    root.mkdir(mode=0o700)
    store = ImmutableBootstrapReceiptStore(root, trusted_uid=os.geteuid())
    real_write = os.write
    calls = 0

    def short_once(fd, value):
        nonlocal calls
        calls += 1
        return real_write(fd, value[: max(1, len(value) // 2)] if calls == 1 else value)

    monkeypatch.setattr(bootstrap_module.os, "write", short_once)
    value = {"operation_id": "bootstrap:short-write", "schema": "test/v1"}
    reference = store.persist(value, phase="attempt")
    assert reference.startswith("platform-bootstrap-receipt:sha256:")
    assert json.loads((root / "bootstrap_short-write-attempt.json").read_text()) == value

    failures = 0

    def fail_after_partial(fd, raw):
        nonlocal failures
        failures += 1
        if failures == 1:
            return real_write(fd, raw[:1])
        raise OSError("injected persistence loss")

    monkeypatch.setattr(bootstrap_module.os, "write", fail_after_partial)
    with pytest.raises(OSError, match="injected persistence loss"):
        store.persist({"operation_id": "bootstrap:failed-write", "schema": "test/v1"}, phase="attempt")
    assert (root / "bootstrap_failed-write-attempt.json").stat().st_size == 1


def test_attempt_persistence_failure_is_authority_ambiguous_without_valid_durable_ref(tmp_path, monkeypatch):
    provider, _, _, _, _ = _provider(tmp_path)
    real_write = os.write
    calls = 0

    def fail_after_partial(fd, raw):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, raw[:1])
        raise OSError("injected persistence loss")

    monkeypatch.setattr(bootstrap_module.os, "write", fail_after_partial)
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=provider.install,
        bootstrap_rollback=provider.rollback,
        bootstrap_validate=provider.preflight,
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
    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert receipt.evidence[0].startswith("platform-bootstrap-attempt-memory:sha256:")
    assert receipt.rollback_receipt is None


def test_nix_leaf_is_disabled_by_default_and_contains_only_fixed_public_material():
    module = Path("nix/a3-platform-bootstrap.nix").read_text()
    package = Path("nix/a3-platform-bootstrap-package.nix").read_text()
    assert "mkEnableOption" in module
    assert "authorizedKeys.keys = [ cfg.sshAuthorizedPublicKey ]" in module
    assert 'NOPASSWD: ${wrapper} ""' in module
    assert 'restrict,command="/run/wrappers/bin/sudo -n -- /run/current-system/sw/bin/tgw-nix-observer-render-wrapper" ssh-ed25519' in module
    assert '!(lib.hasInfix "\\n" cfg.sshAuthorizedPublicKey)' in module
    assert "EXTERNAL_TGW_PROD_FLAKE_IMPORT_BUILD_REQUIRED" == _manifest()["live_flake_gate"]
    assert "attestationPublicKey" in module
    assert "PRIVATE KEY" not in package
    assert "cleanSource" not in package
    assert "nix_observer_render_remote.py" in package
    assert "nix_observer_render_helper.py" in package
    assert "tgw_nix_observer_render_transport.c" in package
