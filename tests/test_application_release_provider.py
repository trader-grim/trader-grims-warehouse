import copy
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import tgw.application_release_provider as provider_module

from tgw.application_release_provider import (
    ApplicationReleaseProviderError,
    REMOTE_PYTHON,
    REMOTE_SUDO,
    SshApplicationReleaseProvider,
    _hash,
    _hash_bytes,
    _memory_bootstrap,
    _validate_response_shape,
    validate_provider_descriptor,
)
from tgw.platform_bootstrap import BootstrapStateAmbiguous


def _descriptor(parameters=None):
    parameters = parameters or {"candidate_commit": "a" * 40}
    h = lambda digit: "sha256:" + digit * 64
    value = {
        "schema": "tgw-w09-application-release-provider/v2",
        "target": {
            "host": "tgw-prod", "address": "100.107.99.66", "port": 22,
            "user": "db",
        },
        "transport": {
            "ssh_path": "/run/current-system/sw/bin/ssh", "ssh_sha256": h("1"),
            "ssh_keygen_path": "/run/current-system/sw/bin/ssh-keygen", "ssh_keygen_sha256": h("9"),
            "known_hosts_path": "/etc/tgw/tgw-prod.known_hosts", "known_hosts_sha256": h("2"),
            "identity_path": "/etc/tgw/w09.key", "identity_sha256": h("3"),
            "transaction_source_path": "/nix/store/source/application_release_remote.py",
            "transaction_source_sha256": h("4"),
            "installer_source_path": "/nix/store/source/release_installer.py",
            "installer_source_sha256": h("a"),
        },
        "candidate": {
            "archive_path": "/opt/TGW/releases/candidate.tar", "archive_sha256": h("5"),
            "commit": "a" * 40, "tree": "b" * 40,
            "effect_parameters_sha256": _hash(parameters),
        },
        "runtime_config": {"path": "/etc/tgw/runtime.json", "content_sha256": h("6")},
        "remote_boundary": {
            "python_path": "/nix/store/00000000000000000000000000000000-python3-3.13.5/bin/python3.13", "python_sha256": h("b"),
            "sudo_path": "/run/wrappers/wrappers.ABC123/sudo", "sudo_sha256": h("c"),
            "bootstrap_sha256": h("d"), "config_path": "/etc/tgw/w09-memory-config.json",
            "config_sha256": h("7"), "nix_system_path": "/nix/store/system-tgw-prod",
            "credential_scope": "pre-existing-db-operator-bootstrap",
            "remote_command_sha256": h("d"),
            "python_stat": {"uid": 0, "gid": 0, "mode": 0o555, "size": 15600},
            "sudo_stat": {"uid": 0, "gid": 0, "mode": 0o4511, "size": 70712},
            "authorized_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        },
        "bounds": {
            "timeout_seconds": 300, "max_output_bytes": 65536,
            "max_diagnostic_bytes": 65536, "max_packet_bytes": 1048576,
        },
        "prerequisite_receipt": {
            "ref": "w09-prerequisite:1", "path": "/etc/tgw/w09-prerequisite.json",
            "sha256": h("8"),
        },
    }
    boundary = value["remote_boundary"]
    bootstrap = _memory_bootstrap(
        installer_sha256=value["transport"]["installer_source_sha256"],
        transaction_sha256=value["transport"]["transaction_source_sha256"],
        helper_config_sha256=boundary["config_sha256"],
        python_sha256=boundary["python_sha256"], sudo_sha256=boundary["sudo_sha256"],
        python_path=boundary["python_path"], sudo_path=boundary["sudo_path"],
        python_stat=boundary["python_stat"], sudo_stat=boundary["sudo_stat"],
        nix_system_path=boundary["nix_system_path"],
    )
    boundary["bootstrap_sha256"] = _hash_bytes(bootstrap.encode())
    boundary["remote_command_sha256"] = _hash_bytes(shlex.join([
        REMOTE_SUDO, "-n", "--", REMOTE_PYTHON, "-I", "-S", "-c", bootstrap,
    ]).encode())
    value["descriptor_hash"] = _hash(value)
    return value


def test_descriptor_requires_fixed_tailnet_address_and_full_parameter_binding():
    parameters = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "5" * 64,
        "runtime_config": {"content_sha256": "sha256:" + "6" * 64},
        "migrations": ["exact"],
        "nix_system_path": "/nix/store/system-tgw-prod",
        "predecessor_observation_hash": "sha256:" + "e" * 64,
        "provider_observation_ref": "w09-prerequisite:1",
        "provider_observation_hash": "sha256:" + "8" * 64,
    }
    descriptor = validate_provider_descriptor(_descriptor(parameters))
    provider = SimpleNamespace(
        descriptor=descriptor,
        _prerequisite={
            "nix_system_path": parameters.get("nix_system_path"),
            "predecessor_observation_hash": parameters.get("predecessor_observation_hash"),
        },
    )
    SshApplicationReleaseProvider._check_parameters(provider, parameters)
    with pytest.raises(ApplicationReleaseProviderError, match="mounted provider"):
        SshApplicationReleaseProvider._check_parameters(
            provider, {**parameters, "migrations": ["neighbor"]},
        )

    bad = copy.deepcopy(_descriptor(parameters))
    bad["target"]["address"] = "tgw-prod"
    bad["descriptor_hash"] = _hash({key: value for key, value in bad.items() if key != "descriptor_hash"})
    with pytest.raises(ApplicationReleaseProviderError, match="exact tgw-prod"):
        validate_provider_descriptor(bad)


