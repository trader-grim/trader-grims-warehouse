"""Separated qualified-runner and proof-signer contract.

The signer never starts candidate code.  It resolves immutable Git and Plan
objects, authenticates a separately provisioned confined runner over HTTPS (or
an operator-provisioned loopback test endpoint), validates the runner's fresh
identity and signed result, then signs the retained proof.  A deployment must
provide the runner; this module intentionally contains no local fallback.
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
import socket
import subprocess
import threading
from collections import deque
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

from tgw.candidate_manifest import load_candidate_test_plan, verify_migration_safety_receipt, verify_test_receipt

SERVICE_CONFIG_SCHEMA = "tgw-qualified-execution-signer-config/v2"
SERVICE_DESCRIPTOR_SCHEMA = "tgw-qualified-execution-signer/v2"
SERVICE_CATALOG_SCHEMA = "tgw-qualified-execution-service-catalog/v2"
RUNNER_DESCRIPTOR_SCHEMA = "tgw-qualified-execution-runner/v1"
POLICY_SCHEMA = "tgw-qualified-execution-policy/v1"
RUNNER_IDENTITY_SCHEMA = "tgw-qualified-runner-identity/v1"
TRANSCRIPT_SCHEMA = "tgw-qualified-runner-transcript/v2"
PROOF_SCHEMA = "tgw-qualified-execution-proof/v2"
REQUEST_SCHEMA = "tgw-qualified-execution-request/v2"
RESPONSE_SCHEMA = "tgw-qualified-execution-response/v2"
RUNNER_IDENTITY_REQUEST_SCHEMA = "tgw-qualified-runner-identity-request/v1"
RUNNER_EXECUTE_REQUEST_SCHEMA = "tgw-qualified-runner-execute-request/v1"
RUNNER_RESPONSE_SCHEMA = "tgw-qualified-runner-response/v1"

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT = re.compile(r"[0-9a-f]{40}\Z")
_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,127}\Z")
_SERVICE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_PROFILE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ENV = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_NONCE = re.compile(r"[A-Za-z0-9_-]{24,128}\Z")
_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}\Z")
_MAX_REQUEST_BYTES = 128 * 1024
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_BODY_READ_SECONDS = 10
_CAPABILITIES = frozenset({"candidate-test-execution", "postgresql-migration-execution", "candidate-review-execution"})


class QualifiedExecutionError(ValueError):
    pass


class QualifiedExecutionConfigurationError(ValueError):
    pass


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
    return _hash_bytes(_canonical(value))


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _hash_ok(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _b64(value: Any, *, label: str, length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise QualifiedExecutionError(f"{label} is invalid")
    try:
        raw = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise QualifiedExecutionError(f"{label} is invalid") from exc
    if base64.b64encode(raw).decode() != value or (length is not None and len(raw) != length):
        raise QualifiedExecutionError(f"{label} is invalid")
    return raw


def _private_key(value: Ed25519PrivateKey | str | bytes) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    raw = value if isinstance(value, bytes) else _b64(value, label="qualified execution private key", length=32) if isinstance(value, str) else None
    if raw is None:
        raise QualifiedExecutionError("qualified execution private key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise QualifiedExecutionError("qualified execution private key is invalid") from exc


def execution_public_key(value: Ed25519PrivateKey | Ed25519PublicKey | str | bytes) -> str:
    key = value if isinstance(value, Ed25519PublicKey) else _private_key(value).public_key()
    return base64.b64encode(key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def _public_key(value: Any) -> Ed25519PublicKey:
    try:
        return Ed25519PublicKey.from_public_bytes(_b64(value, label="qualified execution public key", length=32))
    except ValueError as exc:
        raise QualifiedExecutionError("qualified execution public key is invalid") from exc


def _environment(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise QualifiedExecutionError("qualified execution environment is invalid")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or _ENV.fullmatch(key) is None or not isinstance(item, str):
            raise QualifiedExecutionError("qualified execution environment is invalid")
        normalized[key] = item
    return dict(sorted(normalized.items()))


def _relative(value: Any) -> bool:
    return isinstance(value, str) and _PATH.fullmatch(value) is not None and not value.startswith("/") and ".." not in Path(value).parts


def _endpoint(value: Any) -> str:
    parsed = urlsplit(value) if isinstance(value, str) else None
    loopback = parsed is not None and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        parsed is None
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or (parsed.scheme == "http" and not loopback)
    ):
        raise QualifiedExecutionError("qualified execution endpoint is invalid")
    return str(value).rstrip("/")


def _descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"schema", "id", "client_id", "endpoint", "credential_env", "timeout_seconds"}
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != SERVICE_DESCRIPTOR_SCHEMA:
        raise QualifiedExecutionError("qualified execution signer descriptor is invalid")
    if (
        not isinstance(value.get("id"), str)
        or _SERVICE_ID.fullmatch(value["id"]) is None
        or not isinstance(value.get("client_id"), str)
        or _ID.fullmatch(value["client_id"]) is None
        or value.get("credential_env") is not None
        and (not isinstance(value["credential_env"], str) or _ENV.fullmatch(value["credential_env"]) is None)
        or not isinstance(value.get("timeout_seconds"), int)
        or isinstance(value["timeout_seconds"], bool)
        or not 1 <= value["timeout_seconds"] <= 3600
    ):
        raise QualifiedExecutionError("qualified execution signer descriptor is invalid")
    return {
        "schema": SERVICE_DESCRIPTOR_SCHEMA,
        "id": value["id"],
        "client_id": value["client_id"],
        "endpoint": _endpoint(value["endpoint"]),
        "credential_env": value["credential_env"],
        "timeout_seconds": value["timeout_seconds"],
    }


def execution_service_descriptor_hash(value: Mapping[str, Any]) -> str:
    return _hash(_descriptor(value))


def _runner_descriptor(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "id",
        "runner_identity",
        "namespace_id",
        "endpoint",
        "credential_env",
        "timeout_seconds",
        "attestation_key_id",
        "attestation_public_key",
        "isolation_profile_hash",
        "plan_commit",
        "policy_path",
        "policy_artifact_hash",
        "profiles",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != RUNNER_DESCRIPTOR_SCHEMA:
        raise QualifiedExecutionError("qualified runner descriptor is invalid")
    for field in ("id", "runner_identity", "namespace_id", "attestation_key_id"):
        if not isinstance(value.get(field), str) or _ID.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified runner descriptor identity is invalid")
    if (
        not isinstance(value.get("credential_env"), str)
        or _ENV.fullmatch(value["credential_env"]) is None
        or not isinstance(value.get("timeout_seconds"), int)
        or isinstance(value["timeout_seconds"], bool)
        or not 1 <= value["timeout_seconds"] <= 3600
        or not _hash_ok(value.get("isolation_profile_hash"))
        or not isinstance(value.get("plan_commit"), str)
        or _GIT.fullmatch(value["plan_commit"]) is None
        or not _relative(value.get("policy_path"))
        or not _hash_ok(value.get("policy_artifact_hash"))
        or not isinstance(value.get("profiles"), list)
        or not value["profiles"]
        or len(set(value["profiles"])) != len(value["profiles"])
        or not all(isinstance(profile, str) and _PROFILE_ID.fullmatch(profile) is not None for profile in value["profiles"])
    ):
        raise QualifiedExecutionError("qualified runner descriptor is invalid")
    _public_key(value.get("attestation_public_key"))
    return {**dict(value), "endpoint": _endpoint(value["endpoint"]), "profiles": sorted(value["profiles"])}


def qualified_runner_descriptor_hash(value: Mapping[str, Any]) -> str:
    return _hash(_runner_descriptor(value))


def validate_execution_service_catalog(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {"schema", "catalog_ref", "plan_commit", "policy_artifact_hash", "services"}
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schema") != SERVICE_CATALOG_SCHEMA
        or not isinstance(value.get("catalog_ref"), str)
        or not value["catalog_ref"]
        or not isinstance(value.get("plan_commit"), str)
        or _GIT.fullmatch(value["plan_commit"]) is None
        or not _hash_ok(value.get("policy_artifact_hash"))
        or not isinstance(value.get("services"), list)
        or not value["services"]
    ):
        raise QualifiedExecutionError("qualified execution service catalog is invalid")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    entry_fields = {
        "id",
        "client_id",
        "signer_identity",
        "signer_namespace_id",
        "descriptor_hash",
        "runner_descriptor_hash",
        "policy_artifact_hash",
        "capabilities",
        "attestation_key_id",
        "attestation_public_key",
    }
    for entry in value["services"]:
        if not isinstance(entry, Mapping) or set(entry) != entry_fields:
            raise QualifiedExecutionError("qualified execution catalog entry is invalid")
        identity = (entry.get("id"), entry.get("client_id"))
        capabilities = entry.get("capabilities")
        if (
            not isinstance(identity[0], str)
            or _SERVICE_ID.fullmatch(identity[0]) is None
            or not isinstance(identity[1], str)
            or _ID.fullmatch(identity[1]) is None
            or identity in seen
            or not isinstance(entry.get("signer_identity"), str)
            or _ID.fullmatch(entry["signer_identity"]) is None
            or not isinstance(entry.get("signer_namespace_id"), str)
            or _ID.fullmatch(entry["signer_namespace_id"]) is None
            or not all(_hash_ok(entry.get(field)) for field in ("descriptor_hash", "runner_descriptor_hash", "policy_artifact_hash"))
            or entry["policy_artifact_hash"] != value["policy_artifact_hash"]
            or not isinstance(capabilities, list)
            or not capabilities
            or len(set(capabilities)) != len(capabilities)
            or not all(isinstance(item, str) and item in _CAPABILITIES for item in capabilities)
            or not isinstance(entry.get("attestation_key_id"), str)
            or _ID.fullmatch(entry["attestation_key_id"]) is None
        ):
            raise QualifiedExecutionError("qualified execution catalog entry is invalid")
        _public_key(entry.get("attestation_public_key"))
        seen.add(identity)
        normalized.append({**dict(entry), "capabilities": sorted(capabilities)})
    return {"schema": SERVICE_CATALOG_SCHEMA, "catalog_ref": value["catalog_ref"], "plan_commit": value["plan_commit"], "policy_artifact_hash": value["policy_artifact_hash"], "services": normalized}


def execution_service_catalog_hash(value: Mapping[str, Any]) -> str:
    return _hash(validate_execution_service_catalog(value))


def _catalog_entry(catalog: Mapping[str, Any], service_id: str, client_id: str) -> dict[str, Any]:
    normalized = validate_execution_service_catalog(catalog)
    entry = next((item for item in normalized["services"] if (item["id"], item["client_id"]) == (service_id, client_id)), None)
    if entry is None:
        raise QualifiedExecutionError("qualified execution service is absent from catalog")
    return {**entry, "plan_commit": normalized["plan_commit"], "catalog_policy_artifact_hash": normalized["policy_artifact_hash"]}


def _runtime(value: Any) -> dict[str, Any]:
    fields = {
        "runner_path",
        "runner_sha256",
        "interpreter_path",
        "interpreter_sha256",
        "interpreter_version_sha256",
        "dependency_manifest_path",
        "dependency_manifest_sha256",
        "environment",
        "environment_hash",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or not all(isinstance(value.get(field), str) and value[field] for field in ("runner_path", "interpreter_path", "dependency_manifest_path"))
        or not all(_hash_ok(value.get(field)) for field in ("runner_sha256", "interpreter_sha256", "interpreter_version_sha256", "dependency_manifest_sha256", "environment_hash"))
    ):
        raise QualifiedExecutionError("qualified runner runtime is invalid")
    environment = _environment(value.get("environment"))
    if value["environment_hash"] != _hash(environment):
        raise QualifiedExecutionError("qualified runner environment hash is invalid")
    return {**dict(value), "environment": environment}


def _identity_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "runner_id",
        "runner_identity",
        "namespace_id",
        "nonce",
        "plan_commit",
        "policy_path",
        "policy_artifact_hash",
        "isolation_profile_hash",
        "runtime",
        "attestation_key_id",
    }
    if (
        not isinstance(value, Mapping)
        or set(value) != fields
        or value.get("schema") != RUNNER_IDENTITY_SCHEMA
        or not all(isinstance(value.get(field), str) and _ID.fullmatch(value[field]) is not None for field in ("runner_id", "runner_identity", "namespace_id", "attestation_key_id"))
        or not isinstance(value.get("nonce"), str)
        or _NONCE.fullmatch(value["nonce"]) is None
        or not isinstance(value.get("plan_commit"), str)
        or _GIT.fullmatch(value["plan_commit"]) is None
        or not _relative(value.get("policy_path"))
        or not _hash_ok(value.get("policy_artifact_hash"))
        or not _hash_ok(value.get("isolation_profile_hash"))
    ):
        raise QualifiedExecutionError("qualified runner identity is invalid")
    return {**dict(value), "runtime": _runtime(value.get("runtime"))}


def issue_runner_identity(value: Mapping[str, Any], *, signing_private_key: Ed25519PrivateKey | str | bytes) -> dict[str, Any]:
    payload = _identity_payload(value)
    identity_hash = _hash(payload)
    return {**payload, "identity_hash": identity_hash, "signature": base64.b64encode(_private_key(signing_private_key).sign(_canonical({**payload, "identity_hash": identity_hash}))).decode()}


def _validate_runner_identity(value: Mapping[str, Any], *, descriptor: Mapping[str, Any], nonce: str, plan_commit: str, policy_path: str, policy_artifact_hash: str) -> dict[str, Any]:
    desc = _runner_descriptor(descriptor)
    fields = set(
        _identity_payload(
            {
                "schema": RUNNER_IDENTITY_SCHEMA,
                "runner_id": "a",
                "runner_identity": "a",
                "namespace_id": "a",
                "nonce": "a" * 24,
                "plan_commit": "0" * 40,
                "policy_path": "policy.json",
                "policy_artifact_hash": "sha256:" + "0" * 64,
                "isolation_profile_hash": "sha256:" + "0" * 64,
                "runtime": {
                    "runner_path": "x",
                    "runner_sha256": "sha256:" + "0" * 64,
                    "interpreter_path": "x",
                    "interpreter_sha256": "sha256:" + "0" * 64,
                    "interpreter_version_sha256": "sha256:" + "0" * 64,
                    "dependency_manifest_path": "x",
                    "dependency_manifest_sha256": "sha256:" + "0" * 64,
                    "environment": {},
                    "environment_hash": _hash({}),
                },
                "attestation_key_id": "a",
            }
        ).keys()
    ) | {"identity_hash", "signature"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise QualifiedExecutionError("qualified runner identity is invalid")
    payload = _identity_payload({key: item for key, item in value.items() if key not in {"identity_hash", "signature"}})
    if (
        value.get("identity_hash") != _hash(payload)
        or payload["runner_id"] != desc["id"]
        or payload["runner_identity"] != desc["runner_identity"]
        or payload["namespace_id"] != desc["namespace_id"]
        or payload["attestation_key_id"] != desc["attestation_key_id"]
        or payload["nonce"] != nonce
        or payload["plan_commit"] != plan_commit
        or payload["policy_path"] != policy_path
        or payload["policy_artifact_hash"] != policy_artifact_hash
        or payload["isolation_profile_hash"] != desc["isolation_profile_hash"]
    ):
        raise QualifiedExecutionError("qualified runner identity binding is invalid")
    try:
        _public_key(desc["attestation_public_key"]).verify(
            _b64(value.get("signature"), label="qualified runner identity signature", length=64), _canonical({**payload, "identity_hash": value["identity_hash"]})
        )
    except InvalidSignature as exc:
        raise QualifiedExecutionError("qualified runner identity signature is invalid") from exc
    return {**payload, "identity_hash": value["identity_hash"], "signature": value["signature"]}


def _transcript_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "runner_id",
        "runner_identity",
        "namespace_id",
        "runner_identity_hash",
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
        "policy_path",
        "policy_artifact_hash",
        "inputs",
        "runtime",
        "command",
        "stdout_base64",
        "stderr_base64",
        "stdout_sha256",
        "stderr_sha256",
        "output_hash",
        "output_complete",
        "returncode",
        "timed_out",
        "timeout_enforced",
        "output_limit_enforced",
        "runtime_rehashed_before_dispatch",
        "isolated",
        "isolation_profile_hash",
        "status",
        "attestation_key_id",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != TRANSCRIPT_SCHEMA:
        raise QualifiedExecutionError("qualified runner transcript is invalid")
    for field in ("runner_id", "runner_identity", "namespace_id", "service_id", "client_id", "run_id", "profile_id", "attestation_key_id"):
        if not isinstance(value.get(field), str) or _ID.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified runner transcript identity is invalid")
    if (
        value.get("kind") not in {"test", "migration", "review"}
        or not _hash_ok(value.get("runner_identity_hash"))
        or not all(isinstance(value.get(field), str) and _GIT.fullmatch(value[field]) is not None for field in ("candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit"))
        or not _relative(value.get("policy_path"))
        or not _hash_ok(value.get("policy_artifact_hash"))
        or not _hash_ok(value.get("isolation_profile_hash"))
        or not isinstance(value.get("inputs"), Mapping)
    ):
        raise QualifiedExecutionError("qualified runner transcript binding is invalid")
    command = value.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
        raise QualifiedExecutionError("qualified runner transcript command is invalid")
    stdout, stderr = _b64(value.get("stdout_base64"), label="qualified runner stdout"), _b64(value.get("stderr_base64"), label="qualified runner stderr")
    if value.get("stdout_sha256") != _hash_bytes(stdout) or value.get("stderr_sha256") != _hash_bytes(stderr) or value.get("output_hash") != _hash_bytes(stdout + b"\0" + stderr):
        raise QualifiedExecutionError("qualified runner transcript output hash is invalid")
    if (
        not isinstance(value.get("output_complete"), bool)
        or not isinstance(value.get("returncode"), int)
        or isinstance(value["returncode"], bool)
        or not all(isinstance(value.get(field), bool) for field in ("timed_out", "timeout_enforced", "output_limit_enforced", "runtime_rehashed_before_dispatch", "isolated"))
        or value.get("status") not in {"PASS", "FAIL"}
    ):
        raise QualifiedExecutionError("qualified runner transcript status is invalid")
    return {**dict(value), "inputs": dict(value["inputs"]), "runtime": _runtime(value["runtime"])}


def issue_runner_transcript(value: Mapping[str, Any], *, signing_private_key: Ed25519PrivateKey | str | bytes) -> dict[str, Any]:
    payload = _transcript_payload(value)
    runner_result_hash = _hash(payload)
    return {
        **payload,
        "runner_result_hash": runner_result_hash,
        "runner_signature": base64.b64encode(_private_key(signing_private_key).sign(_canonical({**payload, "runner_result_hash": runner_result_hash}))).decode(),
    }


def _validate_runner_transcript(value: Mapping[str, Any], *, descriptor: Mapping[str, Any], identity: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, Any]:
    desc = _runner_descriptor(descriptor)
    required = set(
        _transcript_payload(
            {
                "schema": TRANSCRIPT_SCHEMA,
                "runner_id": "a",
                "runner_identity": "a",
                "namespace_id": "a",
                "runner_identity_hash": "sha256:" + "0" * 64,
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
                "policy_path": "policy.json",
                "policy_artifact_hash": "sha256:" + "0" * 64,
                "inputs": {},
                "runtime": identity["runtime"],
                "command": ["x"],
                "stdout_base64": "",
                "stderr_base64": "",
                "stdout_sha256": _hash_bytes(b""),
                "stderr_sha256": _hash_bytes(b""),
                "output_hash": _hash_bytes(b"\0"),
                "output_complete": True,
                "returncode": 0,
                "timed_out": False,
                "timeout_enforced": True,
                "output_limit_enforced": True,
                "runtime_rehashed_before_dispatch": True,
                "isolated": True,
                "isolation_profile_hash": desc["isolation_profile_hash"],
                "status": "PASS",
                "attestation_key_id": desc["attestation_key_id"],
            }
        ).keys()
    ) | {"runner_result_hash", "runner_signature"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise QualifiedExecutionError("qualified runner transcript is invalid")
    payload = _transcript_payload({key: item for key, item in value.items() if key not in {"runner_result_hash", "runner_signature"}})
    if (
        value.get("runner_result_hash") != _hash(payload)
        or payload["runner_id"] != desc["id"]
        or payload["runner_identity"] != desc["runner_identity"]
        or payload["namespace_id"] != desc["namespace_id"]
        or payload["runner_identity_hash"] != identity["identity_hash"]
        or payload["attestation_key_id"] != desc["attestation_key_id"]
        or payload["isolation_profile_hash"] != desc["isolation_profile_hash"]
        or payload["runtime"] != identity["runtime"]
    ):
        raise QualifiedExecutionError("qualified runner transcript binding is invalid")
    for field, item in expected.items():
        if payload.get(field) != item:
            raise QualifiedExecutionError("qualified runner transcript expected binding mismatch")
    try:
        _public_key(desc["attestation_public_key"]).verify(
            _b64(value.get("runner_signature"), label="qualified runner transcript signature", length=64), _canonical({**payload, "runner_result_hash": value["runner_result_hash"]})
        )
    except InvalidSignature as exc:
        raise QualifiedExecutionError("qualified runner transcript signature is invalid") from exc
    if payload["status"] == "PASS" and (
        not payload["isolated"]
        or not payload["timeout_enforced"]
        or not payload["output_limit_enforced"]
        or not payload["runtime_rehashed_before_dispatch"]
        or not payload["output_complete"]
        or payload["timed_out"]
        or payload["returncode"] != 0
    ):
        raise QualifiedExecutionError("qualified runner PASS confinement/deadline attestation is invalid")
    return {**payload, "runner_result_hash": value["runner_result_hash"], "runner_signature": value["runner_signature"]}


def _proof_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "schema",
        "service_id",
        "signer_identity",
        "signer_namespace_id",
        "signer_descriptor_hash",
        "client_id",
        "run_id",
        "profile_id",
        "kind",
        "candidate_commit",
        "candidate_tree",
        "base_commit",
        "base_tree",
        "plan_commit",
        "policy_path",
        "policy_artifact_hash",
        "runner_id",
        "runner_identity",
        "namespace_id",
        "runner_descriptor_hash",
        "runner_identity_hash",
        "runner_result_hash",
        "inputs",
        "runtime",
        "command",
        "output_hash",
        "output_complete",
        "returncode",
        "timed_out",
        "status",
        "attestation_key_id",
    }
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != PROOF_SCHEMA:
        raise QualifiedExecutionError("qualified execution proof is invalid")
    for field in (
        "service_id",
        "signer_identity",
        "signer_namespace_id",
        "client_id",
        "run_id",
        "profile_id",
        "runner_id",
        "runner_identity",
        "namespace_id",
        "attestation_key_id",
    ):
        if not isinstance(value.get(field), str) or _ID.fullmatch(value[field]) is None:
            raise QualifiedExecutionError("qualified execution proof identity is invalid")
    if (
        value.get("kind") not in {"test", "migration", "review"}
        or not all(isinstance(value.get(field), str) and _GIT.fullmatch(value[field]) is not None for field in ("candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit"))
        or not _relative(value.get("policy_path"))
        or not all(
            _hash_ok(value.get(field))
            for field in (
                "signer_descriptor_hash",
                "policy_artifact_hash",
                "runner_descriptor_hash",
                "runner_identity_hash",
                "runner_result_hash",
                "output_hash",
            )
        )
        or not isinstance(value.get("inputs"), Mapping)
        or not isinstance(value.get("command"), list)
        or not value["command"]
        or not all(isinstance(item, str) and item for item in value["command"])
        or not isinstance(value.get("output_complete"), bool)
        or not isinstance(value.get("returncode"), int)
        or isinstance(value["returncode"], bool)
        or not isinstance(value.get("timed_out"), bool)
        or value.get("status") not in {"PASS", "FAIL"}
    ):
        raise QualifiedExecutionError("qualified execution proof is invalid")
    return {**dict(value), "inputs": dict(value["inputs"]), "runtime": _runtime(value.get("runtime"))}


def issue_execution_proof(value: Mapping[str, Any], *, signing_private_key: Ed25519PrivateKey | str | bytes) -> dict[str, Any]:
    payload = _proof_payload(value)
    proof_hash = _hash(payload)
    return {**payload, "proof_hash": proof_hash, "signature": base64.b64encode(_private_key(signing_private_key).sign(_canonical({**payload, "proof_hash": proof_hash}))).decode()}


def validate_execution_proof(
    proof: Mapping[str, Any], transcript: Mapping[str, Any], *, catalog: Mapping[str, Any], runner_descriptor: Mapping[str, Any], expected: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    # Verify signer catalog pin, runner descriptor pin, and independently signed
    # runner transcript.  The signer signature alone is deliberately not enough.
    if not isinstance(proof, Mapping) or "proof_hash" not in proof or "signature" not in proof:
        raise QualifiedExecutionError("qualified execution proof is invalid")
    payload = _proof_payload({key: item for key, item in proof.items() if key not in {"proof_hash", "signature"}})
    if set(proof) != set(payload) | {"proof_hash", "signature"} or proof.get("proof_hash") != _hash(payload):
        raise QualifiedExecutionError("qualified execution proof hash is invalid")
    entry = _catalog_entry(catalog, payload["service_id"], payload["client_id"])
    runner = _runner_descriptor(runner_descriptor)
    if (
        entry["signer_identity"] != payload["signer_identity"]
        or entry["signer_namespace_id"] != payload["signer_namespace_id"]
        or entry["descriptor_hash"] != payload["signer_descriptor_hash"]
        or entry["attestation_key_id"] != payload["attestation_key_id"]
        or entry["plan_commit"] != payload["plan_commit"]
        or entry["catalog_policy_artifact_hash"] != payload["policy_artifact_hash"]
        or entry["runner_descriptor_hash"] != payload["runner_descriptor_hash"]
        or qualified_runner_descriptor_hash(runner) != payload["runner_descriptor_hash"]
        or runner["plan_commit"] != payload["plan_commit"]
        or runner["policy_path"] != payload["policy_path"]
        or runner["policy_artifact_hash"] != payload["policy_artifact_hash"]
        or payload["profile_id"] not in runner["profiles"]
    ):
        raise QualifiedExecutionError("qualified execution catalog/runner binding is invalid")
    try:
        _public_key(entry["attestation_public_key"]).verify(
            _b64(proof.get("signature"), label="qualified execution proof signature", length=64), _canonical({**payload, "proof_hash": proof["proof_hash"]})
        )
    except InvalidSignature as exc:
        raise QualifiedExecutionError("qualified execution proof signature is invalid") from exc
    runner_result = _validate_runner_transcript(
        transcript,
        descriptor=runner_descriptor,
        identity={"runtime": payload["runtime"], "identity_hash": payload["runner_identity_hash"]},
        expected={
            field: payload[field]
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
                "policy_path",
                "policy_artifact_hash",
                "runner_identity_hash",
                "inputs",
                "command",
                "output_hash",
                "output_complete",
                "returncode",
                "timed_out",
                "status",
            )
        },
    )
    if (
        runner_result["runner_result_hash"] != payload["runner_result_hash"]
        or runner_result["runner_identity_hash"] != payload["runner_identity_hash"]
        or runner_result["runner_id"] != payload["runner_id"]
        or runner_result["runner_identity"] != payload["runner_identity"]
        or runner_result["namespace_id"] != payload["namespace_id"]
    ):
        raise QualifiedExecutionError("qualified execution proof transcript binding mismatch")
    if payload["status"] == "PASS" and (not payload["output_complete"] or payload["timed_out"] or payload["returncode"] != 0):
        raise QualifiedExecutionError("qualified execution proof PASS status is invalid")
    if expected is not None:
        for field, item in expected.items():
            if payload.get(field) != item:
                raise QualifiedExecutionError("qualified execution proof expected binding mismatch")
    return {**payload, "proof_hash": proof["proof_hash"], "signature": proof["signature"]}


@dataclass(frozen=True)
class Client:
    client_id: str
    credential_env: str
    descriptor_hash: str
    profiles: tuple[str, ...]


@dataclass(frozen=True)
class Policy:
    path: str
    artifact_hash: str
    runtime: Mapping[str, Any]
    profiles: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class QualifiedExecutionConfig:
    service_id: str
    signer_identity: str
    signer_namespace_id: str
    repository: Path
    plan_repository: Path
    plan_commit: str
    policy: Policy
    runner: Mapping[str, Any]
    clients: Mapping[str, Client]
    max_active_requests: int
    max_retained_proofs_per_client: int
    attestation_key_id: str
    attestation_private_key_env: str

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "QualifiedExecutionConfig":
        fields = {
            "schema",
            "service_id",
            "signer_identity",
            "signer_namespace_id",
            "repository",
            "plan_repository",
            "plan_commit",
            "policy_path",
            "policy_artifact_hash",
            "runner",
            "clients",
            "max_active_requests",
            "max_retained_proofs_per_client",
            "attestation_key_id",
            "attestation_private_key_env",
        }
        if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != SERVICE_CONFIG_SCHEMA:
            raise QualifiedExecutionConfigurationError("qualified execution signer configuration is invalid")
        for field in ("signer_identity", "signer_namespace_id", "attestation_key_id"):
            if not isinstance(value.get(field), str) or _ID.fullmatch(value[field]) is None:
                raise QualifiedExecutionConfigurationError("qualified execution signer identity is invalid")
        if not isinstance(value.get("service_id"), str) or _SERVICE_ID.fullmatch(value["service_id"]) is None:
            raise QualifiedExecutionConfigurationError("qualified execution signer identity is invalid")
        if (
            not isinstance(value.get("repository"), str)
            or not isinstance(value.get("plan_repository"), str)
            or not isinstance(value.get("plan_commit"), str)
            or _GIT.fullmatch(value["plan_commit"]) is None
            or not _relative(value.get("policy_path"))
            or not _hash_ok(value.get("policy_artifact_hash"))
            or not isinstance(value.get("attestation_private_key_env"), str)
            or _ENV.fullmatch(value["attestation_private_key_env"]) is None
            or not isinstance(value.get("max_active_requests"), int)
            or isinstance(value["max_active_requests"], bool)
            or not 1 <= value["max_active_requests"] <= 64
            or not isinstance(value.get("max_retained_proofs_per_client"), int)
            or isinstance(value["max_retained_proofs_per_client"], bool)
            or not 1 <= value["max_retained_proofs_per_client"] <= 256
        ):
            raise QualifiedExecutionConfigurationError("qualified execution signer configuration is invalid")
        try:
            repository, plans = Path(value["repository"]).resolve(strict=True), Path(value["plan_repository"]).resolve(strict=True)
            if not repository.is_dir() or not plans.is_dir() or repository == plans or repository.is_relative_to(plans) or plans.is_relative_to(repository):
                raise OSError
            subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repository, check=True, capture_output=True)
            if _git(plans, "rev-parse", f"{value['plan_commit']}^{{commit}}") != value["plan_commit"]:
                raise OSError
            policy_blob = _git_bytes(plans, "show", f"{value['plan_commit']}:{value['policy_path']}")
        except (OSError, QualifiedExecutionError) as exc:
            raise QualifiedExecutionConfigurationError("qualified execution signer trust roots are unavailable") from exc
        if _hash_bytes(policy_blob) != value["policy_artifact_hash"]:
            raise QualifiedExecutionConfigurationError("qualified execution Plan policy artifact hash is invalid")
        policy = _policy(policy_blob, path=value["policy_path"], artifact_hash=value["policy_artifact_hash"])
        try:
            runner = _runner_descriptor(value.get("runner"))
        except QualifiedExecutionError as exc:
            raise QualifiedExecutionConfigurationError("qualified execution runner trust descriptor is invalid") from exc
        if runner["runner_identity"] in {value["signer_identity"], value["signer_namespace_id"]} or runner["namespace_id"] in {
            value["signer_identity"],
            value["signer_namespace_id"],
        }:
            raise QualifiedExecutionConfigurationError("qualified runner must not share signer identity or namespace")
        if runner["plan_commit"] != value["plan_commit"] or runner["policy_path"] != value["policy_path"] or runner["policy_artifact_hash"] != value["policy_artifact_hash"]:
            raise QualifiedExecutionConfigurationError("qualified runner does not pin the configured Plan policy")
        if any(profile not in policy.profiles for profile in runner["profiles"]):
            raise QualifiedExecutionConfigurationError("qualified runner declares an unknown Plan profile")
        clients: dict[str, Client] = {}
        credentials: set[str] = set()
        if not isinstance(value.get("clients"), list) or not value["clients"]:
            raise QualifiedExecutionConfigurationError("qualified execution clients are invalid")
        for item in value["clients"]:
            if (
                not isinstance(item, Mapping)
                or set(item) != {"id", "credential_env", "descriptor_hash", "profiles"}
                or not isinstance(item.get("id"), str)
                or _ID.fullmatch(item["id"]) is None
                or item["id"] in clients
                or not isinstance(item.get("credential_env"), str)
                or _ENV.fullmatch(item["credential_env"]) is None
                or item["credential_env"] in credentials
                or not _hash_ok(item.get("descriptor_hash"))
                or not isinstance(item.get("profiles"), list)
                or not item["profiles"]
                or len(set(item["profiles"])) != len(item["profiles"])
                or not all(isinstance(profile, str) and profile in policy.profiles for profile in item["profiles"])
                or not all(profile in runner["profiles"] for profile in item["profiles"])
            ):
                raise QualifiedExecutionConfigurationError("qualified execution client grant is invalid")
            clients[item["id"]] = Client(item["id"], item["credential_env"], item["descriptor_hash"], tuple(item["profiles"]))
            credentials.add(item["credential_env"])
        return cls(
            value["service_id"],
            value["signer_identity"],
            value["signer_namespace_id"],
            repository,
            plans,
            value["plan_commit"],
            policy,
            runner,
            clients,
            value["max_active_requests"],
            value["max_retained_proofs_per_client"],
            value["attestation_key_id"],
            value["attestation_private_key_env"],
        )


def _policy(source: bytes, *, path: str, artifact_hash: str) -> Policy:
    try:
        value = json.loads(source)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise QualifiedExecutionConfigurationError("qualified execution Plan policy is unreadable") from exc
    fields = {"schema", "policy_id", "runtime", "profiles"}
    if not isinstance(value, Mapping) or set(value) != fields or value.get("schema") != POLICY_SCHEMA or not isinstance(value.get("policy_id"), str) or _ID.fullmatch(value["policy_id"]) is None:
        raise QualifiedExecutionConfigurationError("qualified execution Plan policy is invalid")
    runtime = _runtime(value.get("runtime"))
    profiles_value = value.get("profiles")
    profiles: dict[str, Mapping[str, Any]] = {}
    if not isinstance(profiles_value, list) or not profiles_value:
        raise QualifiedExecutionConfigurationError("qualified execution policy profiles are invalid")
    for profile in profiles_value:
        if (
            not isinstance(profile, Mapping)
            or not isinstance(profile.get("id"), str)
            or _PROFILE_ID.fullmatch(profile["id"]) is None
            or profile["id"] in profiles
            or profile.get("kind") not in {"test", "migration", "review"}
        ):
            raise QualifiedExecutionConfigurationError("qualified execution policy profile is invalid")
        common = {"id", "kind", "timeout_seconds", "max_output_bytes"}
        if (
            not isinstance(profile.get("timeout_seconds"), int)
            or isinstance(profile["timeout_seconds"], bool)
            or not 1 <= profile["timeout_seconds"] <= 3600
            or not isinstance(profile.get("max_output_bytes"), int)
            or isinstance(profile["max_output_bytes"], bool)
            or not 1 <= profile["max_output_bytes"] <= _MAX_RESPONSE_BYTES // 2
        ):
            raise QualifiedExecutionConfigurationError("qualified execution policy profile limits are invalid")
        if profile["kind"] == "test":
            needed = common | {"scope", "test_plan_sha256", "test_runner_path", "test_runner_sha256", "command"}
            valid = (
                profile.get("scope") in {"focused", "full"}
                and _hash_ok(profile.get("test_plan_sha256"))
                and _relative(profile.get("test_runner_path"))
                and _hash_ok(profile.get("test_runner_sha256"))
                and isinstance(profile.get("command"), list)
                and profile["command"]
                and all(isinstance(item, str) and item for item in profile["command"])
            )
        elif profile["kind"] == "migration":
            needed = common | {"migration_path", "schema_snapshot_path", "runner_path", "runner_sha256"}
            valid = (
                _relative(profile.get("migration_path"))
                and (profile.get("schema_snapshot_path") is None or _relative(profile["schema_snapshot_path"]))
                and isinstance(profile.get("runner_path"), str)
                and profile["runner_path"].startswith("/")
                and _hash_ok(profile.get("runner_sha256"))
            )
        else:
            needed = common | {"runner_path", "runner_sha256"}
            valid = isinstance(profile.get("runner_path"), str) and profile["runner_path"].startswith("/") and _hash_ok(profile.get("runner_sha256"))
        if set(profile) != needed or not valid:
            raise QualifiedExecutionConfigurationError("qualified execution policy profile is invalid")
        profiles[profile["id"]] = dict(profile)
    return Policy(path, artifact_hash, runtime, profiles)


def load_qualified_execution_config(path: str | os.PathLike[str]) -> QualifiedExecutionConfig:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise QualifiedExecutionConfigurationError("qualified execution signer configuration is unreadable") from exc
    return QualifiedExecutionConfig.parse(value)


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QualifiedExecutionError("qualified execution Git object is unavailable") from exc


def _git_bytes(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise QualifiedExecutionError("qualified execution Git object is unavailable") from exc


class _RunnerClient:
    def __init__(self, descriptor: Mapping[str, Any], *, environment: Mapping[str, str] | None = None) -> None:
        self.descriptor = _runner_descriptor(descriptor)
        token = (os.environ if environment is None else environment).get(self.descriptor["credential_env"])
        if not token:
            raise QualifiedExecutionConfigurationError("qualified runner credential is unavailable")
        self.token = token

    def _post(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = Request(self.descriptor["endpoint"] + path, data=_canonical(payload), method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(request, timeout=self.descriptor["timeout_seconds"]) as response:  # nosec: externally provisioned runner descriptor
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise QualifiedExecutionError("qualified runner request failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise QualifiedExecutionError("qualified runner response is too large")
        try:
            value = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualifiedExecutionError("qualified runner response is invalid") from exc
        if not isinstance(value, Mapping):
            raise QualifiedExecutionError("qualified runner response is invalid")
        return dict(value)

    def identity(self, *, signer_identity: str, plan_commit: str, policy_path: str, policy_artifact_hash: str) -> dict[str, Any]:
        nonce = secrets.token_urlsafe(24)
        value = self._post(
            "/v1/identity",
            {
                "schema": RUNNER_IDENTITY_REQUEST_SCHEMA,
                "signer_identity": signer_identity,
                "nonce": nonce,
                "plan_commit": plan_commit,
                "policy_path": policy_path,
                "policy_artifact_hash": policy_artifact_hash,
            },
        )
        return _validate_runner_identity(
            value,
            descriptor=self.descriptor,
            nonce=nonce,
            plan_commit=plan_commit,
            policy_path=policy_path,
            policy_artifact_hash=policy_artifact_hash,
        )

    def execute(self, value: Mapping[str, Any]) -> dict[str, Any]:
        response = self._post("/v1/execute", value)
        if (
            response.get("schema") != RUNNER_RESPONSE_SCHEMA
            or set(response) != {"schema", "runner_id", "results"}
            or response.get("runner_id") != self.descriptor["id"]
            or not isinstance(response.get("results"), list)
        ):
            raise QualifiedExecutionError("qualified runner response is invalid")
        return response


class QualifiedExecutionService:
    """Signer authority; candidate workloads run only in the external runner."""

    def __init__(self, config: QualifiedExecutionConfig, credentials: Mapping[str, str], *, signing_private_key: Ed25519PrivateKey | str | bytes, environment: Mapping[str, str] | None = None) -> None:
        if set(credentials) != set(config.clients):
            raise QualifiedExecutionConfigurationError("qualified execution client credentials are unavailable")
        pairs: list[tuple[str, Client]] = []
        for client_id, client in config.clients.items():
            token = credentials.get(client_id)
            if not isinstance(token, str) or not token or any(hmac.compare_digest(token, known) for known, _client in pairs):
                raise QualifiedExecutionConfigurationError("qualified execution client credentials are invalid")
            pairs.append((token, client))
        _private_key(signing_private_key)
        self.config, self._credentials, self._key = config, tuple(pairs), signing_private_key
        self._runner = _RunnerClient(config.runner, environment=environment)
        self._slots = threading.BoundedSemaphore(config.max_active_requests)
        self._completed = {client_id: deque(maxlen=config.max_retained_proofs_per_client) for client_id in config.clients}
        self._completed_lock = threading.Lock()

    def client_for_authorization(self, authorization: str | None) -> Client | None:
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        token = authorization.removeprefix("Bearer ")
        return next((client for known, client in self._credentials if hmac.compare_digest(token, known)), None)

    def acquire_slot(self) -> bool:
        return self._slots.acquire(blocking=False)

    def release_slot(self) -> None:
        self._slots.release()

    def _retain(self, client_id: str, results: Sequence[Mapping[str, Any]]) -> None:
        """Keep bounded proof/transcript readback per credential-bound client."""

        with self._completed_lock:
            retained = self._completed[client_id]
            for result in results:
                retained.append(
                    {
                        "proof": dict(result["proof"]),
                        "transcript": dict(result["transcript"]),
                    }
                )

    def retained_proof(self, client: Client, run_id: str, profile_id: str) -> dict[str, Any] | None:
        if self.config.clients.get(client.client_id) != client or _ID.fullmatch(run_id) is None or _PROFILE_ID.fullmatch(profile_id) is None:
            return None
        with self._completed_lock:
            for retained in reversed(self._completed[client.client_id]):
                proof = retained["proof"]
                if proof["run_id"] == run_id and proof["profile_id"] == profile_id:
                    return {"proof": dict(proof), "transcript": dict(retained["transcript"])}
        return None

    def _candidate(self, value: Mapping[str, Any], client: Client) -> tuple[str, str, str, str]:
        fields = {"schema", "service_id", "client_id", "candidate_commit", "candidate_tree", "base_commit", "base_tree", "plan_commit", "profiles", "review_packet"}
        if (
            not isinstance(value, Mapping)
            or set(value) != fields
            or value.get("schema") != REQUEST_SCHEMA
            or value.get("service_id") != self.config.service_id
            or value.get("client_id") != client.client_id
        ):
            raise _ProtocolError(400, "qualified execution request is invalid")
        if value.get("plan_commit") != self.config.plan_commit:
            raise _ProtocolError(403, "qualified execution Plan is not configured")
        profiles = value.get("profiles")
        if not isinstance(profiles, list) or not profiles or len(set(profiles)) != len(profiles) or not all(isinstance(item, str) and item in client.profiles for item in profiles):
            raise _ProtocolError(403, "qualified execution profile is not granted to client")
        try:
            candidate = _git(self.config.repository, "rev-parse", f"{value['candidate_commit']}^{{commit}}")
            tree = _git(self.config.repository, "rev-parse", f"{candidate}^{{tree}}")
            base = _git(self.config.repository, "rev-parse", f"{value['base_commit']}^{{commit}}")
            base_tree = _git(self.config.repository, "rev-parse", f"{base}^{{tree}}")
            subprocess.run(["git", "merge-base", "--is-ancestor", base, candidate], cwd=self.config.repository, check=True, capture_output=True)
        except (QualifiedExecutionError, subprocess.CalledProcessError) as exc:
            raise _ProtocolError(409, "qualified execution candidate ancestry is invalid") from exc
        if (candidate, tree, base, base_tree) != (value["candidate_commit"], value["candidate_tree"], value["base_commit"], value["base_tree"]):
            raise _ProtocolError(409, "qualified execution candidate tree binding is invalid")
        wants_review = any(self.config.policy.profiles[item]["kind"] == "review" for item in profiles)
        if wants_review != isinstance(value["review_packet"], Mapping):
            raise _ProtocolError(400, "qualified execution review packet binding is invalid")
        return candidate, tree, base, base_tree

    def _validate_artifacts(self, package: Mapping[str, Any], transcript: Mapping[str, Any], candidate: tuple[str, str, str, str]) -> dict[str, Any]:
        profile = self.config.policy.profiles[transcript["profile_id"]]
        result = dict(package)
        if transcript["kind"] == "test":
            if set(package) != {"transcript", "test_receipt", "test_output"}:
                raise QualifiedExecutionError("qualified runner test package is invalid")
            plan = load_candidate_test_plan(self.config.repository, source_commit=candidate[0])
            if (
                plan["sha256"] != profile["test_plan_sha256"]
                or plan["runner_path"] != profile["test_runner_path"]
                or plan["runner_sha256"] != profile["test_runner_sha256"]
                or plan["commands"][profile["scope"]] != profile["command"]
            ):
                raise QualifiedExecutionError("qualified runner test policy/candidate binding is invalid")
            verify_test_receipt(package["test_receipt"], scope=profile["scope"], source_commit=candidate[0], source_tree=candidate[1], test_plan=plan, output_artifact=package["test_output"])
        elif transcript["kind"] == "migration":
            if set(package) != {"transcript", "migration_receipt"}:
                raise QualifiedExecutionError("qualified runner migration package is invalid")
            source = _git_bytes(self.config.repository, "show", f"{candidate[0]}:{profile['migration_path']}")
            snapshot = _git_bytes(self.config.repository, "show", f"{candidate[0]}:{profile['schema_snapshot_path']}") if profile["schema_snapshot_path"] else None
            result["migration_receipt"] = asdict(
                verify_migration_safety_receipt(
                    package["migration_receipt"],
                    candidate_commit=candidate[0],
                    candidate_tree=candidate[1],
                    base_commit=candidate[2],
                    base_tree=candidate[3],
                    migration_paths=(profile["migration_path"],),
                    migration_source=source,
                    schema_snapshot_source=snapshot,
                )
            )
        else:
            if set(package) != {"transcript", "review_packet", "review_result"} or package["review_packet"] is None or package["review_result"] is None:
                raise QualifiedExecutionError("qualified runner review package is invalid")
        return result

    def execute(self, value: Mapping[str, Any], client: Client, *, reserved: bool = False) -> dict[str, Any]:
        acquired = reserved or self.acquire_slot()
        if not acquired:
            raise _ProtocolError(429, "qualified execution request capacity is exhausted")
        try:
            if self.config.clients.get(client.client_id) != client:
                raise _ProtocolError(403, "qualified execution client grant is invalid")
            candidate = self._candidate(value, client)
            # Fresh signed identity is requested immediately before dispatch;
            # the confined runner is responsible for rehashing these executable
            # identities in that identity response and again in its result.
            identity = self._runner.identity(
                signer_identity=self.config.signer_identity,
                plan_commit=self.config.plan_commit,
                policy_path=self.config.policy.path,
                policy_artifact_hash=self.config.policy.artifact_hash,
            )
            if identity["runtime"] != self.config.policy.runtime:
                raise QualifiedExecutionError("qualified runner runtime changed after policy load")
            request = {
                "schema": RUNNER_EXECUTE_REQUEST_SCHEMA,
                "service_id": self.config.service_id,
                "signer_identity": self.config.signer_identity,
                "client_id": client.client_id,
                "runner_id": self.config.runner["id"],
                "runner_descriptor_hash": qualified_runner_descriptor_hash(self.config.runner),
                "runner_identity_hash": identity["identity_hash"],
                "plan_commit": self.config.plan_commit,
                "policy_path": self.config.policy.path,
                "policy_artifact_hash": self.config.policy.artifact_hash,
                "candidate_commit": candidate[0],
                "candidate_tree": candidate[1],
                "base_commit": candidate[2],
                "base_tree": candidate[3],
                "profiles": list(value["profiles"]),
                "review_packet": dict(value["review_packet"]) if value["review_packet"] is not None else None,
            }
            response = self._runner.execute(request)
            if len(response["results"]) != len(value["profiles"]):
                raise QualifiedExecutionError("qualified runner response coverage is invalid")
            results: list[dict[str, Any]] = []
            for profile_id, package in zip(value["profiles"], response["results"], strict=True):
                if not isinstance(package, Mapping) or not isinstance(package.get("transcript"), Mapping):
                    raise QualifiedExecutionError("qualified runner response package is invalid")
                profile = self.config.policy.profiles[profile_id]
                expected = {
                    "runner_id": self.config.runner["id"],
                    "runner_identity": self.config.runner["runner_identity"],
                    "namespace_id": self.config.runner["namespace_id"],
                    "service_id": self.config.service_id,
                    "client_id": client.client_id,
                    "profile_id": profile_id,
                    "kind": profile["kind"],
                    "candidate_commit": candidate[0],
                    "candidate_tree": candidate[1],
                    "base_commit": candidate[2],
                    "base_tree": candidate[3],
                    "plan_commit": self.config.plan_commit,
                    "policy_path": self.config.policy.path,
                    "policy_artifact_hash": self.config.policy.artifact_hash,
                }
                if profile["kind"] == "test":
                    expected["command"] = [self.config.policy.runtime["interpreter_path"], *profile["command"]]
                transcript = _validate_runner_transcript(package["transcript"], descriptor=self.config.runner, identity=identity, expected=expected)
                package = self._validate_artifacts(package, transcript, candidate)
                proof = issue_execution_proof(
                    {
                        "schema": PROOF_SCHEMA,
                        "service_id": self.config.service_id,
                        "signer_identity": self.config.signer_identity,
                        "signer_namespace_id": self.config.signer_namespace_id,
                        "signer_descriptor_hash": client.descriptor_hash,
                        "client_id": client.client_id,
                        "run_id": transcript["run_id"],
                        "profile_id": profile_id,
                        "kind": profile["kind"],
                        "candidate_commit": candidate[0],
                        "candidate_tree": candidate[1],
                        "base_commit": candidate[2],
                        "base_tree": candidate[3],
                        "plan_commit": self.config.plan_commit,
                        "policy_path": self.config.policy.path,
                        "policy_artifact_hash": self.config.policy.artifact_hash,
                        "runner_id": transcript["runner_id"],
                        "runner_identity": transcript["runner_identity"],
                        "namespace_id": transcript["namespace_id"],
                        "runner_descriptor_hash": qualified_runner_descriptor_hash(self.config.runner),
                        "runner_identity_hash": transcript["runner_identity_hash"],
                        "runner_result_hash": transcript["runner_result_hash"],
                        "inputs": transcript["inputs"],
                        "runtime": transcript["runtime"],
                        "command": transcript["command"],
                        "output_hash": transcript["output_hash"],
                        "output_complete": transcript["output_complete"],
                        "returncode": transcript["returncode"],
                        "timed_out": transcript["timed_out"],
                        "status": transcript["status"],
                        "attestation_key_id": self.config.attestation_key_id,
                    },
                    signing_private_key=self._key,
                )
                results.append({**package, "proof": proof, "transcript": transcript})
            response = {
                "schema": RESPONSE_SCHEMA,
                "service_id": self.config.service_id,
                "client_id": client.client_id,
                "candidate_commit": candidate[0],
                "candidate_tree": candidate[1],
                "base_commit": candidate[2],
                "base_tree": candidate[3],
                "plan_commit": self.config.plan_commit,
                "policy_artifact_hash": self.config.policy.artifact_hash,
                "runner_descriptor_hash": qualified_runner_descriptor_hash(self.config.runner),
                "results": results,
            }
            # Results are retained only once the complete response structure is
            # available to the caller; per-client deques prevent one client
            # from consuming another client's retained proof capacity.
            self._retain(client.client_id, results)
            return response
        except QualifiedExecutionError as exc:
            raise _ProtocolError(409, str(exc)) from exc
        finally:
            if acquired:
                self.release_slot()


def create_qualified_execution_server(
    config: QualifiedExecutionConfig,
    credentials: Mapping[str, str],
    *,
    signing_private_key: Ed25519PrivateKey | str | bytes,
    environment: Mapping[str, str] | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
) -> ThreadingHTTPServer:
    state = QualifiedExecutionService(config, credentials, signing_private_key=signing_private_key, environment=environment)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            client = state.client_for_authorization(self.headers.get("Authorization"))
            if client is None or urlsplit(self.path).path != "/v1/proofs" or self.headers.get_content_type() != "application/json":
                self.send_error(404)
                return
            if not state.acquire_slot():
                self.send_error(429)
                return
            reservation_owned = True
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= _MAX_REQUEST_BYTES:
                    raise _ProtocolError(400, "qualified execution request body is invalid")
                self.connection.settimeout(_MAX_BODY_READ_SECONDS)
                raw = self.rfile.read(length)
                self.connection.settimeout(None)
                if len(raw) != length:
                    raise _ProtocolError(400, "qualified execution request body is incomplete")
                value = json.loads(raw.decode())
                if not isinstance(value, Mapping):
                    raise _ProtocolError(400, "qualified execution request is invalid")
                # ``execute(..., reserved=True)`` owns and releases this
                # reservation in its own finally block, including execution
                # failures.  Parsing/read failures above remain owned here.
                reservation_owned = False
                body = _canonical(state.execute(value, client, reserved=True))
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise _ProtocolError(507, "qualified execution response is too large")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (ValueError, json.JSONDecodeError) as exc:
                error = exc if isinstance(exc, _ProtocolError) else _ProtocolError(400, "qualified execution request is invalid")
                self.send_error(error.status, str(error))
            except socket.timeout:
                self.send_error(408, "qualified execution request body timed out")
            finally:
                if reservation_owned:
                    state.release_slot()
                self.connection.settimeout(None)

        def do_GET(self) -> None:  # noqa: N802
            client = state.client_for_authorization(self.headers.get("Authorization"))
            parts = urlsplit(self.path).path.split("/")
            if client is None or len(parts) != 5 or parts[:3] != ["", "v1", "proofs"]:
                self.send_error(404)
                return
            retained = state.retained_proof(client, parts[3], parts[4])
            if retained is None:
                self.send_error(404)
                return
            body = _canonical(retained)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


class QualifiedExecutionClient:
    def __init__(self, descriptor: Mapping[str, Any], *, environment: Mapping[str, str] | None = None) -> None:
        self.descriptor = _descriptor(descriptor)
        self.token = (os.environ if environment is None else environment).get(self.descriptor["credential_env"]) if self.descriptor["credential_env"] else None
        if self.descriptor["credential_env"] and not self.token:
            raise QualifiedExecutionError("qualified execution signer credential is unavailable")

    def execute(
        self, *, candidate_commit: str, candidate_tree: str, base_commit: str, base_tree: str, plan_commit: str, profiles: Sequence[str], review_packet: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        value = {
            "schema": REQUEST_SCHEMA,
            "service_id": self.descriptor["id"],
            "client_id": self.descriptor["client_id"],
            "candidate_commit": candidate_commit,
            "candidate_tree": candidate_tree,
            "base_commit": base_commit,
            "base_tree": base_tree,
            "plan_commit": plan_commit,
            "profiles": list(profiles),
            "review_packet": dict(review_packet) if review_packet is not None else None,
        }
        request = Request(self.descriptor["endpoint"] + "/v1/proofs", data=_canonical(value), method="POST")
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(request, timeout=self.descriptor["timeout_seconds"]) as response:  # nosec: descriptor is externally provisioned
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, OSError) as exc:
            raise QualifiedExecutionError("qualified execution signer request failed") from exc
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise QualifiedExecutionError("qualified execution signer response is too large")
        try:
            response = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QualifiedExecutionError("qualified execution signer response is invalid") from exc
        if not isinstance(response, Mapping) or response.get("schema") != RESPONSE_SCHEMA:
            raise QualifiedExecutionError("qualified execution signer response is invalid")
        return dict(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-qualified-execution-signer")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        config = load_qualified_execution_config(args.config)
        credentials = {key: os.environ.get(client.credential_env, "") for key, client in config.clients.items()}
        key = os.environ.get(config.attestation_private_key_env)
        if not key:
            raise QualifiedExecutionConfigurationError("qualified execution signer key is unavailable")
        server = create_qualified_execution_server(config, credentials, signing_private_key=key, host=args.host, port=args.port)
    except (OSError, QualifiedExecutionConfigurationError) as exc:
        print(str(exc), file=os.sys.stderr)
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
