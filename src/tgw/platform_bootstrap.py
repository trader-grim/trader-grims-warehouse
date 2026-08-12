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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MANIFEST_SCHEMA = "tgw-a3-platform-bootstrap-manifest/v1"
EFFECT_SCHEMA = "tgw-a3-platform-bootstrap-effect/v1"
PLAN_COMMIT = "fb9fee3e9db756ad0f5071525e943794bf1dab9b"
SOLUTION_HASH = "sha256:d28650c26c6a3d26d6c943597ccb7abd7c6670b1703d9ce941ac5ed7a2d73a4d"
TARGET_HOST = "tgw-prod"
FLAKE_REPOSITORY = "tgw-flake"
RETIREMENT_CONDITION = "W10:canonical-gate-operational"
ATTESTATION_KEY_REF = "credential:tgw-platform-bootstrap:attestation-signing"
SSH_KEY_REF = "credential:tgw-platform-bootstrap:ssh-identity"
WRAPPER_PATH = "/run/current-system/sw/bin/tgw-nix-observer-render-wrapper"
SUDO_PATH = "/run/wrappers/bin/sudo"
SUDO_COMMAND = f"{SUDO_PATH} -n -- {WRAPPER_PATH}"
AUTHORIZED_KEY_PREFIX = f'restrict,command="{SUDO_COMMAND}" ssh-ed25519 '
LIVE_FLAKE_GATE = "EXTERNAL_TGW_PROD_FLAKE_IMPORT_BUILD_REQUIRED"

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
        "candidate_receipt",
        "review_receipt",
        "controller_receipt",
        "activation_provider_receipt",
        "health_receipt",
        "probe_receipt",
        "retirement_condition",
        "live_flake_gate",
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
        or value["live_flake_gate"] != LIVE_FLAKE_GATE
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
    for name in (
        "candidate_receipt",
        "review_receipt",
        "controller_receipt",
        "activation_provider_receipt",
        "health_receipt",
        "probe_receipt",
    ):
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

    def validate(
        self,
        expected: Mapping[str, str],
        *,
        key_kind: str,
        public_verifier: bytes,
        trusted_uid: int = 0,
    ) -> None:
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
        try:
            if key_kind == "attestation-ed25519":
                private = serialization.load_pem_private_key(bytes(content), password=None)
                public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
                if len(public_verifier) != 32 or public != public_verifier:
                    raise ValueError("attestation private key differs from the raw public verifier")
            elif key_kind == "ssh-ed25519":
                private = serialization.load_ssh_private_key(bytes(content), password=None)
                public_line = private.public_key().public_bytes(
                    serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
                ).decode("ascii")
                try:
                    authorized = public_verifier.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise ValueError("SSH authorized public key is not ASCII") from exc
                if (
                    "\n" in authorized
                    or "\r" in authorized
                    or not authorized.startswith(AUTHORIZED_KEY_PREFIX)
                    or authorized.removeprefix(AUTHORIZED_KEY_PREFIX) != public_line.removeprefix("ssh-ed25519 ")
                ):
                    raise ValueError("SSH private key differs from the one strict forced authorized key")
            else:
                raise ValueError("external private-key kind is invalid")
            if not isinstance(private, Ed25519PrivateKey):
                raise ValueError("external private key is not Ed25519")
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith(("attestation private", "SSH ", "external private")):
                raise
            raise ValueError("external private key encoding is invalid") from exc


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
        opened = os.fstat(self._directory_fd)
        self._identity = (opened.st_dev, opened.st_ino, opened.st_uid, stat.S_IMODE(opened.st_mode))

    def _revalidate_root(self) -> None:
        opened = os.fstat(self._directory_fd)
        try:
            named = self.root.lstat()
        except OSError as exc:
            raise ValueError("platform-bootstrap receipt root identity is unavailable") from exc
        identity = (opened.st_dev, opened.st_ino, opened.st_uid, stat.S_IMODE(opened.st_mode))
        named_identity = (named.st_dev, named.st_ino, named.st_uid, stat.S_IMODE(named.st_mode))
        if identity != self._identity or named_identity != self._identity or not stat.S_ISDIR(opened.st_mode):
            raise ValueError("platform-bootstrap receipt root identity changed")

    @staticmethod
    def _write_all(fd: int, raw: bytes) -> None:
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("immutable receipt short write")
            offset += written

    def persist(self, value: Mapping[str, Any], *, phase: str) -> str:
        if phase not in {"attempt", "success", "rollback"}:
            raise ValueError("platform-bootstrap receipt phase is invalid")
        self._revalidate_root()
        raw = canonical(value) + b"\n"
        name = f"{value['operation_id'].replace(':', '_')}-{phase}.json"
        fd = -1
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400, dir_fd=self._directory_fd)
        except FileExistsError as exc:
            raise ValueError("platform-bootstrap operation phase is already recorded") from exc
        try:
            created = os.fstat(fd)
            self._write_all(fd, raw)
            os.fsync(fd)
            written = os.fstat(fd)
        finally:
            if fd >= 0:
                os.close(fd)
        os.fsync(self._directory_fd)
        self._revalidate_root()
        read_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self._directory_fd)
        try:
            held = os.fstat(read_fd)
            observed = bytearray()
            while block := os.read(read_fd, 64 * 1024):
                observed.extend(block)
                if len(observed) > len(raw):
                    raise ValueError("immutable receipt reread exceeded its exact size")
        finally:
            os.close(read_fd)
        if (
            (created.st_dev, created.st_ino) != (written.st_dev, written.st_ino)
            or (created.st_dev, created.st_ino) != (held.st_dev, held.st_ino)
            or held.st_uid != self._identity[2]
            or stat.S_IMODE(held.st_mode) != 0o400
            or held.st_size != len(raw)
            or bytes(observed) != raw
        ):
            raise ValueError("immutable receipt held reread or identity differs")
        return "platform-bootstrap-receipt:sha256:" + sha256(raw[:-1]).hexdigest()