def test_remote_response_schema_rejects_untyped_or_empty_evidence():
    response = {
        "schema": "tgw-w09-application-release-response/v1", "operation_id": "w09-a",
        "helper_sha256": "sha256:" + "1" * 64,
        "helper_config_sha256": "sha256:" + "2" * 64,
        "nix_system_path": "/nix/store/system", "status": "SUCCEEDED",
        "evidence": ["health:exact"], "receipt_sha256": "sha256:" + "3" * 64,
    }
    _validate_response_shape(response)
    with pytest.raises(ApplicationReleaseProviderError, match="evidence schema"):
        _validate_response_shape({**response, "evidence": []})
    with pytest.raises(ApplicationReleaseProviderError, match="status"):
        _validate_response_shape({**response, "status": "RETRY"})


def test_memory_bootstrap_is_digest_bound_and_registers_modules_before_exec():
    h = lambda digit: "sha256:" + digit * 64
    bootstrap = _memory_bootstrap(
        installer_sha256=h("1"), transaction_sha256=h("2"),
        helper_config_sha256=h("3"), python_sha256=h("4"), sudo_sha256=h("5"),
        python_path="/nix/store/00000000000000000000000000000000-python/bin/python3",
        sudo_path="/run/wrappers/wrappers.ABC123/sudo",
        python_stat={"uid": 0, "gid": 0, "mode": 0o555, "size": 1},
        sudo_stat={"uid": 0, "gid": 0, "mode": 0o4511, "size": 1},
        nix_system_path="/nix/store/system-tgw-prod",
    )
    assert 'sys.modules["tgw"]=package' in bootstrap
    assert 'sys.modules[installer.__name__]=installer' in bootstrap
    assert 'sys.modules[transaction.__name__]=transaction' in bootstrap
    assert _hash_bytes(bootstrap.encode()).startswith("sha256:")


