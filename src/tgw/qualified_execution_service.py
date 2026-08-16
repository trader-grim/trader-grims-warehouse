"""Qualified execution proofs for candidate tests and PostgreSQL migrations.

This service is intentionally separate from the governed resource service.
Resource retrieval attestations prove that a harness fetched card resources;
they do not prove that a command ran.  This module owns the stronger claim:
an externally configured service resolved one exact Git candidate and ran an
allowlisted profile in its own bounded environment.

No endpoint, bearer, repository, or signing key is supplied by this source.
Those are deployment inputs.  The public catalog is merely a verifier pin.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from tgw.candidate_manifest import (
    CandidateManifestError,
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
    verify_migration_safety_receipt,
)

SERVICE_CONFIG_SCHEMA = "tgw-qualified-execution-service-config/v1"
SERVICE_DESCRIPTOR_SCHEMA = "tgw-qualified-execution-service/v1"
SERVICE_CATALOG_SCHEMA = "tgw-qualified-execution-service-catalog/v1"
REQUEST_SCHEMA = "tgw-qualified-execution-request/v1"
RESPONSE_SCHEMA = "tgw-qualified-execution-response/v1"
PROOF_SCHEMA = "tgw-qualified-execution-proof/v1"
TRANSCRIPT_SCHEMA = "tgw-qualified-execution-transcript/v1"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OBJECT = re.compile(r"[0-9a-f]{40}\Z")
_SERVICE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
_MAX_REQUEST_BYTES = 128 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_CAPABILITIES = frozenset(
    {
        "candidate-test-execution",
        "postgresql-migration-execution",
        "candidate-review-execution",
    }
)


class QualifiedExecutionError(ValueError):
    """A qualified execution request, proof, or configuration is invalid."""


class QualifiedExecutionConfigurationError(ValueError):
    """The externally provisioned execution service configuration is invalid."""


class _ProtocolError(ValueError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise QualifiedExecutionError("qualified execution value is not canonical JSON") from exc


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _valid_relative_path(value: Any) -> bool:
    return isinstance(value, str) and _PATH.fullmatch(value) is not None and not value.startswith("/") and ".." not in Path(value).parts


def _base64(value: Any, *, label: str, length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise QualifiedExecutionError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise QualifiedExecutionError(f"{label} is invalid") from exc
    if base64.b64encode(decoded).decode("ascii") != value or (length is not None and len(decoded) != length):
        raise QualifiedExecutionError(f"{label} is invalid")
    return decoded


def _private_key(value: Ed25519PrivateKey | str | bytes) -> Ed25519PrivateKey:
    raw: bytes
    if isinstance(value, Ed25519PrivateKey):
        return value
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = _base64(value, label="qualified execution private key", length=32)
    else:
        raise QualifiedExecutionError("qualified execution private key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise QualifiedExecutionError("qualified execution private key is invalid") from exc


def execution_public_key(value: Ed25519PrivateKey | Ed25519PublicKey | str | bytes) -> str:
    """Return the canonical raw Ed25519 public-key representation for a catalog."""

    key = value if isinstance(value, Ed25519PublicKey) else _private_key(value).public_key()
    return base64.b64encode(key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)).decode("ascii")


def _public_key(value: Any) -> Ed25519PublicKey:
    try:
        return Ed25519PublicKey.from_public_bytes(_base64(value, label="qualified execution public key", length=32))
    except ValueError as exc:
        raise QualifiedExecutionError("qualified execution public key is invalid") from exc


def _environment(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise QualifiedExecutionError("qualified execution environment is invalid")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _ENV_NAME.fullmatch(key) is None or not isinstance(item, str):
            raise QualifiedExecutionError("qualified execution environment is invalid")
        normalized[key] = item
    return dict(sorted(normalized.items()))


def _descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "id", "client_id", "endpoint", "credential_env", "timeout_seconds"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != SERVICE_DESCRIPTOR_SCHEMA:
        raise QualifiedExecutionError("qualified execution service descriptor is invalid")
    service_id, client_id, endpoint = value.get("id"), value.get("client_id"), value.get("endpoint")
    credential_env, timeout_seconds = value.get("credential_env"), value.get("timeout_seconds")
    parsed = urlsplit(endpoint) if isinstance(endpoint, str) else None
    loopback = parsed is not None and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        not isinstance(service_id, str)
        or _SERVICE_ID.fullmatch(service_id) is None
        or not isinstance(client_id, str)
        or _CLIENT_ID.fullmatch(client_id) is None
        or not isinstance(endpoint, str)
        or parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme == "http" and not loopback)
        or (credential_env is not None and (not isinstance(credential_env, str) or _ENV_NAME.fullmatch(credential_env) is None))
        or not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or not 1 <= timeout_seconds <= 3600
    ):
        raise QualifiedExecutionError("qualified execution service descriptor is invalid")
    return {
        "schema": SERVICE_DESCRIPTOR_SCHEMA,
        "id": service_id,
        "client_id": client_id,
        "endpoint": endpoint.rstrip("/"),
        "credential_env": credential_env,
        "timeout_seconds": timeout_seconds,
    }


def execution_service_descriptor_hash(value: Mapping[str, Any]) -> str:
    return _hash(_descriptor(value))


def validate_execution_service_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {"schema", "catalog_ref", "plan_commit", "services"}
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != SERVICE_CATALOG_SCHEMA:
        raise QualifiedExecutionError("qualified execution service catalog is invalid")
    if not isinstance(value.get("catalog_ref"), str) or not value["catalog_ref"]:
        raise QualifiedExecutionError("qualified execution service catalog reference is invalid")
    if not isinstance(value.get("plan_commit"), str) or _GIT_OBJECT.fullmatch(value["plan_commit"]) is None:
        raise QualifiedExecutionError("qualified execution service catalog Plan binding is invalid")
    services = value.get("services")
    if not isinstance(services, list) or not services:
        raise QualifiedExecutionError("qualified execution service catalog is empty")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in services:
        fields = {"id", "client_id", "descriptor_hash", "capabilities", "attestation_key_id", "attestation_public_key"}
        if not isinstance(item, Mapping) or set(item) != fields:
            raise QualifiedExecutionError("qualified execution service catalog entry is invalid")
        identity = (item.get("id"), item.get("client_id"))
        capabilities = item.get("capabilities")
        if (
            not isinstance(identity[0], str)
            or _SERVICE_ID.fullmatch(identity[0]) is None
            or not isinstance(identity[1], str)
            or _CLIENT_ID.fullmatch(identity[1]) is None
            or identity in seen
            or not _is_hash(item.get("descriptor_hash"))
            or not isinstance(item.get("attestation_key_id"), str)
            or _KEY_ID.fullmatch(item["attestation_key_id"]) is None
            or not isinstance(capabilities, list)
            or not all(isinstance(cap, str) and cap in _CAPABILITIES for cap in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise QualifiedExecutionError("qualified execution service catalog entry is invalid")
        _public_key(item.get("attestation_public_key"))
        seen.add(identity)
        normalized.append(
            {
                "id": identity[0],
                "client_id": identity[1],
                "descriptor_hash": item["descriptor_hash"],
                "capabilities": sorted(capabilities),
                "attestation_key_id": item["attestation_key_id"],
                "attestation_public_key": item["attestation_public_key"],
            }
        )
    return {"schema": SERVICE_CATALOG_SCHEMA, "catalog_ref": value["catalog_ref"], "plan_commit": value["plan_commit"], "services": normalized}


def execution_service_catalog_hash(value: Mapping[str, Any]) -> str:
    return _hash(validate_execution_service_catalog(value))


def execution_service_attestation_key(catalog: Mapping[str, Any], service_id: str, client_id: str) -> dict[str, str]:
    normalized = validate_execution_service_catalog(catalog)
    entry = next((item for item in normalized["services"] if (item["id"], item["client_id"]) == (service_id, client_id)), None)
    if entry is None:
        raise QualifiedExecutionError("qualified execution service is absent from catalog")
    return {"attestation_key_id": entry["attestation_key_id"], "attestation_public_key": entry["attestation_public_key"]}


def _transcript_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "service_id",
        "client_id",
        "run_id",
        "profile_id",
        "kind",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
        "base_tree",
        "plan_commit",
        "command",
        "stdout_base64",
        "stderr_base64",
        "stdout_sha256",
        "stderr_sha256",
        "output_hash",
        "output_complete",
        "returncode",
        "timed_out",
        "status",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != TRANSCRIPT_SCHEMA:
        raise QualifiedExecutionError("qualified execution transcript is invalid")
    for field, pattern in (("service_id", _SERVICE_ID), ("client_id", _CLIENT_ID), ("run_id", _RUN_ID), ("profile_id", _PROFILE_ID)):
        if not isinstance(value.get(field), str) or pattern.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified execution transcript identity is invalid")
    if value.get("kind") not in {"test", "migration", "review"}:
        raise QualifiedExecutionError("qualified execution transcript kind is invalid")
    for field in ("candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified execution transcript Git binding is invalid")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise QualifiedExecutionError("qualified execution transcript command is invalid")
    stdout, stderr = _base64(value.get("stdout_base64"), label="qualified execution stdout"), _base64(value.get("stderr_base64"), label="qualified execution stderr")
    if value.get("stdout_sha256") != _hash_bytes(stdout) or value.get("stderr_sha256") != _hash_bytes(stderr):
        raise QualifiedExecutionError("qualified execution transcript output hash is invalid")
    if value.get("output_hash") != _hash_bytes(stdout + b"\0" + stderr):
        raise QualifiedExecutionError("qualified execution transcript combined output hash is invalid")
    if not isinstance(value.get("output_complete"), bool) or not isinstance(value.get("returncode"), int) or isinstance(value["returncode"], bool) or not isinstance(value.get("timed_out"), bool):
        raise QualifiedExecutionError("qualified execution transcript status is invalid")
    if value.get("status") not in {"PASS", "FAIL"}:
        raise QualifiedExecutionError("qualified execution transcript status is invalid")
    return dict(value)


def _proof_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema",
        "service_id",
        "client_id",
        "run_id",
        "profile_id",
        "kind",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
        "base_tree",
        "plan_commit",
        "inputs",
        "runtime",
        "command",
        "transcript_hash",
        "output_hash",
        "output_complete",
        "returncode",
        "timed_out",
        "status",
        "attestation_key_id",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != PROOF_SCHEMA:
        raise QualifiedExecutionError("qualified execution proof is invalid")
    for field, pattern in (("service_id", _SERVICE_ID), ("client_id", _CLIENT_ID), ("run_id", _RUN_ID), ("profile_id", _PROFILE_ID), ("attestation_key_id", _KEY_ID)):
        if not isinstance(value.get(field), str) or pattern.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified execution proof identity is invalid")
    if value.get("kind") not in {"test", "migration", "review"}:
        raise QualifiedExecutionError("qualified execution proof kind is invalid")
    for field in ("candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified execution proof Git binding is invalid")
    if not isinstance(value.get("inputs"), Mapping) or not isinstance(value.get("runtime"), Mapping):
        raise QualifiedExecutionError("qualified execution proof inputs are invalid")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg for arg in command):
        raise QualifiedExecutionError("qualified execution proof command is invalid")
    for field in ("transcript_hash", "output_hash"):
        if not _is_hash(value.get(field)):
            raise QualifiedExecutionError("qualified execution proof hash is invalid")
    if (
        not isinstance(value.get("output_complete"), bool)
        or not isinstance(value.get("returncode"), int)
        or isinstance(value["returncode"], bool)
        or not isinstance(value.get("timed_out"), bool)
        or value.get("status") not in {"PASS", "FAIL"}
    ):
        raise QualifiedExecutionError("qualified execution proof status is invalid")
    runtime_fields = {
        "interpreter_path",
        "interpreter_sha256",
        "interpreter_version_sha256",
        "dependency_manifest_path",
        "dependency_manifest_sha256",
        "environment",
        "environment_hash",
    }
    runtime = value["runtime"]
    if set(runtime) != runtime_fields or not isinstance(runtime.get("interpreter_path"), str) or not isinstance(runtime.get("dependency_manifest_path"), str):
        raise QualifiedExecutionError("qualified execution proof runtime is invalid")
    if not all(_is_hash(runtime.get(field)) for field in ("interpreter_sha256", "interpreter_version_sha256", "dependency_manifest_sha256", "environment_hash")):
        raise QualifiedExecutionError("qualified execution proof runtime is invalid")
    environment = _environment(runtime.get("environment"))
    if runtime["environment_hash"] != _hash(environment):
        raise QualifiedExecutionError("qualified execution proof environment hash is invalid")
    return {**dict(value), "inputs": dict(value["inputs"]), "runtime": {**dict(runtime), "environment": environment}}


def issue_execution_proof(value: Mapping[str, Any], *, signing_private_key: Ed25519PrivateKey | str | bytes) -> dict[str, Any]:
    """Sign a qualified-execution payload.  Only the execution service calls this in production."""

    payload = _proof_payload(value)
    proof_hash = _hash(payload)
    signed = {**payload, "proof_hash": proof_hash}
    signature = _private_key(signing_private_key).sign(_canonical(signed))
    return {**signed, "signature": base64.b64encode(signature).decode("ascii")}


def validate_execution_proof(
    proof: Mapping[str, Any],
    transcript: Mapping[str, Any],
    *,
    catalog: Mapping[str, Any],
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify a signed proof and its complete retained transcript against a catalog key."""

    required = set(
        _proof_payload(
            {
                "schema": PROOF_SCHEMA,
                "service_id": "a",
                "client_id": "a",
                "run_id": "a",
                "profile_id": "a",
                "kind": "test",
                "candidate_commit": "0" * 40,
                "candidate_tree": "0" * 40,
                "base_commit": "0" * 40,
                "base_tree": "0" * 40,
                "plan_commit": "0" * 40,
                "inputs": {},
                "runtime": {
                    "interpreter_path": "x",
                    "interpreter_sha256": "sha256:" + "0" * 64,
                    "interpreter_version_sha256": "sha256:" + "0" * 64,
                    "dependency_manifest_path": "x",
                    "dependency_manifest_sha256": "sha256:" + "0" * 64,
                    "environment": {},
                    "environment_hash": _hash({}),
                },
                "command": ["x"],
                "transcript_hash": "sha256:" + "0" * 64,
                "output_hash": "sha256:" + "0" * 64,
                "output_complete": True,
                "returncode": 0,
                "timed_out": False,
                "status": "PASS",
                "attestation_key_id": "a",
            }
        ).keys()
    ) | {"proof_hash", "signature"}
    if not isinstance(proof, Mapping) or set(proof) != required:
        raise QualifiedExecutionError("qualified execution proof is invalid")
    payload = _proof_payload({key: item for key, item in proof.items() if key not in {"proof_hash", "signature"}})
    if not _is_hash(proof.get("proof_hash")) or proof["proof_hash"] != _hash(payload):
        raise QualifiedExecutionError("qualified execution proof hash is invalid")
    signature = _base64(proof.get("signature"), label="qualified execution proof signature", length=64)
    key = execution_service_attestation_key(catalog, payload["service_id"], payload["client_id"])
    if payload["attestation_key_id"] != key["attestation_key_id"]:
        raise QualifiedExecutionError("qualified execution proof key identity is invalid")
    try:
        _public_key(key["attestation_public_key"]).verify(signature, _canonical({**payload, "proof_hash": proof["proof_hash"]}))
    except InvalidSignature as exc:
        raise QualifiedExecutionError("qualified execution proof signature is invalid") from exc
    if not isinstance(transcript, Mapping):
        raise QualifiedExecutionError("qualified execution transcript is invalid")
    normalized_transcript = _transcript_payload({key: item for key, item in transcript.items() if key != "transcript_hash"})
    if set(transcript) != set(normalized_transcript) | {"transcript_hash"} or transcript.get("transcript_hash") != _hash(normalized_transcript):
        raise QualifiedExecutionError("qualified execution transcript hash is invalid")
    for field in (
        "service_id",
        "client_id",
        "run_id",
        "profile_id",
        "kind",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
        "base_tree",
        "plan_commit",
        "command",
        "output_hash",
        "output_complete",
        "returncode",
        "timed_out",
        "status",
    ):
        if normalized_transcript[field] != payload[field]:
            raise QualifiedExecutionError("qualified execution proof transcript binding mismatch")
    if payload["transcript_hash"] != transcript["transcript_hash"]:
        raise QualifiedExecutionError("qualified execution proof transcript binding mismatch")
    if payload["status"] == "PASS" and (not payload["output_complete"] or payload["timed_out"] or payload["returncode"] != 0):
        raise QualifiedExecutionError("qualified execution proof passing status is invalid")
    if expected is not None:
        for field, item in expected.items():
            if payload.get(field) != item:
                raise QualifiedExecutionError("qualified execution proof expected binding mismatch")
    return {**payload, "proof_hash": proof["proof_hash"], "signature": proof["signature"]}


