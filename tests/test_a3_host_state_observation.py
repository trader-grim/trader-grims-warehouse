from __future__ import annotations

import ast
import hashlib
import os
import sys
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tgw import a3_host_state_helper
from tgw.a3_host_state_observation import (
    GRANT_SCHEMA,
    HostStateComposition,
    HostStateError,
    HostStateEvidenceStore,
    HostStateObservationController,
    HostStateObservationGrant,
    HostStatePersistenceAmbiguous,
    ObservationHold,
    build_host_state_production_composition,
    decode_helper_response,
    dependency_projection,
    digest,
    encode_helper_response,
    observe_host_state,
    terminal,
    validate_receipt,
    validate_request,
    validate_result,
    validate_sshd_parity,
    validate_terminal,
)
from tgw.a3_observation_authority import DurableObservationToken, ObservationAlreadyConsumed


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _request(*, now: datetime | None = None, transport: dict | None = None) -> dict:
    observed = now or datetime.now(timezone.utc)
    value = {
        "schema": "tgw-prod-a3-host-state-observation-request/v1",
        "operation_id": "observe-host-state",
        "plan": {
            "commit": "1" * 40,
            "solution_sha256": _sha("solution"),
            "closure_sha256": _sha("closure"),
            "approval_sha256": _sha("approval"),
            "authority_sha256": _sha("authority"),
        },
        "target": {
            "host": "tgw-prod",
            "user": "codex",
            "port": 22,
            "system": "x86_64-linux",
            "remote_python": "/usr/bin/python3",
            "remote_git": "/usr/bin/git",
            "repository": "/home/db/tgw-flake",
            "expected_branch": "main",
        },
        "transport": transport
        or {
            "ssh_sha256": _sha("ssh"),
            "ssh_keygen_sha256": _sha("ssh-keygen"),
            "known_hosts_sha256": _sha("known-hosts"),
            "identity_sha256": _sha("identity"),
            "identity_public": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixture fixture",
            "identity_public_sha256": digest(b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFixture fixture\n"),
            "helper_sha256": _sha("helper"),
        },
        "prerequisites": {
            "sshd_parity_sha256": _sha("parity-artifact"),
            "sshd_parity_receipt_sha256": _sha("parity-receipt"),
        },
        "bounds": {"timeout_seconds": 30, "max_output_bytes": 262144, "max_diagnostic_bytes": 65536},
        "freshness": {
            "issued_at": observed.isoformat(),
            "expires_at": (observed + timedelta(minutes=5)).isoformat(),
        },
        "policy": {
            "read_only": True,
            "remote_write": False,
            "repository_write": False,
            "nix": False,
            "network_beyond_ssh": False,
            "platform_bootstrap_grant_consumption": False,
        },
    }
    value["request_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(value))
    return value


def _fixture_host(tmp_path: Path) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    current = tmp_path / "current-system"
    profile = tmp_path / "system-profile"
    cas = "/nix/store/" + "a" * 32 + "-nixos-system-tgw-prod"
    current.symlink_to(cas)
    profile.symlink_to(cas)
    repo = tmp_path / "tgw-flake"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git/refs/heads").mkdir(parents=True)
    (repo / ".git/refs/heads/main").write_text("1" * 40 + "\n")
    return current, profile, repo


def _observe(tmp_path: Path, request: dict | None = None) -> tuple[dict, dict]:
    value = request or _request()
    current, profile, repo = _fixture_host(tmp_path)
    receipt = observe_host_state(
        value,
        current_system=current,
        system_profile=profile,
        repository=repo,
        python_path=Path("/usr/bin/python3"),
        git_path=Path("/usr/bin/git"),
        trusted_uid=0,
        allow_fixture=True,
    )
    return value, receipt


def _identity(path: Path) -> dict:
    st = path.stat()
    return {
        "path": str(path.absolute()),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mode": st.st_mode & 0o7777,
        "dev": st.st_dev,
        "ino": st.st_ino,
        "nlink": st.st_nlink,
    }


def test_local_host_observation_is_zero_effect_and_dependency_compatible(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path)
    assert receipt["current_cas"] == receipt["profile_cas"]
    assert receipt["repository"]["branch"] == "main"
    assert receipt["effects"] == {"remote_write": False, "repository_write": False, "nix": False}
    projection = dependency_projection(receipt, request, ssh_sha256=request["transport"]["ssh_sha256"], descriptor_sha256=_sha("composition"))
    assert projection["status"] == "SATISFIED"
    assert projection["receipt"]["tools"]["git_sha256"] == receipt["tools"]["git"]["sha256"]


def test_current_and_profile_cas_mismatch_holds(tmp_path: Path) -> None:
    request = _request()
    current, profile, repo = _fixture_host(tmp_path)
    profile.unlink()
    profile.symlink_to("/nix/store/" + "b" * 32 + "-other")
    with pytest.raises(ObservationHold, match="CAS differ"):
        observe_host_state(
            request,
            current_system=current,
            system_profile=profile,
            repository=repo,
            python_path=Path("/usr/bin/python3"),
            git_path=Path("/usr/bin/git"),
            trusted_uid=0,
            allow_fixture=True,
        )


def test_store_target_must_exist_in_production_observation(tmp_path: Path) -> None:
    module = __import__("tgw.a3_host_state_observation", fromlist=["_symlink_observation"])
    link = tmp_path / "current-system"
    link.symlink_to("/nix/store/" + "a" * 32 + "-missing")
    with pytest.raises(ObservationHold, match="absent"):
        module._symlink_observation(link)


def test_wrong_production_branch_holds(tmp_path: Path) -> None:
    request = _request()
    current, profile, repo = _fixture_host(tmp_path)
    (repo / ".git/HEAD").write_text("ref: refs/heads/master\n")
    with pytest.raises(ObservationHold, match="branch"):
        observe_host_state(
            request,
            current_system=current,
            system_profile=profile,
            repository=repo,
            python_path=Path("/usr/bin/python3"),
            git_path=Path("/usr/bin/git"),
            trusted_uid=0,
            allow_fixture=True,
        )


def test_missing_production_branch_ref_is_rejected(tmp_path: Path) -> None:
    request = _request()
    current, profile, repo = _fixture_host(tmp_path)
    (repo / ".git/refs/heads/main").unlink()
    with pytest.raises(ObservationHold, match="ref"):
        observe_host_state(
            request,
            current_system=current,
            system_profile=profile,
            repository=repo,
            python_path=Path("/usr/bin/python3"),
            git_path=Path("/usr/bin/git"),
            trusted_uid=0,
            allow_fixture=True,
        )


def test_request_requires_mounted_authorities_in_production() -> None:
    with pytest.raises(HostStateError, match="Plan authority"):
        validate_request(_request())
    assert validate_request(_request(), allow_fixture=True)["target"]["expected_branch"] == "main"


def test_default_composition_is_truthfully_not_executable() -> None:
    composition = HostStateComposition()
    assert composition.status == "NOT_EXECUTABLE"
    assert composition.receipt_sha256.startswith("sha256:")
    with pytest.raises(ObservationHold):
        composition.execute(_request())


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("bounds", "timeout_seconds"), True),
        (("target", "expected_branch"), "master"),
        (("policy", "nix"), True),
        (("prerequisites", "sshd_parity_sha256"), "ambient"),
    ],
)
def test_request_mutations_are_rejected(path: tuple[str, str], replacement: object) -> None:
    request = _request()
    request[path[0]][path[1]] = replacement
    with pytest.raises(HostStateError):
        validate_request(request, allow_fixture=True)