def test_memory_bootstrap_executes_exact_framed_modules_without_persistence(monkeypatch):
    python = os.path.realpath(sys.executable)
    python_raw = Path(python).read_bytes()
    python_stat = Path(python).stat()
    monkeypatch.setattr(provider_module, "REMOTE_SUDO", python)
    monkeypatch.setattr(provider_module, "REMOTE_PYTHON", python)
    installer = b"SCHEMA='fixture'\n"
    transaction = (
        b"import sys\n"
        b"def memory_main(request, config, archive, runtime, helper_sha256):\n"
        b"    sys.stdout.buffer.write(b'framed-ok')\n"
        b"    return 0\n"
    )
    config = b'{"fixture":true}'
    bodies = (
        ("release_installer", installer),
        ("application_transaction", transaction),
        ("helper_config", config),
        ("candidate_archive", b"archive"),
        ("runtime_config", b"runtime"),
    )
    bootstrap = _memory_bootstrap(
        installer_sha256=_hash_bytes(installer),
        transaction_sha256=_hash_bytes(transaction),
        helper_config_sha256=_hash_bytes(config),
        python_sha256=_hash_bytes(python_raw), sudo_sha256=_hash_bytes(python_raw),
        python_path=python, sudo_path=python,
        python_stat={
            "uid": python_stat.st_uid, "gid": python_stat.st_gid,
            "mode": python_stat.st_mode & 0o7777, "size": python_stat.st_size,
        },
        sudo_stat={
            "uid": python_stat.st_uid, "gid": python_stat.st_gid,
            "mode": python_stat.st_mode & 0o7777, "size": python_stat.st_size,
        },
        nix_system_path=str(Path("/run/current-system").resolve()),
    )
    unsigned = {
        "schema": provider_module.FRAME_SCHEMA, "request": {"fixture": True},
        "blobs": [
            {"name": name, "size": len(raw), "sha256": _hash_bytes(raw)}
            for name, raw in bodies
        ],
    }
    header = json.dumps(
        {**unsigned, "frame_hash": _hash(unsigned)},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    packet = len(header).to_bytes(8, "big") + header + b"".join(raw for _, raw in bodies)
    result = subprocess.run(
        [python, "-I", "-c", bootstrap], input=packet, capture_output=True,
        timeout=10, check=False,
    )
    assert result.returncode == 0
    assert result.stdout == b"framed-ok"


def test_second_seal_failure_closes_first_memfd(monkeypatch):
    parameters = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "5" * 64,
        "runtime_config": {"content_sha256": "sha256:" + "6" * 64},
        "nix_system_path": "/nix/store/system-tgw-prod",
        "predecessor_observation_hash": "sha256:" + "e" * 64,
        "provider_observation_ref": "w09-prerequisite:1",
        "provider_observation_hash": "sha256:" + "8" * 64,
    }
    provider = object.__new__(SshApplicationReleaseProvider)
    object.__setattr__(provider, "_descriptor", validate_provider_descriptor(_descriptor(parameters)))
    null_fds = tuple(os.open("/dev/null", os.O_RDONLY) for _ in range(3))
    object.__setattr__(provider, "_fds", null_fds)
    object.__setattr__(provider, "_raw", (
        b"ssh", b"hosts", b"identity", b"transaction", b"installer",
        b"archive", b"runtime", b"helper-config",
    ))
    object.__setattr__(provider, "_prerequisite", {
        "nix_system_path": parameters.get("nix_system_path"),
        "predecessor_observation_hash": parameters.get("predecessor_observation_hash"),
        "observed_at": "2026-08-16T00:00:00Z", "expires_at": "2030-01-01T00:00:00Z",
    })
    object.__setattr__(provider, "_identities", ())
    object.__setattr__(provider, "_frozen", True)
    first_fd, write_fd = os.pipe()
    os.close(write_fd)
    calls = 0

    def seal(_name, _raw):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first_fd
        raise OSError("second seal failed")

    monkeypatch.setattr(provider_module, "_sealed", seal)
    monkeypatch.setattr(SshApplicationReleaseProvider, "preflight", lambda _self, _parameters: None)
    try:
        with pytest.raises(OSError, match="second seal"):
            provider._dispatch("rollback", parameters)
        with pytest.raises(OSError):
            os.fstat(first_fd)
    finally:
        provider.close()


def test_preflight_rechecks_observation_expiry_before_authority_consumption(monkeypatch):
    provider = object.__new__(SshApplicationReleaseProvider)
    object.__setattr__(provider, "_prerequisite", {
        "observed_at": "2020-01-01T00:00:00Z", "expires_at": "2020-01-02T00:00:00Z",
    })
    object.__setattr__(provider, "_identities", ())
    object.__setattr__(provider, "_fds", ())
    object.__setattr__(provider, "_raw", ())
    object.__setattr__(provider, "_frozen", True)
    monkeypatch.setattr(SshApplicationReleaseProvider, "_check_parameters", lambda _self, _parameters: None)
    with pytest.raises(ApplicationReleaseProviderError, match="expired before authority consumption"):
        provider.preflight({})


def test_popen_failure_is_typed_prelaunch_ambiguity_and_closes_seals(monkeypatch):
    parameters = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "5" * 64,
        "runtime_config": {"content_sha256": "sha256:" + "6" * 64},
        "nix_system_path": "/nix/store/system-tgw-prod",
        "predecessor_observation_hash": "sha256:" + "e" * 64,
        "provider_observation_ref": "w09-prerequisite:1",
        "provider_observation_hash": "sha256:" + "8" * 64,
    }
    provider = object.__new__(SshApplicationReleaseProvider)
    object.__setattr__(provider, "_descriptor", validate_provider_descriptor(_descriptor(parameters)))
    fds = tuple(os.open("/dev/null", os.O_RDONLY) for _ in range(3))
    object.__setattr__(provider, "_fds", fds)
    object.__setattr__(provider, "_raw", (
        b"ssh", b"hosts", b"identity", b"transaction", b"installer",
        b"archive", b"runtime", b"helper-config",
    ))
    object.__setattr__(provider, "_prerequisite", {
        "nix_system_path": parameters["nix_system_path"],
        "predecessor_observation_hash": parameters["predecessor_observation_hash"],
        "observed_at": "2026-08-16T00:00:00Z", "expires_at": "2030-01-01T00:00:00Z",
    })
    object.__setattr__(provider, "_identities", ())
    object.__setattr__(provider, "_frozen", True)
    monkeypatch.setattr(
        provider_module.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("exec failed")),
    )
    monkeypatch.setattr(SshApplicationReleaseProvider, "preflight", lambda _self, _parameters: None)
    try:
        with pytest.raises(BootstrapStateAmbiguous, match="before remote launch") as caught:
            provider._dispatch("install", parameters)
        assert caught.value.rollback_required is False
    finally:
        provider.close()
