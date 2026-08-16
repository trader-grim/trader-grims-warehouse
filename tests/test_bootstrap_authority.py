import json
import multiprocessing
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import tgw.bootstrap_authority as authority_module
from tgw.bootstrap_authority import (
    ApplicationBootstrapGrant,
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
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    now = datetime(2029, 1, 1, tzinfo=timezone.utc)

    receipt = authority.consume(grant.grant_id, effect_hash=grant.effect.effect_hash, generation=grant.effect.generation, now=now)

    assert receipt == json.loads(path.read_text())
    assert receipt["target_host"] == "tgw-prod"
    assert receipt["candidate_commit"] == "b" * 40
    assert receipt["receipt_id"].startswith("bootstrap-consumption:sha256:")
    assert path.stat().st_mode & 0o777 == 0o400
    lock_path = tmp_path / f".{path.name}.lock"
    assert lock_path.is_file()
    assert lock_path.stat().st_mode & 0o777 == 0o400
    assert lock_path.stat().st_size == 0
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
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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
        "kind": "coding-release",
        "generation": "g",
        "parameters": {"root_id": "production-releases", "candidate_commit": "b" * 40},
    }
    with pytest.raises(ValueError, match="exact platform bootstrap"):
        _grant(effect=legacy)


def test_bootstrap_grant_accepts_only_exact_w09_application_contract_binding():
    commit = "b" * 40
    effect = {
        "kind": "approval-platform-bootstrap-deployment",
        "generation": "release-b",
        "parameters": {
            "schema": "tgw-approval-application-bootstrap/v1",
            "application_contract_ref": f"candidate:{commit}:application-bootstrap:v1",
            "application_contract_hash": "sha256:" + "1" * 64,
        },
    }
    application_plan = "f0a8cf22b2c7b2f064292a048ffcb8ee98919e99"
    application_solution = "sha256:1c3684135769e5dcabcaf130c55df160a4cecc0d3ebcee6ccd129ab97cdd709b"
    value = {
        "plan_commit": application_plan,
        "solution_hash": application_solution,
        "target_host": "tgw-prod",
        "root_id": "production-releases",
        "candidate_commit": commit,
        "effect": effect,
        "expires_at": "2030-01-01T00:00:00Z",
        "deployment_uses": 1,
        "retirement_condition": "W10:canonical-gate-operational",
    }
    grant = ApplicationBootstrapGrant.parse(value)
    assert grant.effect.generation == "release-b"
    with pytest.raises(ValueError):
        ApplicationBootstrapGrant.parse(
            {
                **value,
                "effect": {
                    "kind": "approval-platform-bootstrap-deployment",
                    "generation": "platform-bb5c67d",
                    "parameters": _parameters(),
                },
            }
        )
    for field, value in (
        ("application_contract_ref", "candidate:" + "c" * 40 + ":application-bootstrap:v1"),
        ("application_contract_hash", "sha256:not-a-digest"),
    ):
        bad = json.loads(json.dumps(effect))
        bad["parameters"][field] = value
        with pytest.raises(ValueError, match="does not match"):
            _grant(effect=bad, plan_commit=application_plan, solution_hash=application_solution)


