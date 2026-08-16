"""Privileged context broker for provider-neutral governed reviews.

The review provider can request one exact card-bound context run, but it never
receives the backend resource-service credential or either signing key.  The
broker fetches every resource through its own fixed backend identity, verifies
the backend service attestation, and then issues the provider-bound attestation
which the controller independently reads back from the broker service.

The daemon is loopback-only and consumes externally issued request/readback
credentials; TLS termination and secret issuance remain deployment-owned.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import stat
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import unquote, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    issue_harness_retrieval_attestation,
)

BROKER_REQUEST_SCHEMA = "tgw-context-review-broker-request/v1"
BROKER_BUNDLE_SCHEMA = "tgw-context-review-resource-bundle/v1"
BROKER_CONFIG_SCHEMA = "tgw-context-review-broker-config/v1"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_GRANT_WINDOW_SECONDS = 900


class ReviewContextBrokerError(ValueError):
    """The privileged broker could not prove an exact context retrieval."""


class HarnessRunResolver(Protocol):
    def fetch(self, ref: str) -> Any: ...


class PrivilegedReviewContextBroker:
    """Bridge a fixed privileged backend identity to one provider-bound run."""

    def __init__(
        self, *, backend: HTTPRegisteredResourceResolver,
        backend_execution_identity: str,
        backend_attestation_key_id: str,
        backend_attestation_public_key: str,
        service_id: str, client_id: str, attestation_key_id: str,
        signing_private_key: Ed25519PrivateKey | str | bytes,
        max_retained_runs: int = 1024,
        retained_ttl_seconds: int = 900,
        clock: Any = time.monotonic,
        grant_clock: Any = lambda: datetime.now(timezone.utc),
    ) -> None:
        if (
            not backend_execution_identity
            or not backend_attestation_key_id
            or not backend_attestation_public_key
            or not service_id
            or not client_id
            or not attestation_key_id
            or not isinstance(max_retained_runs, int)
            or not 1 <= max_retained_runs <= 10_000
            or not isinstance(retained_ttl_seconds, int)
            or not 1 <= retained_ttl_seconds <= 86_400
        ):
            raise ReviewContextBrokerError("review context broker configuration is invalid")
        self._backend = backend
        self._backend_execution_identity = backend_execution_identity
        self._backend_attestation_key_id = backend_attestation_key_id
        self._backend_attestation_public_key = backend_attestation_public_key
        self._service_id = service_id
        self._client_id = client_id
        self._attestation_key_id = attestation_key_id
        self._signing_private_key = signing_private_key
        self._max_retained_runs = max_retained_runs
        self._retained_ttl_seconds = retained_ttl_seconds
        self._clock = clock
        self._grant_clock = grant_clock
        self._bundles: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock = threading.Lock()

    @property
    def client_id(self) -> str:
        return self._client_id

    @staticmethod
    def _request(value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema", "card_hash", "role", "execution_identity",
            "handoff_hash", "resource_receipt_hash", "resources",
            "challenge", "skill_contract_hash", "client_id",
            "issued_at", "not_before", "expires_at",
        }
        if (
            not isinstance(value, Mapping)
            or set(value) != required
            or value.get("schema") != BROKER_REQUEST_SCHEMA
            or value.get("role") != "independent-review"
            or not all(
                isinstance(value.get(name), str) and value[name]
                for name in (
                    "card_hash", "execution_identity", "handoff_hash",
                    "resource_receipt_hash", "client_id",
                )
            )
            or not isinstance(value.get("challenge"), str)
            or len(value["challenge"]) != 64
            or not set(value["challenge"]) <= set("0123456789abcdef")
            or not isinstance(value.get("skill_contract_hash"), str)
            or not value["skill_contract_hash"].startswith("sha256:")
            or not isinstance(value.get("resources"), Mapping)
            or set(value["resources"]) != CARD_RESOURCE_NAMES
            or not all(
                isinstance(value.get(name), str)
                for name in ("issued_at", "not_before", "expires_at")
            )
        ):
            raise ReviewContextBrokerError("review context broker request is invalid")
        try:
            issued_at, not_before, expires_at = (
                datetime.fromisoformat(str(value[name]).replace("Z", "+00:00"))
                for name in ("issued_at", "not_before", "expires_at")
            )
        except ValueError as exc:
            raise ReviewContextBrokerError(
                "review context broker grant time is invalid"
            ) from exc
        if (
            any(item.tzinfo is None for item in (issued_at, not_before, expires_at))
            or not issued_at <= not_before < expires_at
            or (expires_at - issued_at).total_seconds() > MAX_GRANT_WINDOW_SECONDS
        ):
            raise ReviewContextBrokerError("review context broker grant window is invalid")
        resources = {}
        for name in sorted(CARD_RESOURCE_NAMES):
            binding = value["resources"][name]
            if (
                not isinstance(binding, Mapping)
                or set(binding) != {"ref", "hash"}
                or not isinstance(binding.get("ref"), str)
                or not binding["ref"]
                or not isinstance(binding.get("hash"), str)
                or not binding["hash"].startswith("sha256:")
            ):
                raise ReviewContextBrokerError("review context broker resource binding is invalid")
            resources[name] = dict(binding)
        return {**dict(value), "resources": resources}

    def validate_grant(self, value: Mapping[str, Any]) -> dict[str, Any]:
        """Validate exact client and wall-clock freshness before grant consumption."""

        request = self._request(value)
        now = self._grant_clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ReviewContextBrokerError("review context broker clock is invalid")
        not_before = datetime.fromisoformat(request["not_before"].replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(request["expires_at"].replace("Z", "+00:00"))
        if request["client_id"] != self._client_id or not not_before <= now < expires_at:
            raise ReviewContextBrokerError("review context broker grant is not active")
        return request

    def execute(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = self.validate_grant(value)
        try:
            run = self._backend.begin_harness_run(
                card_hash=request["card_hash"], role="independent-review",
                execution_identity=self._backend_execution_identity,
                handoff_hash=request["handoff_hash"],
                resource_receipt_hash=request["resource_receipt_hash"],
                resources=request["resources"],
            )
            resolver: HarnessRunResolver = self._backend.for_harness_run(run)
            fetched = {}
            total_bytes = 0
            for name, binding in request["resources"].items():
                resource = resolver.fetch(binding["ref"])
                if resource.content_hash() != binding["hash"]:
                    raise ReviewContextBrokerError(
                        f"review context broker resource differs: {name}"
                    )
                if not isinstance(resource.value, bytes):
                    raise ReviewContextBrokerError(
                        f"review context broker resource is not bytes: {name}"
                    )
                total_bytes += len(resource.value)
                if total_bytes > MAX_BUNDLE_BYTES:
                    raise ReviewContextBrokerError(
                        "review context broker resource bundle exceeds its bound"
                    )
                fetched[name] = {
                    "ref": binding["ref"], "hash": binding["hash"],
                    "content_sha256": "sha256:" + hashlib.sha256(
                        resource.value
                    ).hexdigest(),
                    "content_base64": base64.b64encode(resource.value).decode("ascii"),
                }
            backend_attestation = self._backend.complete_harness_run(run)
            self._backend.verify_harness_retrieval_attestation(
                backend_attestation,
                card_hash=request["card_hash"], role="independent-review",
                execution_identity=self._backend_execution_identity,
                handoff_hash=request["handoff_hash"],
                resource_receipt_hash=request["resource_receipt_hash"],
                resources=request["resources"],
                attestation_key_id=self._backend_attestation_key_id,
                attestation_public_key=self._backend_attestation_public_key,
            )
        except (AttributeError, ResourceVerificationError) as exc:
            raise ReviewContextBrokerError(
                "review context broker backend retrieval failed"
            ) from exc
        attestation = issue_harness_retrieval_attestation(
            {
                "schema": HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
                "service_id": self._service_id, "client_id": self._client_id,
                "run_id": run["run_id"], "card_hash": request["card_hash"],
                "role": request["role"],
                "execution_identity": request["execution_identity"],
                "handoff_hash": request["handoff_hash"],
                "resource_receipt_hash": request["resource_receipt_hash"],
                "resources": request["resources"],
                "attestation_key_id": self._attestation_key_id,
            },
            signing_private_key=self._signing_private_key,
        )
        unsigned_bundle = {
            "schema": BROKER_BUNDLE_SCHEMA,
            "client_id": request["client_id"],
            "challenge": request["challenge"],
            "skill_contract_hash": request["skill_contract_hash"],
            "retrieval_attestation": attestation,
            "resources": fetched,
        }
        bundle = {**unsigned_bundle, "bundle_hash": _hash(unsigned_bundle)}
        if len(_canonical(bundle)) > MAX_BUNDLE_BYTES:
            raise ReviewContextBrokerError(
                "review context broker encoded resource bundle exceeds its bound"
            )
        with self._lock:
            now = self._clock()
            self._bundles = {
                run_id: retained for run_id, retained in self._bundles.items()
                if retained[1] > now
            }
            if len(self._bundles) >= self._max_retained_runs:
                raise ReviewContextBrokerError("review context broker run capacity is exhausted")
            if attestation["run_id"] in self._bundles:
                raise ReviewContextBrokerError("review context broker run identity is duplicated")
            self._bundles[attestation["run_id"]] = (
                bundle, now + self._retained_ttl_seconds,
            )
        return dict(bundle)

    def read_bundle(self, run_id: str, *, client_id: str, consume: bool = True) -> dict[str, Any]:
        """Return one exact client-bound bundle and consume it by default."""

        with self._lock:
            retained = self._bundles.get(run_id)
            if retained is None or retained[1] <= self._clock():
                self._bundles.pop(run_id, None)
                retained = None
            elif retained[0].get("client_id") != client_id:
                retained = None
            elif consume:
                del self._bundles[run_id]
        if retained is None:
            raise ReviewContextBrokerError("review context broker run is unavailable")
        return dict(retained[0])

    def read_bundle_by_challenge(
        self, challenge: str, *, client_id: str, consume: bool = True,
    ) -> dict[str, Any]:
        """Consume the sole exact client/challenge bundle retained by the service."""

        if not isinstance(challenge, str) or len(challenge) != 64:
            raise ReviewContextBrokerError("review context broker challenge is invalid")
        with self._lock:
            now = self._clock()
            self._bundles = {
                run_id: retained for run_id, retained in self._bundles.items()
                if retained[1] > now
            }
            matches = [
                (run_id, retained) for run_id, retained in self._bundles.items()
                if retained[0].get("client_id") == client_id
                and retained[0].get("challenge") == challenge
            ]
            if len(matches) != 1:
                raise ReviewContextBrokerError("review context broker challenge is unavailable")
            run_id, retained = matches[0]
            if consume:
                del self._bundles[run_id]
        return dict(retained[0])

    def read_attestation(self, run_id: str) -> dict[str, Any]:
        """Compatibility accessor for the signed part of a retained bundle."""

        return dict(self.read_bundle(run_id, client_id=self._client_id)["retrieval_attestation"])


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def create_review_context_broker_server(
    broker: PrivilegedReviewContextBroker, *,
    request_grants: Mapping[str, Mapping[str, Any]],
    readback_credentials: Mapping[str, str],
    host: str = "127.0.0.1", port: int = 0,
) -> ThreadingHTTPServer:
    """Create the authenticated one-attempt broker HTTP boundary.

    Request bearer credentials are pre-bound to an exact client and request
    body and are consumed before backend work. Readback uses a disjoint bearer
    namespace. The provider receives neither credential.
    """

    if host not in {"127.0.0.1", "::1"} or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ReviewContextBrokerError("review context broker bind address is invalid")
    grants = {
        credential: PrivilegedReviewContextBroker._request(value)
        for credential, value in request_grants.items()
    }
    if (
        not grants
        or any(not isinstance(key, str) or not key for key in grants)
        or any(grant["client_id"] != broker.client_id for grant in grants.values())
        or set(readback_credentials) != {broker.client_id}
        or any(
            not isinstance(client, str) or not client
            or not isinstance(credential, str) or not credential
            for client, credential in readback_credentials.items()
        )
        or len(set(readback_credentials.values())) != len(readback_credentials)
        or set(grants) & set(readback_credentials.values())
    ):
        raise ReviewContextBrokerError("review context broker credentials are invalid")
    grants_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _json(self, status: int, value: Mapping[str, Any]) -> None:
            raw = _canonical(value)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _bearer(self) -> str | None:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") and len(header) > 7 else None

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/v1/review-context":
                self.send_error(404)
                return
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length) if raw_length is not None else -1
            except ValueError:
                length = -1
            if self.headers.get_content_type() != "application/json" or not 0 <= length <= MAX_REQUEST_BYTES:
                self.send_error(400)
                return
            try:
                value = json.loads(self.rfile.read(length))
                normalized = PrivilegedReviewContextBroker._request(value)
            except (UnicodeDecodeError, json.JSONDecodeError, ReviewContextBrokerError):
                self.send_error(400)
                return
            credential = self._bearer()
            with grants_lock:
                expected = grants.get(credential or "")
                if expected is None or not hmac.compare_digest(
                    _canonical(expected), _canonical(normalized)
                ):
                    self.send_error(404)
                    return
                try:
                    broker.validate_grant(normalized)
                except ReviewContextBrokerError:
                    self.send_error(404)
                    return
                del grants[credential or ""]
            try:
                self._json(200, broker.execute(normalized))
            except ReviewContextBrokerError:
                self.send_error(409)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            run_prefix = "/v1/review-context-runs/"
            challenge_prefix = "/v1/review-context-challenges/"
            suffix = "/bundle"
            if (
                parsed.query
                or not parsed.path.endswith(suffix)
                or not parsed.path.startswith((run_prefix, challenge_prefix))
            ):
                self.send_error(404)
                return
            credential = self._bearer()
            client_id = next((
                client for client, expected in readback_credentials.items()
                if credential is not None and hmac.compare_digest(credential, expected)
            ), None)
            if client_id is None:
                self.send_error(404)
                return
            try:
                if parsed.path.startswith(challenge_prefix):
                    challenge = unquote(
                        parsed.path[len(challenge_prefix):-len(suffix)]
                    )
                    bundle = broker.read_bundle_by_challenge(
                        challenge, client_id=client_id,
                    )
                else:
                    run_id = unquote(parsed.path[len(run_prefix):-len(suffix)])
                    bundle = broker.read_bundle(run_id, client_id=client_id)
                self._json(200, bundle)
            except ReviewContextBrokerError:
                self.send_error(404)

    if host == "::1":
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        return IPv6ThreadingHTTPServer((host, port), Handler)
    return ThreadingHTTPServer((host, port), Handler)


def broker_server_from_config(
    value: Mapping[str, Any], *, environment: Mapping[str, str],
    host: str, port: int,
) -> ThreadingHTTPServer:
    """Construct the non-test service from one externally protected config."""

    required = {
        "schema", "backend_descriptor", "backend_execution_identity",
        "backend_attestation_key_id", "backend_attestation_public_key",
        "service_id", "client_id", "attestation_key_id",
        "attestation_public_key",
        "signing_private_key_env", "request_grants", "readback_clients",
    }
    if not isinstance(value, Mapping) or set(value) != required or value.get("schema") != BROKER_CONFIG_SCHEMA:
        raise ReviewContextBrokerError("review context broker config is invalid")
    signing_env = value.get("signing_private_key_env")
    signing_key = environment.get(signing_env, "") if isinstance(signing_env, str) else ""
    if not signing_key:
        raise ReviewContextBrokerError("review context broker signing key is unavailable")
    try:
        signing_private_key = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(signing_key, validate=True)
        )
        derived_public_key = base64.b64encode(
            signing_private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        ).decode()
        backend_public_key = base64.b64decode(
            str(value.get("backend_attestation_public_key", "")), validate=True,
        )
        configured_public_key = base64.b64decode(
            str(value.get("attestation_public_key", "")), validate=True,
        )
    except ValueError as exc:
        raise ReviewContextBrokerError("review context broker signing key is invalid") from exc
    if (
        len(backend_public_key) != 32
        or len(configured_public_key) != 32
        or derived_public_key != value.get("attestation_public_key")
    ):
        raise ReviewContextBrokerError("review context broker signing identity differs")
    try:
        backend = HTTPRegisteredResourceResolver.from_descriptor(
            value["backend_descriptor"], environment=environment,
        )
        backend.check_health(
            attestation_key_id=str(value["backend_attestation_key_id"]),
        )
    except ResourceVerificationError as exc:
        raise ReviewContextBrokerError("review context broker backend is invalid") from exc
    grants_value = value.get("request_grants")
    readback_value = value.get("readback_clients")
    if not isinstance(grants_value, list) or not grants_value or not isinstance(readback_value, list) or not readback_value:
        raise ReviewContextBrokerError("review context broker client configuration is invalid")
    request_grants = {}
    for item in grants_value:
        if not isinstance(item, Mapping) or set(item) != {"client_id", "credential_env", "request"}:
            raise ReviewContextBrokerError("review context broker request grant is invalid")
        credential = environment.get(str(item["credential_env"]), "")
        request = dict(item["request"]) if isinstance(item["request"], Mapping) else None
        if not credential or request is None or request.get("client_id") != item["client_id"] or credential in request_grants:
            raise ReviewContextBrokerError("review context broker request grant is invalid")
        request_grants[credential] = request
    readback_credentials = {}
    for item in readback_value:
        if not isinstance(item, Mapping) or set(item) != {"client_id", "credential_env"}:
            raise ReviewContextBrokerError("review context broker readback client is invalid")
        credential = environment.get(str(item["credential_env"]), "")
        if (
            not credential
            or item["client_id"] in readback_credentials
            or credential in readback_credentials.values()
        ):
            raise ReviewContextBrokerError("review context broker readback client is invalid")
        readback_credentials[str(item["client_id"])] = credential
    if set(readback_credentials) != {str(value["client_id"])}:
        raise ReviewContextBrokerError("review context broker readback coverage is invalid")
    broker = PrivilegedReviewContextBroker(
        backend=backend,
        backend_execution_identity=str(value["backend_execution_identity"]),
        backend_attestation_key_id=str(value["backend_attestation_key_id"]),
        backend_attestation_public_key=str(value["backend_attestation_public_key"]),
        service_id=str(value["service_id"]), client_id=str(value["client_id"]),
        attestation_key_id=str(value["attestation_key_id"]),
        signing_private_key=signing_private_key,
    )
    return create_review_context_broker_server(
        broker, request_grants=request_grants,
        readback_credentials=readback_credentials, host=host, port=port,
    )


def _load_protected_config(path: Path) -> tuple[dict[str, Any], int, tuple[int, ...]]:
    """Open one root-protected config without following a replaceable leaf."""

    resolved_parent = path.parent.resolve(strict=True)
    for ancestor in (resolved_parent, *resolved_parent.parents):
        value = ancestor.stat(follow_symlinks=False)
        mode = stat.S_IMODE(value.st_mode)
        if value.st_uid != 0 or mode & 0o022 and not mode & stat.S_ISVTX:
            raise ReviewContextBrokerError(
                "review context broker config ancestor is not protected"
            )
    named_before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        value = os.fstat(descriptor)
        identity = (
            value.st_dev, value.st_ino, value.st_uid, value.st_gid,
            stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns,
        )
        if (
            not stat.S_ISREG(value.st_mode)
            or value.st_uid != 0
            or stat.S_IMODE(value.st_mode) & 0o022
            or value.st_nlink != 1
            or value.st_size < 2
            or value.st_size > MAX_REQUEST_BYTES
            or (named_before.st_dev, named_before.st_ino)
            != (value.st_dev, value.st_ino)
        ):
            raise ReviewContextBrokerError("review context broker config is not protected")
        raw = os.pread(descriptor, MAX_REQUEST_BYTES + 1, 0)
        if len(raw) != value.st_size:
            raise ReviewContextBrokerError("review context broker config changed during read")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ReviewContextBrokerError("review context broker config is invalid")
        return parsed, descriptor, identity
    except Exception:
        os.close(descriptor)
        raise


def _attach_config_guard(
    server: ThreadingHTTPServer, *, path: Path, descriptor: int,
    identity: tuple[int, ...],
) -> None:
    original_close = server.server_close

    def guarded_close() -> None:
        try:
            value = os.fstat(descriptor)
            current = (
                value.st_dev, value.st_ino, value.st_uid, value.st_gid,
                stat.S_IMODE(value.st_mode), value.st_nlink, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns,
            )
            named = path.lstat()
            if current != identity or (named.st_dev, named.st_ino) != identity[:2]:
                raise ReviewContextBrokerError(
                    "review context broker config changed while held"
                )
        finally:
            os.close(descriptor)
            original_close()

    server.server_close = guarded_close  # type: ignore[method-assign]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-governed-review-context-broker")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args(argv)
    descriptor: int | None = None
    try:
        value, descriptor, identity = _load_protected_config(arguments.config)
        server = broker_server_from_config(
            value, environment=os.environ, host=arguments.host, port=arguments.port,
        )
        _attach_config_guard(
            server, path=arguments.config, descriptor=descriptor, identity=identity,
        )
    except (OSError, json.JSONDecodeError, ReviewContextBrokerError) as exc:
        if descriptor is not None:
            os.close(descriptor)
        print(json.dumps({"status": "HOLD", "reason": str(exc)}), file=os.sys.stderr)
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