HostOperation = Callable[[Mapping[str, Any]], Mapping[str, Any]]
CurrentSystem = Callable[[], str]
RecordResolver = Callable[[str], Mapping[str, Any]]
ClosureResolver = Callable[[str], Mapping[str, Any]]


class BootstrapStateAmbiguous(RuntimeError):
    def __init__(self, message: str, *, evidence: tuple[str, ...], rollback_required: bool = True):
        super().__init__(message)
        self.evidence = evidence
        self.rollback_required = rollback_required


def _validate_self_hashed_record(
    value: Any, reference: str, *, schema: str, prefix: str, trusted_uid: int
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != schema or not _self_hashed_record(value):
        raise ValueError(f"{schema} record is invalid")
    if reference != prefix + value["receipt_sha256"]:
        raise ValueError(f"{schema} record reference is invalid")
    path = Path(str(value.get("record_path", "")))
    expected = canonical(value)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        observed = bytearray()
        while block := os.read(fd, 64 * 1024):
            observed.extend(block)
            if len(observed) > len(expected):
                raise ValueError(f"{schema} immutable record exceeds its exact size")
    except OSError as exc:
        raise ValueError(f"{schema} immutable record is unavailable") from exc
    finally:
        if "fd" in locals():
            os.close(fd)
    if (
        not path.is_absolute()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or stat.S_IMODE(metadata.st_mode) != 0o400
        or metadata.st_size != len(expected)
        or bytes(observed) != expected
    ):
        raise ValueError(f"{schema} immutable record identity differs")
    return dict(value)


def _self_hashed_record(value: Mapping[str, Any]) -> bool:
    unsigned = dict(value)
    claimed = unsigned.pop("receipt_sha256", None)
    return claimed == digest(unsigned)


def _held_artifact(record: Any, expected: Mapping[str, str], *, name: str, trusted_uid: int) -> bytes:
    fields = {"schema", "name", "artifact_ref", "path", "sha256", "size", "owner_uid", "mode"}
    if (
        not isinstance(record, Mapping)
        or set(record) != fields
        or record["schema"] != "tgw-a3-platform-bootstrap-artifact/v1"
        or record["name"] != name
        or record["artifact_ref"] != expected["artifact_ref"]
        or record["sha256"] != expected["sha256"]
        or record["owner_uid"] != trusted_uid
        or record["mode"] not in {"0400", "0444", "0555"}
        or not isinstance(record["size"], int)
        or not 1 <= record["size"] <= 64 * 1024 * 1024
    ):
        raise ValueError(f"platform-bootstrap {name} immutable artifact record is invalid")
    path = Path(str(record["path"]))
    if not path.is_absolute():
        raise ValueError(f"platform-bootstrap {name} artifact path is invalid")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        content = bytearray()
        while block := os.read(fd, 1024 * 1024):
            content.extend(block)
            if len(content) > record["size"]:
                raise ValueError(f"platform-bootstrap {name} artifact exceeds its record")
    except OSError as exc:
        raise ValueError(f"platform-bootstrap {name} artifact is unavailable") from exc
    finally:
        if "fd" in locals():
            os.close(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or f"{stat.S_IMODE(metadata.st_mode):04o}" != record["mode"]
        or metadata.st_size != record["size"]
        or "sha256:" + sha256(content).hexdigest() != expected["sha256"]
    ):
        raise ValueError(f"platform-bootstrap {name} artifact identity differs")
    return bytes(content)


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
        resolve_record: RecordResolver,
        resolve_closure: ClosureResolver,
        trusted_key_uid: int = 0,
    ):
        self.manifest = validate_platform_bootstrap_manifest(manifest)
        for operation in (current_system, activate_successor, verify_health, verify_probe, activate_prior, resolve_record, resolve_closure):
            if not callable(operation):
                raise ValueError("platform-bootstrap host operation is unavailable")
        self.resolve_record = resolve_record
        self.resolve_closure = resolve_closure
        self.trusted_key_uid = trusted_key_uid
        artifacts = self._resolve_artifacts()
        bindings = self.manifest["credential_bindings"]
        attestation_key.validate(
            bindings["attestation_signing"],
            key_kind="attestation-ed25519",
            public_verifier=artifacts["attestation_public_key"],
            trusted_uid=trusted_key_uid,
        )
        ssh_key.validate(
            bindings["ssh_identity"],
            key_kind="ssh-ed25519",
            public_verifier=artifacts["ssh_authorized_public_key"],
            trusted_uid=trusted_key_uid,
        )
        self.receipts = receipts
        self.current_system = current_system
        self.activate_successor = activate_successor
        self.verify_health = verify_health
        self.verify_probe = verify_probe
        self.activate_prior = activate_prior

    def _resolve_artifacts(self) -> dict[str, bytes]:
        return {
            name: _held_artifact(
                self.resolve_record(binding["artifact_ref"]), binding, name=name, trusted_uid=self.trusted_key_uid
            )
            for name, binding in self.manifest["artifacts"].items()
        }

    def _exact(self, parameters: Mapping[str, Any]) -> dict[str, Any]:
        effect_parameters = dict(parameters)
        generation = effect_parameters.pop("generation", None)
        if not isinstance(generation, str) or not _IDENTITY.fullmatch(generation):
            raise ValueError("platform-bootstrap effect generation is invalid")
        observed = validate_platform_bootstrap_effect(effect_parameters)
        if observed != self.manifest:
            raise ValueError("platform-bootstrap effect differs from the mounted manifest")
        return observed

    def preflight(self, parameters: Mapping[str, Any]) -> None:
        """Resolve every immutable W08 input before authority can be consumed."""
        manifest = self._exact({**parameters, "generation": parameters.get("generation", "preflight")})
        self._resolve_artifacts()
        candidate = _validate_self_hashed_record(
            self.resolve_record(manifest["candidate_receipt"]),
            manifest["candidate_receipt"],
            schema="tgw-a3-platform-bootstrap-candidate/v1",
            prefix="candidate:",
            trusted_uid=self.trusted_key_uid,
        )
        expected_candidate = {
            "flake_commit": manifest["flake_commit"],
            "flake_tree": manifest["flake_tree"],
            "successor_system": manifest["successor_system"],
            "artifacts": manifest["artifacts"],
            "status": "REVIEWED_IMMUTABLE",
        }
        if any(candidate.get(key) != expected for key, expected in expected_candidate.items()):
            raise ValueError("platform-bootstrap candidate record differs from the manifest")
        review = _validate_self_hashed_record(
            self.resolve_record(manifest["review_receipt"]),
            manifest["review_receipt"],
            schema="tgw-a3-platform-bootstrap-review/v1",
            prefix="review:",
            trusted_uid=self.trusted_key_uid,
        )
        controller = _validate_self_hashed_record(
            self.resolve_record(manifest["controller_receipt"]),
            manifest["controller_receipt"],
            schema="tgw-a3-platform-bootstrap-controller/v1",
            prefix="controller:",
            trusted_uid=self.trusted_key_uid,
        )
        if (
            review.get("status") != "PASS"
            or review.get("candidate_receipt") != manifest["candidate_receipt"]
            or controller.get("status") != "PASS"
            or controller.get("candidate_receipt") != manifest["candidate_receipt"]
            or controller.get("review_receipt") != manifest["review_receipt"]
        ):
            raise ValueError("platform-bootstrap review/controller record chain is invalid")
        closure = _validate_self_hashed_record(
            self.resolve_closure(manifest["successor_system"]),
            manifest["activation_provider_receipt"],
            schema="tgw-a3-platform-bootstrap-closure-membership/v1",
            prefix="activation-provider:",
            trusted_uid=self.trusted_key_uid,
        )
        if (
            closure.get("status") != "EXACT_MEMBER"
            or closure.get("successor_system") != manifest["successor_system"]
            or closure.get("candidate_receipt") != manifest["candidate_receipt"]
            or closure.get("flake_commit") != manifest["flake_commit"]
            or closure.get("flake_tree") != manifest["flake_tree"]
            or closure.get("artifacts") != manifest["artifacts"]
        ):
            raise ValueError("platform-bootstrap successor closure membership is invalid")

    def install(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest = self._exact(parameters)
        attempt = {
            "schema": "tgw-a3-platform-bootstrap-attempt/v1",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "expected_current_system": manifest["expected_current_system"],
            "successor_system": manifest["successor_system"],
        }
        try:
            attempt_receipt = self.receipts.persist(attempt, phase="attempt")
        except Exception as exc:
            raise BootstrapStateAmbiguous(
                "platform-bootstrap attempt persistence is ambiguous",
                evidence=("platform-bootstrap-attempt-memory:" + digest(attempt),),
                rollback_required=False,
            ) from exc
        if self.current_system() != manifest["expected_current_system"]:
            raise BootstrapStateAmbiguous(
                "platform-bootstrap expected-current CAS changed immediately before activation",
                evidence=(attempt_receipt,),
                rollback_required=False,
            )
        activation = self.activate_successor(manifest)
        if (
            activation.get("status") != "activated"
            or activation.get("from_system") != manifest["prior_system"]
            or activation.get("to_system") != manifest["successor_system"]
            or activation.get("provider_receipt") != manifest["activation_provider_receipt"]
            or not _RECEIPT.fullmatch(str(activation.get("receipt")))
        ):
            raise RuntimeError("platform-bootstrap activation result is invalid")
        if self.current_system() != manifest["successor_system"]:
            raise BootstrapStateAmbiguous(
                "platform-bootstrap successor readback is not exact",
                evidence=(attempt_receipt, str(activation["receipt"])),
            )
        health = self.verify_health(manifest)
        probe = self.verify_probe(manifest)
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
            "candidate_receipt": manifest["candidate_receipt"],
            "review_receipt": manifest["review_receipt"],
            "controller_receipt": manifest["controller_receipt"],
            "activation_receipt": activation["receipt"],
            "activation_provider_receipt": manifest["activation_provider_receipt"],
            "health_receipt": manifest["health_receipt"],
            "probe_receipt": manifest["probe_receipt"],
        }
        success_receipt = self.receipts.persist(result, phase="success")
        return {
            "evidence": [
                attempt_receipt,
                success_receipt,
                manifest["review_receipt"],
                manifest["controller_receipt"],
                activation["receipt"],
                manifest["activation_provider_receipt"],
                manifest["health_receipt"],
                manifest["probe_receipt"],
            ]
        }

    def rollback(self, parameters: Mapping[str, Any]) -> Mapping[str, Any]:
        manifest = self._exact(parameters)
        observed = self.current_system()
        if observed == manifest["prior_system"]:
            result = {
                "schema": "tgw-a3-platform-bootstrap-rollback/v1",
                "operation_id": manifest["operation_id"],
                "manifest_sha256": manifest["manifest_sha256"],
                "from_system": manifest["prior_system"],
                "restored_system": manifest["prior_system"],
                "outcome": "NOOP_ALREADY_PRIOR",
            }
            return {"receipt": self.receipts.persist(result, phase="rollback")}
        if observed != manifest["successor_system"]:
            raise BootstrapStateAmbiguous(
                "platform-bootstrap rollback observed neither prior nor successor closure",
                evidence=("platform-bootstrap-state:sha256:" + sha256(observed.encode()).hexdigest(),),
            )
        restored = self.activate_prior(manifest)
        if (
            restored.get("status") != "activated"
            or restored.get("from_system") != manifest["successor_system"]
            or restored.get("to_system") != manifest["prior_system"]
            or not _RECEIPT.fullmatch(str(restored.get("receipt")))
            or self.current_system() != manifest["prior_system"]
        ):
            raise RuntimeError("platform-bootstrap prior-closure rollback is invalid")
        result = {
            "schema": "tgw-a3-platform-bootstrap-rollback/v1",
            "operation_id": manifest["operation_id"],
            "manifest_sha256": manifest["manifest_sha256"],
            "from_system": manifest["successor_system"],
            "restored_system": manifest["prior_system"],
            "outcome": "RESTORED_PRIOR",
            "activation_receipt": restored["receipt"],
        }
        return {"receipt": self.receipts.persist(result, phase="rollback")}