@dataclass(frozen=True)
class RuntimeProfile:
    interpreter_path: Path
    interpreter_sha256: str
    dependency_manifest_path: Path
    dependency_manifest_sha256: str
    environment: Mapping[str, str]


@dataclass(frozen=True)
class Client:
    client_id: str
    credential_env: str
    allowed_profiles: tuple[str, ...]


@dataclass(frozen=True)
class Profile:
    profile_id: str
    kind: str
    timeout_seconds: int
    max_output_bytes: int
    scope: str | None = None
    test_plan_sha256: str | None = None
    test_runner_path: str | None = None
    test_runner_sha256: str | None = None
    command: tuple[str, ...] | None = None
    migration_path: str | None = None
    schema_snapshot_path: str | None = None
    runner_path: Path | None = None
    runner_sha256: str | None = None


@dataclass(frozen=True)
class QualifiedExecutionConfig:
    service_id: str
    repository: Path
    plan_repository: Path
    plan_commit: str
    max_active_requests: int
    clients: Mapping[str, Client]
    attestation_key_id: str
    attestation_private_key_env: str
    runtime: RuntimeProfile
    profiles: Mapping[str, Profile]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "QualifiedExecutionConfig":
        fields = {
            "schema",
            "service_id",
            "repository",
            "plan_repository",
            "plan_commit",
            "max_active_requests",
            "clients",
            "attestation_key_id",
            "attestation_private_key_env",
            "runtime",
            "profiles",
        }
        if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != SERVICE_CONFIG_SCHEMA:
            raise QualifiedExecutionConfigurationError("qualified execution service configuration is invalid")
        service_id, repository, plan_repository, plan_commit, max_active_requests = (
            value.get("service_id"),
            value.get("repository"),
            value.get("plan_repository"),
            value.get("plan_commit"),
            value.get("max_active_requests"),
        )
        if not isinstance(service_id, str) or _SERVICE_ID.fullmatch(service_id) is None or not isinstance(repository, str) or not isinstance(plan_repository, str):
            raise QualifiedExecutionConfigurationError("qualified execution service identity is invalid")
        if not isinstance(plan_commit, str) or _GIT_OBJECT.fullmatch(plan_commit) is None:
            raise QualifiedExecutionConfigurationError("qualified execution Plan binding is invalid")
        if not isinstance(max_active_requests, int) or isinstance(max_active_requests, bool) or not 1 <= max_active_requests <= 64:
            raise QualifiedExecutionConfigurationError("qualified execution request capacity is invalid")
        root = Path(repository)
        try:
            if not root.is_absolute() or root.is_symlink() or not root.resolve(strict=True).is_dir():
                raise OSError
            root = root.resolve(strict=True)
            subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=root, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise QualifiedExecutionConfigurationError("qualified execution repository is unavailable") from exc
        plan_root = Path(plan_repository)
        try:
            if not plan_root.is_absolute() or plan_root.is_symlink() or not plan_root.resolve(strict=True).is_dir():
                raise OSError
            plan_root = plan_root.resolve(strict=True)
            if plan_root == root or root.is_relative_to(plan_root) or plan_root.is_relative_to(root):
                raise OSError
            configured_plan = str(_git(plan_root, "rev-parse", f"{plan_commit}^{{commit}}")).strip()
        except (OSError, QualifiedExecutionError) as exc:
            raise QualifiedExecutionConfigurationError("qualified execution Plan source is unavailable") from exc
        if configured_plan != plan_commit:
            raise QualifiedExecutionConfigurationError("qualified execution Plan binding is invalid")
        clients_value = value.get("clients")
        clients: dict[str, Client] = {}
        envs: set[str] = set()
        if not isinstance(clients_value, list) or not clients_value:
            raise QualifiedExecutionConfigurationError("qualified execution clients are invalid")
        for item in clients_value:
            if not isinstance(item, Mapping) or set(item) != {"id", "credential_env", "profiles"}:
                raise QualifiedExecutionConfigurationError("qualified execution client is invalid")
            client_id, credential_env, allowed_profiles = item.get("id"), item.get("credential_env"), item.get("profiles")
            if (
                not isinstance(client_id, str)
                or _CLIENT_ID.fullmatch(client_id) is None
                or client_id in clients
                or not isinstance(credential_env, str)
                or _ENV_NAME.fullmatch(credential_env) is None
                or credential_env in envs
                or not isinstance(allowed_profiles, list)
                or not allowed_profiles
                or not all(isinstance(profile_id, str) and _PROFILE_ID.fullmatch(profile_id) for profile_id in allowed_profiles)
                or len(set(allowed_profiles)) != len(allowed_profiles)
            ):
                raise QualifiedExecutionConfigurationError("qualified execution client is invalid")
            clients[client_id] = Client(client_id, credential_env, tuple(allowed_profiles))
            envs.add(credential_env)
        key_id, key_env = value.get("attestation_key_id"), value.get("attestation_private_key_env")
        if not isinstance(key_id, str) or _KEY_ID.fullmatch(key_id) is None or not isinstance(key_env, str) or _ENV_NAME.fullmatch(key_env) is None:
            raise QualifiedExecutionConfigurationError("qualified execution signing configuration is invalid")
        runtime_value = value.get("runtime")
        runtime_fields = {"interpreter_path", "interpreter_sha256", "dependency_manifest_path", "dependency_manifest_sha256", "environment"}
        if not isinstance(runtime_value, Mapping) or set(runtime_value) != runtime_fields:
            raise QualifiedExecutionConfigurationError("qualified execution runtime is invalid")
        try:
            interpreter, dependency = Path(runtime_value["interpreter_path"]), Path(runtime_value["dependency_manifest_path"])
            if (
                not interpreter.is_absolute()
                or interpreter.is_symlink()
                or not interpreter.resolve(strict=True).is_file()
                or not dependency.is_absolute()
                or dependency.is_symlink()
                or not dependency.resolve(strict=True).is_file()
            ):
                raise OSError
            interpreter, dependency = interpreter.resolve(strict=True), dependency.resolve(strict=True)
            interpreter_hash, dependency_hash = _hash_bytes(interpreter.read_bytes()), _hash_bytes(dependency.read_bytes())
        except (OSError, TypeError) as exc:
            raise QualifiedExecutionConfigurationError("qualified execution runtime is unavailable") from exc
        if runtime_value.get("interpreter_sha256") != interpreter_hash or runtime_value.get("dependency_manifest_sha256") != dependency_hash:
            raise QualifiedExecutionConfigurationError("qualified execution runtime digest is invalid")
        try:
            environment = _environment(runtime_value.get("environment"))
        except QualifiedExecutionError as exc:
            raise QualifiedExecutionConfigurationError("qualified execution runtime environment is invalid") from exc
        profiles_value = value.get("profiles")
        if not isinstance(profiles_value, list) or not profiles_value:
            raise QualifiedExecutionConfigurationError("qualified execution profiles are invalid")
        profiles: dict[str, Profile] = {}
        for item in profiles_value:
            if not isinstance(item, Mapping):
                raise QualifiedExecutionConfigurationError("qualified execution profile is invalid")
            profile_id, kind = item.get("id"), item.get("kind")
            common = {"id", "kind", "timeout_seconds", "max_output_bytes"}
            if not isinstance(profile_id, str) or _PROFILE_ID.fullmatch(profile_id) is None or profile_id in profiles or kind not in {"test", "migration", "review"}:
                raise QualifiedExecutionConfigurationError("qualified execution profile is invalid")
            timeout, output_limit = item.get("timeout_seconds"), item.get("max_output_bytes")
            if (
                not isinstance(timeout, int)
                or isinstance(timeout, bool)
                or not 1 <= timeout <= 3600
                or not isinstance(output_limit, int)
                or isinstance(output_limit, bool)
                or not 1 <= output_limit <= _MAX_RESPONSE_BYTES // 2
            ):
                raise QualifiedExecutionConfigurationError("qualified execution profile limits are invalid")
            if kind == "test":
                required = common | {"scope", "test_plan_sha256", "test_runner_path", "test_runner_sha256", "command"}
                if (
                    set(item) != required
                    or item.get("scope") not in {"focused", "full"}
                    or not _is_hash(item.get("test_plan_sha256"))
                    or not _valid_relative_path(item.get("test_runner_path"))
                    or not _is_hash(item.get("test_runner_sha256"))
                    or not isinstance(item.get("command"), list)
                    or not item["command"]
                    or not all(isinstance(argument, str) and argument for argument in item["command"])
                ):
                    raise QualifiedExecutionConfigurationError("qualified execution test profile is invalid")
                profiles[profile_id] = Profile(
                    profile_id,
                    kind,
                    timeout,
                    output_limit,
                    scope=item["scope"],
                    test_plan_sha256=item["test_plan_sha256"],
                    test_runner_path=item["test_runner_path"],
                    test_runner_sha256=item["test_runner_sha256"],
                    command=tuple(item["command"]),
                )
            elif kind == "migration":
                required = common | {"migration_path", "schema_snapshot_path", "runner_path", "runner_sha256"}
                if (
                    set(item) != required
                    or not _valid_relative_path(item.get("migration_path"))
                    or (item.get("schema_snapshot_path") is not None and not _valid_relative_path(item.get("schema_snapshot_path")))
                    or not isinstance(item.get("runner_path"), str)
                    or not _is_hash(item.get("runner_sha256"))
                ):
                    raise QualifiedExecutionConfigurationError("qualified execution migration profile is invalid")
                runner = Path(item["runner_path"])
                try:
                    if not runner.is_absolute() or runner.is_symlink() or not runner.resolve(strict=True).is_file() or _hash_bytes(runner.resolve(strict=True).read_bytes()) != item["runner_sha256"]:
                        raise OSError
                    runner = runner.resolve(strict=True)
                except OSError as exc:
                    raise QualifiedExecutionConfigurationError("qualified execution migration runner is invalid") from exc
                profiles[profile_id] = Profile(
                    profile_id,
                    kind,
                    timeout,
                    output_limit,
                    migration_path=item["migration_path"],
                    schema_snapshot_path=item["schema_snapshot_path"],
                    runner_path=runner,
                    runner_sha256=item["runner_sha256"],
                )
            else:
                required = common | {"runner_path", "runner_sha256"}
                if set(item) != required or not isinstance(item.get("runner_path"), str) or not _is_hash(item.get("runner_sha256")):
                    raise QualifiedExecutionConfigurationError("qualified execution review profile is invalid")
                runner = Path(item["runner_path"])
                try:
                    if not runner.is_absolute() or runner.is_symlink() or not runner.resolve(strict=True).is_file() or _hash_bytes(runner.resolve(strict=True).read_bytes()) != item["runner_sha256"]:
                        raise OSError
                    runner = runner.resolve(strict=True)
                except OSError as exc:
                    raise QualifiedExecutionConfigurationError("qualified execution review runner is invalid") from exc
                profiles[profile_id] = Profile(
                    profile_id,
                    kind,
                    timeout,
                    output_limit,
                    runner_path=runner,
                    runner_sha256=item["runner_sha256"],
                )
        if any(not set(client.allowed_profiles) <= set(profiles) for client in clients.values()):
            raise QualifiedExecutionConfigurationError("qualified execution client profile grant is invalid")
        return cls(
            str(service_id),
            root,
            plan_root,
            plan_commit,
            max_active_requests,
            clients,
            str(key_id),
            str(key_env),
            RuntimeProfile(interpreter, interpreter_hash, dependency, dependency_hash, environment),
            profiles,
        )


