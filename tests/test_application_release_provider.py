import copy
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import tgw.application_release_provider as provider_module

from tgw.application_release_provider import (
    ApplicationReleaseProviderError,
    REMOTE_COMMAND,
    REMOTE_HELPER,
    SshApplicationReleaseProvider,
    _hash,
    _validate_response_shape,
    validate_provider_descriptor,
)


def _descriptor(parameters=None):
    parameters = parameters or {"candidate_commit": "a" * 40}
    h = lambda digit: "sha256:" + digit * 64
    value = {
        "schema": "tgw-w09-application-release-provider/v1",
        "target": {
            "host": "tgw-prod", "address": "100.107.99.66", "port": 22,
            "user": "tgw-release-bootstrap",
        },
        "transport": {
            "ssh_path": "/run/current-system/sw/bin/ssh", "ssh_sha256": h("1"),
            "ssh_keygen_path": "/run/current-system/sw/bin/ssh-keygen", "ssh_keygen_sha256": h("9"),
            "known_hosts_path": "/etc/tgw/tgw-prod.known_hosts", "known_hosts_sha256": h("2"),
            "identity_path": "/etc/tgw/w09.key", "identity_sha256": h("3"),
            "helper_source_path": "/nix/store/source/application_release_remote.py",
            "helper_source_sha256": h("4"),
        },
        "candidate": {
            "archive_path": "/opt/TGW/releases/candidate.tar", "archive_sha256": h("5"),
            "commit": "a" * 40, "tree": "b" * 40,
            "effect_parameters_sha256": _hash(parameters),
        },
        "runtime_config": {"path": "/etc/tgw/runtime.json", "content_sha256": h("6")},
        "remote_boundary": {
            "forced_command": REMOTE_COMMAND, "sudo_command": REMOTE_COMMAND,
            "helper_path": REMOTE_HELPER, "helper_sha256": h("4"), "config_sha256": h("7"),
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
    value["descriptor_hash"] = _hash(value)
    return value


def test_descriptor_requires_fixed_tailnet_address_and_full_parameter_binding():
    parameters = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "5" * 64,
        "runtime_config": {"content_sha256": "sha256:" + "6" * 64},
        "migrations": ["exact"],
    }
    descriptor = validate_provider_descriptor(_descriptor(parameters))
    provider = SimpleNamespace(descriptor=descriptor)
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


def test_nix_boundary_uses_same_no_argv_forced_command_and_exact_sudo_allowlist():
    module = Path("nix/application-release-bootstrap.nix").read_text(encoding="utf-8")
    assert 'sudoCommand = "/run/wrappers/bin/sudo -n -- ${helper}";' in module
    assert 'restrict,command="${sudoCommand}" ssh-ed25519 ' in module
    assert '${cfg.remoteUser} ALL=(root) NOPASSWD: ${helper} ""' in module
    assert 'openssh.authorizedKeys.keyFiles = [ ];' in module


def test_second_seal_failure_closes_first_memfd(monkeypatch):
    parameters = {
        "candidate_commit": "a" * 40, "candidate_tree": "b" * 40,
        "archive_sha256": "sha256:" + "5" * 64,
        "runtime_config": {"content_sha256": "sha256:" + "6" * 64},
    }
    provider = object.__new__(SshApplicationReleaseProvider)
    object.__setattr__(provider, "_descriptor", validate_provider_descriptor(_descriptor(parameters)))
    null_fds = tuple(os.open("/dev/null", os.O_RDONLY) for _ in range(3))
    object.__setattr__(provider, "_fds", null_fds)
    object.__setattr__(provider, "_raw", (b"ssh", b"hosts", b"identity", b"helper", b"archive", b"config"))
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
    try:
        with pytest.raises(OSError, match="second seal"):
            provider._dispatch("rollback", parameters)
        with pytest.raises(OSError):
            os.fstat(first_fd)
    finally:
        provider.close()
