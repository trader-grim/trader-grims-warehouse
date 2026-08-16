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
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
    HARNESS_RUN_SCHEMA,
    RESOURCE_RESPONSE_SCHEMA,
    RESOURCE_SERVICE_HEALTH_SCHEMA,
    content_hash,
)

SERVICE_CONFIG_SCHEMA = "tgw-governed-resource-service-config/v1"
# The resolver bounds the complete JSON response at 4 MiB.  Keep enough room
# for base64 expansion and the response envelope, not merely the raw export.
MAX_RESOURCE_BYTES = 3 * 1024 * 1024 - 1024
MAX_REQUEST_BYTES = 128 * 1024

_CONFIG_FIELDS = {"schema", "service_id", "credential_env", "resources"}
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


def _valid_identity(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 1024


def _valid_run_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 128 and all(
        character.isascii() and (character.isalnum() or character in "_-") for character in value
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
class ResourceServiceConfig:
    """Validated configuration with resource bytes snapshotted at startup."""

    service_id: str
    credential_env: str
    resources: Mapping[str, FrozenResource]

    @classmethod
    def parse(cls, value: Mapping[str, Any]) -> "ResourceServiceConfig":
        if not isinstance(value, Mapping) or set(value) != _CONFIG_FIELDS:
            raise ResourceServiceConfigurationError("governed resource service configuration is invalid")
        if value.get("schema") != SERVICE_CONFIG_SCHEMA:
            raise ResourceServiceConfigurationError("governed resource service configuration schema is invalid")
        service_id = value.get("service_id")
        credential_env = value.get("credential_env")
        if not _valid_service_id(service_id):
            raise ResourceServiceConfigurationError("governed resource service identity is invalid")
        if not _valid_credential_env(credential_env):
            raise ResourceServiceConfigurationError("governed resource service credential environment is invalid")
        resources = value.get("resources")
        if not isinstance(resources, list) or not resources:
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
        return cls(service_id=str(service_id), credential_env=credential_env, resources=frozen)


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
    seen: set[str] = field(default_factory=set)
    attestation: dict[str, Any] | None = None


class _ResourceServiceState:
    def __init__(self, config: ResourceServiceConfig, credential: str) -> None:
        if not isinstance(credential, str) or not credential:
            raise ResourceServiceConfigurationError("governed resource service credential is unavailable")
        self.config = config
        self._credential = credential
        self._runs: dict[str, _HarnessRun] = {}
        self._lock = threading.Lock()

    def authorized(self, authorization: str | None) -> bool:
        return authorization is not None and hmac.compare_digest(authorization, f"Bearer {self._credential}")

    def resource(self, ref: str, run_id: str | None) -> FrozenResource:
        if not _valid_ref(ref):
            raise _ProtocolError(404, "resource is unavailable")
        with self._lock:
            resource = self.config.resources.get(ref)
            if resource is None:
                raise _ProtocolError(404, "resource is unavailable")
            if run_id is not None:
                run = self._runs.get(run_id)
                if run is None:
                    raise _ProtocolError(404, "harness run is unavailable")
                bindings = run.value["resources"]
                if ref not in {binding["ref"] for binding in bindings.values()}:
                    raise _ProtocolError(403, "resource is not bound to harness run")
                run.seen.add(ref)
            return resource

    def begin_run(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema", "service_id", "card_hash", "role", "execution_identity",
            "handoff_hash", "resource_receipt_hash", "resources",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise _ProtocolError(400, "harness retrieval run is invalid")
        if value.get("schema") != HARNESS_RUN_SCHEMA or value.get("service_id") != self.config.service_id:
            raise _ProtocolError(400, "harness retrieval run identity is invalid")
        if not all(_valid_hash(value.get(field)) for field in ("card_hash", "handoff_hash", "resource_receipt_hash")):
            raise _ProtocolError(400, "harness retrieval run hash binding is invalid")
        if not _valid_identity(value.get("role")) or not _valid_identity(value.get("execution_identity")):
            raise _ProtocolError(400, "harness retrieval run identity is invalid")
        bindings = _resource_bindings(value.get("resources"))
        for binding in bindings.values():
            resource = self.config.resources.get(binding["ref"])
            if resource is None or resource.content_hash != binding["hash"]:
                raise _ProtocolError(409, "harness retrieval run resource binding is unavailable")
        payload = {
            "schema": HARNESS_RUN_SCHEMA,
            "service_id": self.config.service_id,
            "card_hash": value["card_hash"],
            "role": value["role"],
            "execution_identity": value["execution_identity"],
            "handoff_hash": value["handoff_hash"],
            "resource_receipt_hash": value["resource_receipt_hash"],
            "resources": bindings,
        }
        with self._lock:
            run_id = secrets.token_urlsafe(24)
            while run_id in self._runs:
                run_id = secrets.token_urlsafe(24)
            self._runs[run_id] = _HarnessRun(value=payload)
        return {**payload, "run_id": run_id}

    def complete_run(self, run_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
        if not _valid_run_id(run_id) or value != {"schema": HARNESS_RUN_SCHEMA, "run_id": run_id}:
            raise _ProtocolError(400, "harness retrieval completion is invalid")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise _ProtocolError(404, "harness run is unavailable")
            expected = {binding["ref"] for binding in run.value["resources"].values()}
            if run.seen != expected:
                raise _ProtocolError(409, "harness run has not retrieved every bound resource")
            if run.attestation is None:
                unsigned = {
                    "schema": HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
                    "service_id": self.config.service_id,
                    "run_id": run_id,
                    "card_hash": run.value["card_hash"],
                    "role": run.value["role"],
                    "execution_identity": run.value["execution_identity"],
                    "handoff_hash": run.value["handoff_hash"],
                    "resource_receipt_hash": run.value["resource_receipt_hash"],
                    "resources": run.value["resources"],
                }
                run.attestation = {**unsigned, "attestation_hash": _hash(unsigned)}
            return dict(run.attestation)

    def attestation(self, run_id: str) -> dict[str, Any]:
        if not _valid_run_id(run_id):
            raise _ProtocolError(404, "harness run is unavailable")
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.attestation is None:
                raise _ProtocolError(404, "harness run attestation is unavailable")
            return dict(run.attestation)


class _ConfiguredResourceServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_resource_service_server(
    config: ResourceServiceConfig, credential: str, *, host: str = "127.0.0.1", port: int = 0,
) -> ThreadingHTTPServer:
    """Create the HTTP service without selecting a deployment endpoint or catalog."""

    if not isinstance(host, str) or not host or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ResourceServiceConfigurationError("governed resource service bind address is invalid")
    state = _ResourceServiceState(config, credential)

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

        def _authorized(self) -> bool:
            if state.authorized(self.headers.get("Authorization")):
                return True
            # Do not identify a privileged resource service to an unauthenticated caller.
            self.send_error(404)
            return False

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
            if not self._authorized():
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
                            "status": "healthy",
                        },
                    )
                    return
                resource_prefix = "/v1/resources/"
                if path.startswith(resource_prefix):
                    ref = unquote(path.removeprefix(resource_prefix))
                    run_id = self.headers.get("X-TGW-Harness-Run")
                    resource = state.resource(ref, run_id)
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
                    self._send_json(200, state.attestation(unquote(raw_run_id)))
                    return
                self.send_error(404)
            except _ProtocolError as exc:
                self._reject(exc)

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                return
            parsed = self._path()
            if parsed is None:
                return
            path, _ = parsed
            try:
                value = self._json_body()
                if path == "/v1/harness-runs":
                    self._send_json(200, state.begin_run(value))
                    return
                prefix = "/v1/harness-runs/"
                suffix = "/complete"
                if path.startswith(prefix) and path.endswith(suffix):
                    raw_run_id = path[len(prefix):-len(suffix)].strip("/")
                    self._send_json(200, state.complete_run(unquote(raw_run_id), value))
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
        credential = os.environ.get(config.credential_env)
        if not credential:
            raise ResourceServiceConfigurationError("governed resource service credential is unavailable")
        server = create_resource_service_server(config, credential, host=args.host, port=args.port)
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
