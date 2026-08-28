"""Portable registered-resource service for governed execution cards.

The service is deliberately narrow.  It serves only a configured, immutable
snapshot of named byte resources, authenticates every request, and records the
resources a harness actually retrieved before issuing an attestation.  Runtime
topology, credentials, and qualified service catalogs are deployment inputs;
none are embedded in source or inferred by this module.
"""

from __future__ import annotations

import argparse
import base64
import hmac
import json
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import unquote, urlsplit

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
    HARNESS_RUN_SCHEMA,
    RESOURCE_RESPONSE_SCHEMA,
    RESOURCE_SERVICE_HEALTH_SCHEMA,
    content_hash,
    ed25519_public_key,
    issue_harness_retrieval_attestation,
)

SERVICE_CONFIG_SCHEMA = "tgw-governed-resource-service-config/v5"
SERVICE_CONFIG_REGISTRY_SCHEMA = "tgw-governed-resource-service-config/v6"
RESOURCE_GENERATION_SCHEMA = "tgw-registered-resource-generation/v1"
# The resolver separately bounds the complete JSON response.  Keep enough
# room for current exact resources while preserving the aggregate review cap.
# A real TGW source-snapshot preimage and its CodeGraph are each larger than
# the old fixture-sized 3 MiB bound.  Keep the per-resource limit below the
# separately enforced 64 MiB aggregate review bundle while admitting the
# current exact 12 MiB/8 MiB resources.
MAX_RESOURCE_BYTES = 24 * 1024 * 1024
MAX_REQUEST_BYTES = 128 * 1024

_CONFIG_FIELDS = {
    "schema", "service_id", "clients", "attestation_key_id",
    "attestation_private_key_env", "harness_run_ttl_seconds", "completed_run_ttl_seconds",
    "max_open_runs_per_client", "max_completed_runs_per_client", "resources",
}
_REGISTRY_CONFIG_FIELDS = _CONFIG_FIELDS | {"resource_registry_root"}
_CLIENT_FIELDS = {"id", "credential_env", "execution_identity", "role"}
_RESOURCE_FIELDS = {"ref", "path", "content_hash"}


class ResourceServiceConfigurationError(ValueError):
    """A local service configuration is absent, malformed, or stale."""


class _ProtocolError(ValueError):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return content_hash(_canonical(value))


def _valid_hash(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _valid_ref(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 1024 and not any(character.isspace() for character in value)


def _valid_service_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value[0].islower()
        and value[0].isascii()
        and all(character.isascii() and (character.islower() or character.isdigit() or character == "-") for character in value)
    )


def _valid_credential_env(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and (value[0].isupper() or value[0] == "_")
        and value[0].isascii()
        and all(character.isascii() and (character.isupper() or character.isdigit() or character == "_") for character in value)
    )


def _valid_key_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isascii()
        and value[0].isalnum()
        and all(character.isascii() and (character.isalnum() or character in ".-_") for character in value)
    )


def _valid_client_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 128
        and value[0].isalnum()
        and value[0].isascii()
        and all(character.isascii() and (character.isalnum() or character in ".-_") for character in value)
    )


