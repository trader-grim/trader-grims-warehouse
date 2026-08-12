from __future__ import annotations

import base64
import copy
import fcntl
import io
import json
import os
import pwd
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw import nix_observer_render_evaluation as provider
from tgw import nix_observer_render_helper as helper
from tgw import nix_observer_render_remote as remote_bootstrap
from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.nixos_observer_render_evaluation import (
    ATTEMPT_RECEIPT_ROOT,
    AUDITED_A2_COMMIT,
    EFFECT_KIND,
    HELPER_SHA256,
    PRODUCTION_COMPOSITION_PATH,
    REMOTE_SUDO_PATH,
    REMOTE_WRAPPER_PATH,
    CompositionHold,
    ImmutableTerminalReceiptStore,
    ObserverRenderController,
    RemoteAttemptAmbiguous,
    RemoteRenderFailure,
    RenderComposition,
    RenderTransportError,
    SshObserverRenderTransport,
    TerminalPersistenceError,
    _attempt_receipt,
    _attestation_payload,
    _digest_bytes,
    canonical,
    load_composition,
    main,
    serialize_remote_argv,
    validate_handler_success,
)
from tgw.plan_authority import TypedEffect


def _fixtures():
    path = Path(__file__).with_name("test_nix_observer_render_helper.py")
    spec = spec_from_file_location("a3_render_helper_fixtures", path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _case(tmp_path: Path, *, mode: str = "success"):
    tmp_path.mkdir()
    return _fixtures()._make_a2_case(tmp_path, mode=mode)


def _terminal(case, *, cleanup_failure: bool = False):
    fixtures = _fixtures()
    return fixtures._run_bootstrap(
        case["wire"],
        a2_authority=case["authority"],
        scratch_root=case["scratch"],
        cleanup_failure=cleanup_failure,
    )


def _effect(case, *, generation: str = "render-a3-1"):
    return {"kind": EFFECT_KIND, "generation": generation, "parameters": case["request"]}


def _self_hash(value):
    value["receipt_sha256"] = _digest_bytes(canonical(value))
    return value


def _write_immutable(path: Path, value: bytes, mode: int = 0o444) -> None:
    path.write_bytes(value)
    path.chmod(mode)


def _identity(path: Path) -> dict[str, object]:
    metadata = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": _digest_bytes(path.read_bytes()),
        "size": metadata.st_size,
        "owner_uid": metadata.st_uid,
        "mode": stat.S_IMODE(metadata.st_mode),
    }