def test_receipt_crypto_and_effect_mutations_are_rejected(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path)
    assert validate_receipt(receipt, request) == receipt
    changed = deepcopy(receipt)
    changed["effects"]["nix"] = True
    with pytest.raises(HostStateError):
        validate_receipt(changed, request)
    changed = deepcopy(receipt)
    changed["tools"]["git"]["version"] = True
    changed["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in changed.items() if key != "receipt_sha256"}))
    with pytest.raises(HostStateError):
        validate_receipt(changed, request)
    changed = deepcopy(receipt)
    changed["tools"]["python"]["size"] = True
    with pytest.raises(HostStateError):
        validate_receipt(changed, request)


def test_helper_frame_is_exact_and_bounded(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path)
    assert decode_helper_response(encode_helper_response(receipt), request) == receipt
    with pytest.raises(HostStateError):
        decode_helper_response(encode_helper_response(receipt) + b"x", request)
    with pytest.raises(HostStateError):
        decode_helper_response(b"", request)


def test_streamed_helper_terminal_is_closed_and_fresh() -> None:
    request = _request()
    now = datetime.now(timezone.utc)
    remote = a3_host_state_helper._terminal(
        outcome="FAILED",
        stage="remote",
        code="HELPER_FAILED",
        request_sha256=request["request_sha256"],
        now=now,
        diagnostic=b"HelperError",
    )
    assert decode_helper_response(encode_helper_response(remote), request, now=now) == remote
    with pytest.raises(HostStateError, match="stale"):
        decode_helper_response(encode_helper_response(remote), request, now=now + timedelta(minutes=11))
    for controller_only in (
        terminal(
            outcome="AMBIGUOUS",
            stage="authority",
            code="TOKEN_UNCERTAIN",
            dispatched=False,
            request_sha256=request["request_sha256"],
            observed_at=now.isoformat(),
        ),
        terminal(
            outcome="PASS",
            stage="complete",
            code="NONE",
            dispatched=True,
            request_sha256=request["request_sha256"],
            observed_at=now.isoformat(),
        ),
    ):
        with pytest.raises(HostStateError, match="non-remote"):
            decode_helper_response(encode_helper_response(controller_only), request, now=now)


@pytest.mark.parametrize(
    "tuple_value",
    list(
        [
            ("PASS", "complete", "NONE", True),
            ("HOLD", "prelaunch", "NOT_READY", False),
            ("FAILED", "prelaunch", "VALIDATION_FAILED", False),
            ("HOLD", "remote", "HOST_NOT_READY", True),
            ("FAILED", "remote", "HELPER_FAILED", True),
            ("AMBIGUOUS", "authority", "TOKEN_UNCERTAIN", False),
            ("AMBIGUOUS", "dispatch", "DISPATCH_UNCERTAIN", True),
            ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN", True),
        ]
    ),
)
def test_terminal_state_table(tuple_value: tuple[str, str, str, bool]) -> None:
    value = terminal(
        outcome=tuple_value[0],
        stage=tuple_value[1],
        code=tuple_value[2],
        dispatched=tuple_value[3],
        request_sha256=_sha("request"),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    assert validate_terminal(value) == value
    changed = deepcopy(value)
    changed["dispatched"] = not changed["dispatched"]
    with pytest.raises(HostStateError):
        validate_terminal(changed)


def test_terminal_binds_diagnostic_bytes_and_freshness() -> None:
    now = datetime.now(timezone.utc)
    value = terminal(
        outcome="FAILED",
        stage="remote",
        code="HELPER_FAILED",
        dispatched=True,
        request_sha256=_sha("request"),
        observed_at=now.isoformat(),
        diagnostic=b"HelperError",
    )
    assert validate_terminal(value, now=now) == value
    changed = deepcopy(value)
    changed["diagnostic_b64"] = ""
    changed["terminal_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: item for key, item in changed.items() if key != "terminal_sha256"}))
    with pytest.raises(HostStateError):
        validate_terminal(changed)
    with pytest.raises(HostStateError, match="stale"):
        validate_terminal(value, now=now + timedelta(minutes=11))


def test_sshd_parity_is_typed_and_fresh() -> None:
    module = __import__("tgw.a3_host_state_observation", fromlist=["canonical"])
    now = datetime.now(timezone.utc)
    value = {
        "schema": "tgw-prod-a3-host-state-sshd-parity/v1",
        "status": "PASS",
        "ssh_sha256": _sha("ssh"),
        "identity_public": "ssh-ed25519 AAAA fixture",
        "known_hosts_sha256": _sha("hosts"),
        "identity_public_sha256": digest(b"ssh-ed25519 AAAA fixture\n"),
        "sshd_sha256": _sha("sshd"),
        "sshd_config_sha256": _sha("config"),
        "host_key_public_sha256": _sha("host-public"),
        "observed_at": now.isoformat(),
        "correct_key": True,
        "wrong_key_rejected": True,
        "default_key_rejected": True,
        "agent_rejected": True,
        "ambient_config_rejected": True,
        "framing_verified": True,
        "process_group_verified": True,
        "ssh_argv_policy": module._ssh_argv_policy(_request()),
        "local_process_environment": module._local_process_environment(),
        "evidence": {
            role: {
                "path": f"/protected/{role}",
                "sha256": {
                    "sshd_executable": _sha("sshd"),
                    "sshd_config": _sha("config"),
                    "host_key_public": _sha("host-public"),
                }.get(role, _sha(role)),
                "size": 10,
            }
            for role in {
                "sshd_executable",
                "sshd_config",
                "host_key_public",
                "correct_key_log",
                "wrong_key_log",
                "default_key_log",
                "agent_rejection_log",
                "ambient_config_rejection_log",
                "framing_log",
                "process_group_log",
            }
        },
    }
    value["receipt_sha256"] = digest(module.canonical(value))
    assert validate_sshd_parity(value, now=now) == value
    with pytest.raises(HostStateError, match="stale"):
        validate_sshd_parity(value, now=now + timedelta(hours=25))
    changed = deepcopy(value)
    changed["evidence"] = ["unbound"]
    changed["receipt_sha256"] = digest(module.canonical({key: item for key, item in changed.items() if key != "receipt_sha256"}))
    with pytest.raises(HostStateError):
        validate_sshd_parity(changed)
    changed = deepcopy(value)
    changed["ssh_argv_policy"] = ["ambient"]
    changed["receipt_sha256"] = digest(module.canonical({key: item for key, item in changed.items() if key != "receipt_sha256"}))
    request = _request()
    request["prerequisites"] = {
        "sshd_parity_sha256": digest(module.canonical(changed)),
        "sshd_parity_receipt_sha256": changed["receipt_sha256"],
    }
    request["request_sha256"] = digest(module.canonical({key: item for key, item in request.items() if key != "request_sha256"}))
    with pytest.raises(HostStateError, match="transport differs"):
        validate_request(
            request,
            parity_authority=changed,
            allow_fixture=True,
        )


class _FixtureProvider:
    def __init__(self, receipt: dict | None, *, ready: bool = True) -> None:
        self.receipt = receipt
        self.ready = ready
        self.calls = 0

    def prepare_launch(self, request: dict):
        if not self.ready:
            raise ObservationHold("not ready")

        def launch():
            self.calls += 1
            return self.receipt

        launch.close = lambda: None
        return launch


def _grant_and_roots(tmp_path: Path, request: dict):
    tmp_path.mkdir(parents=True, exist_ok=True)
    token_root = tmp_path / "tokens"
    evidence_root = tmp_path / "evidence"
    token_root.mkdir(mode=0o700)
    evidence_root.mkdir(mode=0o700)
    store = HostStateEvidenceStore(evidence_root)
    module = __import__("tgw.a3_host_state_observation", fromlist=["canonical"])
    artifact_fields = {
        "ssh_sha256",
        "ssh_keygen_sha256",
        "known_hosts_sha256",
        "identity_sha256",
        "identity_public_sha256",
        "helper_sha256",
    }
    composition_value = {
        "schema": "tgw-prod-a3-host-state-observation-composition/v1",
        "status": "EXECUTABLE",
        "request_sha256": request["request_sha256"],
        "plan_authority": {
            "path": "/protected/plan.json",
            "sha256": _sha("plan-file"),
            "identity": [1, 2, 0, 0, 0o444, 1, 10, 1, 1],
        },
        "sshd_parity_authority": {
            "path": "/protected/parity.json",
            "sha256": request["prerequisites"]["sshd_parity_sha256"],
            "identity": [1, 3, 0, 0, 0o444, 1, 10, 1, 1],
            "evidence_identities": {role: [1, 4 + index, 0, 0, 0o444, 1, 10, 1, 1] for index, role in enumerate(sorted(module._PARITY_EVIDENCE_ROLES))},
        },
        "artifacts": {
            field: {
                "path": f"/protected/{field}",
                "sha256": request["transport"][field],
                "identity": [1, 20, 0, 0, 0o444, 1, 10, 1, 1],
            }
            for field in artifact_fields
        },
        "ssh_version": {
            "value": "OpenSSH fixture",
            "sha256": digest(b"OpenSSH fixture\n"),
            "b64": "T3BlblNTSCBmaXh0dXJlCg==",
        },
        "ssh_argv_policy": module._ssh_argv_policy(request),
        "local_process_environment": module._local_process_environment(),
        "token_root_identity": _identity(token_root),
        "evidence_root_identity": store.identity,
    }
    composition = digest(module.canonical(composition_value))
    grant = HostStateObservationGrant._fixture_issue(
        request=request,
        composition_sha256=composition,
        token_root_identity=_identity(token_root),
        evidence_root_identity=store.identity,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    token = DurableObservationToken(str(token_root.absolute()), grant.value["grant_sha256"])
    return grant, token, store, composition, composition_value


def _grant_authority(grant: HostStateObservationGrant) -> dict:
    return {
        "path": "/protected/host-state-grant.json",
        "sha256": _sha("mounted-grant-file"),
        "grant_sha256": grant.value["grant_sha256"],
        "identity": [1, 97, 0, 0, 0o444, 1, 1024, 1, 1],
    }


def _grant_observation(grant: HostStateObservationGrant) -> dict:
    import tgw.a3_host_state_observation as host_state

    authority = _grant_authority(grant)
    value = {
        "schema": host_state.GRANT_OBSERVATION_SCHEMA,
        **authority,
        "held_identity": list(authority["identity"]),
        "named_identity": list(authority["identity"]),
        "held_sha256": authority["sha256"],
        "postcheck": "PASS",
    }
    value["observation_sha256"] = digest(host_state.canonical(value))
    return value


def _unbound_grant_authority() -> dict:
    return {
        "path": "/protected/unbound-host-state-grant.json",
        "sha256": _sha("unbound-mounted-grant-file"),
        "grant_sha256": _sha("unbound-grant"),
        "identity": [1, 98, 0, 0, 0o444, 1, 1024, 1, 1],
    }


def test_controller_holds_before_consuming_authority(tmp_path: Path) -> None:
    request = _request()
    grant, token, store, composition, _composition_value = _grant_and_roots(tmp_path, request)
    with pytest.raises(HostStateError, match="grant is not mounted"):
        HostStateObservationController().execute(request=request, composition=object(), grant=grant, token=token)
    assert not list((tmp_path / "tokens").iterdir())


def test_controller_consumes_once_and_persists_atomic_result(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path / "host")
    grant, token, store, composition, composition_value = _grant_and_roots(tmp_path / "authority", request)
    token.consume()
    success = terminal(
        outcome="PASS",
        stage="complete",
        code="NONE",
        dispatched=True,
        request_sha256=request["request_sha256"],
        observed_at=receipt["observed_at"],
    )
    dependency = dependency_projection(
        receipt,
        request,
        ssh_sha256=request["transport"]["ssh_sha256"],
        descriptor_sha256=composition,
    )
    refs = store.persist(
        request=request,
        receipt=receipt,
        terminal_value=success,
        grant=grant.value,
        grant_observation=_grant_observation(grant),
        token_identity=token.identity,
        dependency=dependency,
        composition_value=composition_value,
    )
    assert {Path(ref["path"]).name for ref in refs} >= {"request.json", "receipt.json", "terminal.json", "manifest.json"}
    result = {
        "schema": "tgw-prod-a3-host-state-observation-result/v1",
        "composition_sha256": composition,
        "evidence_root_identity": store.identity,
        "terminal": success,
        "receipt": receipt,
        "dependency": dependency,
        "evidence": list(refs),
        "persistence": None,
    }
    result["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(result))
    assert (
        validate_result(
            result,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=store.identity,
            expected_grant_authority=_grant_authority(grant),
        )
        == result
    )
    missing_composition = deepcopy(result)
    missing_composition["evidence"] = [ref for ref in refs if Path(ref["path"]).name != "composition.json"]
    missing_composition["result_sha256"] = digest(
        __import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in missing_composition.items() if key != "result_sha256"})
    )
    with pytest.raises(HostStateError):
        validate_result(
            missing_composition,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=store.identity,
            expected_grant_authority=_grant_authority(grant),
        )
    with pytest.raises(ObservationAlreadyConsumed):
        token.consume()


def test_failed_terminal_is_durably_bound_to_composition(tmp_path: Path) -> None:
    request = _request()
    grant, token, store, composition, composition_value = _grant_and_roots(tmp_path, request)
    token.consume()
    failed = terminal(
        outcome="FAILED",
        stage="remote",
        code="HELPER_FAILED",
        dispatched=True,
        request_sha256=request["request_sha256"],
        observed_at=datetime.now(timezone.utc).isoformat(),
        diagnostic=b"HelperError",
    )
    refs = store.persist_terminal(
        request=request,
        terminal_value=failed,
        grant=grant.value,
        grant_observation=_grant_observation(grant),
        token_identity=token.identity,
        composition_sha256=composition,
        composition_value=composition_value,
    )
    result = {
        "schema": "tgw-prod-a3-host-state-observation-result/v1",
        "composition_sha256": composition,
        "evidence_root_identity": store.identity,
        "terminal": failed,
        "receipt": None,
        "dependency": None,
        "evidence": list(refs),
        "persistence": None,
    }
    result["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(result))
    assert (
        validate_result(
            result,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=store.identity,
            expected_grant_authority=_grant_authority(grant),
        )
        == result
    )


def test_evidence_store_rejects_symlink_ancestor(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    with pytest.raises(HostStateError, match="symlink"):
        HostStateEvidenceStore(alias / "evidence")


@pytest.mark.parametrize("success_path", [False, True])
def test_post_publish_validation_failure_is_typed_ambiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    success_path: bool,
) -> None:
    import tgw.a3_host_state_observation as host_state

    request, receipt = _observe(tmp_path / "host")
    grant, token, store, composition, composition_value = _grant_and_roots(tmp_path / "authority", request)
    token.consume()
    terminal_value = terminal(
        outcome="PASS" if success_path else "FAILED",
        stage="complete" if success_path else "remote",
        code="NONE" if success_path else "HELPER_FAILED",
        dispatched=True,
        request_sha256=request["request_sha256"],
        observed_at=receipt["observed_at"],
    )

    def fail_after_publication(_paths: tuple[Path, ...]) -> tuple[dict, ...]:
        raise HostStateError("injected post-publication validation failure")

    monkeypatch.setattr(host_state, "_validate_evidence_paths", fail_after_publication)
    with pytest.raises(HostStatePersistenceAmbiguous) as caught:
        if success_path:
            store.persist(
                request=request,
                receipt=receipt,
                terminal_value=terminal_value,
                grant=grant.value,
                grant_observation=_grant_observation(grant),
                token_identity=token.identity,
                dependency=dependency_projection(
                    receipt,
                    request,
                    ssh_sha256=request["transport"]["ssh_sha256"],
                    descriptor_sha256=composition,
                ),
                composition_value=composition_value,
            )
        else:
            store.persist_terminal(
                request=request,
                terminal_value=terminal_value,
                grant=grant.value,
                grant_observation=_grant_observation(grant),
                token_identity=token.identity,
                composition_sha256=composition,
                composition_value=composition_value,
            )
    assert caught.value.terminal["stage"] == "persistence"
    assert caught.value.terminal["dispatched"] is True
    assert caught.value.context["original_terminal"] == terminal_value
    assert caught.value.context["status"] == "UNVERIFIED_NOT_DURABLE"
    assert any(path.is_dir() and not path.name.startswith(".attempt-") for path in store.root.iterdir())


def test_grant_prepublication_change_is_typed_and_not_published(
    tmp_path: Path,
) -> None:
    request = _request()
    grant, token, store, composition, composition_value = _grant_and_roots(tmp_path, request)
    token.consume()
    failed = terminal(
        outcome="FAILED",
        stage="remote",
        code="HELPER_FAILED",
        dispatched=True,
        request_sha256=request["request_sha256"],
        observed_at=datetime.now(timezone.utc).isoformat(),
    )

    def changed_before_publish() -> None:
        raise HostStateError("mounted grant changed")

    with pytest.raises(HostStatePersistenceAmbiguous) as caught:
        store.persist_terminal(
            request=request,
            terminal_value=failed,
            grant=grant.value,
            grant_observation=_grant_observation(grant),
            token_identity=token.identity,
            composition_sha256=composition,
            composition_value=composition_value,
            before_publish=changed_before_publish,
        )
    assert caught.value.terminal["stage"] == "persistence"
    assert caught.value.context["attempted_attachments"]["grant-authority.json"]["postcheck"] == "PASS"
    assert not any(path.is_dir() and not path.name.startswith(".attempt-") for path in store.root.iterdir())


def test_grant_rejects_bool_attempt_and_foreign_plan(tmp_path: Path) -> None:
    request = _request()
    grant, token, _store, _composition, _composition_value = _grant_and_roots(tmp_path, request)
    assert not hasattr(token, "__dict__")
    with pytest.raises(AttributeError, match="immutable"):
        token._grant_sha256 = _sha("replacement")
    changed = dict(grant.value)
    changed["attempts"] = True
    with pytest.raises(HostStateError):
        HostStateObservationGrant.validate(changed, request=request)
    changed = deepcopy(grant.value)
    changed["plan"]["commit"] = "2" * 40
    changed["grant_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in changed.items() if key != "grant_sha256"}))
    with pytest.raises(HostStateError):
        HostStateObservationGrant.validate(changed, request=request)


def test_production_authority_types_and_clock_seam_are_sealed() -> None:
    import inspect

    with pytest.raises(TypeError):

        class _Token(DurableObservationToken):
            pass

    with pytest.raises(TypeError):

        class _Store(HostStateEvidenceStore):
            pass

    with pytest.raises(TypeError):

        class _Grant(HostStateObservationGrant):
            pass

    assert "now" not in inspect.signature(HostStateObservationController.execute).parameters
    assert not hasattr(HostStateObservationGrant, "issue")
    with pytest.raises(HostStateError, match="loaded as authority"):
        HostStateObservationGrant({}, _token=object())


def test_mounted_grant_state_is_immutable() -> None:
    import tgw.a3_host_state_observation as host_state

    mounted = object.__new__(host_state.MountedHostStateObservationGrant)
    object.__setattr__(mounted, "_sealed", False)
    object.__setattr__(mounted, "_fd", -1)
    object.__setattr__(mounted, "_grant_raw", b"{}")
    object.__setattr__(mounted, "_identity", (1, 2, 0, 0, 0o444, 1, 2, 1, 1))
    object.__setattr__(mounted, "_path", Path("/protected/grant.json"))
    object.__setattr__(mounted, "_sha256", _sha("grant"))
    object.__setattr__(mounted, "_sealed", True)
    assert not hasattr(mounted, "__dict__")
    for field in ("fd", "grant", "identity", "path", "sha256", "_fd", "_grant_raw"):
        with pytest.raises(AttributeError, match="immutable"):
            setattr(mounted, field, object())


def test_controller_closes_prepared_authorities_on_type_rejection(
    tmp_path: Path,
) -> None:
    import tgw.a3_host_state_observation as host_state

    request = _request()
    grant, token, store, _composition, _composition_value = _grant_and_roots(tmp_path, request)
    closed: list[bool] = []

    def launch() -> None:
        raise AssertionError("launch must not run")

    launch.close = lambda: closed.append(True)  # type: ignore[attr-defined]
    composition = object.__new__(host_state.HostStateProductionComposition)
    composition.provider = None
    composition.evidence_store = store
    composition.launch = launch
    composition.authority_check = lambda: None
    composition.value = {}
    composition.receipt_sha256 = _sha("composition")
    composition.used = False
    with pytest.raises(HostStateError, match="grant is not mounted"):
        HostStateObservationController().execute(
            request=request,
            composition=composition,
            grant=grant,
            token=token,
        )
    assert closed == [True]
    assert store.ready() is False
    assert token.ready() is False


def test_controller_closes_composition_and_grant_on_token_type_rejection(
    tmp_path: Path,
) -> None:
    import tgw.a3_host_state_observation as host_state

    request = _request()
    grant, _token, store, _composition, _composition_value = _grant_and_roots(tmp_path, request)
    grant_path = tmp_path / "mounted-grant.json"
    grant_path.write_bytes(host_state.canonical(grant.value))
    grant_fd = os.open(grant_path, os.O_RDONLY | os.O_NOFOLLOW)
    mounted = object.__new__(host_state.MountedHostStateObservationGrant)
    object.__setattr__(mounted, "_sealed", False)
    object.__setattr__(mounted, "_fd", grant_fd)
    object.__setattr__(mounted, "_grant_raw", host_state.canonical(grant.value))
    object.__setattr__(mounted, "_identity", host_state._inode_identity(os.fstat(grant_fd)))
    object.__setattr__(mounted, "_path", grant_path)
    object.__setattr__(mounted, "_sha256", digest(grant_path.read_bytes()))
    object.__setattr__(mounted, "_sealed", True)
    closed: list[bool] = []

    def launch() -> None:
        raise AssertionError("launch must not run")

    launch.close = lambda: closed.append(True)  # type: ignore[attr-defined]
    composition = object.__new__(host_state.HostStateProductionComposition)
    composition.provider = None
    composition.evidence_store = store
    composition.launch = launch
    composition.authority_check = lambda: None
    composition.value = {}
    composition.receipt_sha256 = _sha("composition")
    composition.used = False
    with pytest.raises(HostStateError, match="token is not sealed"):
        HostStateObservationController().execute(
            request=request,
            composition=composition,
            grant=mounted,
            token=object(),
        )
    assert closed == [True]
    assert store.ready() is False
    with pytest.raises(OSError):
        os.fstat(grant_fd)


def test_result_rejects_forged_terminal_and_evidence(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path)
    success = terminal(
        outcome="PASS",
        stage="complete",
        code="NONE",
        dispatched=True,
        request_sha256=request["request_sha256"],
        observed_at=receipt["observed_at"],
    )
    dependency = dependency_projection(receipt, request, ssh_sha256=request["transport"]["ssh_sha256"], descriptor_sha256=_sha("composition"))
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    root_identity = _identity(evidence_root)
    result = {
        "schema": "tgw-prod-a3-host-state-observation-result/v1",
        "composition_sha256": _sha("foreign"),
        "evidence_root_identity": root_identity,
        "terminal": success,
        "receipt": receipt,
        "dependency": dependency,
        "evidence": ["garbage"],
        "persistence": None,
    }
    result["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(result))
    with pytest.raises(HostStateError):
        validate_result(
            result,
            request,
            expected_composition_sha256=_sha("composition"),
            expected_evidence_root_identity=root_identity,
            expected_grant_authority=_unbound_grant_authority(),
        )


@pytest.mark.parametrize(
    ("outcome", "stage", "code"),
    [
        ("HOLD", "prelaunch", "NOT_READY"),
        ("FAILED", "prelaunch", "VALIDATION_FAILED"),
        ("AMBIGUOUS", "persistence", "PERSISTENCE_UNCERTAIN"),
    ],
)
def test_result_rejects_terminal_not_emitted_by_controller(
    tmp_path: Path,
    outcome: str,
    stage: str,
    code: str,
) -> None:
    request = _request()
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    root_identity = _identity(evidence_root)
    terminal_value = terminal(
        outcome=outcome,
        stage=stage,
        code=code,
        dispatched=False,
        request_sha256=request["request_sha256"],
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    result = {
        "schema": "tgw-prod-a3-host-state-observation-result/v1",
        "composition_sha256": _sha("composition"),
        "evidence_root_identity": root_identity,
        "terminal": terminal_value,
        "receipt": None,
        "dependency": None,
        "evidence": [],
        "persistence": None,
    }
    result["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(result))
    with pytest.raises(HostStateError):
        validate_result(
            result,
            request,
            expected_composition_sha256=_sha("composition"),
            expected_evidence_root_identity=root_identity,
            expected_grant_authority=_unbound_grant_authority(),
        )


@pytest.mark.parametrize("dispatched", [False, True])
def test_result_accepts_controller_persistence_uncertainty_without_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dispatched: bool,
) -> None:
    import tgw.a3_host_state_observation as host_state

    request = _request()
    grant, token, store, composition, composition_value = _grant_and_roots(tmp_path, request)
    token.consume()
    original_terminal = terminal(
        outcome="FAILED" if dispatched else "AMBIGUOUS",
        stage="remote" if dispatched else "authority",
        code="HELPER_FAILED" if dispatched else "TOKEN_UNCERTAIN",
        dispatched=dispatched,
        request_sha256=request["request_sha256"],
        observed_at=datetime.now(timezone.utc).isoformat(),
        diagnostic=b"origin",
    )

    def fail_after_publication(_paths: tuple[Path, ...]) -> tuple[dict, ...]:
        raise HostStateError("injected post-publication validation failure")

    monkeypatch.setattr(host_state, "_validate_evidence_paths", fail_after_publication)
    with pytest.raises(HostStatePersistenceAmbiguous) as caught:
        store.persist_terminal(
            request=request,
            terminal_value=original_terminal,
            grant=grant.value,
            grant_observation=_grant_observation(grant),
            token_identity=token.identity,
            composition_sha256=composition,
            composition_value=composition_value,
        )
    terminal_value = caught.value.terminal
    root_identity = store.identity
    result = {
        "schema": "tgw-prod-a3-host-state-observation-result/v1",
        "composition_sha256": composition,
        "evidence_root_identity": root_identity,
        "terminal": terminal_value,
        "receipt": None,
        "dependency": None,
        "evidence": [],
        "persistence": caught.value.context,
    }
    result["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical(result))
    assert (
        validate_result(
            result,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=root_identity,
            expected_grant_authority=_grant_authority(grant),
        )
        == result
    )
    with pytest.raises(HostStateError):
        host_state._validate_persistence_context(
            caught.value.context,
            request=request,
            expected_composition_sha256=_sha("foreign-composition"),
            evidence_root=root_identity,
            expected_grant_authority=_grant_authority(grant),
            persistence_terminal=terminal_value,
        )
    changed = deepcopy(result)
    changed["persistence"]["attempt_name"] = "0" * 64
    changed["persistence"]["context_sha256"] = digest(
        __import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in changed["persistence"].items() if key != "context_sha256"})
    )
    changed["result_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in changed.items() if key != "result_sha256"}))
    with pytest.raises(HostStateError, match="attempt identity"):
        validate_result(
            changed,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=root_identity,
            expected_grant_authority=_grant_authority(grant),
        )
    store.root.rename(tmp_path / "displaced-evidence")
    assert (
        validate_result(
            result,
            request,
            expected_composition_sha256=composition,
            expected_evidence_root_identity=root_identity,
            expected_grant_authority=_grant_authority(grant),
        )
        == result
    )


def test_ssh_policy_closes_global_host_and_ambient_auth_fallbacks() -> None:
    import tgw.a3_host_state_observation as host_state

    policy = host_state._ssh_argv_policy(_request())
    for option in (
        "-oGlobalKnownHostsFile=/dev/null",
        "-oPreferredAuthentications=publickey",
        "-oKbdInteractiveAuthentication=no",
        "-oGSSAPIAuthentication=no",
        "-oHostbasedAuthentication=no",
        "-oControlMaster=no",
        "-oControlPath=none",
        "-oUpdateHostKeys=no",
        "-oVerifyHostKeyDNS=no",
        "-oPermitLocalCommand=no",
        "-oForwardAgent=no",
        "-oForwardX11=no",
        "-T",
    ):
        assert option in policy
    assert host_state._local_process_environment() == {
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "TZ": "UTC",
    }


def test_production_provider_cannot_be_subclassed() -> None:
    from tgw.a3_host_state_observation import SshHostStateProvider

    with pytest.raises(TypeError):

        class _Forged(SshHostStateProvider):
            pass


def test_fixture_provider_cannot_enter_production_composition(tmp_path: Path) -> None:
    from tgw.a3_host_state_observation import SshHostStateProvider

    request = _request()
    files = []
    for name in ("ssh", "keygen", "hosts", "private", "public", "helper"):
        path = tmp_path / name
        path.write_text("fixture")
        files.append(path)
    provider = SshHostStateProvider.fixture(
        request=request,
        ssh_path=files[0],
        ssh_keygen_path=files[1],
        known_hosts_path=files[2],
        identity_path=files[3],
        identity_public_path=files[4],
        helper_path=files[5],
    )
    token_root = tmp_path / "tokens"
    evidence_root = tmp_path / "evidence"
    token_root.mkdir(mode=0o700)
    evidence_root.mkdir(mode=0o700)
    with pytest.raises(HostStateError, match="sealed"):
        build_host_state_production_composition(
            provider=provider,
            token_root_identity=_identity(token_root),
            evidence_store=HostStateEvidenceStore(evidence_root),
        )


def test_fixture_ssh_path_uses_exact_argv_framing_and_group_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tgw.a3_host_state_observation as host_state
    from tgw.a3_host_state_observation import SshHostStateProvider

    host_root = tmp_path / "observed"
    initial_request = _request()
    _unused, receipt = _observe(host_root, initial_request)
    frame = encode_helper_response(receipt)
    ssh = tmp_path / "ssh"
    keygen = tmp_path / "ssh-keygen"
    hosts = tmp_path / "known-hosts"
    identity = tmp_path / "identity"
    identity_public = tmp_path / "identity.pub"
    helper = tmp_path / "helper.py"
    ssh.write_text(f"#!/usr/bin/python3\nimport os,sys\nwhile os.read(0,65536): pass\nos.write(1,{frame!r})\n")
    keygen.write_text(f"#!/usr/bin/python3\nimport os\nassert 'LD_PRELOAD' not in os.environ\nprint({initial_request['transport']['identity_public']!r})\n")
    hosts.write_text("tgw-prod ssh-ed25519 QUFBQQ==\n")
    identity.write_text("fixture-private-material\n")
    identity_public.write_text(initial_request["transport"]["identity_public"] + "\n")
    helper.write_bytes(Path(a3_host_state_helper.__file__).read_bytes())
    ssh.chmod(0o755)
    keygen.chmod(0o755)
    hosts.chmod(0o444)
    identity.chmod(0o400)
    identity_public.chmod(0o444)
    helper.chmod(0o444)
    transport = {
        "ssh_sha256": digest(ssh.read_bytes()),
        "ssh_keygen_sha256": digest(keygen.read_bytes()),
        "known_hosts_sha256": digest(hosts.read_bytes()),
        "identity_sha256": digest(identity.read_bytes()),
        "identity_public": initial_request["transport"]["identity_public"],
        "identity_public_sha256": digest(identity_public.read_bytes()),
        "helper_sha256": digest(helper.read_bytes()),
    }
    request = _request(transport=transport)
    receipt["request_sha256"] = request["request_sha256"]
    receipt["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    frame = encode_helper_response(receipt)
    ssh.write_text(f"#!/usr/bin/python3\nimport os\nwhile os.read(0,65536): pass\nos.write(1,{frame!r})\n")
    ssh.chmod(0o755)
    request["transport"]["ssh_sha256"] = digest(ssh.read_bytes())
    request["request_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in request.items() if key != "request_sha256"}))
    receipt["request_sha256"] = request["request_sha256"]
    receipt["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    ssh.write_text(f"#!/usr/bin/python3\nimport os\nwhile os.read(0,65536): pass\nos.write(1,{encode_helper_response(receipt)!r})\n")
    ssh.chmod(0o755)
    # The response bytes affect the fake executable hash, so one final request/receipt
    # cycle closes that fixture recursion before construction.
    request["transport"]["ssh_sha256"] = digest(ssh.read_bytes())
    request["request_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in request.items() if key != "request_sha256"}))
    receipt["request_sha256"] = request["request_sha256"]
    receipt["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    # Avoid a hash recursion in the fixture executable: write the final frame beside
    # it and have the fake SSH read that fixed file. Production has no such seam.
    frame_path = tmp_path / "frame"
    frame_path.write_bytes(encode_helper_response(receipt))
    frame_path.chmod(0o444)
    ssh.write_text(
        "#!/usr/bin/python3\n"
        "import os\n"
        "assert 'LD_PRELOAD' not in os.environ\n"
        "while os.read(0,65536): pass\n"
        f"fd=os.open({str(frame_path)!r},os.O_RDONLY);os.write(1,os.read(fd,1048576));os.close(fd)\n"
    )
    ssh.chmod(0o755)
    request["transport"]["ssh_sha256"] = digest(ssh.read_bytes())
    request["request_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in request.items() if key != "request_sha256"}))
    receipt["request_sha256"] = request["request_sha256"]
    receipt["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    frame_path.chmod(0o644)
    frame_path.write_bytes(encode_helper_response(receipt))
    frame_path.chmod(0o444)
    monkeypatch.setenv("LD_PRELOAD", "/ambient/not-admitted.so")
    provider = SshHostStateProvider.fixture(
        request=request,
        ssh_path=ssh,
        ssh_keygen_path=keygen,
        known_hosts_path=hosts,
        identity_path=identity,
        identity_public_path=identity_public,
        helper_path=helper,
    )
    with pytest.raises(HostStateError, match="sealed composition"):
        provider.prepare_launch(request)
    launch = provider.prepare_launch(request, _token=host_state._COMPOSITION_SEAL)
    assert launch() == receipt

    before = set(os.listdir("/proc/self/fd"))
    failing_launch = provider.prepare_launch(request, _token=host_state._COMPOSITION_SEAL)
    real_sealed = host_state._sealed
    calls = 0

    def fail_second_seal(name: str, raw: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected identity seal failure")
        return real_sealed(name, raw)

    monkeypatch.setattr(host_state, "_sealed", fail_second_seal)
    with pytest.raises(OSError, match="identity seal failure"):
        failing_launch()
    assert set(os.listdir("/proc/self/fd")) == before


def test_grant_schema_is_distinct() -> None:
    assert GRANT_SCHEMA == "tgw-prod-a3-host-state-observation-grant/v1"
    assert sys.executable
    assert os.path.isabs(sys.executable)


def test_streamed_helper_is_stdlib_only_and_validates_exact_request() -> None:
    helper_path = Path(a3_host_state_helper.__file__)
    tree = ast.parse(helper_path.read_text())
    imports = {alias.name.split(".", 1)[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imports.update(node.module.split(".", 1)[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module)
    assert "tgw" not in imports
    request = _request()
    assert a3_host_state_helper._validate_request(request, datetime.now(timezone.utc))["request_sha256"] == request["request_sha256"]


def test_provider_descriptor_is_self_hashed_and_truthfully_not_executable() -> None:
    import json

    from tgw.a3_host_state_observation import canonical

    root = Path(__file__).resolve().parents[1]
    path = root / "agent-services/providers/tgw-prod-a3-host-state-observation-REQUIRED.json"
    descriptor = json.loads(path.read_bytes())
    claimed = descriptor.pop("descriptor_sha256")
    assert claimed == digest(canonical(descriptor))
    assert descriptor["status"] == "IMPLEMENTED_NOT_EXECUTABLE"
    assert descriptor["dispatchable"] is False
    assert descriptor["grant"] is None and descriptor["request"] is None
    assert descriptor["source"]["controller_sha256"] == digest((root / descriptor["source"]["controller_path"]).read_bytes())
    assert descriptor["source"]["stdlib_helper_sha256"] == digest((root / descriptor["source"]["stdlib_helper_path"]).read_bytes())
