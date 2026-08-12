from __future__ import annotations

import base64
import io
import json
import os
import pwd
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import Mock

import pytest

from tgw import nix_observer_render_helper as helper
from tgw.effect_handlers import AuthorityEffectController, EffectOutcome, TypedEffectHandlerRegistry
from tgw.nixos_observer_render_evaluation import (
    EFFECT_KIND,
    PRODUCTION_NETWORK_NAMESPACE,
    REMOTE_BOOTSTRAP_WRAPPER,
    REMOTE_ISOLATED_BOOTSTRAP,
    REMOTE_ISOLATED_BOOTSTRAP_B64,
    ImmutableTerminalReceiptStore,
    NetworkNamespaceDescriptor,
    ObserverRenderController,
    RemoteRenderFailure,
    RenderTransportError,
    SshObserverRenderTransport,
    SshTransportIdentity,
    TerminalPersistenceError,
    TransportExchange,
    _digest_bytes,
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


def _production_success():
    path = Path(__file__).with_name("test_nix_observer_render_evaluation.py")
    spec = spec_from_file_location("a3_render_contract_fixtures", path)
    assert spec and spec.loader
    fixtures = module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    request = fixtures.request()
    authority = helper.PRODUCTION_A2_AUTHORITY
    request.update(
        provider_sha256=_digest_bytes(Path("src/tgw/nix_observer_render_evaluation.py").read_bytes()),
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
    provider = fixtures.result(request)
    provider["systemd_verify"]["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    provider["receipt_sha256"] = _digest_bytes(helper.canonical({key: value for key, value in provider.items() if key != "receipt_sha256"}))
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
    binding = helper.WireBinding(
        request_bytes=1,
        helper_bytes=1,
        tool_descriptor_bytes=1,
        archive_bytes=1,
        request_sha256=request["request_sha256"],
        helper_sha256="sha256:" + "2" * 64,
        tool_descriptor_sha256="sha256:" + "3" * 64,
        archive_sha256=request["archive_sha256"],
    )
    closure = [{"path": provider["output_root"], "nar_sha256": "sha256:" + "4" * 64}]
    terminal = {
        "schema": helper.SUCCESS_SCHEMA,
        "outcome": "VERIFIED",
        "provider_receipt_sha256": provider["receipt_sha256"],
        "closure_manifest": closure,
        "closure_manifest_sha256": _digest_bytes(helper.canonical(closure)),
        "closure_path_count": 1,
        "cleanup": "removed",
        "effects": helper._a2_effects(True),
        "provider_receipt": provider,
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
    return request, terminal


class MemoryStore:
    def __init__(self, error: BaseException | None = None):
        self.values = []
        self.error = error

    def persist(self, value):
        self.values.append(value)
        if self.error:
            raise self.error
        return {"artifact_ref": "artifact:" + value["receipt_sha256"]}


def _exchange(case, completed):
    return TransportExchange(("ssh",), completed.returncode, completed.stdout, case["binding"], case["tool_descriptor"])


def test_controller_validates_exact_outer_and_inner_success_then_persists(tmp_path):
    case = _case(tmp_path / "case")
    completed = _terminal(case)
    store = MemoryStore()
    controller = ObserverRenderController(lambda _request: _exchange(case, completed), store, authority=case["authority"])

    result = controller(_effect(case))

    assert result["schema"] == helper.SUCCESS_SCHEMA
    assert result["provider_receipt"]["outcome"] == "VERIFIED"
    assert store.values == [result]


def test_controller_validates_exact_a2_failure_then_persists_and_raises(tmp_path):
    case = _case(tmp_path / "case", mode="nix-failure")
    completed = _terminal(case)
    store = MemoryStore()
    controller = ObserverRenderController(lambda _request: _exchange(case, completed), store, authority=case["authority"])

    with pytest.raises(RemoteRenderFailure) as raised:
        controller(_effect(case))

    assert raised.value.terminal["schema"] == helper.A2_FAILURE_SCHEMA
    assert raised.value.terminal["stage"] == "a2-build"
    assert store.values == [raised.value.terminal]


@pytest.mark.parametrize("payload", [b"not-json", b"[]", b'{"schema":"old-render-result/v0"}'])
def test_controller_rejects_malformed_or_legacy_remote_terminal(tmp_path, payload):
    case = _case(tmp_path / "case")
    exchange = TransportExchange(("ssh",), 1, payload, case["binding"], case["tool_descriptor"])
    with pytest.raises(RenderTransportError):
        ObserverRenderController(lambda _request: exchange, MemoryStore(), authority=case["authority"])(_effect(case))


def test_controller_propagates_bounded_transport_timeout_without_fabricating_terminal(tmp_path):
    case = _case(tmp_path / "case")

    def timeout(_request):
        raise RenderTransportError("remote render timed out")

    store = MemoryStore()
    with pytest.raises(RenderTransportError, match="timed out"):
        ObserverRenderController(timeout, store, authority=case["authority"])(_effect(case))
    assert store.values == []


@pytest.mark.parametrize("error", [OSError("disk"), PermissionError("denied"), RenderTransportError("contradictory")])
def test_terminal_store_errors_preserve_validated_terminal_as_ambiguous(tmp_path, error):
    case = _case(tmp_path / "case")
    completed = _terminal(case)
    controller = ObserverRenderController(lambda _request: _exchange(case, completed), MemoryStore(error), authority=case["authority"])
    with pytest.raises(TerminalPersistenceError) as raised:
        controller(_effect(case))
    assert raised.value.terminal["schema"] == helper.SUCCESS_SCHEMA


def test_failure_terminal_store_error_does_not_discard_validated_a2_failure(tmp_path):
    case = _case(tmp_path / "case", mode="nix-failure")
    completed = _terminal(case)
    controller = ObserverRenderController(lambda _request: _exchange(case, completed), MemoryStore(OSError("disk")), authority=case["authority"])
    with pytest.raises(TerminalPersistenceError) as raised:
        controller(_effect(case))
    assert raised.value.terminal["schema"] == helper.A2_FAILURE_SCHEMA
    assert raised.value.terminal["stage"] == "a2-build"


def test_immutable_terminal_store_is_exclusive_and_content_addressed(tmp_path):
    root = tmp_path / "terminals"
    store = ImmutableTerminalReceiptStore(root)
    value = {"schema": "test-terminal/v1", "receipt_sha256": "sha256:" + "a" * 64}
    first = store.persist(value)
    second = store.persist(value)
    assert first == second
    path = Path(first["path"])
    assert path.read_bytes() == json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    assert path.stat().st_mode & 0o777 == 0o400
    store.close()


def _fake_transport(case, tmp_path: Path, *, identity_change=None):
    archive = tmp_path / "source.tar"
    archive.write_bytes(case["wire"][-case["binding"].archive_bytes :])
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("100.107.99.66 ssh-ed25519 AAAA\n")
    known_hosts.chmod(0o600)
    namespace = NetworkNamespaceDescriptor(
        schema="tgw-render-network-isolation/v1",
        kind="python-os-unshare-newnet",
        remote_python="/run/current-system/sw/bin/python3",
        remote_python_sha256="sha256:" + "1" * 64,
        prerequisite_receipt_sha256=case["request"]["host_identity_receipt_sha256"],
        network=False,
    )
    identity = SshTransportIdentity(
        ssh_executable=Path("/usr/bin/ssh"),
        ssh_sha256=_digest_bytes(Path("/usr/bin/ssh").read_bytes()),
        remote_host="100.107.99.66",
        remote_user="codex",
        remote_port=22,
        helper_path=Path(helper.__file__),
        helper_sha256=_digest_bytes(Path(helper.__file__).read_bytes()),
        known_hosts_sha256=_digest_bytes(known_hosts.read_bytes()),
        namespace=namespace,
        require_sudo=True,
    )
    if identity_change:
        identity = SshTransportIdentity(**{**identity.__dict__, **identity_change})
    seen = {}

    def invoke(command, **kwargs):
        seen.update(command=command, kwargs=kwargs)
        return subprocess.CompletedProcess(command, 1, b"{}", b"")

    transport = SshObserverRenderTransport(
        lambda _ref: archive,
        known_hosts=known_hosts,
        identity=identity,
        tool_authority=_fixtures().LOCAL_TOOL_AUTHORITY,
        invoke=invoke,
        _test_identity=True,
    )
    return transport, seen


def test_transport_constructs_one_exact_packet_and_one_shlex_remote_boundary(tmp_path):
    case = _case(tmp_path / "case")
    transport, seen = _fake_transport(case, tmp_path)
    exchange = transport(case["request"])
    packet = seen["kwargs"]["input"]
    assert helper.parse_prefix(packet[: helper.PREFIX.size]) == exchange.binding
    assert packet == case["wire"]
    command = seen["command"]
    assert command.count("--") == 1
    assert "codex@100.107.99.66" in command
    assert "sudo -n --" in command[-1]
    assert "python-os-unshare" not in command[-1]
    assert REMOTE_BOOTSTRAP_WRAPPER.split(";")[0] in command[-1]
    assert REMOTE_ISOLATED_BOOTSTRAP_B64 in command[-1]


def test_production_descriptor_enforces_new_network_namespace_before_helper():
    assert PRODUCTION_NETWORK_NAMESPACE.kind == "python-os-unshare-newnet"
    assert PRODUCTION_NETWORK_NAMESPACE.network is False
    assert "os.unshare(os.CLONE_NEWNET)" in REMOTE_ISOLATED_BOOTSTRAP
    assert "namespace-unchanged" in REMOTE_ISOLATED_BOOTSTRAP
    assert "if fields and fields[-1]!=\"lo\"" in REMOTE_ISOLATED_BOOTSTRAP
    assert REMOTE_ISOLATED_BOOTSTRAP.index("os.unshare") < REMOTE_ISOLATED_BOOTSTRAP.index("tgw-render-packet-bootstrap")


def test_fake_remote_timeout_is_normalized_and_bounded(tmp_path):
    case = _case(tmp_path / "case")
    transport, _ = _fake_transport(case, tmp_path)

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    transport.invoke = timeout
    with pytest.raises(RenderTransportError, match="timed out"):
        transport(case["request"])


@pytest.mark.parametrize(
    "change,match",
    [
        ({"remote_host": "tgw-prod"}, "literal IP"),
        ({"helper_sha256": "sha256:" + "0" * 64}, "artifact identity"),
        (
            {
                "namespace": NetworkNamespaceDescriptor(
                    schema="tgw-render-network-isolation/v1",
                    kind="python-os-unshare-newnet",
                    remote_python="/run/current-system/sw/bin/python3",
                    remote_python_sha256="sha256:" + "1" * 64,
                    prerequisite_receipt_sha256="sha256:" + "0" * 64,
                    network=False,
                )
            },
            "descriptor",
        ),
    ],
)
def test_transport_identity_drift_fails_before_ssh(tmp_path, change, match):
    case = _case(tmp_path / "case")
    transport, seen = _fake_transport(case, tmp_path, identity_change=change)
    with pytest.raises(RenderTransportError, match=match):
        transport(case["request"])
    assert seen == {}


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


@pytest.mark.skipif(not Path("/usr/sbin/sshd").is_file(), reason="OpenSSH server unavailable")
def test_real_ephemeral_sshd_preserves_helper_packet_framing(tmp_path):
    case = _case(tmp_path / "case")
    user = pwd.getpwuid(os.getuid()).pw_name
    client_key = tmp_path / "client"
    host_key = tmp_path / "host"
    for key in (client_key, host_key):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    authorized = tmp_path / "authorized_keys"
    authorized.write_bytes(client_key.with_suffix(".pub").read_bytes())
    port = _free_port()
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
        host_public = host_key.with_suffix(".pub").read_text().split()
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text(f"[127.0.0.1]:{port} {host_public[0]} {host_public[1]}\n")
        fixtures = _fixtures()
        preamble = (
            "_TEST_ONLY_TOOL_AUTHORITY="
            + fixtures._authority_literal()
            + "\ndef injected(source,request): return 'ssh-framing-e2e'\n_TEST_ONLY_EXECUTOR=injected\n"
        )
        program = base64.b64encode((preamble + helper.BOOTSTRAP).encode()).decode("ascii")
        wrapper = "import base64,sys;exec(compile(base64.b64decode(sys.argv[1]),'<e2e>','exec'))"
        remote = serialize_remote_argv([sys.executable, "-I", "-c", wrapper, program])
        command = [
            "/usr/bin/ssh",
            "-F",
            "/dev/null",
            "-oBatchMode=yes",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={known_hosts}",
            f"-oIdentityFile={client_key}",
            "-p",
            str(port),
            "--",
            f"{user}@127.0.0.1",
            remote,
        ]
        archive_fd = os.open(case["scratch"].parent / "source.tar", os.O_RDONLY)
        try:
            header = case["wire"][: -case["binding"].archive_bytes]
            completed = SshObserverRenderTransport._invoke_streaming(
                command,
                header,
                archive_fd,
                timeout=10,
                max_output=case["request"]["max_output_bytes"],
                pass_fds=(),
            )
        finally:
            os.close(archive_fd)
        terminal = json.loads(completed.stdout)
        assert completed.returncode == 0
        assert terminal["schema"] == helper.TEST_MARKER_SCHEMA
        assert terminal["marker"] == "ssh-framing-e2e"
        assert helper.validate_terminal(
            terminal,
            binding=case["binding"],
            request=case["request"],
            tool_descriptor=case["tool_descriptor"],
            allow_test_marker=True,
            _test_tool_authority=fixtures.LOCAL_TOOL_AUTHORITY,
        ) == terminal
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=3)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait()


def test_cli_is_stdin_only_and_accepts_only_exact_typed_effect(tmp_path):
    case = _case(tmp_path / "case")
    completed = _terminal(case)
    terminal = json.loads(completed.stdout)
    output = io.BytesIO()
    error = io.StringIO()

    def compose():
        return (lambda effect: terminal), {"schema": "test-composition/v1"}

    assert main([], input_stream=io.BytesIO(json.dumps(_effect(case)).encode()), output_stream=output, error_stream=error, compose=compose) == 0
    assert json.loads(output.getvalue()) == terminal
    assert main(["/tmp/request.json"], input_stream=io.BytesIO(), output_stream=io.BytesIO(), error_stream=error, compose=compose) == 2


def test_handler_success_validator_rechecks_exact_outer_and_inner_receipts():
    request, terminal = _production_success()
    assert validate_handler_success(terminal, request=request) == terminal
    for field, value in (
        ("provider_receipt_sha256", "sha256:" + "0" * 64),
        ("execution_policy_sha256", "sha256:" + "0" * 64),
        ("schema", "old-render-success/v0"),
    ):
        changed = json.loads(json.dumps(terminal))
        changed[field] = value
        changed["receipt_sha256"] = _digest_bytes(helper.canonical({key: item for key, item in changed.items() if key != "receipt_sha256"}))
        with pytest.raises(RenderTransportError):
            validate_handler_success(changed, request=request)


def test_effect_kind_and_handler_are_exact_and_emit_only_validated_evidence():
    request, terminal = _production_success()
    seen = {}

    def provider(effect):
        seen.update(effect)
        return terminal

    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        nixos_observer_render_evaluation=provider,
    )
    effect_value = {"kind": EFFECT_KIND, "generation": "render-a3-1", "parameters": request}
    effect = TypedEffect.parse(effect_value)
    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "authority:a3"})).execute(request_id="request:a3", effect=effect)
    assert receipt.outcome is EffectOutcome.SUCCEEDED
    assert receipt.handler_id == "nixos-observer-render-evaluation@1"
    assert receipt.evidence == ("nixos-observer-render:" + terminal["receipt_sha256"],)
    assert seen["kind"] == EFFECT_KIND and seen["generation"] == "render-a3-1"
    assert "argv" not in seen["parameters"] and "path" not in seen["parameters"]

    broadened = json.loads(json.dumps(effect_value))
    broadened["parameters"]["argv"] = ["nixos-rebuild", "switch"]
    with pytest.raises(ValueError):
        registry.prepare(TypedEffect.parse(broadened))


def test_handler_preserves_remote_cleanup_ambiguity_as_ambiguous_outcome():
    request, _ = _production_success()
    failure = {"schema": helper.A2_FAILURE_SCHEMA, "outcome": "AMBIGUOUS"}

    def provider(_effect):
        raise RemoteRenderFailure(failure, {"artifact_ref": "artifact:sha256:" + "1" * 64})

    registry = TypedEffectHandlerRegistry(
        release_install=Mock(),
        release_rollback=Mock(),
        flake_push=Mock(),
        flake_switch_record=Mock(),
        dependency_resubmit=Mock(),
        nixos_observer_render_evaluation=provider,
    )
    effect = TypedEffect.parse({"kind": EFFECT_KIND, "generation": "render-a3-ambiguous", "parameters": request})
    receipt = AuthorityEffectController(registry, Mock(return_value={"receipt_id": "authority:a3"})).execute(request_id="request:a3", effect=effect)
    assert receipt.outcome is EffectOutcome.AMBIGUOUS
    assert not receipt.evidence