def _composition(
    tmp_path: Path,
    case,
    private_key: Ed25519PrivateKey,
    *,
    host: str = "100.107.99.66",
    port: int = 22,
    user: str = "codex",
    identity_file: Path | None = None,
    known_hosts: Path | None = None,
) -> tuple[RenderComposition, Path]:
    tmp_path.mkdir(exist_ok=True)
    request_path = tmp_path / "request.json"
    _write_immutable(request_path, canonical(case["request"]))
    if known_hosts is None:
        known_hosts = tmp_path / "known_hosts"
        token = host if port == 22 else f"[{host}]:{port}"
        _write_immutable(known_hosts, f"{token} ssh-ed25519 AAAA\n".encode(), 0o600)
    if identity_file is None:
        identity_file = tmp_path / "review_identity"
        _write_immutable(identity_file, b"-----BEGIN OPENSSH PRIVATE KEY-----\ntest-only\n-----END OPENSSH PRIVATE KEY-----\n", 0o600)
    public_path = tmp_path / "attestation.pub.raw"
    public_raw = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    _write_immutable(public_path, public_raw)
    archive = case["scratch"].parent / "source.tar"
    archive.chmod(0o444)
    components = {
        "transport": _identity(Path(__file__).parents[1] / "src/tgw/nixos_observer_render_evaluation.py"),
        "helper": _identity(Path(helper.__file__)),
        "provider": _identity(Path(provider.__file__)),
        "remote_bootstrap": _identity(Path(remote_bootstrap.__file__)),
        "remote_bootstrap_remote_path": "/nix/store/11111111111111111111111111111111-render-remote.py",
        "helper_remote_path": "/nix/store/22222222222222222222222222222222-render-helper.py",
    }
    assert components["helper"]["sha256"] == HELPER_SHA256
    wrapper_sha = "sha256:" + "a" * 64
    sudoers_sha = "sha256:" + "b" * 64
    remote_python_sha = "sha256:" + "c" * 64
    remote_ip_sha = "sha256:" + "d" * 64
    remote_sudo_sha = "sha256:" + "e" * 64
    source = {
        "commit": case["request"]["source_commit"],
        "tree": case["request"]["source_tree"],
        "artifact_ref": case["request"]["artifact_ref"],
        "archive": _identity(archive),
    }
    source_audit = _self_hash(
        {
            "schema": "tgw-nixos-observer-render-source-audit/v1",
            "status": "PASS",
            "audited_a2_commit": AUDITED_A2_COMMIT,
            "source": {"commit": source["commit"], "tree": source["tree"]},
            "helper_sha256": components["helper"]["sha256"],
            "provider_sha256": components["provider"]["sha256"],
            "transport_sha256": components["transport"]["sha256"],
            "remote_bootstrap_sha256": components["remote_bootstrap"]["sha256"],
            "observed_at": "2026-08-12T19:00:00Z",
        }
    )
    audit_path = tmp_path / "source-audit.json"
    _write_immutable(audit_path, canonical(source_audit))
    source["audit_receipt"] = _identity(audit_path)
    freeze = _self_hash(
        {
            "schema": "tgw-nixos-observer-render-freeze-receipt/v1",
            "status": "FROZEN_AFTER_SOURCE_PASS",
            "source_pass_receipt_sha256": source["audit_receipt"]["sha256"],
            "source": {
                "commit": source["commit"],
                "tree": source["tree"],
                "artifact_ref": source["artifact_ref"],
                "archive_sha256": source["archive"]["sha256"],
            },
            "request_sha256": case["request"]["request_sha256"],
            "sequence": ["SOURCE_PASS", "SOURCE_FREEZE", "REQUEST_FREEZE"],
            "observed_at": "2026-08-12T19:00:01Z",
        }
    )
    freeze_path = tmp_path / "freeze-receipt.json"
    _write_immutable(freeze_path, canonical(freeze))
    source["freeze_receipt"] = _identity(freeze_path)
    prerequisite = _self_hash(
        {
            "schema": "tgw-nixos-observer-render-wrapper-prerequisite/v1",
            "status": "INSTALLED",
            "wrapper": {"path": REMOTE_WRAPPER_PATH, "sha256": wrapper_sha, "owner_uid": 0, "mode": 0o555, "no_argv": True},
            "remote_bootstrap": {"path": components["remote_bootstrap_remote_path"], "sha256": components["remote_bootstrap"]["sha256"]},
            "helper": {"path": components["helper_remote_path"], "sha256": HELPER_SHA256},
            "python": {"path": "/run/current-system/sw/bin/python3", "sha256": remote_python_sha},
            "ip": {"path": "/run/current-system/sw/bin/ip", "sha256": remote_ip_sha},
            "sudo": {"path": REMOTE_SUDO_PATH, "sha256": remote_sudo_sha},
            "sudoers": {"user": user, "runas": "root", "command": REMOTE_WRAPPER_PATH, "arguments": [], "nopasswd": True, "sha256": sudoers_sha},
            "attestation_public_key_sha256": _identity(public_path)["sha256"],
            "remote_uid": os.getuid(),
            "remote_gid": os.getgid(),
        }
    )
    prerequisite_path = tmp_path / "wrapper-prerequisite.json"
    _write_immutable(prerequisite_path, canonical(prerequisite))
    value = {
        "schema": "tgw-nixos-observer-render-composition/v2",
        "audited_a2_commit": AUDITED_A2_COMMIT,
        "request_sha256": case["request"]["request_sha256"],
        "request": {"artifact": _identity(request_path), "request_sha256": case["request"]["request_sha256"]},
        "source": source,
        "components": components,
        "ssh": {
            "executable": _identity(Path("/usr/bin/ssh")),
            "remote_host": host,
            "remote_user": user,
            "remote_port": port,
            "known_hosts": _identity(known_hosts),
            "identity_file": _identity(identity_file),
        },
        "wrapper": {
            "path": REMOTE_WRAPPER_PATH,
            "sha256": wrapper_sha,
            "prerequisite_receipt": _identity(prerequisite_path),
            "sudoers_sha256": sudoers_sha,
            "attestation_public_key": _identity(public_path),
            "remote_uid": os.getuid(),
            "remote_gid": os.getgid(),
            "remote_python_path": "/run/current-system/sw/bin/python3",
            "remote_python_sha256": remote_python_sha,
            "remote_ip_path": "/run/current-system/sw/bin/ip",
            "remote_ip_sha256": remote_ip_sha,
            "remote_sudo_sha256": remote_sudo_sha,
        },
        "receipt_roots": {"attempts": str(tmp_path / "attempts"), "terminals": str(tmp_path / "terminals")},
    }
    _self_hash(value)
    descriptor_path = tmp_path / "composition.json"
    _write_immutable(descriptor_path, canonical(value), 0o400)
    return load_composition(descriptor_path, _trusted_owner_uid=os.getuid(), _allow_test_source=True), descriptor_path


class MemoryStore:
    def __init__(self, error: BaseException | None = None):
        self.values = []
        self.error = error

    def persist(self, value):
        self.values.append(value)
        if self.error:
            raise self.error
        raw = canonical(value)
        return {"artifact_ref": "artifact:" + _digest_bytes(raw), "sha256": _digest_bytes(raw), "size": len(raw)}


