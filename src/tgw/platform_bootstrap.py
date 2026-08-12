"""Closed A3 W09 platform-bootstrap manifest and non-shell provider.

The provider accepts one canonical manifest, exact external private-key
bindings, and typed host operations.  It never accepts an argv, executable,
host, path, or secret from an effect request.
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

MANIFEST_SCHEMA = "tgw-a3-platform-bootstrap-manifest/v1"
EFFECT_SCHEMA = "tgw-a3-platform-bootstrap-effect/v1"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
SOLUTION_HASH = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
TARGET_HOST = "tgw-prod"
FLAKE_REPOSITORY = "tgw-flake"
RETIREMENT_CONDITION = "W10:canonical-gate-operational"
ATTESTATION_KEY_REF = "credential:tgw-platform-bootstrap:attestation-signing"
SSH_KEY_REF = "credential:tgw-platform-bootstrap:ssh-identity"

_SHA1 = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/-]{0,191}")
_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,191}")
_RECEIPT = re.compile(r"[a-z][a-z0-9-]{0,47}:sha256:[0-9a-f]{64}")
_SYSTEM = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-nixos-system-tgw-prod-[A-Za-z0-9._+-]+")
_ARTIFACTS = frozenset(
    {
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
    }
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: Any) -> str:
    return "sha256:" + sha256(canonical(value)).hexdigest()


def _self_hashed(value: Mapping[str, Any]) -> bool:
    unsigned = dict(value)
    claimed = unsigned.pop("manifest_sha256", None)
    return claimed == digest(unsigned)


def _artifact(value: Any, *, name: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"artifact_ref", "sha256"}:
        raise ValueError(f"bootstrap {name} artifact binding is not exact")
    reference, checksum = value["artifact_ref"], value["sha256"]
    if not isinstance(checksum, str) or not _DIGEST.fullmatch(checksum) or reference != "artifact:" + checksum:
        raise ValueError(f"bootstrap {name} artifact digest is invalid")
    return {"artifact_ref": reference, "sha256": checksum}


def validate_platform_bootstrap_manifest(value: Any) -> dict[str, Any]:
    """Validate the sole A3 install schema; older review-egress fields are refused."""
    fields = {
        "schema",
        "plan_commit",
        "solution_hash",
        "target_host",
        "flake_repository_id",
        "flake_commit",
        "flake_tree",
        "expected_current_system",
        "successor_system",
        "prior_system",
        "artifacts",
        "credential_bindings",
        "operation_id",
        "review_receipt",
        "controller_receipt",
        "health_receipt",
        "probe_receipt",
        "retirement_condition",
        "manifest_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("platform-bootstrap manifest is not the exact A3 schema")
    if not _self_hashed(value):
        raise ValueError("platform-bootstrap manifest digest is invalid")
    if (
        value["plan_commit"] != PLAN_COMMIT
        or value["solution_hash"] != SOLUTION_HASH
        or value["target_host"] != TARGET_HOST
        or value["flake_repository_id"] != FLAKE_REPOSITORY
        or value["retirement_condition"] != RETIREMENT_CONDITION
    ):
        raise ValueError("platform-bootstrap Plan, solution, target, or retirement binding is invalid")
    if not _SHA1.fullmatch(str(value["flake_commit"])) or not _SHA1.fullmatch(str(value["flake_tree"])):
        raise ValueError("platform-bootstrap reviewed flake identity is invalid")
    systems = (value["expected_current_system"], value["successor_system"], value["prior_system"])
    if any(not isinstance(item, str) or not _SYSTEM.fullmatch(item) for item in systems):
        raise ValueError("platform-bootstrap closure identity is invalid")
    if value["prior_system"] != value["expected_current_system"] or value["successor_system"] == value["prior_system"]:
        raise ValueError("platform-bootstrap prior/successor CAS or rollback binding is invalid")
    if not isinstance(value["artifacts"], Mapping) or set(value["artifacts"]) != _ARTIFACTS:
        raise ValueError("platform-bootstrap artifact set is not exact")
    artifacts = {name: _artifact(value["artifacts"][name], name=name) for name in sorted(_ARTIFACTS)}
    credentials = value["credential_bindings"]
    if (
        not isinstance(credentials, Mapping)
        or set(credentials) != {"attestation_signing", "ssh_identity"}
        or not all(isinstance(binding, Mapping) and set(binding) == {"ref", "sha256"} for binding in credentials.values())
        or credentials["attestation_signing"]["ref"] != ATTESTATION_KEY_REF
        or credentials["ssh_identity"]["ref"] != SSH_KEY_REF
        or any(not _DIGEST.fullmatch(str(binding["sha256"])) for binding in credentials.values())
    ):
        raise ValueError("platform-bootstrap external credential binding is invalid")
    if not isinstance(value["operation_id"], str) or not _OPERATION.fullmatch(value["operation_id"]):
        raise ValueError("platform-bootstrap operation_id is invalid")
    for name in ("review_receipt", "controller_receipt", "health_receipt", "probe_receipt"):
        if not isinstance(value[name], str) or not _RECEIPT.fullmatch(value[name]):
            raise ValueError(f"platform-bootstrap {name} is invalid")
    return {
        **dict(value),
        "artifacts": artifacts,
        "credential_bindings": {name: dict(binding) for name, binding in credentials.items()},
    }


def platform_bootstrap_effect_parameters(manifest: Mapping[str, Any]) -> dict[str, str]:
    manifest = validate_platform_bootstrap_manifest(manifest)
    raw = canonical(manifest).decode("utf-8")
    return {"schema": EFFECT_SCHEMA, "manifest_json": raw, "manifest_sha256": digest(manifest)}


def validate_platform_bootstrap_effect(parameters: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(parameters, Mapping) or set(parameters) != {"schema", "manifest_json", "manifest_sha256"}:
        raise ValueError("platform-bootstrap effect parameters are not exact")
    if parameters["schema"] != EFFECT_SCHEMA or not isinstance(parameters["manifest_json"], str):
        raise ValueError("platform-bootstrap effect schema is invalid")
    try:
        decoded = json.loads(parameters["manifest_json"])
    except json.JSONDecodeError as exc:
        raise ValueError("platform-bootstrap manifest JSON is invalid") from exc
    manifest = validate_platform_bootstrap_manifest(decoded)
    if canonical(manifest).decode("utf-8") != parameters["manifest_json"] or parameters["manifest_sha256"] != digest(manifest):
        raise ValueError("platform-bootstrap effect is not canonical or hash-bound")
    return manifest


@dataclass(frozen=True)
class ExternalPrivateKey:
    ref: str
    path: Path
    sha256: str

    def validate(self, expected: Mapping[str, str], *, trusted_uid: int = 0) -> None:
        path = Path(self.path)
        if self.ref != expected["ref"] or self.sha256 != expected["sha256"] or not _DIGEST.fullmatch(self.sha256):
            raise ValueError("external private-key reference or digest differs from the manifest")
        if not path.is_absolute() or str(path).startswith("/nix/store/"):
            raise ValueError("private key must be an external absolute non-Nix path")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != trusted_uid or stat.S_IMODE(metadata.st_mode) != 0o400:
                raise ValueError("external private key must be a trusted root-owned 0400 regular file")
            content = bytearray()
            while block := os.read(fd, 64 * 1024):
                content.extend(block)
                if len(content) > 1024 * 1024:
                    raise ValueError("external private key exceeds its byte bound")
        except OSError as exc:
            raise ValueError("external private key is unavailable") from exc
        finally:
            if "fd" in locals():
                os.close(fd)
        observed = "sha256:" + sha256(content).hexdigest()
        if observed != self.sha256:
            raise ValueError("external private-key content digest differs")


class ImmutableBootstrapReceiptStore:
    """One claim and one terminal receipt per exact operation identity."""

    def __init__(self, root: Path, *, trusted_uid: int = 0):
        self.root = Path(root)
        try:
            metadata = self.root.lstat()
        except OSError as exc:
            raise ValueError("platform-bootstrap receipt root is unavailable") from exc
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != trusted_uid
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ValueError("platform-bootstrap receipt root is unavailable")
        self._directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def persist(self, value: Mapping[str, Any], *, phase: str) -> str:
        if phase not in {"attempt", "success", "rollback"}:
            raise ValueError("platform-bootstrap receipt phase is invalid")
        raw = canonical(value) + b"\n"
        name = f"{value['operation_id'].replace(':', '_')}-{phase}.json"
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._directory_fd)
        except FileExistsError as exc:
            raise ValueError("platform-bootstrap operation phase is already recorded") from exc
        try:
            os.write(fd, raw)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self._directory_fd)
        return "platform-bootstrap-receipt:sha256:" + sha256(raw[:-1]).hexdigest()


HostOperation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
CurrentSystem = Callable[[], str]


class A3PlatformBootstrapProvider:
    """Exact manifest provider; host mutations live behind closed typed callbacks."""

    def __init__(
        self,
        manifest: Mapping[str, Any],
        *,
        attestation_key: ExternalPrivateKey,
        ssh_key: ExternalPrivateKey,
        receipts: ImmutableBootstrapReceiptStore,
        current_system: CurrentSystem,
        activate_successor: HostOperation,
        verify_health: HostOperation,
        verify_probe: HostOperation,
        activate_prior: HostOperation,
        trusted_key_uid: int = 0,
    ):
        self.manifest = validate_platform_bootstrap_manifest(manifest)
        bindings = self.manifest["credential_bindings"]
        attestation_key.validate(bindings["attestation_signing"], trusted_uid=trusted_key_uid)
        ssh_key.validate(bindings["ssh_identity"], trusted_uid=trusted_key_uid)
        for operation in (current_system, activate_successor, verify_health, verify_probe, activate_prior):
            if not callable(operation):
                raise ValueError("platform-bootstrap host operation is unavailable")
        self.receipts = receipts
        self.current_system = current_system
        self.activate_successor = activate_successor
        self.verify_health = verify_health
        self.verify_probe = verify_probe
        self.activate_prior = activate_prior

    def _exact(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        effect_parameters = dict(parameters)
        generation = effect_parameters.pop("generation", None)
        if not isinstance(generation, str) or not _IDENTITY.fullmatch(generation):
            raise ValueError("platform-bootstrap effect generation is invalid")
        observed = validate_platform_bootstrap_effect(effect_parameters)
        if observed != self.manifest:
            raise ValueError("platform-bootstrap effect differs from the mounted manifest")
        return observed

    def install(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest = self._exact(parameters)
        if self.current_system() != manifest["expected_current_system"]:
            raise ValueError("platform-bootstrap expected-current CAS failed")
        attempt = {
            "schema": "tgw-a3-platform-bootstrap-attempt/v1",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "expected_current_system": manifest["expected_current_system"],
            "successor_system": manifest["successor_system"],
        }
        attempt_receipt = self.receipts.persist(attempt, phase="attempt")
        activation = self.activate_successor(manifest)
        health = self.verify_health(manifest)
        probe = self.verify_probe(manifest)
        if activation.get("status") != "activated" or activation.get("system") != manifest["successor_system"]:
            raise RuntimeError("platform-bootstrap activation result is invalid")
        if health.get("status") != "healthy" or health.get("receipt") != manifest["health_receipt"]:
            raise RuntimeError("platform-bootstrap health receipt is invalid")
        if probe.get("status") != "passed" or probe.get("receipt") != manifest["probe_receipt"]:
            raise RuntimeError("platform-bootstrap probe receipt is invalid")
        result = {
            "schema": "tgw-a3-platform-bootstrap-success/v1",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "prior_system": manifest["prior_system"],
            "successor_system": manifest["successor_system"],
            "attempt_receipt": attempt_receipt,
            "health_receipt": manifest["health_receipt"],
            "probe_receipt": manifest["probe_receipt"],
        }
        success_receipt = self.receipts.persist(result, phase="success")
        return {"evidence": [attempt_receipt, success_receipt, manifest["health_receipt"], manifest["probe_receipt"]]}

    def rollback(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest = self._exact(parameters)
        restored = self.activate_prior(manifest)
        if restored.get("status") != "activated" or restored.get("system") != manifest["prior_system"]:
            raise RuntimeError("platform-bootstrap prior-closure rollback is invalid")
        result = {
            "schema": "tgw-a3-platform-bootstrap-rollback/v1",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "restored_system": manifest["prior_system"],
        }
        return {"receipt": self.receipts.persist(result, phase="rollback")}
