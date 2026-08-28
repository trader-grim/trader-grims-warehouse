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
import tempfile
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
    resource_service_attestation_key,
    resource_service_catalog_hash,
    verify_resource_service_registration,
)

BROKER_REQUEST_SCHEMA = "tgw-context-review-broker-request/v2"
BROKER_BUNDLE_SCHEMA = "tgw-context-review-resource-bundle/v1"
BROKER_CONFIG_SCHEMA = "tgw-context-review-broker-config/v2"
BROKER_FILE_CONFIG_SCHEMA = "tgw-context-review-broker-config/v3"
BROKER_GRANT_SCHEMA = "tgw-context-review-broker-file-grant/v1"
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
        backend_catalog_ref: str, backend_catalog_hash: str,
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
            or not backend_catalog_ref
            or not backend_catalog_hash
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
        self._backend_catalog_ref = backend_catalog_ref
        self._backend_catalog_hash = backend_catalog_hash
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

    @property
    def backend_catalog_binding(self) -> tuple[str, str]:
        return self._backend_catalog_ref, self._backend_catalog_hash

    @staticmethod
    def _request(value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema", "card_hash", "role", "execution_identity",
            "handoff_hash", "resource_receipt_hash", "resources",
            "challenge", "skill_contract_hash", "client_id",
            "resource_service_catalog_ref", "resource_service_catalog_hash",
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
                    "resource_service_catalog_ref", "resource_service_catalog_hash",
                )
            )
            or not isinstance(value.get("challenge"), str)
            or len(value["challenge"]) != 64
            or not set(value["challenge"]) <= set("0123456789abcdef")
            or not isinstance(value.get("skill_contract_hash"), str)
            or not value["skill_contract_hash"].startswith("sha256:")
            or not value["resource_service_catalog_hash"].startswith("sha256:")
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


def derive_request_bearer(master_credential: str, request: Mapping[str, Any]) -> str:
    """Derive a request-unique bearer without placing it in durable request data."""

    if (
        not isinstance(master_credential, str)
        or len(master_credential) < 32
        or any(character.isspace() for character in master_credential)
    ):
        raise ReviewContextBrokerError("review context broker request credential is invalid")
    normalized = PrivilegedReviewContextBroker._request(request)
    digest = hmac.new(
        master_credential.encode(),
        b"tgw-review-request-bearer/v1\0" + _canonical(normalized),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class FileReviewContextGrantStore:
    """Root-owned, restart-safe one-use exact-request grants."""

    def __init__(
        self, root: Path, *, master_credential: str, client_id: str,
        catalog_ref: str, catalog_hash: str,
    ) -> None:
        if (
            not isinstance(master_credential, str)
            or len(master_credential) < 32
            or any(character.isspace() for character in master_credential)
            or not isinstance(client_id, str)
            or not client_id
            or not isinstance(catalog_ref, str)
            or not catalog_ref
            or not isinstance(catalog_hash, str)
            or not catalog_hash.startswith("sha256:")
        ):
            raise ReviewContextBrokerError(
                "review context broker grant store binding is invalid"
            )
        self.root = root.resolve(strict=True)
        if self.root != root:
            raise ReviewContextBrokerError("review context broker grant root is not direct")
        self.consumed_root = self.root / "consumed"
        for path in (self.root, self.consumed_root):
            observed = path.stat(follow_symlinks=False)
            if (
                path.is_symlink()
                or not stat.S_ISDIR(observed.st_mode)
                or observed.st_uid != 0
                or observed.st_gid != 0
                or stat.S_IMODE(observed.st_mode) & 0o022
            ):
                raise ReviewContextBrokerError("review context broker grant store is not protected")
        self.master_credential = master_credential
        self.client_id = client_id
        self.catalog_ref = catalog_ref
        self.catalog_hash = catalog_hash
        self._lock = threading.Lock()

    def _path(self, challenge: str, *, consumed: bool = False) -> Path:
        if len(challenge) != 64 or not set(challenge) <= set("0123456789abcdef"):
            raise ReviewContextBrokerError("review context broker challenge is invalid")
        return (self.consumed_root if consumed else self.root) / f"{challenge}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            named = path.lstat()
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ReviewContextBrokerError("review context broker grant is unavailable") from exc
        try:
            observed = os.fstat(descriptor)
            if (
                not stat.S_ISREG(observed.st_mode)
                or observed.st_uid != 0
                or observed.st_gid != 0
                or stat.S_IMODE(observed.st_mode) & 0o022
                or observed.st_nlink != 1
                or not 2 <= observed.st_size <= MAX_REQUEST_BYTES
                or (named.st_dev, named.st_ino) != (observed.st_dev, observed.st_ino)
            ):
                raise ReviewContextBrokerError("review context broker grant is not protected")
            raw = os.pread(descriptor, MAX_REQUEST_BYTES + 1, 0)
            after = os.fstat(descriptor)
            if len(raw) != observed.st_size or any(
                getattr(after, field) != getattr(observed, field)
                for field in (
                    "st_dev", "st_ino", "st_mode", "st_uid", "st_gid",
                    "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns",
                )
            ):
                raise ReviewContextBrokerError("review context broker grant changed while held")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ReviewContextBrokerError("review context broker grant is invalid")
            return value
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewContextBrokerError("review context broker grant is invalid") from exc
        finally:
            os.close(descriptor)

    def issue(self, request: Mapping[str, Any]) -> dict[str, str]:
        """Atomically issue one exact grant; idempotent only before consumption."""

        if os.geteuid() != 0:
            raise ReviewContextBrokerError("review context broker grant issuance requires root")
        normalized = PrivilegedReviewContextBroker._request(request)
        if (
            normalized["client_id"] != self.client_id
            or normalized["resource_service_catalog_ref"] != self.catalog_ref
            or normalized["resource_service_catalog_hash"] != self.catalog_hash
        ):
            raise ReviewContextBrokerError("review context broker grant binding differs")
        bearer = derive_request_bearer(self.master_credential, normalized)
        value = {
            "schema": BROKER_GRANT_SCHEMA,
            "request": normalized,
            "request_hash": _hash(normalized),
            "bearer_hash": "sha256:" + hashlib.sha256(bearer.encode()).hexdigest(),
        }
        path = self._path(normalized["challenge"])
        if self._path(normalized["challenge"], consumed=True).exists():
            raise ReviewContextBrokerError("review context broker grant was already consumed")
        if path.exists():
            if self._read(path) != value:
                raise ReviewContextBrokerError("review context broker grant challenge is reused")
            return {"request_hash": value["request_hash"], "bearer_hash": value["bearer_hash"]}
        descriptor, temporary_name = tempfile.mkstemp(prefix=".grant-", dir=self.root)
        temporary = Path(temporary_name)
        try:
            raw = _canonical(value) + b"\n"
            os.write(descriptor, raw)
            os.fsync(descriptor)
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o400)
            os.close(descriptor)
            descriptor = -1
            os.link(temporary, path, follow_symlinks=False)
            parent = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
        return {"request_hash": value["request_hash"], "bearer_hash": value["bearer_hash"]}

    def consume(self, credential: str | None, request: Mapping[str, Any]) -> None:
        """Consume before backend work so crash/replay remains fail-closed."""

        normalized = PrivilegedReviewContextBroker._request(request)
        expected_bearer = derive_request_bearer(self.master_credential, normalized)
        if credential is None or not hmac.compare_digest(credential, expected_bearer):
            raise ReviewContextBrokerError("review context broker grant is unavailable")
        path = self._path(normalized["challenge"])
        with self._lock:
            value = self._read(path)
            if (
                set(value) != {"schema", "request", "request_hash", "bearer_hash"}
                or value.get("schema") != BROKER_GRANT_SCHEMA
                or value.get("request_hash") != _hash(normalized)
                or value.get("request") != normalized
                or value.get("bearer_hash")
                != "sha256:" + hashlib.sha256(expected_bearer.encode()).hexdigest()
            ):
                raise ReviewContextBrokerError("review context broker grant differs")
            try:
                os.replace(path, self._path(normalized["challenge"], consumed=True))
                for directory in (self.root, self.consumed_root):
                    descriptor = os.open(
                        directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    )
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
            except OSError as exc:
                raise ReviewContextBrokerError(
                    "review context broker grant is unavailable"
                ) from exc


class _ConfiguredReviewContextBrokerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def create_review_context_broker_server(
    broker: PrivilegedReviewContextBroker, *,
    request_grants: Mapping[str, Mapping[str, Any]] | None = None,
    grant_store: FileReviewContextGrantStore | None = None,
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
        for credential, value in (request_grants or {}).items()
    }
    catalog_ref, catalog_hash = broker.backend_catalog_binding
    if (
        not grants and grant_store is None
        or any(not isinstance(key, str) or not key for key in grants)
        or any(grant["client_id"] != broker.client_id for grant in grants.values())
        or any(
            grant["resource_service_catalog_ref"] != catalog_ref
            or grant["resource_service_catalog_hash"] != catalog_hash
            for grant in grants.values()
        )
        or set(readback_credentials) != {broker.client_id}
        or any(
            not isinstance(client, str) or not client
            or not isinstance(credential, str) or not credential
            for client, credential in readback_credentials.items()
        )
        or len(set(readback_credentials.values())) != len(readback_credentials)
        or set(grants) & set(readback_credentials.values())
        or grant_store is not None and (
            grant_store.client_id != broker.client_id
        )
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
                try:
                    broker.validate_grant(normalized)
                    if grant_store is not None:
                        grant_store.consume(credential, normalized)
                    else:
                        expected = grants.get(credential or "")
                        if expected is None or not hmac.compare_digest(
                            _canonical(expected), _canonical(normalized)
                        ):
                            raise ReviewContextBrokerError(
                                "review context broker grant is unavailable"
                            )
                        del grants[credential or ""]
                except ReviewContextBrokerError:
                    self.send_error(404)
                    return
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
    return _ConfiguredReviewContextBrokerServer((host, port), Handler)


def broker_server_from_config(
    value: Mapping[str, Any], *, environment: Mapping[str, str],
    host: str, port: int,
) -> ThreadingHTTPServer:
    """Construct the non-test service from one externally protected config."""

    common = {
        "schema", "backend_descriptor", "backend_execution_identity",
        "backend_attestation_key_id", "backend_attestation_public_key",
        "backend_resource_service_catalog",
        "backend_resource_service_catalog_hash",
        "service_id", "client_id", "attestation_key_id",
        "attestation_public_key",
        "signing_private_key_env", "readback_clients",
    }
    legacy = common | {"request_grants"}
    file_backed = common | {
        "request_grant_root", "request_credential_env",
        "request_resource_service_catalog_ref",
        "request_resource_service_catalog_hash",
    }
    if (
        not isinstance(value, Mapping)
        or value.get("schema") == BROKER_CONFIG_SCHEMA and set(value) != legacy
        or value.get("schema") == BROKER_FILE_CONFIG_SCHEMA and set(value) != file_backed
        or value.get("schema") not in {BROKER_CONFIG_SCHEMA, BROKER_FILE_CONFIG_SCHEMA}
    ):
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
        backend_catalog = value["backend_resource_service_catalog"]
        backend_catalog_hash = resource_service_catalog_hash(backend_catalog)
        if backend_catalog_hash != value["backend_resource_service_catalog_hash"]:
            raise ResourceVerificationError(
                "registered resource service catalog hash differs"
            )
        backend_descriptor = verify_resource_service_registration(
            backend_catalog, value["backend_descriptor"],
        )
        backend_key = resource_service_attestation_key(
            backend_catalog, backend_descriptor["id"], backend_descriptor["client_id"],
        )
        if (
            backend_key["attestation_key_id"]
            != value["backend_attestation_key_id"]
            or backend_key["attestation_public_key"]
            != value["backend_attestation_public_key"]
        ):
            raise ResourceVerificationError(
                "registered resource service attestation identity differs"
            )
    except ResourceVerificationError as exc:
        raise ReviewContextBrokerError("review context broker backend is invalid") from exc
    readback_value = value.get("readback_clients")
    if not isinstance(readback_value, list) or not readback_value:
        raise ReviewContextBrokerError("review context broker client configuration is invalid")
    request_grants = {}
    grant_store = None
    if value.get("schema") == BROKER_CONFIG_SCHEMA:
        grants_value = value.get("request_grants")
        if not isinstance(grants_value, list) or not grants_value:
            raise ReviewContextBrokerError("review context broker client configuration is invalid")
        for item in grants_value:
            if not isinstance(item, Mapping) or set(item) != {"client_id", "credential_env", "request"}:
                raise ReviewContextBrokerError("review context broker request grant is invalid")
            credential = environment.get(str(item["credential_env"]), "")
            request = dict(item["request"]) if isinstance(item["request"], Mapping) else None
            if (
                not credential
                or request is None
                or request.get("client_id") != item["client_id"]
                or request.get("resource_service_catalog_ref")
                != backend_catalog["catalog_ref"]
                or request.get("resource_service_catalog_hash") != backend_catalog_hash
                or credential in request_grants
            ):
                raise ReviewContextBrokerError("review context broker request grant is invalid")
            request_grants[credential] = request
    else:
        credential_env = value.get("request_credential_env")
        master_credential = environment.get(str(credential_env), "")
        raw_grant_root = value.get("request_grant_root")
        request_catalog_ref = value.get("request_resource_service_catalog_ref")
        request_catalog_hash = value.get("request_resource_service_catalog_hash")
        if (
            not master_credential
            or not isinstance(raw_grant_root, str)
            or not Path(raw_grant_root).is_absolute()
            or not isinstance(request_catalog_ref, str)
            or not request_catalog_ref
            or not isinstance(request_catalog_hash, str)
            or not request_catalog_hash.startswith("sha256:")
        ):
            raise ReviewContextBrokerError("review context broker grant store configuration is invalid")
        grant_store = FileReviewContextGrantStore(
            Path(raw_grant_root), master_credential=master_credential,
            client_id=str(value["client_id"]),
            catalog_ref=request_catalog_ref,
            catalog_hash=request_catalog_hash,
        )
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
    try:
        backend = HTTPRegisteredResourceResolver.from_descriptor(
            backend_descriptor, environment=environment,
        )
        backend.check_health(
            attestation_key_id=str(value["backend_attestation_key_id"]),
        )
    except ResourceVerificationError as exc:
        raise ReviewContextBrokerError("review context broker backend is invalid") from exc
    broker = PrivilegedReviewContextBroker(
        backend=backend,
        backend_execution_identity=str(value["backend_execution_identity"]),
        backend_attestation_key_id=str(value["backend_attestation_key_id"]),
        backend_attestation_public_key=str(value["backend_attestation_public_key"]),
        backend_catalog_ref=str(backend_catalog["catalog_ref"]),
        backend_catalog_hash=backend_catalog_hash,
        service_id=str(value["service_id"]), client_id=str(value["client_id"]),
        attestation_key_id=str(value["attestation_key_id"]),
        signing_private_key=signing_private_key,
    )
    return create_review_context_broker_server(
        broker, request_grants=request_grants, grant_store=grant_store,
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