def _envelope(composition: RenderComposition, terminal_raw: bytes, returncode: int, private_key: Ed25519PrivateKey):
    probe = {
        "schema": "tgw-render-netns-negative-probe/v1",
        "links": ["lo"],
        "loopback_state": "down",
        "ipv4_route_count": 0,
        "ipv6_route_count": 0,
        "direct_probe": "ENETUNREACH",
    }
    value = {
        "schema": "tgw-nixos-observer-render-wrapper-envelope/v1",
        "request_sha256": composition.value["request_sha256"],
        "helper_sha256": HELPER_SHA256,
        "remote_bootstrap_sha256": composition.components["remote_bootstrap"]["sha256"],
        "remote_python_sha256": composition.wrapper["remote_python_sha256"],
        "remote_ip_sha256": composition.wrapper["remote_ip_sha256"],
        "wrapper_sha256": composition.wrapper["sha256"],
        "wrapper_prerequisite_receipt_sha256": composition.wrapper["prerequisite_receipt"]["sha256"],
        "namespace": {
            "schema": "tgw-render-network-namespace-evidence/v1",
            "before": "net:[101]",
            "after": "net:[202]",
            "changed": True,
            "pre": probe,
            "post": probe,
        },
        "child": {
            "uid": composition.wrapper["remote_uid"],
            "gid": composition.wrapper["remote_gid"],
            "returncode": returncode,
            "terminal_bytes": len(terminal_raw),
            "terminal_sha256": _digest_bytes(terminal_raw),
            "terminal_b64": base64.b64encode(terminal_raw).decode(),
        },
        "attestation": {
            "algorithm": "ed25519",
            "public_key_sha256": composition.wrapper["attestation_public_key"]["sha256"],
            "signature": "",
        },
    }
    value["attestation"]["signature"] = base64.b64encode(private_key.sign(_attestation_payload(value))).decode()
    return value


def _transport(tmp_path: Path, case, composition, private_key, *, terminal=None):
    seen = {}
    completed = terminal or _terminal(case)
    outer = _envelope(composition, completed.stdout, completed.returncode, private_key)

    def invoke(command, **kwargs):
        pass_fds = kwargs["pass_fds"]
        seen.update(
            command=command,
            kwargs=kwargs,
            sealed_modes=[stat.S_IMODE(os.fstat(fd).st_mode) for fd in pass_fds[1:]],
            seals=[fcntl.fcntl(fd, fcntl.F_GET_SEALS) for fd in pass_fds[1:]],
        )
        return subprocess.CompletedProcess(command, completed.returncode, canonical(outer), b"")

    attempts = MemoryStore()
    transport = SshObserverRenderTransport(
        composition,
        attempts,
        tool_authority=_fixtures().LOCAL_TOOL_AUTHORITY,
        invoke=invoke,
        _use_sudo=True,
    )
    return transport, attempts, seen


def test_production_has_no_candidate_constants_and_missing_composition_holds():
    module = Path("src/tgw/nixos_observer_render_evaluation.py").read_text()
    assert "SOURCE_REF =" not in module
    assert "SOURCE_PATH =" not in module
    assert str(PRODUCTION_COMPOSITION_PATH) in module
    with pytest.raises(CompositionHold, match="composition"):
        load_composition(Path("/definitely/absent/composition.json"))


