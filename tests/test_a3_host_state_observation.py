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
    ObservationHold,
    decode_helper_response,
    dependency_projection,
    digest,
    encode_helper_response,
    observe_host_state,
    terminal,
    validate_receipt,
    validate_request,
    validate_result,
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


@pytest.mark.parametrize(
    "tuple_value",
    list(
        [
            ("PASS", "complete", "NONE", True),
            ("HOLD", "prelaunch", "NOT_READY", False),
            ("FAILED", "prelaunch", "VALIDATION_FAILED", False),
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
    composition = _sha("composition")
    store = HostStateEvidenceStore(evidence_root)
    grant = HostStateObservationGrant.issue(
        request=request,
        composition_sha256=composition,
        token_root_identity=_identity(token_root),
        evidence_root_identity=store.identity,
        expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )
    token = DurableObservationToken(str(token_root.absolute()), grant.value["grant_sha256"])
    return grant, token, store, composition


def test_controller_holds_before_consuming_authority(tmp_path: Path) -> None:
    request = _request()
    grant, token, store, composition = _grant_and_roots(tmp_path, request)
    provider = _FixtureProvider(None, ready=False)
    result = HostStateObservationController(allow_test_provider=True).execute(
        request=request,
        provider=provider,
        grant=grant,
        token=token,
        evidence_store=store,
        composition_sha256=composition,
    )
    assert result["terminal"]["outcome"] == "HOLD"
    assert provider.calls == 0
    assert not list((tmp_path / "tokens").iterdir())


def test_controller_consumes_once_and_persists_atomic_result(tmp_path: Path) -> None:
    request, receipt = _observe(tmp_path / "host")
    grant, token, store, composition = _grant_and_roots(tmp_path / "authority", request)
    provider = _FixtureProvider(receipt)
    result = HostStateObservationController(allow_test_provider=True).execute(
        request=request,
        provider=provider,
        grant=grant,
        token=token,
        evidence_store=store,
        composition_sha256=composition,
    )
    assert validate_result(result, request)["terminal"]["outcome"] == "PASS"
    assert provider.calls == 1
    with pytest.raises(ObservationAlreadyConsumed):
        HostStateObservationController(allow_test_provider=True).execute(
            request=request,
            provider=provider,
            grant=grant,
            token=token,
            evidence_store=store,
            composition_sha256=composition,
        )


def test_grant_rejects_bool_attempt_and_foreign_plan(tmp_path: Path) -> None:
    request = _request()
    grant, _token, _store, _composition = _grant_and_roots(tmp_path, request)
    changed = dict(grant.value)
    changed["attempts"] = True
    with pytest.raises(HostStateError):
        HostStateObservationGrant.validate(changed, request=request)
    changed = deepcopy(grant.value)
    changed["plan"]["commit"] = "2" * 40
    changed["grant_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in changed.items() if key != "grant_sha256"}))
    with pytest.raises(HostStateError):
        HostStateObservationGrant.validate(changed, request=request)


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
    result = {"schema": "tgw-prod-a3-host-state-observation-result/v1", "terminal": success, "receipt": receipt, "dependency": dependency, "evidence": ["garbage"]}
    with pytest.raises(HostStateError):
        validate_result(result, request)


def test_production_provider_cannot_be_subclassed() -> None:
    from tgw.a3_host_state_observation import SshHostStateProvider

    with pytest.raises(TypeError):

        class _Forged(SshHostStateProvider):
            pass


def test_fixture_ssh_path_uses_exact_argv_framing_and_group_cleanup(tmp_path: Path) -> None:
    from tgw.a3_host_state_observation import SshHostStateProvider

    host_root = tmp_path / "observed"
    initial_request = _request()
    _unused, receipt = _observe(host_root, initial_request)
    frame = encode_helper_response(receipt)
    ssh = tmp_path / "ssh"
    keygen = tmp_path / "ssh-keygen"
    hosts = tmp_path / "known-hosts"
    identity = tmp_path / "identity"
    helper = tmp_path / "helper.py"
    ssh.write_text(f"#!/usr/bin/python3\nimport os,sys\nwhile os.read(0,65536): pass\nos.write(1,{frame!r})\n")
    keygen.write_text(f"#!/usr/bin/python3\nprint({initial_request['transport']['identity_public']!r})\n")
    hosts.write_text("tgw-prod ssh-ed25519 QUFBQQ==\n")
    identity.write_text("fixture-private-material\n")
    helper.write_bytes(Path(a3_host_state_helper.__file__).read_bytes())
    ssh.chmod(0o755)
    keygen.chmod(0o755)
    hosts.chmod(0o444)
    identity.chmod(0o400)
    helper.chmod(0o444)
    transport = {
        "ssh_sha256": digest(ssh.read_bytes()),
        "ssh_keygen_sha256": digest(keygen.read_bytes()),
        "known_hosts_sha256": digest(hosts.read_bytes()),
        "identity_sha256": digest(identity.read_bytes()),
        "identity_public": initial_request["transport"]["identity_public"],
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
    ssh.write_text(f"#!/usr/bin/python3\nimport os\nwhile os.read(0,65536): pass\nfd=os.open({str(frame_path)!r},os.O_RDONLY);os.write(1,os.read(fd,1048576));os.close(fd)\n")
    ssh.chmod(0o755)
    request["transport"]["ssh_sha256"] = digest(ssh.read_bytes())
    request["request_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in request.items() if key != "request_sha256"}))
    receipt["request_sha256"] = request["request_sha256"]
    receipt["receipt_sha256"] = digest(__import__("tgw.a3_host_state_observation", fromlist=["canonical"]).canonical({key: value for key, value in receipt.items() if key != "receipt_sha256"}))
    frame_path.chmod(0o644)
    frame_path.write_bytes(encode_helper_response(receipt))
    frame_path.chmod(0o444)
    provider = SshHostStateProvider.fixture(
        request=request,
        ssh_path=ssh,
        ssh_keygen_path=keygen,
        known_hosts_path=hosts,
        identity_path=identity,
        helper_path=helper,
    )
    launch = provider.prepare_launch(request)
    assert launch() == receipt


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