def _valid_identity(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 1024


def _valid_run_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(
        character.isascii() and (character.isalnum() or character in "_-") for character in value
    )


def _valid_git_object(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _resource_bindings(value: Any) -> dict[str, dict[str, str]]:
    if not isinstance(value, Mapping) or set(value) != CARD_RESOURCE_NAMES:
        raise _ProtocolError(400, "harness retrieval run resources are invalid")
    normalized: dict[str, dict[str, str]] = {}
    for name in sorted(CARD_RESOURCE_NAMES):
        binding = value[name]
        if (
            not isinstance(binding, Mapping)
            or set(binding) != {"ref", "hash"}
            or not _valid_ref(binding.get("ref"))
            or not _valid_hash(binding.get("hash"))
        ):
            raise _ProtocolError(400, "harness retrieval run resource binding is invalid")
        normalized[name] = {"ref": str(binding["ref"]), "hash": str(binding["hash"])}
    return normalized


@dataclass(frozen=True)
class FrozenResource:
    ref: str
    content: bytes
    content_hash: str


@dataclass(frozen=True)
class RegisteredResourceGeneration:
    """One immutable candidate-specific resource generation."""

    generation: str
    source: Mapping[str, Any]
    resources: Mapping[str, FrozenResource]
    bindings: Mapping[str, Mapping[str, str]]


def _protected_directory(path: Path, label: str) -> os.stat_result:
    try:
        observed = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise ResourceServiceConfigurationError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != 0
        or observed.st_gid != 0
        or stat.S_IMODE(observed.st_mode) & 0o022
    ):
        raise ResourceServiceConfigurationError(f"{label} is not protected")
    return observed


def _protected_generation_file(path: Path, label: str, *, limit: int) -> bytes:
    try:
        named = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ResourceServiceConfigurationError(f"{label} is unavailable") from exc
    try:
        observed = os.fstat(descriptor)
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != 0
            or observed.st_gid != 0
            or stat.S_IMODE(observed.st_mode) & 0o022
            or observed.st_nlink != 1
            or observed.st_size > limit
            or (named.st_dev, named.st_ino) != (observed.st_dev, observed.st_ino)
        ):
            raise ResourceServiceConfigurationError(f"{label} is not protected")
        raw = bytearray()
        while len(raw) <= limit:
            block = os.read(descriptor, min(1024 * 1024, limit + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        if len(raw) != observed.st_size or len(raw) > limit:
            raise ResourceServiceConfigurationError(f"{label} exceeds its bound")
        after = os.fstat(descriptor)
        if any(
            getattr(after, field) != getattr(observed, field)
            for field in (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
            )
        ):
            raise ResourceServiceConfigurationError(f"{label} changed while held")
        return bytes(raw)
    finally:
        os.close(descriptor)


def load_registered_resource_generation(
    registry_root: Path, generation: str,
) -> RegisteredResourceGeneration:
    """Load one root-owned immutable generation without following links."""

    if not _valid_run_id(generation):
        raise ResourceServiceConfigurationError("registered resource generation identity is invalid")
    root = registry_root.resolve(strict=True)
    if root != registry_root:
        raise ResourceServiceConfigurationError("registered resource registry path is not direct")
    _protected_directory(root, "registered resource registry")
    generation_root = root / generation
    _protected_directory(generation_root, "registered resource generation")
    manifest_raw = _protected_generation_file(
        generation_root / "manifest.json",
        "registered resource generation manifest",
        limit=MAX_REQUEST_BYTES,
    )
    try:
        manifest = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResourceServiceConfigurationError("registered resource generation manifest is invalid") from exc
    if (
        not isinstance(manifest, Mapping)
        or set(manifest) != {"schema", "generation", "source", "resources"}
        or manifest.get("schema") != RESOURCE_GENERATION_SCHEMA
        or manifest.get("generation") != generation
        or not isinstance(manifest.get("source"), Mapping)
        or set(manifest["source"]) != {"commit", "tree", "canonical_installed"}
        or manifest["source"].get("commit") != generation
        or not _valid_git_object(manifest["source"].get("commit"))
        or not _valid_git_object(manifest["source"].get("tree"))
        or manifest["source"].get("canonical_installed") is not False
        or not isinstance(manifest.get("resources"), list)
        or len(manifest["resources"]) != len(CARD_RESOURCE_NAMES)
    ):
        raise ResourceServiceConfigurationError("registered resource generation manifest is invalid")
    frozen: dict[str, FrozenResource] = {}
    bindings: dict[str, dict[str, str]] = {}
    total = 0
    for item in manifest["resources"]:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"name", "ref", "path", "content_hash"}
            or item.get("name") not in CARD_RESOURCE_NAMES
            or item["name"] in frozen
            or not _valid_ref(item.get("ref"))
            or not _valid_hash(item.get("content_hash"))
            or not isinstance(item.get("path"), str)
        ):
            raise ResourceServiceConfigurationError("registered resource generation entry is invalid")
        relative = Path(item["path"])
        if relative.is_absolute() or not relative.parts or any(
            part in {"", ".", ".."} for part in relative.parts
        ):
            raise ResourceServiceConfigurationError("registered resource generation path is invalid")
        resource_path = generation_root / relative
        try:
            if resource_path.resolve(strict=True).parent != (generation_root / "resources").resolve(strict=True):
                raise ResourceServiceConfigurationError("registered resource generation path escapes its root")
        except OSError as exc:
            raise ResourceServiceConfigurationError("registered resource generation resource is unavailable") from exc
        content = _protected_generation_file(
            resource_path, "registered resource generation resource", limit=MAX_RESOURCE_BYTES,
        )
        total += len(content)
        if total > 64 * 1024 * 1024 or content_hash(content) != item["content_hash"]:
            raise ResourceServiceConfigurationError("registered resource generation content differs")
        resource = FrozenResource(str(item["ref"]), content, str(item["content_hash"]))
        frozen[str(item["name"])] = resource
        bindings[str(item["name"])] = {"ref": resource.ref, "hash": resource.content_hash}
    if set(frozen) != CARD_RESOURCE_NAMES or len({item.ref for item in frozen.values()}) != len(frozen):
        raise ResourceServiceConfigurationError("registered resource generation coverage is invalid")
    return RegisteredResourceGeneration(
        generation=generation,
        source=dict(manifest["source"]),
        resources=frozen,
        bindings=bindings,
    )