def load_qualified_execution_config(path: str | os.PathLike[str]) -> QualifiedExecutionConfig:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualifiedExecutionConfigurationError("qualified execution service configuration is unreadable") from exc
    return QualifiedExecutionConfig.parse(value)


def _git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    try:
        completed = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=text)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QualifiedExecutionError("qualified execution candidate Git object is unavailable") from exc
    return completed.stdout


def _exact_candidate(config: QualifiedExecutionConfig, value: Mapping[str, Any], client: Client) -> tuple[str, str, str, str, str]:
    fields = {
        "schema",
        "service_id",
        "client_id",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
        "base_tree",
        "plan_commit",
        "profiles",
        "review_packet",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != REQUEST_SCHEMA or value.get("service_id") != config.service_id:
        raise _ProtocolError(400, "qualified execution request is invalid")
    for field in ("candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit"):
        if not isinstance(value.get(field), str) or _GIT_OBJECT.fullmatch(value[field]) is None:
            raise _ProtocolError(400, "qualified execution request Git binding is invalid")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or not profiles or not all(isinstance(item, str) and _PROFILE_ID.fullmatch(item) for item in profiles) or len(set(profiles)) != len(profiles):
        raise _ProtocolError(400, "qualified execution request profiles are invalid")
    if any(profile not in config.profiles for profile in profiles):
        raise _ProtocolError(403, "qualified execution profile is not configured")
    if not set(profiles) <= set(client.allowed_profiles):
        raise _ProtocolError(403, "qualified execution profile is not granted to client")
    if value.get("plan_commit") != config.plan_commit:
        raise _ProtocolError(403, "qualified execution Plan is not configured")
    try:
        candidate = str(_git(config.repository, "rev-parse", f"{value['candidate_commit']}^{{commit}}")).strip()
        candidate_tree = str(_git(config.repository, "rev-parse", f"{candidate}^{{tree}}")).strip()
        base = str(_git(config.repository, "rev-parse", f"{value['base_commit']}^{{commit}}")).strip()
        base_tree = str(_git(config.repository, "rev-parse", f"{base}^{{tree}}")).strip()
        subprocess.run(["git", "merge-base", "--is-ancestor", base, candidate], cwd=config.repository, check=True, capture_output=True)
    except (QualifiedExecutionError, subprocess.CalledProcessError) as exc:
        raise _ProtocolError(409, "qualified execution candidate ancestry is invalid") from exc
    if candidate != value["candidate_commit"] or candidate_tree != value["candidate_tree"] or base != value["base_commit"] or base_tree != value["base_tree"]:
        raise _ProtocolError(409, "qualified execution candidate tree binding is invalid")
    return candidate, candidate_tree, base, base_tree, str(value["plan_commit"])


@dataclass(frozen=True)
class _BoundedResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    timed_out: bool
    output_complete: bool


def _run_bounded(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int,
    max_output_bytes: int,
) -> _BoundedResult:
    """Run an allowlisted command, killing on deadline or unretained output.

    A passing proof is emitted only if the complete stdout/stderr body fits the
    configured retention limit.  An overflowing process is killed and can
    retain only a marked prefix, never a passing transcript.
    """

    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        return _BoundedResult(b"", str(exc).encode("utf-8", errors="replace"), 127, False, True)
    assert process.stdout is not None and process.stderr is not None

    def kill_process_group() -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    total, timed_out, overflow = 0, False, False
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                kill_process_group()
            for key, _mask in selector.select(timeout=max(0.0, min(0.1, remaining))):
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = max_output_bytes - total
                if available < len(chunk):
                    output[key.data].extend(chunk[: max(available, 0)])
                    total += max(available, 0)
                    overflow = True
                    kill_process_group()
                else:
                    output[key.data].extend(chunk)
                    total += len(chunk)
        returncode = process.wait(timeout=5)
    finally:
        selector.close()
        if process.poll() is None:
            kill_process_group()
            process.wait()
    return _BoundedResult(bytes(output["stdout"]), bytes(output["stderr"]), returncode, timed_out, not overflow)


class QualifiedExecutionService:
    """Actual execution authority configured outside a candidate repository."""

    def __init__(self, config: QualifiedExecutionConfig, credentials: Mapping[str, str], *, signing_private_key: Ed25519PrivateKey | str | bytes) -> None:
        if set(credentials) != set(config.clients):
            raise QualifiedExecutionConfigurationError("qualified execution credentials are unavailable")
        resolved: list[tuple[str, Client]] = []
        for client_id, client in config.clients.items():
            credential = credentials.get(client_id)
            if not isinstance(credential, str) or not credential or any(hmac.compare_digest(credential, known) for known, _ in resolved):
                raise QualifiedExecutionConfigurationError("qualified execution credentials are invalid")
            resolved.append((credential, client))
        try:
            execution_public_key(signing_private_key)
        except QualifiedExecutionError as exc:
            raise QualifiedExecutionConfigurationError("qualified execution signing key is invalid") from exc
        self.config, self._credentials, self._key = config, tuple(resolved), signing_private_key
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(config.max_active_requests)

    def client_for_authorization(self, authorization: str | None) -> Client | None:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        bearer = authorization.removeprefix("Bearer ")
        for known, client in self._credentials:
            if hmac.compare_digest(bearer, known):
                return client
        return None

    def _runtime(self, worktree: Path) -> tuple[dict[str, str], dict[str, Any]]:
        runtime = self.config.runtime
        environment = dict(runtime.environment)
        # No ambient environment is inherited.  These two paths are service
        # created and are recorded exactly in the signed runtime binding.
        environment["PYTHONPATH"] = str(worktree / "src")
        environment["TGW_LOG_ROOT"] = str(worktree / ".qualified-execution-logs")
        version = _run_bounded([str(runtime.interpreter_path), "--version"], cwd=worktree, environment=environment, timeout_seconds=15, max_output_bytes=16 * 1024)
        if version.returncode != 0 or version.timed_out or not version.output_complete:
            raise QualifiedExecutionError("qualified execution interpreter is unavailable")
        normalized_environment = dict(sorted(environment.items()))
        return normalized_environment, {
            "interpreter_path": str(runtime.interpreter_path),
            "interpreter_sha256": runtime.interpreter_sha256,
            "interpreter_version_sha256": _hash_bytes(version.stdout + b"\0" + version.stderr),
            "dependency_manifest_path": str(runtime.dependency_manifest_path),
            "dependency_manifest_sha256": runtime.dependency_manifest_sha256,
            "environment": normalized_environment,
            "environment_hash": _hash(normalized_environment),
        }

    def _transcript(self, *, client: Client, run_id: str, profile: Profile, candidate: tuple[str, str, str, str, str], command: list[str], result: _BoundedResult) -> dict[str, Any]:
        commit, tree, base, base_tree, plan = candidate
        status = "PASS" if result.returncode == 0 and not result.timed_out and result.output_complete else "FAIL"
        unsigned = {
            "schema": TRANSCRIPT_SCHEMA,
            "service_id": self.config.service_id,
            "client_id": client.client_id,
            "run_id": run_id,
            "profile_id": profile.profile_id,
            "kind": profile.kind,
            "candidate_commit": commit,
            "candidate_tree": tree,
            "base_commit": base,
            "base_tree": base_tree,
            "plan_commit": plan,
            "command": command,
            "stdout_base64": base64.b64encode(result.stdout).decode("ascii"),
            "stderr_base64": base64.b64encode(result.stderr).decode("ascii"),
            "stdout_sha256": _hash_bytes(result.stdout),
            "stderr_sha256": _hash_bytes(result.stderr),
            "output_hash": _hash_bytes(result.stdout + b"\0" + result.stderr),
            "output_complete": result.output_complete,
            "returncode": result.returncode,
            "timed_out": result.timed_out,
            "status": status,
        }
        return {**unsigned, "transcript_hash": _hash(unsigned)}

    def _proof(
        self,
        *,
        client: Client,
        run_id: str,
        profile: Profile,
        candidate: tuple[str, str, str, str, str],
        command: list[str],
        transcript: Mapping[str, Any],
        runtime: Mapping[str, Any],
        inputs: Mapping[str, Any],
        status: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema": PROOF_SCHEMA,
            "service_id": self.config.service_id,
            "client_id": client.client_id,
            "run_id": run_id,
            "profile_id": profile.profile_id,
            "kind": profile.kind,
            "candidate_commit": candidate[0],
            "candidate_tree": candidate[1],
            "base_commit": candidate[2],
            "base_tree": candidate[3],
            "plan_commit": candidate[4],
            "inputs": dict(inputs),
            "runtime": dict(runtime),
            "command": command,
            "transcript_hash": transcript["transcript_hash"],
            "output_hash": transcript["output_hash"],
            "output_complete": transcript["output_complete"],
            "returncode": transcript["returncode"],
            "timed_out": transcript["timed_out"],
            "status": status or transcript["status"],
            "attestation_key_id": self.config.attestation_key_id,
        }
        return issue_execution_proof(payload, signing_private_key=self._key)

    def _execute_test(
        self, client: Client, run_id: str, profile: Profile, candidate: tuple[str, str, str, str, str], worktree: Path, environment: Mapping[str, str], runtime: Mapping[str, Any]
    ) -> dict[str, Any]:
        try:
            test_plan = load_candidate_test_plan(self.config.repository, source_commit=candidate[0])
        except CandidateManifestError as exc:
            raise QualifiedExecutionError("qualified execution canonical test plan is invalid") from exc
        if (
            test_plan["sha256"] != profile.test_plan_sha256
            or test_plan["runner_path"] != profile.test_runner_path
            or test_plan["runner_sha256"] != profile.test_runner_sha256
            or test_plan["commands"][str(profile.scope)] != list(profile.command or ())
        ):
            raise QualifiedExecutionError("qualified execution canonical test plan is not service-approved")
        command = [str(self.config.runtime.interpreter_path), *(profile.command or ())]
        result = _run_bounded(command, cwd=worktree, environment=environment, timeout_seconds=profile.timeout_seconds, max_output_bytes=profile.max_output_bytes)
        transcript = self._transcript(client=client, run_id=run_id, profile=profile, candidate=candidate, command=command, result=result)
        output = create_test_output_artifact(
            scope=str(profile.scope), command=list(profile.command or ()), source_commit=candidate[0], source_tree=candidate[1], stdout=result.stdout, stderr=result.stderr
        )
        receipt = create_test_receipt(
            scope=str(profile.scope),
            command=list(profile.command or ()),
            source_commit=candidate[0],
            source_tree=candidate[1],
            returncode=result.returncode,
            test_plan=test_plan,
            output_artifact=output,
        )
        inputs = {
            "scope": profile.scope,
            "test_plan_path": test_plan["path"],
            "test_plan_sha256": test_plan["sha256"],
            "test_runner_path": test_plan["runner_path"],
            "test_runner_sha256": test_plan["runner_sha256"],
            "test_receipt_hash": receipt["receipt_hash"],
            "test_output_artifact_hash": output["artifact_hash"],
        }
        proof_status = "PASS" if transcript["status"] == "PASS" and receipt["status"] == "PASS" else "FAIL"
        return {
            "proof": self._proof(client=client, run_id=run_id, profile=profile, candidate=candidate, command=command, transcript=transcript, runtime=runtime, inputs=inputs, status=proof_status),
            "transcript": transcript,
            "test_receipt": receipt,
            "test_output": output,
        }

    def _execute_migration(
        self, client: Client, run_id: str, profile: Profile, candidate: tuple[str, str, str, str, str], worktree: Path, environment: Mapping[str, str], runtime: Mapping[str, Any]
    ) -> dict[str, Any]:
        assert profile.runner_path is not None and profile.migration_path is not None and profile.runner_sha256 is not None
        # Never place a service output under candidate-controlled paths: a
        # committed symlink must not redirect the externally configured runner
        # into another filesystem location.
        output_path = worktree.parent / f".qualified-migration-receipt-{run_id}.json"
        command = [str(profile.runner_path), "--repo", str(self.config.repository), "--commit", candidate[0], "--base-commit", candidate[2], "--output", str(output_path)]
        result = _run_bounded(command, cwd=worktree, environment=environment, timeout_seconds=profile.timeout_seconds, max_output_bytes=profile.max_output_bytes)
        transcript = self._transcript(client=client, run_id=run_id, profile=profile, candidate=candidate, command=command, result=result)
        source = bytes(_git(self.config.repository, "show", f"{candidate[0]}:{profile.migration_path}", text=False))
        snapshot = bytes(_git(self.config.repository, "show", f"{candidate[0]}:{profile.schema_snapshot_path}", text=False)) if profile.schema_snapshot_path else None
        receipt: dict[str, Any] | None = None
        if transcript["status"] == "PASS":
            try:
                receipt_value = json.loads(output_path.read_text(encoding="utf-8"))
                receipt = asdict(
                    verify_migration_safety_receipt(
                        receipt_value,
                        candidate_commit=candidate[0],
                        candidate_tree=candidate[1],
                        base_commit=candidate[2],
                        base_tree=candidate[3],
                        migration_paths=(profile.migration_path,),
                        migration_source=source,
                        schema_snapshot_source=snapshot,
                    )
                )
            except (OSError, json.JSONDecodeError, CandidateManifestError):
                transcript = {**transcript, "status": "FAIL"}
                unsigned = {key: item for key, item in transcript.items() if key != "transcript_hash"}
                transcript = {**unsigned, "transcript_hash": _hash(unsigned)}
        inputs = {
            "migration_path": profile.migration_path,
            "migration_sha256": _hash_bytes(source),
            "schema_snapshot_path": profile.schema_snapshot_path,
            "schema_snapshot_sha256": _hash_bytes(snapshot) if snapshot is not None else None,
            "runner_path": str(profile.runner_path),
            "runner_sha256": profile.runner_sha256,
            "migration_receipt_hash": receipt["receipt_hash"] if receipt is not None else None,
        }
        return {
            "proof": self._proof(
                client=client,
                run_id=run_id,
                profile=profile,
                candidate=candidate,
                command=command,
                transcript=transcript,
                runtime=runtime,
                inputs=inputs,
                status="PASS" if receipt is not None and transcript["status"] == "PASS" else "FAIL",
            ),
            "transcript": transcript,
            "migration_receipt": receipt,
        }

    def _execute_review(
        self,
        client: Client,
        run_id: str,
        profile: Profile,
        candidate: tuple[str, str, str, str, str],
        worktree: Path,
        environment: Mapping[str, str],
        runtime: Mapping[str, Any],
        review_packet: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run an operator-pinned review runner over the exact packet bytes.

        The review packet is an input, rather than an authority claim.  Its
        canonical bytes are fed to the configured runner and both those bytes
        and the complete parsed result are bound into the signed proof.  A
        later result cannot therefore be substituted for a valid review run.
        """

        assert profile.runner_path is not None and profile.runner_sha256 is not None
        packet = dict(review_packet)
        packet_bytes = _canonical(packet)
        # A file avoids an unbounded stdin write before the bounded process
        # supervisor begins draining pipes.  The service creates this exact
        # canonical packet; a candidate cannot select a different input path.
        packet_path = worktree.parent / f".tgw-qualified-review-packet-{run_id}.json"
        packet_path.write_bytes(packet_bytes)
        command = [str(profile.runner_path), "--review-packet", str(packet_path)]
        result = _run_bounded(
            command,
            cwd=worktree,
            environment=environment,
            timeout_seconds=profile.timeout_seconds,
            max_output_bytes=profile.max_output_bytes,
        )
        transcript = self._transcript(
            client=client,
            run_id=run_id,
            profile=profile,
            candidate=candidate,
            command=command,
            result=result,
        )
        review_result: dict[str, Any] | None = None
        if transcript["status"] == "PASS":
            try:
                parsed = json.loads(result.stdout.decode("utf-8"))
                if not isinstance(parsed, Mapping):
                    raise ValueError
                review_result = dict(parsed)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                unsigned = {key: item for key, item in transcript.items() if key != "transcript_hash"}
                transcript = {**unsigned, "status": "FAIL", "transcript_hash": _hash({**unsigned, "status": "FAIL"})}
        inputs = {
            "review_packet_content_sha256": _hash_bytes(packet_bytes),
            "review_packet_hash": packet.get("packet_hash"),
            "review_result_content_sha256": _hash(review_result) if review_result is not None else None,
            "review_result_hash": review_result.get("result_hash") if review_result is not None else None,
            "runner_path": str(profile.runner_path),
            "runner_sha256": profile.runner_sha256,
        }
        return {
            "proof": self._proof(
                client=client,
                run_id=run_id,
                profile=profile,
                candidate=candidate,
                command=command,
                transcript=transcript,
                runtime=runtime,
                inputs=inputs,
                status="PASS" if review_result is not None and transcript["status"] == "PASS" else "FAIL",
            ),
            "transcript": transcript,
            "review_packet": packet,
            "review_result": review_result,
        }

    def execute(self, value: Mapping[str, Any], client: Client) -> dict[str, Any]:
        if self.config.clients.get(client.client_id) != client:
            raise _ProtocolError(403, "qualified execution client grant is invalid")
        if not self._slots.acquire(blocking=False):
            raise _ProtocolError(429, "qualified execution request capacity is exhausted")
        try:
            return self._execute(value, client)
        finally:
            self._slots.release()

    def _execute(self, value: Mapping[str, Any], client: Client) -> dict[str, Any]:
        if value.get("client_id") != client.client_id:
            raise _ProtocolError(400, "qualified execution client identity is invalid")
        candidate = _exact_candidate(self.config, value, client)
        requested = list(value["profiles"])
        review_packet = value["review_packet"]
        wants_review = any(self.config.profiles[profile_id].kind == "review" for profile_id in requested)
        if wants_review != isinstance(review_packet, Mapping):
            raise _ProtocolError(400, "qualified execution review packet binding is invalid")
        results: list[dict[str, Any]] = []
        with self._lock, tempfile.TemporaryDirectory(prefix="tgw-qualified-execution-") as temporary:
            try:
                for index, profile_id in enumerate(requested, start=1):
                    # A profile may execute untrusted candidate code.  It gets
                    # a fresh detached tree, never the filesystem observed by
                    # a previous test/migration/review profile.
                    worktree = Path(temporary) / f"candidate-{index}"
                    profile = self.config.profiles[profile_id]
                    try:
                        subprocess.run(
                            ["git", "-C", str(self.config.repository), "worktree", "add", "--detach", str(worktree), candidate[0]],
                            check=True,
                            capture_output=True,
                        )
                        environment, runtime = self._runtime(worktree)
                        run_id = secrets.token_urlsafe(24)
                        if profile.kind == "test":
                            results.append(self._execute_test(client, run_id, profile, candidate, worktree, environment, runtime))
                        elif profile.kind == "migration":
                            results.append(self._execute_migration(client, run_id, profile, candidate, worktree, environment, runtime))
                        else:
                            assert isinstance(review_packet, Mapping)
                            results.append(self._execute_review(client, run_id, profile, candidate, worktree, environment, runtime, review_packet))
                    finally:
                        if worktree.exists():
                            subprocess.run(
                                ["git", "-C", str(self.config.repository), "worktree", "remove", "--force", str(worktree)],
                                check=False,
                                capture_output=True,
                            )
            except (OSError, subprocess.CalledProcessError, QualifiedExecutionError) as exc:
                raise _ProtocolError(409, str(exc)) from exc
        return {
            "schema": RESPONSE_SCHEMA,
            "service_id": self.config.service_id,
            "client_id": client.client_id,
            "candidate_commit": candidate[0],
            "candidate_tree": candidate[1],
            "base_commit": candidate[2],
            "base_tree": candidate[3],
            "plan_commit": candidate[4],
            "results": results,
        }


def create_qualified_execution_server(
    config: QualifiedExecutionConfig, credentials: Mapping[str, str], *, signing_private_key: Ed25519PrivateKey | str | bytes, host: str = "127.0.0.1", port: int = 0
) -> ThreadingHTTPServer:
    if not isinstance(host, str) or not host or not isinstance(port, int) or not 0 <= port <= 65535:
        raise QualifiedExecutionConfigurationError("qualified execution service bind address is invalid")
    state = QualifiedExecutionService(config, credentials, signing_private_key=signing_private_key)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _send(self, status: int, value: Mapping[str, Any]) -> None:
            body = _canonical(value)
            if len(body) > _MAX_RESPONSE_BYTES:
                self.send_error(507, "qualified execution response is too large")
                return
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            client = state.client_for_authorization(self.headers.get("Authorization"))
            if client is None:
                self.send_error(404)
                return
            parsed = urlsplit(self.path)
            if parsed.path != "/v1/proofs" or parsed.query or parsed.fragment or self.headers.get_content_type() != "application/json":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= _MAX_REQUEST_BYTES:
                    raise _ProtocolError(400, "qualified execution request body is invalid")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise _ProtocolError(400, "qualified execution request body is incomplete")
                value = json.loads(raw.decode("utf-8"))
                if not isinstance(value, Mapping):
                    raise _ProtocolError(400, "qualified execution request is invalid")
                self._send(200, state.execute(value, client))
            except (ValueError, json.JSONDecodeError) as exc:
                error = exc if isinstance(exc, _ProtocolError) else _ProtocolError(400, "qualified execution request is invalid")
                self.send_error(error.status, str(error))

    return ThreadingHTTPServer((host, port), Handler)


class QualifiedExecutionClient:
    """Thin client: it submits identities; it never executes or signs a proof."""

    def __init__(self, descriptor: Mapping[str, Any], *, environment: Mapping[str, str] | None = None) -> None:
        normalized = _descriptor(descriptor)
        credential = None
        if normalized["credential_env"] is not None:
            credential = (os.environ if environment is None else environment).get(normalized["credential_env"])
            if not credential:
                raise QualifiedExecutionError("qualified execution service credential is unavailable")
        self._descriptor, self._credential = normalized, credential

    def execute(
        self,
        *,
        candidate_commit: str,
        candidate_tree: str,
        base_commit: str,
        base_tree: str,
        plan_commit: str,
        profiles: Sequence[str],
        review_packet: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema": REQUEST_SCHEMA,
            "service_id": self._descriptor["id"],
            "client_id": self._descriptor["client_id"],
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "base_commit": base_commit,
            "base_tree": base_tree,
            "plan_commit": plan_commit,
            "profiles": list(profiles),
            "review_packet": dict(review_packet) if review_packet is not None else None,
        }
        request = Request(self._descriptor["endpoint"] + "/v1/proofs", data=_canonical(payload), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if self._credential is not None:
            request.add_header("Authorization", f"Bearer {self._credential}")
        try:
            with urlopen(request, timeout=self._descriptor["timeout_seconds"]) as response:  # nosec: descriptor is operator-provisioned
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise QualifiedExecutionError("qualified execution service request failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise QualifiedExecutionError("qualified execution service response is too large")
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualifiedExecutionError("qualified execution service response is invalid") from exc
        if not isinstance(response, Mapping) or response.get("schema") != RESPONSE_SCHEMA:
            raise QualifiedExecutionError("qualified execution service response is invalid")
        return dict(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-qualified-execution-service")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_qualified_execution_config(args.config)
        credentials = {client_id: os.environ.get(client.credential_env, "") for client_id, client in config.clients.items()}
        key = os.environ.get(config.attestation_private_key_env)
        if not key:
            raise QualifiedExecutionConfigurationError("qualified execution signing key is unavailable")
        server = create_qualified_execution_server(config, credentials, signing_private_key=key, host=args.host, port=args.port)
    except (OSError, QualifiedExecutionConfigurationError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=os.sys.stderr)
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0
