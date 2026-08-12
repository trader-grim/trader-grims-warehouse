import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import tgw.bootstrap_authority as authority_module
from tgw.bootstrap_authority import (
    BootstrapAuthorityState,
    BootstrapConsumptionAmbiguous,
    BootstrapGrant,
    BootstrapSessionAuthority,
)
from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.platform_bootstrap import (
    ATTESTATION_KEY_REF,
    MANIFEST_SCHEMA,
    PLAN_COMMIT,
    RETIREMENT_CONDITION,
    SOLUTION_HASH,
    SSH_KEY_REF,
    digest,
    platform_bootstrap_effect_parameters,
    platform_bootstrap_request_binding,
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
        "request_binding": "bootstrap-request:sha256:" + "9" * 64,
        "candidate_receipt": "candidate:sha256:" + "0" * 64,
        "review_receipt": "review:sha256:" + "1" * 64,
        "controller_receipt": "controller:sha256:" + "2" * 64,
        "activation_receipt": "activation:sha256:" + "6" * 64,
        "activation_provider_receipt": "activation-provider:sha256:" + "5" * 64,
        "health_receipt": "health:sha256:" + "3" * 64,
        "probe_receipt": "probe:sha256:" + "4" * 64,
        "retirement_condition": RETIREMENT_CONDITION,
        "live_flake_gate": "EXTERNAL_TGW_PROD_FLAKE_IMPORT_BUILD_REQUIRED",
        "live_sshd_gate": "EXTERNAL_TGW_PROD_SSHD_T_USER_CODEX_REQUIRED",
    }
    manifest["request_binding"] = platform_bootstrap_request_binding(manifest)
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
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)

    receipt = authority.consume(grant.grant_id, effect_hash=grant.effect.effect_hash, generation=grant.effect.generation, now=now)

    assert receipt == json.loads(path.read_text())
    assert receipt["target_host"] == "tgw-prod"
    assert receipt["candidate_commit"] == "b" * 40
    assert receipt["receipt_id"].startswith("bootstrap-consumption:sha256:")
    assert path.stat().st_mode & 0o777 == 0o400
    assert authority.state is BootstrapAuthorityState.SPENT
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
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    arguments = {"request_id": grant.grant_id, "effect_hash": grant.effect.effect_hash, "generation": grant.effect.generation}
    arguments[field] = value
    with pytest.raises(ValueError, match=message):
        authority.consume(**arguments, now=datetime(2029, 1, 1, tzinfo=timezone.utc))
    assert not path.exists()


def test_stale_plan_and_expired_or_broadened_grants_fail_closed(tmp_path):
    grant = _grant()
    with pytest.raises(ValueError, match="different Plan commit"):
        BootstrapSessionAuthority(
            grant,
            receipt_path=tmp_path / "receipt",
            current_plan_commit="0" * 40,
            trusted_uid=os.geteuid(),
        )
    authority = BootstrapSessionAuthority(
        grant,
        receipt_path=tmp_path / "receipt",
        current_plan_commit=grant.plan_commit,
        trusted_uid=os.geteuid(),
    )
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