def test_consumption_store_loops_short_writes_and_held_rereads(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "short-consumption.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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


def test_partial_consumption_write_is_terminal_ambiguity_without_replay_or_valid_receipt(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "partial-consumption.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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


def test_production_authority_rejects_grant_replacement_and_method_shadowing():
    authority = object.__new__(BootstrapSessionAuthority)
    object.__setattr__(authority, "grant", _grant())
    object.__setattr__(authority, "_production_authority", True)
    object.__setattr__(authority, "_bindings_frozen", True)
    with pytest.raises(AttributeError, match="immutable"):
        authority.grant = _grant()
    with pytest.raises(AttributeError, match="cannot be shadowed"):
        authority.consume = lambda *_args, **_kwargs: {}
    authority.__dict__["consume"] = lambda *_args, **_kwargs: {}
    assert authority.consume.__func__ is BootstrapSessionAuthority.consume


def test_successful_consume_stays_spent_after_receipt_is_unlinked(tmp_path):
    grant = _grant()
    path = tmp_path / "unlinked-after-success.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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


def test_two_instances_wait_for_stalled_writer_then_validate_durable_spent_receipt(tmp_path):
    grant = _grant()
    path = tmp_path / "two-instance-stalled.json"
    writer = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    loser = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    writer_entered = threading.Event()
    release_writer = threading.Event()
    loser_observed_receipt = threading.Event()
    loser_done = threading.Event()
    outcomes: list[tuple[str, str]] = []
    real_write_all = writer._write_all
    real_existing_status = loser._existing_receipt_status

    def stalled_write(fd, data):
        writer_entered.set()
        assert release_writer.wait(timeout=5)
        real_write_all(fd, data)

    def observed_status():
        loser_observed_receipt.set()
        return real_existing_status()

    writer._write_all = stalled_write
    loser._existing_receipt_status = observed_status

    def invoke(authority, label):
        try:
            authority.consume(
                grant.grant_id,
                effect_hash=grant.effect.effect_hash,
                generation=grant.effect.generation,
                now=datetime(2029, 1, 1, tzinfo=timezone.utc),
            )
            outcomes.append((label, "success"))
        except Exception as exc:  # noqa: BLE001 - exact cross-instance result asserted below
            outcomes.append((label, type(exc).__name__ + ":" + str(exc)))
        if label == "loser":
            loser_done.set()

    writer_thread = threading.Thread(target=invoke, args=(writer, "writer"))
    loser_thread = threading.Thread(target=invoke, args=(loser, "loser"))
    writer_thread.start()
    assert writer_entered.wait(timeout=5)
    loser_thread.start()
    assert not loser_observed_receipt.wait(timeout=0.1)
    assert not loser_done.is_set()
    release_writer.set()
    writer_thread.join(timeout=5)
    loser_thread.join(timeout=5)

    assert outcomes == [("writer", "success"), ("loser", "ValueError:bootstrap grant is already consumed")]
    assert writer.state is BootstrapAuthorityState.SPENT
    assert loser.state is BootstrapAuthorityState.SPENT
    durable_receipt_id = json.loads(path.read_text())["receipt_id"]
    assert durable_receipt_id.startswith("bootstrap-consumption:sha256:")
    assert writer._spent_receipt_id == durable_receipt_id
    assert loser._spent_receipt_id == durable_receipt_id


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="fork is unavailable")
def test_two_processes_same_receipt_produce_one_success_and_one_spent_replay(tmp_path):
    grant = _grant()
    path = tmp_path / "multiprocess.json"
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(3)
    results = context.Queue()

    def consume_in_child():
        authority = BootstrapSessionAuthority(
            grant,
            receipt_path=path,
            current_plan_commit=grant.plan_commit,
            trusted_uid=os.geteuid(),
        )
        barrier.wait()
        try:
            authority.consume(
                grant.grant_id,
                effect_hash=grant.effect.effect_hash,
                generation=grant.effect.generation,
                now=datetime(2029, 1, 1, tzinfo=timezone.utc),
            )
            results.put(("success", authority.state.value))
        except Exception as exc:  # noqa: BLE001 - exact process result asserted below
            results.put((type(exc).__name__, str(exc), authority.state.value))

    processes = [context.Process(target=consume_in_child) for _ in range(2)]
    for process in processes:
        process.start()
    barrier.wait()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    observed = sorted(results.get(timeout=2) for _ in processes)
    assert observed == [
        ("ValueError", "bootstrap grant is already consumed", "SPENT"),
        ("success", "SPENT"),
    ]
    assert json.loads(path.read_text())["receipt_id"].startswith("bootstrap-consumption:sha256:")


@pytest.mark.parametrize("attack", ["symlink", "mode", "content"])
def test_unsafe_lock_artifact_is_terminal_ambiguity_before_receipt_creation(tmp_path, attack):
    grant = _grant()
    path = tmp_path / f"unsafe-lock-{attack}.json"
    lock_path = tmp_path / f".{path.name}.lock"
    if attack == "symlink":
        target = tmp_path / "lock-target"
        target.touch(mode=0o400)
        lock_path.symlink_to(target)
    else:
        lock_path.write_bytes(b"unexpected" if attack == "content" else b"")
        lock_path.chmod(0o600 if attack == "mode" else 0o400)
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    arguments = {
        "request_id": grant.grant_id,
        "effect_hash": grant.effect.effect_hash,
        "generation": grant.effect.generation,
        "now": datetime(2029, 1, 1, tzinfo=timezone.utc),
    }
    with pytest.raises(BootstrapConsumptionAmbiguous) as first:
        authority.consume(**arguments)
    with pytest.raises(BootstrapConsumptionAmbiguous) as retry:
        authority.consume(**arguments)
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert retry.value.evidence == first.value.evidence
    assert not path.exists()


def test_lock_owner_mismatch_is_terminal_ambiguity_before_receipt_creation(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "lock-owner.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    real_validate = authority._validate_lock_artifact

    def reject_owner(lock_fd):
        real_validate(lock_fd)
        raise OSError("injected lock owner mismatch")

    monkeypatch.setattr(authority, "_validate_lock_artifact", reject_owner)
    with pytest.raises(BootstrapConsumptionAmbiguous, match="persistence is ambiguous"):
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert not path.exists()


def test_lock_reference_replacement_after_flock_is_terminal_ambiguity(tmp_path, monkeypatch):
    grant = _grant()
    path = tmp_path / "lock-replaced.json"
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    lock_path = tmp_path / authority._lock_name
    replacement = tmp_path / "replacement-lock"
    replacement.touch(mode=0o400)
    real_flock = authority_module.fcntl.flock
    replaced = False

    def replace_after_acquire(fd, operation):
        nonlocal replaced
        result = real_flock(fd, operation)
        if operation == authority_module.fcntl.LOCK_EX and not replaced:
            lock_path.unlink()
            replacement.replace(lock_path)
            replaced = True
        return result

    monkeypatch.setattr(authority_module.fcntl, "flock", replace_after_acquire)
    with pytest.raises(BootstrapConsumptionAmbiguous):
        authority.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert replaced
    assert authority.state is BootstrapAuthorityState.AMBIGUOUS
    assert not path.exists()


@pytest.mark.parametrize("repeat", range(5))
def test_replacement_during_receipt_write_never_yields_mixed_success_and_ambiguity(tmp_path, repeat):
    grant = _grant()
    path = tmp_path / f"write-lock-replaced-{repeat}.json"
    writer = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    replacement_instance = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    lock_path = tmp_path / writer._lock_name
    replacement_lock = tmp_path / f"replacement-during-write-{repeat}.lock"
    replacement_lock.touch(mode=0o400)
    writer_stalled = threading.Event()
    release_writer = threading.Event()
    outcomes: list[tuple[str, object]] = []
    real_write_all = writer._write_all

    def replaceable_stalled_write(fd, data):
        writer_stalled.set()
        assert release_writer.wait(timeout=5)
        real_write_all(fd, data)

    writer._write_all = replaceable_stalled_write

    def consume(authority, identity):
        try:
            value = authority.consume(
                grant.grant_id,
                effect_hash=grant.effect.effect_hash,
                generation=grant.effect.generation,
                now=datetime(2029, 1, 1, tzinfo=timezone.utc),
            )
            outcomes.append((identity, value))
        except Exception as exc:  # noqa: BLE001 - exact compromised-lock classification asserted below
            outcomes.append((identity, exc))

    writer_thread = threading.Thread(target=consume, args=(writer, "writer"))
    writer_thread.start()
    assert writer_stalled.wait(timeout=5)
    lock_path.unlink()
    replacement_lock.replace(lock_path)
    replacement_thread = threading.Thread(target=consume, args=(replacement_instance, "replacement-instance"))
    replacement_thread.start()
    replacement_thread.join(timeout=5)
    assert not replacement_thread.is_alive()
    release_writer.set()
    writer_thread.join(timeout=5)

    assert not writer_thread.is_alive()
    assert {identity for identity, _ in outcomes} == {"writer", "replacement-instance"}
    assert all(isinstance(result, BootstrapConsumptionAmbiguous) for _, result in outcomes)
    assert writer.state is BootstrapAuthorityState.AMBIGUOUS
    assert replacement_instance.state is BootstrapAuthorityState.AMBIGUOUS
    durable_receipt_id = json.loads(path.read_text())["receipt_id"]
    assert durable_receipt_id.startswith("bootstrap-consumption:sha256:")
    reconciler = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    with pytest.raises(ValueError, match="already consumed"):
        reconciler.consume(
            grant.grant_id,
            effect_hash=grant.effect.effect_hash,
            generation=grant.effect.generation,
            now=datetime(2029, 1, 1, tzinfo=timezone.utc),
        )
    assert reconciler.state is BootstrapAuthorityState.SPENT
    assert reconciler._spent_receipt_id == durable_receipt_id


def test_precreated_invalid_receipt_makes_authority_terminally_ambiguous(tmp_path):
    grant = _grant()
    path = tmp_path / "precreated.json"
    path.write_bytes(b"not-a-valid-consumption-receipt\n")
    path.chmod(0o400)
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
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
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    real_fsync = os.fsync
    removed = False

    def unlink_on_directory_fsync(fd):
        nonlocal removed
        real_fsync(fd)
        if fd == authority._directory_fd and path.exists() and not removed:
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
    authority = BootstrapSessionAuthority(grant, receipt_path=path, current_plan_commit=grant.plan_commit, trusted_uid=os.geteuid())
    real_fsync = os.fsync
    attacked = False

    def attack_on_directory_fsync(fd):
        nonlocal attacked
        real_fsync(fd)
        if fd == authority._directory_fd and path.exists() and not attacked:
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


def test_authority_controller_classifies_consumption_persistence_loss_without_invoking_handler(tmp_path, monkeypatch):
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

    receipt = AuthorityEffectController(registry, authority.consume).execute(request_id=grant.grant_id, effect=grant.effect)

    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert receipt.evidence
    assert receipt.authority_receipt_id == receipt.evidence[0]
    handler.assert_not_called()