@dataclass(frozen=True)
class ResourceServiceClient:
    """One configured credential principal for the resource service.

    The bearer itself is runtime-only.  This durable configuration binds that
    bearer source to one client identity, one execution identity, and one role
    it is allowed to assert when it opens a retrieval run.
    """

    client_id: str
    credential_env: str
    execution_identity: str
    role: str


@dataclass(frozen=True)
class ResourceServiceConfig:
    """Validated configuration with resource bytes snapshotted at startup."""

    service_id: str
    clients: Mapping[str, ResourceServiceClient]
    attestation_key_id: str
    attestation_private_key_env: str
    harness_run_ttl_seconds: int
    completed_run_ttl_seconds: int
    max_open_runs_per_client: int
    max_completed_runs_per_client: int
    resources: Mapping[str, FrozenResource]
    resource_registry_root: Path | None = None

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ResourceServiceConfig":
        if not isinstance(value, Mapping) or frozenset(value) not in {
            frozenset(_CONFIG_FIELDS), frozenset(_REGISTRY_CONFIG_FIELDS),
        }:
            raise ResourceServiceConfigurationError("governed resource service configuration is invalid")
        if (
            value.get("schema") == SERVICE_CONFIG_SCHEMA
            and set(value) != _CONFIG_FIELDS
            or value.get("schema") == SERVICE_CONFIG_REGISTRY_SCHEMA
            and set(value) != _REGISTRY_CONFIG_FIELDS
            or value.get("schema") not in {SERVICE_CONFIG_SCHEMA, SERVICE_CONFIG_REGISTRY_SCHEMA}
        ):
            raise ResourceServiceConfigurationError("governed resource service configuration schema is invalid")
        service_id = value.get("service_id")
        clients = value.get("clients")
        attestation_key_id = value.get("attestation_key_id")
        attestation_private_key_env = value.get("attestation_private_key_env")
        harness_run_ttl_seconds = value.get("harness_run_ttl_seconds")
        completed_run_ttl_seconds = value.get("completed_run_ttl_seconds")
        max_open_runs_per_client = value.get("max_open_runs_per_client")
        max_completed_runs_per_client = value.get("max_completed_runs_per_client")
        if not _valid_service_id(service_id):
            raise ResourceServiceConfigurationError("governed resource service identity is invalid")
        if not isinstance(clients, list) or not clients:
            raise ResourceServiceConfigurationError("governed resource service clients are invalid")
        normalized_clients: dict[str, ResourceServiceClient] = {}
        credential_envs: set[str] = set()
        execution_identities: set[str] = set()
        for client in clients:
            if not isinstance(client, Mapping) or set(client) != _CLIENT_FIELDS:
                raise ResourceServiceConfigurationError("governed resource service client binding is invalid")
            client_id = client.get("id")
            credential_env = client.get("credential_env")
            execution_identity = client.get("execution_identity")
            role = client.get("role")
            if (
                not _valid_client_id(client_id)
                or client_id in normalized_clients
                or not _valid_credential_env(credential_env)
                or credential_env in credential_envs
                or not _valid_identity(execution_identity)
                or execution_identity in execution_identities
                or not _valid_identity(role)
            ):
                raise ResourceServiceConfigurationError("governed resource service client binding is invalid")
            normalized_clients[client_id] = ResourceServiceClient(
                client_id=client_id,
                credential_env=credential_env,
                execution_identity=execution_identity,
                role=role,
            )
            credential_envs.add(credential_env)
            execution_identities.add(execution_identity)
        if not _valid_key_id(attestation_key_id):
            raise ResourceServiceConfigurationError("governed resource service attestation key identity is invalid")
        if not _valid_credential_env(attestation_private_key_env):
            raise ResourceServiceConfigurationError("governed resource service private key environment is invalid")
        if (
            not isinstance(harness_run_ttl_seconds, int)
            or isinstance(harness_run_ttl_seconds, bool)
            or not 1 <= harness_run_ttl_seconds <= 3600
        ):
            raise ResourceServiceConfigurationError("governed resource service harness run TTL is invalid")
        if (
            not isinstance(completed_run_ttl_seconds, int)
            or isinstance(completed_run_ttl_seconds, bool)
            or not 1 <= completed_run_ttl_seconds <= 86_400
        ):
            raise ResourceServiceConfigurationError("governed resource service completed run TTL is invalid")
        if (
            not isinstance(max_open_runs_per_client, int)
            or isinstance(max_open_runs_per_client, bool)
            or not 1 <= max_open_runs_per_client <= 10_000
        ):
            raise ResourceServiceConfigurationError("governed resource service open run capacity is invalid")
        if (
            not isinstance(max_completed_runs_per_client, int)
            or isinstance(max_completed_runs_per_client, bool)
            or not 1 <= max_completed_runs_per_client <= 10_000
        ):
            raise ResourceServiceConfigurationError("governed resource service completed run capacity is invalid")
        resources = value.get("resources")
        if not isinstance(resources, list) or (
            not resources and value.get("schema") != SERVICE_CONFIG_REGISTRY_SCHEMA
        ):
            raise ResourceServiceConfigurationError("governed resource service resources are invalid")
        frozen: dict[str, FrozenResource] = {}
        for item in resources:
            if not isinstance(item, Mapping) or set(item) != _RESOURCE_FIELDS:
                raise ResourceServiceConfigurationError("governed resource service resource entry is invalid")
            ref = item.get("ref")
            raw_path = item.get("path")
            expected_hash = item.get("content_hash")
            if not _valid_ref(ref) or not isinstance(raw_path, str) or not _valid_hash(expected_hash):
                raise ResourceServiceConfigurationError("governed resource service resource binding is invalid")
            path = Path(raw_path)
            if not path.is_absolute() or path.is_symlink():
                raise ResourceServiceConfigurationError("governed resource service resource path must be an absolute regular file")
            try:
                resolved = path.resolve(strict=True)
                if resolved != path or not resolved.is_file():
                    raise ResourceServiceConfigurationError("governed resource service resource path must be an absolute regular file")
                resource_content = resolved.read_bytes()
            except OSError as exc:
                raise ResourceServiceConfigurationError("governed resource service resource is unreadable") from exc
            if len(resource_content) > MAX_RESOURCE_BYTES:
                raise ResourceServiceConfigurationError("governed resource service resource is too large")
            actual_hash = content_hash(resource_content)
            if actual_hash != expected_hash:
                raise ResourceServiceConfigurationError("governed resource service resource content hash mismatch")
            if ref in frozen:
                raise ResourceServiceConfigurationError("governed resource service resource reference is duplicated")
            frozen[ref] = FrozenResource(ref=str(ref), content=resource_content, content_hash=actual_hash)
        registry_root = None
        if value.get("schema") == SERVICE_CONFIG_REGISTRY_SCHEMA:
            raw_registry_root = value.get("resource_registry_root")
            if not isinstance(raw_registry_root, str) or not Path(raw_registry_root).is_absolute():
                raise ResourceServiceConfigurationError("governed resource service registry root is invalid")
            registry_root = Path(raw_registry_root).resolve(strict=True)
            if registry_root != Path(raw_registry_root):
                raise ResourceServiceConfigurationError("governed resource service registry root is not direct")
            _protected_directory(registry_root, "registered resource registry")
        return cls(
            service_id=str(service_id),
            clients=normalized_clients,
            attestation_key_id=str(attestation_key_id),
            attestation_private_key_env=str(attestation_private_key_env),
            harness_run_ttl_seconds=harness_run_ttl_seconds,
            completed_run_ttl_seconds=completed_run_ttl_seconds,
            max_open_runs_per_client=max_open_runs_per_client,
            max_completed_runs_per_client=max_completed_runs_per_client,
            resources=frozen,
            resource_registry_root=registry_root,
        )