def test_composition_requires_fresh_exact_source_pass_and_current_a2_components(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, descriptor = _composition(tmp_path / "composition", case, private)
    assert composition.value["audited_a2_commit"] == AUDITED_A2_COMMIT
    assert composition.components["helper"]["sha256"] == HELPER_SHA256
    assert composition.validate_request(case["request"]) == case["request"]

    stale = json.loads(descriptor.read_bytes())
    audit_path = Path(stale["source"]["audit_receipt"]["path"])
    audit = json.loads(audit_path.read_bytes())
    audit["status"] = "HOLD"
    audit.pop("receipt_sha256")
    _self_hash(audit)
    audit_path.chmod(0o644)
    _write_immutable(audit_path, canonical(audit))
    stale["source"]["audit_receipt"] = _identity(audit_path)
    stale.pop("receipt_sha256")
    _self_hash(stale)
    descriptor.chmod(0o600)
    _write_immutable(descriptor, canonical(stale), 0o400)
    with pytest.raises(CompositionHold, match="PASS"):
        load_composition(descriptor, _trusted_owner_uid=os.getuid(), _allow_test_source=True)


def test_composition_refuses_old_request_source_or_provider_before_transport(tmp_path):
    case = _case(tmp_path / "case")
    composition, _ = _composition(tmp_path / "composition", case, Ed25519PrivateKey.generate())
    for field, replacement in (
        ("source_commit", "f" * 40),
        ("source_tree", "e" * 40),
        ("provider_sha256", "sha256:" + "0" * 64),
    ):
        changed = copy.deepcopy(case["request"])
        changed[field] = replacement
        changed.pop("request_sha256")
        changed["request_sha256"] = _digest_bytes(provider.canonical(changed))
        with pytest.raises(CompositionHold):
            composition.validate_request(changed)


def test_composition_refuses_request_freeze_not_generated_after_source_pass(tmp_path):
    case = _case(tmp_path / "case")
    _, descriptor = _composition(tmp_path / "composition", case, Ed25519PrivateKey.generate())
    value = json.loads(descriptor.read_bytes())
    freeze_path = Path(value["source"]["freeze_receipt"]["path"])
    freeze = json.loads(freeze_path.read_bytes())
    freeze["observed_at"] = "2026-08-12T18:59:59Z"
    freeze.pop("receipt_sha256")
    _self_hash(freeze)
    freeze_path.chmod(0o644)
    _write_immutable(freeze_path, canonical(freeze))
    value["source"]["freeze_receipt"] = _identity(freeze_path)
    value.pop("receipt_sha256")
    _self_hash(value)
    descriptor.chmod(0o600)
    _write_immutable(descriptor, canonical(value), 0o400)
    with pytest.raises(CompositionHold, match="after source PASS"):
        load_composition(descriptor, _trusted_owner_uid=os.getuid(), _allow_test_source=True)


def test_transport_uses_dedicated_auth_sealed_host_key_fixed_no_argv_wrapper_and_closed_env(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    transport, attempts, seen = _transport(tmp_path, case, composition, private)
    exchange = transport(case["request"], generation="render-a3-1")

    assert seen["kwargs"]["input"] == case["wire"]
    assert seen["kwargs"]["env"] == {"LC_ALL": "C"}
    command = seen["command"]
    assert command.count("--") == 1
    assert "-F" in command and "/dev/null" in command
    assert "-oIdentitiesOnly=yes" in command
    assert "-oIdentityAgent=none" in command
    assert any(token.startswith("-oIdentityFile=/proc/") for token in command)
    assert any(token.startswith("-oUserKnownHostsFile=/proc/") for token in command)
    assert seen["sealed_modes"] == [0o400, 0o400]
    expected_seals = fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
    assert seen["seals"] == [expected_seals, expected_seals]
    assert "-oGlobalKnownHostsFile=/dev/null" in command
    assert "codex@100.107.99.66" in command
    assert command[-1] == serialize_remote_argv([REMOTE_SUDO_PATH, "-n", "--", REMOTE_WRAPPER_PATH])
    assert "python" not in command[-1] and "-c" not in command[-1]
    assert len(attempts.values) == 1
    assert exchange.attempt_receipt["outcome"] == "LAUNCHED_UNRECONCILED"
    assert exchange.attempt_receipt["reconciliation"]["required"] is True


def test_post_launch_timeout_is_ambiguous_with_immutable_reconciliation_receipt(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    transport, attempts, _ = _transport(tmp_path, case, composition, private)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    transport.invoke = timeout
    with pytest.raises(RemoteAttemptAmbiguous) as raised:
        transport(case["request"], generation="render-a3-timeout")
    assert raised.value.attempt_receipt == attempts.values[0]
    assert raised.value.reconciliation["request_sha256"] == case["request"]["request_sha256"]


@pytest.mark.parametrize("payload", [b"", b"not-json", b"[]", b'{"schema":"old/v0"}'])
def test_every_empty_malformed_or_lost_post_launch_receipt_is_ambiguous(tmp_path, payload):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    transport, _, _ = _transport(tmp_path, case, composition, private)
    transport.invoke = lambda command, **kwargs: subprocess.CompletedProcess(command, 255, payload, b"")
    controller = ObserverRenderController(transport, MemoryStore(), composition=composition, authority=case["authority"])
    with pytest.raises(RemoteAttemptAmbiguous) as raised:
        controller(_effect(case))
    assert raised.value.attempt_receipt["outcome"] == "LAUNCHED_UNRECONCILED"


def test_controller_accepts_signed_pre_and_post_probe_evidence_then_persists(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    completed = _terminal(case)
    transport, _, _ = _transport(tmp_path, case, composition, private, terminal=completed)
    store = MemoryStore()
    controller = ObserverRenderController(transport, store, composition=composition, authority=case["authority"])

    result = controller(_effect(case))

    assert result["terminal"]["schema"] == helper.SUCCESS_SCHEMA
    assert result["transport_receipt"]["namespace"]["pre"] == result["transport_receipt"]["namespace"]["post"]
    assert result["transport_receipt"]["namespace"]["pre"]["direct_probe"] == "ENETUNREACH"
    assert store.values == [result["terminal"]]


def test_signed_namespace_or_wrapper_identity_attack_is_ambiguous_not_failed(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    completed = _terminal(case)
    outer = _envelope(composition, completed.stdout, completed.returncode, private)
    outer["namespace"]["post"]["direct_probe"] = "connected"
    transport, _, _ = _transport(tmp_path, case, composition, private, terminal=completed)
    transport.invoke = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, canonical(outer), b"")
    controller = ObserverRenderController(transport, MemoryStore(), composition=composition, authority=case["authority"])
    with pytest.raises(RemoteAttemptAmbiguous):
        controller(_effect(case))


def test_only_validated_remote_failure_becomes_failed(tmp_path):
    case = _case(tmp_path / "case", mode="nix-failure")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    completed = _terminal(case)
    transport, _, _ = _transport(tmp_path, case, composition, private, terminal=completed)
    store = MemoryStore()
    controller = ObserverRenderController(transport, store, composition=composition, authority=case["authority"])
    with pytest.raises(RemoteRenderFailure) as raised:
        controller(_effect(case))
    assert raised.value.terminal["schema"] == helper.A2_FAILURE_SCHEMA
    assert raised.value.terminal["outcome"] == "FAILED"
    assert store.values == [raised.value.terminal]


def test_terminal_persistence_loss_remains_ambiguous_and_keeps_attempt_binding(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    composition, _ = _composition(tmp_path / "composition", case, private)
    transport, attempts, _ = _transport(tmp_path, case, composition, private)
    controller = ObserverRenderController(transport, MemoryStore(OSError("disk")), composition=composition, authority=case["authority"])
    with pytest.raises(TerminalPersistenceError) as raised:
        controller(_effect(case))
    assert raised.value.terminal["schema"] == helper.SUCCESS_SCHEMA
    assert raised.value.attempt_receipt == attempts.values[0]


def test_immutable_receipt_store_is_exclusive_and_content_addressed(tmp_path):
    store = ImmutableTerminalReceiptStore(tmp_path / "receipts")
    value = {"schema": "test-terminal/v1", "outcome": "TEST"}
    first = store.persist(value)
    second = store.persist(value)
    assert first == second
    path = Path(first["path"])
    assert path.read_bytes() == canonical(value)
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    store.close()


def _production_success(tmp_path: Path):
    path = Path(__file__).with_name("test_nix_observer_render_evaluation.py")
    spec = spec_from_file_location("a3_render_contract_fixtures", path)
    assert spec and spec.loader
    fixtures = module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    request = fixtures.request()
    authority = helper.PRODUCTION_A2_AUTHORITY
    request.update(
        provider_sha256=_digest_bytes(Path(provider.__file__).read_bytes()),
        host_identity_receipt_sha256=authority.a2_prerequisite_receipt_sha256,
        systemd_analyze_sha256=authority.systemd_analyze_sha256,
        systemd_analyze_version=authority.systemd_analyze_version,
        systemd_analyze_version_stdout_sha256=authority.systemd_analyze_version_stdout_sha256,
        systemd_analyze_version_stdout_bytes=authority.systemd_analyze_version_stdout_bytes,
        input_closure_manifest=[
            {
                "node": "nixpkgs",
                "rev": "ac62194c3917d5f474c1a844b6fd6da2db95077d",
                "lock_nar_hash": "sha256-16KkgfdYqjaeRGBaYsNrhPRRENs0qzkQVUooNHtoy2w=",
                "store_path": authority.input_path,
                "nar_sha256": authority.input_nar_sha256,
            }
        ],
    )
    request["input_closure_manifest_sha256"] = _digest_bytes(helper.canonical(request["input_closure_manifest"]))
    request.pop("request_sha256")
    request["request_sha256"] = _digest_bytes(helper.canonical(request))
    public_path = tmp_path / "public.raw"
    private = Ed25519PrivateKey.generate()
    _write_immutable(public_path, private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    components = {
        "transport": _identity(Path(__file__).parents[1] / "src/tgw/nixos_observer_render_evaluation.py"),
        "helper": _identity(Path(helper.__file__)),
        "provider": _identity(Path(provider.__file__)),
        "remote_bootstrap": _identity(Path(remote_bootstrap.__file__)),
        "remote_bootstrap_remote_path": "/nix/store/1-remote.py",
        "helper_remote_path": "/nix/store/2-helper.py",
    }
    value = {
        "schema": "tgw-nixos-observer-render-composition/v2",
        "audited_a2_commit": AUDITED_A2_COMMIT,
        "request_sha256": request["request_sha256"],
        "request": {},
        "source": {
            "commit": request["source_commit"],
            "tree": request["source_tree"],
            "artifact_ref": request["artifact_ref"],
            "archive": {"path": "/not-reopened-by-handler", "sha256": request["archive_sha256"], "size": 1, "owner_uid": 0, "mode": 0o444},
            "audit_receipt": {},
        },
        "components": components,
        "ssh": {
            "remote_user": "codex",
            "remote_host": "100.107.99.66",
            "remote_port": 22,
            "executable": {"sha256": "sha256:" + "1" * 64},
            "known_hosts": {"sha256": "sha256:" + "2" * 64},
            "identity_file": {"sha256": "sha256:" + "3" * 64},
        },
        "wrapper": {
            "path": REMOTE_WRAPPER_PATH,
            "sha256": "sha256:" + "a" * 64,
            "prerequisite_receipt": {"sha256": "sha256:" + "b" * 64},
            "sudoers_sha256": "sha256:" + "c" * 64,
            "attestation_public_key": _identity(public_path),
            "remote_uid": os.getuid(),
            "remote_gid": os.getgid(),
            "remote_python_path": "/run/current-system/sw/bin/python3",
            "remote_python_sha256": "sha256:" + "d" * 64,
            "remote_ip_path": "/run/current-system/sw/bin/ip",
            "remote_ip_sha256": "sha256:" + "e" * 64,
            "remote_sudo_sha256": "sha256:" + "f" * 64,
        },
        "receipt_roots": {"attempts": str(ATTEMPT_RECEIPT_ROOT), "terminals": "/tmp/test"},
    }
    _self_hash(value)
    composition = RenderComposition(value, request, True)
    binding = helper.WireBinding(
            request_bytes=len(canonical(request)),
            helper_bytes=components["helper"]["size"],
            tool_descriptor_bytes=len(canonical(helper._expected_tool_descriptor(request["request_sha256"], helper.PRODUCTION_GIT_AUTHORITY))),
            archive_bytes=1,
            request_sha256=request["request_sha256"],
            helper_sha256=HELPER_SHA256,
            tool_descriptor_sha256=_digest_bytes(canonical(helper._expected_tool_descriptor(request["request_sha256"], helper.PRODUCTION_GIT_AUTHORITY))),
            archive_sha256=request["archive_sha256"],
    )
    provider_receipt = fixtures.result(request)
    provider_receipt["systemd_verify"]["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider_receipt["receipt_sha256"] = _digest_bytes(helper.canonical({key: item for key, item in provider_receipt.items() if key != "receipt_sha256"}))
    policy = {
        "schema": "tgw-nix-observer-render-execution-policy/v1",
        "environment": {
            "HOME": str(helper.SCRATCH_ROOT / ("run-" + "1" * 32) / "nix-home"),
            "TMPDIR": str(helper.SCRATCH_ROOT / ("run-" + "1" * 32) / "tmp"),
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": "/no-ambient-path",
            "NIX_REMOTE": "local",
            "NIX_CONFIG": helper.NIX_CONFIG,
        },
        "nix_argv_prefix": list(helper.NIX_ARGV_PREFIX),
        "render_attr": helper.RENDER_ATTR,
        "build_selector": "evaluated-drv^out",
        "ambient_environment_inherited": False,
        "remote_builders": False,
        "builder_substitutes": False,
        "sandbox_required": True,
        "sandbox_fallback": False,
    }
    closure = [{"path": provider_receipt["output_root"], "nar_sha256": "sha256:" + "4" * 64}]
    terminal = {
        "schema": helper.SUCCESS_SCHEMA,
        "outcome": "VERIFIED",
        "provider_receipt_sha256": provider_receipt["receipt_sha256"],
        "closure_manifest": closure,
        "closure_manifest_sha256": _digest_bytes(helper.canonical(closure)),
        "closure_path_count": 1,
        "cleanup": "removed",
        "effects": helper._a2_effects(True),
        "provider_receipt": provider_receipt,
        **helper._a2_terminal_base(
            binding,
            request,
            tool_manifest_sha256=_digest_bytes(helper.canonical(helper._a2_tool_manifest(authority))),
            effect_sha256=_digest_bytes(helper.canonical(helper.RENDER_EFFECT)),
            execution_policy=policy,
            authority=authority,
        ),
    }
    terminal["receipt_sha256"] = _digest_bytes(helper.canonical(terminal))
    terminal_raw = canonical(terminal)
    transport_receipt = _envelope(composition, terminal_raw, 0, private)
    attempt = _attempt_receipt(composition, request, "render-a3-handler")
    attempt_raw = canonical(attempt)
    attempt_digest = _digest_bytes(attempt_raw)
    terminal_digest = _digest_bytes(terminal_raw)
    result = {
        "schema": "tgw-nixos-observer-render-handler-result/v1",
        "generation": "render-a3-handler",
        "composition_sha256": composition.receipt_sha256,
        "attempt_receipt": attempt,
        "attempt_ref": {
            "artifact_ref": "artifact:" + attempt_digest,
            "path": str(Path(value["receipt_roots"]["attempts"]) / (attempt_digest.removeprefix("sha256:") + ".json")),
            "sha256": attempt_digest,
            "size": len(attempt_raw),
        },
        "transport_receipt": transport_receipt,
        "terminal": terminal,
        "terminal_ref": {
            "artifact_ref": "artifact:" + terminal_digest,
            "path": str(Path(value["receipt_roots"]["terminals"]) / (terminal_digest.removeprefix("sha256:") + ".json")),
            "sha256": terminal_digest,
            "size": len(terminal_raw),
        },
    }
    _self_hash(result)
    return request, composition, result


def test_handler_rebinds_to_compiled_helper_transport_provider_source_and_request(tmp_path):
    request, composition, result = _production_success(tmp_path)
    assert validate_handler_success(result, request=request, composition=composition) == result
    for mutation in ("helper", "provider", "request", "transport"):
        changed = copy.deepcopy(result)
        if mutation == "helper":
            changed["transport_receipt"]["helper_sha256"] = "sha256:" + "0" * 64
        elif mutation == "provider":
            changed["terminal"]["provider_sha256"] = "sha256:" + "0" * 64
        elif mutation == "request":
            changed["transport_receipt"]["request_sha256"] = "sha256:" + "0" * 64
        else:
            changed["composition_sha256"] = "sha256:" + "0" * 64
        changed.pop("receipt_sha256")
        _self_hash(changed)
        with pytest.raises(RenderTransportError):
            validate_handler_success(changed, request=request, composition=composition)


def test_typed_handler_emits_outer_bound_receipt_and_classifies_ambiguity_hold_and_failure(tmp_path):
    request, composition, result = _production_success(tmp_path)

    def success(_effect):
        return result

    success.composition = composition
    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        nixos_observer_render_evaluation=success,
    )
    effect = TypedEffect.parse({"kind": EFFECT_KIND, "generation": "render-a3-handler", "parameters": request})
    controller = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "authority:a3"}))
    receipt = controller.execute(request_id="request:a3", effect=effect)
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.evidence == ("nixos-observer-render:" + result["receipt_sha256"],)

    attempt = result["attempt_receipt"]
    attempt_ref = result["attempt_ref"]

    def ambiguous(_effect):
        raise RemoteAttemptAmbiguous("lost", attempt, attempt_ref)

    ambiguous.composition = composition
    registry._providers[effect.kind] = ("nixos-observer-render-evaluation@1", registry._observer_render_provider(ambiguous), None)
    assert controller.execute(request_id="request:a3", effect=effect).outcome is EffectOutcome.AMBIGUOUS

    def attacked(_effect):
        changed = copy.deepcopy(result)
        changed["transport_receipt"]["wrapper_sha256"] = "sha256:" + "0" * 64
        changed.pop("receipt_sha256")
        _self_hash(changed)
        return changed

    attacked.composition = composition
    registry._providers[effect.kind] = ("nixos-observer-render-evaluation@1", registry._observer_render_provider(attacked), None)
    assert controller.execute(request_id="request:a3", effect=effect).outcome is EffectOutcome.AMBIGUOUS

    def held(_effect):
        raise CompositionHold("wrapper prerequisite absent")

    held.composition = composition
    registry._providers[effect.kind] = ("nixos-observer-render-evaluation@1", registry._observer_render_provider(held), None)
    assert controller.execute(request_id="request:a3", effect=effect).outcome is EffectOutcome.HOLD

    def failed(_effect):
        terminal = {"schema": helper.A2_FAILURE_SCHEMA, "outcome": "FAILED"}
        raise RemoteRenderFailure(terminal, {"sha256": "sha256:" + "1" * 64}, attempt_ref)

    failed.composition = composition
    registry._providers[effect.kind] = ("nixos-observer-render-evaluation@1", registry._observer_render_provider(failed), None)
    assert controller.execute(request_id="request:a3", effect=effect).outcome is EffectOutcome.FAILED


def test_remote_bootstrap_and_native_wrapper_are_fixed_no_argument_contracts(tmp_path):
    remote = Path("src/tgw/nix_observer_render_remote.py").read_text()
    native = Path("src/native/tgw_nix_observer_render_transport.c").read_text()
    assert "len(sys.argv) != 1" in remote
    assert "TGW_RENDER_HELPER_FD" in remote
    assert "argc != 1" in native
    assert "unshare(CLONE_NEWNET)" in native
    assert native.count("negative_probe(&cfg, ipfd)") == 2
    assert "drop_identity(&cfg)" in native
    assert native.index("unshare(CLONE_NEWNET)") < native.index("drop_identity(&cfg)")
    assert "EVP_DigestSign" in native
    assert "-c" not in native
    binary = tmp_path / "wrapper"
    subprocess.run(["gcc", "-Wall", "-Wextra", "-Werror", "-o", str(binary), "src/native/tgw_nix_observer_render_transport.c", "-lcrypto"], check=True)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _wait_port(port: int, process: subprocess.Popen, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("ephemeral sshd exited")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("ephemeral sshd did not listen")


def _fixture_wrapper(path: Path, case, composition: RenderComposition, private: Ed25519PrivateKey) -> None:
    fixtures = _fixtures()
    preamble = (
        "_TEST_ONLY_TOOL_AUTHORITY="
        + fixtures._authority_literal()
        + "\n_TEST_ONLY_A2_AUTHORITY="
        + fixtures._a2_authority_literal(case["authority"])
        + "\n_TEST_ONLY_SCRATCH_ROOT="
        + repr(str(case["scratch"]))
        + "\n"
    )
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    probe = {
        "schema": "tgw-render-netns-negative-probe/v1",
        "links": ["lo"],
        "loopback_state": "down",
        "ipv4_route_count": 0,
        "ipv6_route_count": 0,
        "direct_probe": "ENETUNREACH",
    }
    source = f"""#!{sys.executable}
import base64,hashlib,json,subprocess,sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
if len(sys.argv)!=1: raise SystemExit(125)
packet=sys.stdin.buffer.read()
child=subprocess.run([sys.executable,'-I','-c',{(preamble + helper.BOOTSTRAP)!r}],input=packet,capture_output=True,check=False)
probe={probe!r}
value={{'schema':'tgw-nixos-observer-render-wrapper-envelope/v1','request_sha256':{composition.value['request_sha256']!r},'helper_sha256':{HELPER_SHA256!r},'remote_bootstrap_sha256':{composition.components['remote_bootstrap']['sha256']!r},'remote_python_sha256':{composition.wrapper['remote_python_sha256']!r},'remote_ip_sha256':{composition.wrapper['remote_ip_sha256']!r},'wrapper_sha256':{composition.wrapper['sha256']!r},'wrapper_prerequisite_receipt_sha256':{composition.wrapper['prerequisite_receipt']['sha256']!r},'namespace':{{'schema':'tgw-render-network-namespace-evidence/v1','before':'net:[301]','after':'net:[302]','changed':True,'pre':probe,'post':probe}},'child':{{'uid':{composition.wrapper['remote_uid']},'gid':{composition.wrapper['remote_gid']},'returncode':child.returncode,'terminal_bytes':len(child.stdout),'terminal_sha256':'sha256:'+hashlib.sha256(child.stdout).hexdigest(),'terminal_b64':base64.b64encode(child.stdout).decode()}},'attestation':{{'algorithm':'ed25519','public_key_sha256':{composition.wrapper['attestation_public_key']['sha256']!r},'signature':''}}}}
def canon(v): return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
n=value['namespace']; c=value['child']
fields=[value['schema'],value['request_sha256'],value['helper_sha256'],value['remote_bootstrap_sha256'],
 value['remote_python_sha256'],value['remote_ip_sha256'],value['wrapper_sha256'],
 value['wrapper_prerequisite_receipt_sha256'],n['before'],n['after'],'1',
 'sha256:'+hashlib.sha256(canon(n['pre'])).hexdigest(),
 'sha256:'+hashlib.sha256(canon(n['post'])).hexdigest(),str(c['uid']),str(c['gid']),
 str(c['returncode']),str(c['terminal_bytes']),c['terminal_sha256']]
value['attestation']['signature']=base64.b64encode(Ed25519PrivateKey.from_private_bytes({private_raw!r}).sign(b'\\0'.join(item.encode('ascii') for item in fields))).decode()
sys.stdout.buffer.write(canon(value)); raise SystemExit(child.returncode)
"""
    path.write_text(source)
    path.chmod(0o555)


@pytest.mark.skipif(not Path("/usr/sbin/sshd").is_file(), reason="OpenSSH server unavailable")
def test_real_ephemeral_sshd_exercises_exact_auth_host_key_wrapper_and_full_a2_packet(tmp_path):
    case = _case(tmp_path / "case")
    private = Ed25519PrivateKey.generate()
    user = pwd.getpwuid(os.getuid()).pw_name
    client_key = tmp_path / "client"
    host_key = tmp_path / "host"
    for key in (client_key, host_key):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    client_key.chmod(0o600)
    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(client_key.with_suffix(".pub").read_bytes())
    port = _free_port()
    host_public = host_key.with_suffix(".pub").read_text().split()
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_public[0]} {host_public[1]}\n")
    known_hosts.chmod(0o600)
    composition, _ = _composition(
        tmp_path / "composition",
        case,
        private,
        host="127.0.0.1",
        port=port,
        user=user,
        identity_file=client_key,
        known_hosts=known_hosts,
    )
    wrapper = tmp_path / "fixed-wrapper"
    changed = copy.deepcopy(composition.value)
    changed["wrapper"]["path"] = str(wrapper)
    composition = RenderComposition(changed, composition.request, True)
    _fixture_wrapper(wrapper, case, composition, private)
    config = tmp_path / "sshd_config"
    config.write_text(
        "\n".join(
            [
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                f"HostKey {host_key}",
                f"AuthorizedKeysFile {authorized}",
                f"PidFile {tmp_path / 'sshd.pid'}",
                "StrictModes no",
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "UsePAM no",
                f"AllowUsers {user}",
                "LogLevel ERROR",
            ]
        )
        + "\n"
    )
    daemon = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        try:
            _wait_port(port, daemon)
        except RuntimeError:
            detail = daemon.stderr.read().decode(errors="replace") if daemon.stderr else ""
            pytest.skip("ephemeral sshd cannot run in this test namespace: " + detail)
        attempts = MemoryStore()
        transport = SshObserverRenderTransport(composition, attempts, tool_authority=_fixtures().LOCAL_TOOL_AUTHORITY, _use_sudo=False)
        controller = ObserverRenderController(transport, MemoryStore(), composition=composition, authority=case["authority"])
        result = controller(_effect(case, generation="render-a3-sshd"))
        assert result["terminal"]["schema"] == helper.SUCCESS_SCHEMA
        assert result["transport_receipt"]["namespace"]["post"]["direct_probe"] == "ENETUNREACH"
        assert attempts.values[0]["wrapper_sha256"] == composition.wrapper["sha256"]

        wrong_key = tmp_path / "wrong-client"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(wrong_key)], check=True)
        wrong_key.chmod(0o600)
        wrong_value = copy.deepcopy(composition.value)
        wrong_value["ssh"]["identity_file"] = _identity(wrong_key)
        wrong_composition = RenderComposition(wrong_value, composition.request, True)
        wrong_transport = SshObserverRenderTransport(
            wrong_composition,
            MemoryStore(),
            tool_authority=_fixtures().LOCAL_TOOL_AUTHORITY,
            _use_sudo=False,
        )
        wrong_controller = ObserverRenderController(wrong_transport, MemoryStore(), composition=wrong_composition, authority=case["authority"])
        with pytest.raises(RemoteAttemptAmbiguous):
            wrong_controller(_effect(case, generation="render-a3-wrong-auth"))
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=3)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()


def test_cli_reports_hold_without_launch_and_remains_stdin_only(tmp_path):
    case = _case(tmp_path / "case")
    error = io.StringIO()

    def hold():
        raise CompositionHold("post-audit composition missing")

    assert main([], input_stream=io.BytesIO(canonical(_effect(case))), output_stream=io.BytesIO(), error_stream=error, compose=hold) == 3
    assert "HOLD" in error.getvalue()
    assert main(["/tmp/request.json"], input_stream=io.BytesIO(), output_stream=io.BytesIO(), error_stream=io.StringIO(), compose=hold) == 2
