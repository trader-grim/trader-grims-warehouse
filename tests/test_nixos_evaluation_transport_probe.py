import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.nixos_evaluation_transport_probe import (
    REMOTE_PROGRAM,
    TransportProbeError,
    run_transport_probe,
    validate_transport_probe,
)

REQUEST = "sha256:" + "a" * 64
PYTHON = "sha256:" + "b" * 64


def receipt():
    value = {
        "schema": "tgw-nixos-reviewed-evaluation-transport-probe/v1",
        "request_hash": REQUEST,
        "reached": ["ssh", "sudo", "python", "stdin-frame"],
        "python_version": "3.13.5",
        "remote_python_sha256": PYTHON,
        "forbidden_effects": {key: False for key in ("scratch", "archive", "nix", "build", "activation", "profile_write", "home_db_write", "live_flake_write", "deployment")},
    }
    value["receipt_sha256"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def test_validator_accepts_only_exact_self_hashed_zero_effect_receipt():
    assert validate_transport_probe(receipt(), request_hash=REQUEST, remote_python_sha256=PYTHON) == receipt()
    for mutation in ({"reached": ["ssh"]}, {"raw_stderr": "secret"}, {"remote_python_sha256": "sha256:" + "c" * 64}):
        forged = {**receipt(), **mutation}
        with pytest.raises(TransportProbeError):
            validate_transport_probe(forged, request_hash=REQUEST, remote_python_sha256=PYTHON)


def test_fixed_probe_reuses_exact_transport_and_frames_only_request_hash(tmp_path, monkeypatch):
    hosts = tmp_path / "known_hosts"
    hosts.write_text("100.107.99.66 ssh-ed25519 AAAA")
    hosts.chmod(0o400)
    ssh = Path("/usr/bin/ssh")
    calls = []

    def invoke(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt()).encode(), b"")

    monkeypatch.setattr("tgw.nixos_evaluation_transport_probe.subprocess.run", invoke)
    result = run_transport_probe(
        known_hosts=hosts,
        request_hash=REQUEST,
        ssh_sha256="sha256:" + hashlib.sha256(ssh.read_bytes()).hexdigest(),
        known_hosts_sha256="sha256:" + hashlib.sha256(hosts.read_bytes()).hexdigest(),
        remote_python_sha256=PYTHON,
    )
    assert result == receipt()
    argv, kwargs = calls[0]
    assert "codex@100.107.99.66" in argv and ["sudo", "-n", "--", "/run/current-system/sw/bin/python3", "-I", "-c", REMOTE_PROGRAM] == argv[-7:]
    assert kwargs["input"] == REQUEST.encode() and len(kwargs["input"]) == 71
    assert not any(token in REMOTE_PROGRAM for token in ("/var/tmp", "nix-store", "nix build", "tarfile", "subprocess", 'open("/'))


def test_nonzero_reports_only_bounded_code_and_hashes(tmp_path, monkeypatch):
    hosts = tmp_path / "known_hosts"
    hosts.write_text("100.107.99.66 ssh-ed25519 AAAA")
    hosts.chmod(0o400)
    monkeypatch.setattr(
        "tgw.nixos_evaluation_transport_probe.subprocess.run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, b"private out", b"private error"),
    )
    with pytest.raises(TransportProbeError) as captured:
        run_transport_probe(
            known_hosts=hosts,
            request_hash=REQUEST,
            ssh_sha256="sha256:" + hashlib.sha256(Path("/usr/bin/ssh").read_bytes()).hexdigest(),
            known_hosts_sha256="sha256:" + hashlib.sha256(hosts.read_bytes()).hexdigest(),
            remote_python_sha256=PYTHON,
        )
    assert "private" not in str(captured.value) and "return_code=1" in str(captured.value)