def load_resource_service_config(path: str | os.PathLike[str]) -> ResourceServiceConfig:
    """Load a local, explicit service snapshot.  No source template is runnable."""

    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceServiceConfigurationError("governed resource service configuration is unreadable") from exc
    return ResourceServiceConfig.parse(value)


@dataclass
class _HarnessRun:
    value: dict[str, Any]
    client_id: str
    created_monotonic: float
    seen: set[str] = field(default_factory=set)
    attestation: dict[str, Any] | None = None
    completed_monotonic: float | None = None
    completed_response_returned: bool = False
    resources: Mapping[str, FrozenResource] = field(default_factory=dict)


class _ResourceServiceState:
    def __init__(
        self, config: ResourceServiceConfig, credentials: Mapping[str, str], signing_private_key: str | bytes,
        *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(credentials, Mapping) or set(credentials) != set(config.clients):
            raise ResourceServiceConfigurationError("governed resource service client credentials are unavailable")
        normalized_credentials: list[tuple[str, ResourceServiceClient]] = []
        for client_id, client in config.clients.items():
            credential = credentials.get(client_id)
            if not isinstance(credential, str) or not credential:
                raise ResourceServiceConfigurationError("governed resource service client credential is unavailable")
            if any(hmac.compare_digest(credential, known) for known, _client in normalized_credentials):
                raise ResourceServiceConfigurationError("governed resource service client credentials are not distinct")
            normalized_credentials.append((credential, client))
        self.config = config
        self._credentials = tuple(normalized_credentials)
        self._signing_private_key = signing_private_key
        self._clock = clock
        self._runs: dict[str, _HarnessRun] = {}
        self._lock = threading.Lock()

    def generation(self, generation: str) -> RegisteredResourceGeneration:
        if self.config.resource_registry_root is None:
            raise _ProtocolError(404, "registered resource generation is unavailable")
        try:
            return load_registered_resource_generation(
                self.config.resource_registry_root, generation,
            )
        except ResourceServiceConfigurationError as exc:
            raise _ProtocolError(404, "registered resource generation is unavailable") from exc

    def _resources_for_bindings(
        self, bindings: Mapping[str, Mapping[str, str]],
    ) -> Mapping[str, FrozenResource]:
        static = {
            name: self.config.resources[binding["ref"]]
            for name, binding in bindings.items()
            if binding["ref"] in self.config.resources
            and self.config.resources[binding["ref"]].content_hash == binding["hash"]
        }
        if len(static) == len(bindings):
            return static
        root = self.config.resource_registry_root
        if root is None:
            raise _ProtocolError(409, "harness retrieval run resource binding is unavailable")
        try:
            candidates = sorted(
                item.name for item in root.iterdir()
                if item.is_dir() and not item.is_symlink() and _valid_run_id(item.name)
            )
        except OSError as exc:
            raise _ProtocolError(409, "registered resource registry is unavailable") from exc
        if len(candidates) > 1024:
            raise _ProtocolError(409, "registered resource registry capacity is exceeded")
        matches: list[RegisteredResourceGeneration] = []
        for generation in candidates:
            try:
                registered = load_registered_resource_generation(root, generation)
            except ResourceServiceConfigurationError:
                continue
            if dict(registered.bindings) == dict(bindings):
                matches.append(registered)
        if len(matches) != 1:
            raise _ProtocolError(409, "harness retrieval run resource binding is unavailable")
        return matches[0].resources

    def client_for_authorization(self, authorization: str | None) -> ResourceServiceClient | None:
        """Resolve one bearer to its configured principal without trusting claims.

        A successful request is never just "authenticated": all state-changing
        operations receive the exact principal derived from its credential.
        """

        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return None
        credential = authorization.removeprefix("Bearer ")
        for configured_credential, client in self._credentials:
            if hmac.compare_digest(credential, configured_credential):
                return client
        return None

    def _reclaim_expired_runs(self) -> None:
        now = self._clock()
        open_deadline = now - self.config.harness_run_ttl_seconds
        completed_deadline = now - self.config.completed_run_ttl_seconds
        expired = [
            run_id
            for run_id, run in self._runs.items()
            if (
                (run.attestation is None and run.created_monotonic <= open_deadline)
                or (
                    run.attestation is not None
                    and run.completed_response_returned
                    and run.completed_monotonic is not None
                    and run.completed_monotonic <= completed_deadline
                )
            )
        ]
        for run_id in expired:
            del self._runs[run_id]

    def completed_response_returned(self, run_id: str, client: ResourceServiceClient) -> None:
        """Permit bounded retention only after the completed attestation was sent.

        A service never evicts a completed run before returning the signed
        attestation that names it.  Capacity reclamation then removes the
        oldest *already returned* records, so an evicted run cannot strand a
        caller without the receipt it needs to retain as evidence.
        """

        with self._lock:
            self._reclaim_expired_runs()
            run = self._runs.get(run_id)
            if run is None or run.attestation is None or run.client_id != client.client_id:
                return
            run.completed_response_returned = True
            completed = sorted(
                (
                    (record.completed_monotonic or record.created_monotonic, candidate)
                    for candidate, record in self._runs.items()
                    if (
                        record.client_id == client.client_id
                        and record.attestation is not None
                        and record.completed_response_returned
                    )
                ),
            )
            overflow = len(completed) - self.config.max_completed_runs_per_client
            for _completed_at, candidate in completed[:max(overflow, 0)]:
                del self._runs[candidate]

    def discard_unreturned_completed_run(self, run_id: str, client: ResourceServiceClient) -> None:
        """Drop an attestation that failed before it could leave the service.

        A broken response cannot be used for later verification, so retaining it
        would only create an unbounded completed-run sink.  Successfully sent
        receipts use :meth:`completed_response_returned` instead.
        """

        with self._lock:
            run = self._runs.get(run_id)
            if run is not None and run.attestation is not None and run.client_id == client.client_id:
                del self._runs[run_id]

    def resource(self, ref: str, run_id: str | None, client: ResourceServiceClient) -> FrozenResource:
        if not _valid_ref(ref):
            raise _ProtocolError(404, "resource is unavailable")
        if not _valid_run_id(run_id):
            raise _ProtocolError(403, "resource retrieval requires a bound harness run")
        with self._lock:
            self._reclaim_expired_runs()
            run = self._runs.get(run_id)
            if run is None:
                raise _ProtocolError(404, "harness run is unavailable")
            if run.client_id != client.client_id:
                raise _ProtocolError(403, "harness run client is invalid")
            bindings = run.value["resources"]
            if ref not in {binding["ref"] for binding in bindings.values()}:
                raise _ProtocolError(403, "resource is not bound to harness run")
            resource = next(
                (item for item in run.resources.values() if item.ref == ref), None,
            )
            if resource is None:
                raise _ProtocolError(404, "resource is unavailable")
            run.seen.add(ref)
            return resource

    def begin_run(self, value: Mapping[str, Any], client: ResourceServiceClient) -> dict[str, Any]:
        required = {
            "schema", "service_id", "client_id", "card_hash", "role", "execution_identity",
            "handoff_hash", "resource_receipt_hash", "resources",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise _ProtocolError(400, "harness retrieval run is invalid")
        if (
            value.get("schema") != HARNESS_RUN_SCHEMA
            or value.get("service_id") != self.config.service_id
            or value.get("client_id") != client.client_id
        ):
            raise _ProtocolError(400, "harness retrieval run identity is invalid")
        if not all(_valid_hash(value.get(field)) for field in ("card_hash", "handoff_hash", "resource_receipt_hash")):
            raise _ProtocolError(400, "harness retrieval run hash binding is invalid")
        if (
            not _valid_identity(value.get("role"))
            or not _valid_identity(value.get("execution_identity"))
            or value["role"] != client.role
            or value["execution_identity"] != client.execution_identity
        ):
            raise _ProtocolError(400, "harness retrieval run identity is invalid")
        bindings = _resource_bindings(value.get("resources"))
        resources = self._resources_for_bindings(bindings)
        payload = {
            "schema": HARNESS_RUN_SCHEMA,
            "service_id": self.config.service_id,
            "client_id": client.client_id,
            "card_hash": value["card_hash"],
            "role": value["role"],
            "execution_identity": value["execution_identity"],
            "handoff_hash": value["handoff_hash"],
            "resource_receipt_hash": value["resource_receipt_hash"],
            "resources": bindings,
        }
        with self._lock:
            self._reclaim_expired_runs()
            open_runs = sum(
                run.client_id == client.client_id and run.attestation is None
                for run in self._runs.values()
            )
            if open_runs >= self.config.max_open_runs_per_client:
                raise _ProtocolError(429, "harness run capacity is exhausted")
            run_id = secrets.token_urlsafe(24)
            while run_id in self._runs:
                run_id = secrets.token_urlsafe(24)
            self._runs[run_id] = _HarnessRun(
                value=payload, client_id=client.client_id, created_monotonic=self._clock(),
                resources=resources,
            )
        return {**payload, "run_id": run_id}

    def complete_run(self, run_id: str, value: Mapping[str, Any], client: ResourceServiceClient) -> dict[str, Any]:
        if not _valid_run_id(run_id) or value != {"schema": HARNESS_RUN_SCHEMA, "run_id": run_id}:
            raise _ProtocolError(400, "harness retrieval completion is invalid")
        with self._lock:
            self._reclaim_expired_runs()
            run = self._runs.get(run_id)
            if run is None:
                raise _ProtocolError(404, "harness run is unavailable")
            if run.client_id != client.client_id:
                raise _ProtocolError(403, "harness run client is invalid")
            expected = {binding["ref"] for binding in run.value["resources"].values()}
            if run.seen != expected:
                raise _ProtocolError(409, "harness run has not retrieved every bound resource")
            if run.attestation is None:
                payload = {
                    "schema": HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
                    "service_id": self.config.service_id,
                    "client_id": run.client_id,
                    "run_id": run_id,
                    "card_hash": run.value["card_hash"],
                    "role": run.value["role"],
                    "execution_identity": run.value["execution_identity"],
                    "handoff_hash": run.value["handoff_hash"],
                    "resource_receipt_hash": run.value["resource_receipt_hash"],
                    "resources": run.value["resources"],
                    "attestation_key_id": self.config.attestation_key_id,
                }
                try:
                    run.attestation = issue_harness_retrieval_attestation(
                        payload, signing_private_key=self._signing_private_key,
                    )
                except ValueError as exc:
                    raise ResourceServiceConfigurationError(
                        "governed resource service private signing key is invalid"
                    ) from exc
                run.completed_monotonic = self._clock()
            return dict(run.attestation)

    def attestation(self, run_id: str, client: ResourceServiceClient) -> dict[str, Any]:
        if not _valid_run_id(run_id):
            raise _ProtocolError(404, "harness run is unavailable")
        with self._lock:
            self._reclaim_expired_runs()
            run = self._runs.get(run_id)
            if run is None or run.attestation is None:
                raise _ProtocolError(404, "harness run attestation is unavailable")
            if run.client_id != client.client_id:
                raise _ProtocolError(403, "harness run client is invalid")
            return dict(run.attestation)


class _ConfiguredResourceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_resource_service_server(
    config: ResourceServiceConfig, client_credentials: Mapping[str, str], *, signing_private_key: str | bytes,
    host: str = "127.0.0.1", port: int = 0, clock: Callable[[], float] = time.monotonic,
) -> ThreadingHTTPServer:
    """Create the HTTP service without selecting a deployment endpoint or catalog."""

    if not isinstance(host, str) or not host or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ResourceServiceConfigurationError("governed resource service bind address is invalid")
    try:
        state = _ResourceServiceState(config, client_credentials, signing_private_key, clock=clock)
        # Parse at construction, rather than deferring the deployment-key fault
        # until the first harness tries to complete a run.
        ed25519_public_key(signing_private_key)
    except ValueError as exc:
        raise ResourceServiceConfigurationError("governed resource service private signing key is invalid") from exc

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            # Request URLs include only public refs, but the server stays silent by default.
            return

        def _send_json(self, status: int, value: Mapping[str, Any]) -> None:
            body = _canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reject(self, error: _ProtocolError) -> None:
            self.send_error(error.status, str(error))

        def _authorized(self) -> ResourceServiceClient | None:
            client = state.client_for_authorization(self.headers.get("Authorization"))
            if client is not None:
                return client
            # Do not identify a privileged resource service to an unauthenticated caller.
            self.send_error(404)
            return None

        def _path(self) -> tuple[str, str] | None:
            parsed = urlsplit(self.path)
            if parsed.query or parsed.fragment:
                self.send_error(404)
                return None
            return parsed.path, parsed.query

        def _json_body(self) -> Mapping[str, Any]:
            if self.headers.get_content_type() != "application/json":
                raise _ProtocolError(400, "request content type is invalid")
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError as exc:
                raise _ProtocolError(400, "request content length is invalid") from exc
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise _ProtocolError(400, "request body is invalid")
            body = self.rfile.read(length)
            if len(body) != length:
                raise _ProtocolError(400, "request body is incomplete")
            try:
                value = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise _ProtocolError(400, "request body is not JSON") from exc
            if not isinstance(value, Mapping):
                raise _ProtocolError(400, "request body is invalid")
            return value

        def do_GET(self) -> None:  # noqa: N802
            client = self._authorized()
            if client is None:
                return
            parsed = self._path()
            if parsed is None:
                return
            path, _ = parsed
            try:
                if path == "/v1/health":
                    self._send_json(
                        200,
                        {
                            "schema": RESOURCE_SERVICE_HEALTH_SCHEMA,
                            "service_id": config.service_id,
                            "attestation_key_id": config.attestation_key_id,
                            "status": "healthy",
                        },
                    )
                    return
                generation_prefix = "/v1/registered-generations/"
                if path.startswith(generation_prefix):
                    generation = unquote(path.removeprefix(generation_prefix))
                    registered = state.generation(generation)
                    self._send_json(
                        200,
                        {
                            "schema": RESOURCE_GENERATION_SCHEMA,
                            "service_id": config.service_id,
                            "generation": registered.generation,
                            "source": registered.source,
                            "resources": registered.bindings,
                        },
                    )
                    return
                resource_prefix = "/v1/resources/"
                if path.startswith(resource_prefix):
                    ref = unquote(path.removeprefix(resource_prefix))
                    run_id = self.headers.get("X-TGW-Harness-Run")
                    resource = state.resource(ref, run_id, client)
                    self._send_json(
                        200,
                        {
                            "schema": RESOURCE_RESPONSE_SCHEMA,
                            "service_id": config.service_id,
                            "ref": ref,
                            "content_base64": base64.b64encode(resource.content).decode("ascii"),
                        },
                    )
                    return
                attestation_prefix = "/v1/harness-runs/"
                if path.startswith(attestation_prefix) and path.endswith("/attestation"):
                    raw_run_id = path[len(attestation_prefix):-len("/attestation")].strip("/")
                    self._send_json(200, state.attestation(unquote(raw_run_id), client))
                    return
                self.send_error(404)
            except _ProtocolError as exc:
                self._reject(exc)

        def do_POST(self) -> None:  # noqa: N802
            client = self._authorized()
            if client is None:
                return
            parsed = self._path()
            if parsed is None:
                return
            path, _ = parsed
            try:
                value = self._json_body()
                if path == "/v1/harness-runs":
                    self._send_json(200, state.begin_run(value, client))
                    return
                prefix = "/v1/harness-runs/"
                suffix = "/complete"
                if path.startswith(prefix) and path.endswith(suffix):
                    raw_run_id = path[len(prefix):-len(suffix)].strip("/")
                    run_id = unquote(raw_run_id)
                    result = state.complete_run(run_id, value, client)
                    try:
                        self._send_json(200, result)
                    except OSError:
                        state.discard_unreturned_completed_run(run_id, client)
                        return
                    state.completed_response_returned(run_id, client)
                    return
                self.send_error(404)
            except _ProtocolError as exc:
                self._reject(exc)

    return _ConfiguredResourceServer((host, port), Handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tgw-governed-resource-service",
        description="serve explicit immutable resources for a governed execution card",
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        config = load_resource_service_config(args.config)
        client_credentials = {
            client_id: os.environ.get(client.credential_env, "")
            for client_id, client in config.clients.items()
        }
        if not all(client_credentials.values()):
            raise ResourceServiceConfigurationError("governed resource service client credential is unavailable")
        signing_private_key = os.environ.get(config.attestation_private_key_env)
        if not signing_private_key:
            raise ResourceServiceConfigurationError("governed resource service private signing key is unavailable")
        server = create_resource_service_server(
            config, client_credentials, signing_private_key=signing_private_key, host=args.host, port=args.port,
        )
    except (OSError, ResourceServiceConfigurationError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}), file=os.sys.stderr)
        return 2
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
