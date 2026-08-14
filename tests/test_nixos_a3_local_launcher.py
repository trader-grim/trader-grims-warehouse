from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _launcher() -> ModuleType:
    path = Path("agent-services/providers/nixos_a3_local_launcher.py")
    spec = importlib.util.spec_from_file_location("nixos_a3_local_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installer() -> ModuleType:
    path = Path("agent-services/providers/install_nixos_a3_local_launcher.py")
    spec = importlib.util.spec_from_file_location("install_nixos_a3_local_launcher", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config() -> dict[str, Any]:
    return {
        "codex_uid": 1004,
        "codex_gid": 1004,
        "max_timeout_seconds": 60,
        "max_output_bytes": 1_048_576,
        "max_processes": 32,
        "max_memory_bytes": 2_147_483_648,
    }


def _packet(module: ModuleType, launcher_raw: bytes, config_raw: bytes, prerequisite_raw: bytes) -> bytes:
    value = {
        "schema": module.PACKET_SCHEMA,
        "composition_sha256": "sha256:" + "1" * 64,
        "request_sha256": "sha256:" + "2" * 64,
        "launch_nonce": "3" * 64,
        "attempt_id": "attempt:" + "4" * 64,
        "issued_at": "2026-08-14T12:00:00.000000Z",
        "expires_at": "2026-08-14T12:01:00.000000Z",
        "logical_argv": ["/bin/true"],
        "cwd": "/",
        "env": {"LANG": "C.UTF-8"},
        "timeout_seconds": 60,
        "max_output_bytes": 1_048_576,
        "pass_fds": [],
        "launcher_sha256": module.digest_raw(launcher_raw),
        "config_sha256": module.digest_raw(config_raw),
        "prerequisite_sha256": module.digest_raw(prerequisite_raw),
    }
    value["packet_sha256"] = module.digest(value)
    return module.canonical(value)


def test_rfc8032_signing_matches_the_published_empty_message_vector() -> None:
    module = _launcher()
    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    expected_public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    expected_signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert module.public_key(seed) == expected_public
    assert module.sign(seed, b"") == expected_signature
    Ed25519PublicKey.from_public_bytes(expected_public).verify(expected_signature, b"")


def test_installer_and_launcher_derive_the_same_public_identity() -> None:
    launcher = _launcher()
    installer = _installer()
    seed = bytes(range(32))
    assert installer.public_key(seed) == launcher.public_key(seed)
    private = serialization.load_pem_private_key(installer._pkcs8_pem(seed), password=None)
    assert private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw) == launcher.public_key(seed)


def test_packet_parser_accepts_only_the_exact_bound_canonical_packet() -> None:
    module = _launcher()
    launcher_raw = b"launcher"
    config_raw = b"config"
    prerequisite_raw = b"prerequisite"
    raw = _packet(module, launcher_raw, config_raw, prerequisite_raw)
    packet = module._parse_packet(raw, _config(), launcher_raw, config_raw, prerequisite_raw)
    assert packet["logical_argv"] == ["/bin/true"]
    assert packet["packet_sha256"] == module.digest({key: value for key, value in packet.items() if key != "packet_sha256"})

    mutated = dict(module.json.loads(raw))
    mutated["max_output_bytes"] += 1
    with pytest.raises(module.LauncherError, match="self-hash"):
        module._parse_packet(module.canonical(mutated), _config(), launcher_raw, config_raw, prerequisite_raw)


@pytest.mark.parametrize("field,value", [("timeout_seconds", True), ("max_output_bytes", 1_048_577), ("pass_fds", [2])])
def test_packet_parser_rejects_broadened_bounds(field: str, value: Any) -> None:
    module = _launcher()
    raw = _packet(module, b"launcher", b"config", b"prerequisite")
    packet = dict(module.json.loads(raw))
    packet[field] = value
    packet["packet_sha256"] = module.digest({key: item for key, item in packet.items() if key != "packet_sha256"})
    with pytest.raises(module.LauncherError):
        module._parse_packet(module.canonical(packet), _config(), b"launcher", b"config", b"prerequisite")


def test_network_evidence_is_typed_and_does_not_claim_the_controller_namespace_isolated() -> None:
    module = _launcher()
    evidence, lo_only, routes_only_lo = module._network_evidence()
    assert evidence["schema"] == module.RAW_EVIDENCE_SCHEMA
    assert "lo" in evidence["interfaces"]
    assert not (lo_only and routes_only_lo)


def test_flake_declares_each_dynamic_per_system_attrset_once() -> None:
    flake = Path("flake.nix").read_text()
    assert flake.count("packages.${system} = {") == 1
    assert flake.count("checks.${system} = {") == 1
    assert "packages.${system}.a3-platform-bootstrap" not in flake