def test_consumption_store_loops_short_writes_and_held_rereads(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "short-consumption.json"
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    real_write = os.write
    calls = 0

    def short_writes(fd, raw):
        nonlocal calls
        calls += 1
        return real_write(fd, raw[: max(1, len(raw) // 2)])

    monkeypatch.setattr(authority_module.os, "write", short_writes)
    receipt = authority.consume(
        grant.grant_id,
        effect_hash=grant.effect.effect_hash,
        generation=grant.effect.generation,
        now=datetime(2029, 1, 1, tzinfo=timezone.utc),
    )
    assert calls > 1
    assert json.loads(path.read_text()) == receipt


def test_partial_consumption_write_is_terminal_ambiguity_without_replay_or_valid_receipt(
    tmp_path, monkeypatch
):
    grant = _grant()
    path = tmp_path / "partial-consumption.json"
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    real_write = os.write
    calls = 0

    def fail_after_partial(fd, raw):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, raw[:1])
        raise OSError("injected authority persistence loss")

    monkeypatch.setattr(authority_module.os, "write", fail_after_partial)
    arguments = {
        "request_id": grant.grant_id,
        "effect_hash": grant.effect.effect_hash,
        "generation": grant.effect.generation,
        "now": datetime(2029, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(BootstrapConsumptionAmbiguous) as first:
        authority.consume(**arguments)
    assert first.value.evidence[0].startswith("bootstrap-consumption-ambiguity:sha256:")
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert path.stat().st_size == 1
    with pytest.raises(BootstrapConsumptionAmbiguous) as replay:
        authority.consume(**arguments)
    assert replay.value.evidence == first.value.evidence


def test_consumption_open_or_root_identity_failure_is_typed_ambiguity(tmp_path, monkeypatch):
    grant = _grant()
    authority = BootstrapSessionAuthority(
        grant,
        receipt_path=tmp_path / "open-failure.json",
        current_plan_commit=grant.plan_commit,
        trusted_uid=os.geteuid(),
    )
    monkeypatch.setattr(authority_module.os, "open", Mock(side_effect=PermissionError("injected open loss")))
    with pytest.raises(BootstrapConsumptionAmbiguous) as raised:
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert raised.value.evidence
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    monkeypatch.undo()
    with pytest.raises(BootstrapConsumptionAmbiguous) as retry:
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert retry.value.evidence == raised.value.evidence

    root = tmp_path / "held-root"
    root.mkdir(mode=0o700)
    held = BootstrapSessionAuthority(
        grant,
        receipt_path=root / "receipt.json",
        current_plan_commit=grant.plan_commit,
        trusted_uid=os.geteuid(),
    )
    old_root = tmp_path / "old-held-root"
    root.rename(old_root)
    root.mkdir(mode=0o700)
    with pytest.raises(BootstrapConsumptionAmbiguous) as replaced:
        held.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert replaced.value.evidence


def test_constructor_rejects_root_swapped_to_world_writable_after_held_open(tmp_path, monkeypatch):
    grant = _grant()
    root = tmp_path / "constructor-root"
    root.mkdir(mode=0o700)
    receipt_path = root / "receipt.json"
    held_root = tmp_path / "constructor-held-root"
    real_open = os.open
    swapped = False

    def swap_after_directory_open(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if Path(path) == root and flags & os.O_DIRECTORY and not swapped:
            root.rename(held_root)
            root.mkdir(mode=0o777)
            root.chmod(0o777)
            swapped = True
        return fd

    monkeypatch.setattr(authority_module.os, "open", swap_after_directory_open)
    with pytest.raises(ValueError, match="changed while opening"):
        BootstrapSessionAuthority(
            grant,
            receipt_path=receipt_path,
            current_plan_commit=grant.plan_commit,
            trusted_uid=os.geteuid(),
        )
    assert swapped
    assert not receipt_path.exists()


def test_successful_consume_stays_spent_after_receipt_is_unlinked(tmp_path):
    grant = _grant()
    path = tmp_path / "unlinked-after-success.json"
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    arguments = {
        "request_id": grant.grant_id,
        "effect_hash": grant.effect.effect_hash,
        "generation": grant.effect.generation,
        "now": datetime(2029, 1, 1, tzinfo=timezone.utc),
    }
    authority.consume(**arguments)
    path.unlink()

    with pytest.raises(ValueError, match="already consumed"):
        authority.consume(**arguments)
    assert authority.state is BootstrapAuthorityState.SPENT
    assert not path.exists()


def test_concurrent_consume_serializes_to_one_success_and_one_spent_refusal(tmp_path):
    grant = _grant()
    path = tmp_path / "concurrent.json"
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    barrier = threading.Barrier(3)
    outcomes: list[tuple[str, object]] = []
    outcomes_lock = threading.Lock()

    def consume_once():
        barrier.wait()
        try:
            value = authority.consume(
                grant.grant_id,
                effect_hash=grant.effect.effect_hash,
                generation=grant.effect.generation,
                now=datetime(2029, 1, 1, tzinfo=timezone.utc),
            )
            outcome = ("success", value)
        except Exception as exc:  # noqa: BLE001 - exact cross-thread outcome is asserted below
            outcome = ("error", exc)
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=consume_once) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert [kind for kind, _ in outcomes].count("success") == 1
    errors = [value for kind, value in outcomes if kind == "error"]
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert not isinstance(errors[0], BootstrapConsumptionAmbiguous)
    assert "already consumed" in str(errors[0])
    assert authority.state is BootstrapAuthorityState.SPENT


def test_precreated_invalid_receipt_makes_authority_terminally_ambiguous(tmp_path):
    grant = _grant()
    path = tmp_path / "precreated.json"
    path.write_bytes(b"not-a-valid-consumption-receipt\n")
    path.chmod(0o400)
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    arguments = {
        "request_id": grant.grant_id,
        "effect_hash": grant.effect.effect_hash,
        "generation": grant.effect.generation,
        "now": datetime(2029, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(BootstrapConsumptionAmbiguous) as first:
        authority.consume(**arguments)
    path.unlink()
    with pytest.raises(BootstrapConsumptionAmbiguous) as retry:
        authority.consume(**arguments)
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert retry.value.evidence == first.value.evidence
    assert not path.exists()


def test_unlink_during_directory_fsync_cannot_return_consumption_success(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "unlink-during-fsync.json"
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    real_fsync = os.fsync
    removed = False

    def unlink_on_directory_fsync(fd):
        nonlocal removed
        real_fsync(fd)
        if fd == authority._directory_fd and not removed:
            path.unlink()
            removed = True

    monkeypatch.setattr(authority_module.os, "fsync", unlink_on_directory_fsync)
    with pytest.raises(BootstrapConsumptionAmbiguous) as raised:
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert removed
    assert raised.value.evidence
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert not path.exists()


@pytest.mark.parametrize("attack", ["replace", "symlink"])
def test_replace_or_symlink_during_directory_fsync_cannot_return_success(tmp_path, monkeypatch, attack):
    grant = _grant()
    path = tmp_path / f"{attack}-during-fsync.json"
    replacement = tmp_path / f"{attack}-replacement.json"
    replacement.write_bytes(b"replacement\n")
    replacement.chmod(0o400)
    authority = BootstrapSessionAuthority(
        grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid()
    )
    real_fsync = os.fsync
    attacked = False

    def attack_on_directory_fsync(fd):
        nonlocal attacked
        real_fsync(fd)
        if fd == authority._directory_fd and not attacked:
            path.unlink()
            if attack == "replace":
                replacement.replace(path)
            else:
                path.symlink_to(replacement)
            attacked = True

    monkeypatch.setattr(authority_module.os, "fsync", attack_on_directory_fsync)
    with pytest.raises(BootstrapConsumptionAmbiguous) as raised:
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert attacked
    assert raised.value.evidence
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS


def test_authority_controller_classifies_consumption_persistence_loss_without_invoking_handler(
    tmp_path, monkeypatch
):
    grant = _grant()
    authority = BootstrapSessionAuthority(
        grant,
        receipt_path=tmp_path / "controller-partial.json",
        current_plan_commit=grant.plan_commit,
        trusted_uid=os.geteuid(),
    )
    handler = Mock()
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        bootstrap_install=handler,
        bootstrap_rollback=Mock(),
        bootstrap_validate=Mock(),
    )
    monkeypatch.setattr(authority_module.os, "write", Mock(side_effect=OSError("injected authority loss")))

    receipt = AuthorityEffectController(registry, authority.consume).execute(
        request_id=grant.grant_id, effect=grant.effect
    )

    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert receipt.evidence
    assert receipt.authority_receipt_id == receipt.evidence[0]
    handler.assert_not_called()
