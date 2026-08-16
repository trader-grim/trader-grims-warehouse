"""Registered, content-addressed inputs for governed execution cards.

The runner never receives a second, copied execution context.  Promptcraft
gets a compact card-bound receipt, while an authenticated harness must fetch
the actual resources through its own opened, card-bound service run before it
can return PASS evidence.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

RESOURCE_RECEIPT_SCHEMA = "tgw-execution-resource-receipt/v1"
RESOURCE_SERVICE_SCHEMA = "tgw-registered-resource-service/v2"
RESOURCE_SERVICE_CATALOG_SCHEMA = "tgw-registered-resource-service-catalog/v3"
RESOURCE_SERVICE_HEALTH_SCHEMA = "tgw-registered-resource-health/v1"
RESOURCE_RESPONSE_SCHEMA = "tgw-registered-resource/v1"
HARNESS_RUN_SCHEMA = "tgw-registered-resource-harness-run/v2"
HARNESS_RETRIEVAL_ATTESTATION_SCHEMA = "tgw-registered-resource-retrieval-attestation/v3"
CARD_RESOURCE_NAMES = frozenset(
    {
        "plan_input",
        "plan_commit",
        "plan_graph",
        "codegraph_snapshot",
        "source_tree",
        "execution_environment",
        "authority_conditions",
        "receipt_sink",
    }
)
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SERVICE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MAX_RESOURCE_RESPONSE_BYTES = 4 * 1024 * 1024
_RESOURCE_SERVICE_DESCRIPTOR_FIELDS = {
    "schema", "id", "client_id", "endpoint", "credential_env", "timeout_seconds",
}
# A role is not qualified merely because it was given an URL.  The service
# catalog declares the capability that the role launcher needs: content-addressed
# retrieval of each of the independently bound sources in an execution card.
RESOURCE_SERVICE_CAPABILITIES = frozenset(
    {
        "registered-resource-retrieval",
        "plan-source",
        "plan-graph",
        "codegraph-snapshot",
        "source-tree",
        "execution-environment",
        "authority-conditions",
        "receipt-sink",
    }
)


class ResourceVerificationError(ValueError):
    """A required registered resource was absent, stale, or substituted."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def content_hash(content: bytes) -> str:
    """Return the standard content address for a byte resource."""

    return "sha256:" + hashlib.sha256(content).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _base64(value: Any, *, label: str, length: int) -> bytes:
    if not isinstance(value, str):
        raise ResourceVerificationError(f"{label} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (TypeError, ValueError) as exc:
        raise ResourceVerificationError(f"{label} is invalid") from exc
    if len(decoded) != length or base64.b64encode(decoded).decode("ascii") != value:
        raise ResourceVerificationError(f"{label} is invalid")
    return decoded


def _public_key(value: str) -> Ed25519PublicKey:
    try:
        return Ed25519PublicKey.from_public_bytes(_base64(value, label="harness retrieval attestation public key", length=32))
    except ValueError as exc:
        raise ResourceVerificationError("harness retrieval attestation public key is invalid") from exc


def _private_key(value: Ed25519PrivateKey | str | bytes) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    if isinstance(value, bytes):
        raw = value
    elif isinstance(value, str):
        raw = _base64(value, label="harness retrieval attestation private key", length=32)
    else:
        raise ResourceVerificationError("harness retrieval attestation private key is invalid")
    if len(raw) != 32:
        raise ResourceVerificationError("harness retrieval attestation private key is invalid")
    try:
        return Ed25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise ResourceVerificationError("harness retrieval attestation private key is invalid") from exc


def ed25519_public_key(value: Ed25519PrivateKey | Ed25519PublicKey | str | bytes) -> str:
    """Return the canonical base64 raw Ed25519 public key for a catalog entry."""

    if isinstance(value, Ed25519PublicKey):
        key = value
    else:
        key = _private_key(value).public_key()
    return base64.b64encode(
        key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    ).decode("ascii")


def _attestation_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema", "service_id", "client_id", "run_id", "card_hash", "role", "execution_identity",
        "handoff_hash", "resource_receipt_hash", "resources", "attestation_key_id",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise ResourceVerificationError("harness retrieval attestation is invalid")
    if value.get("schema") != HARNESS_RETRIEVAL_ATTESTATION_SCHEMA:
        raise ResourceVerificationError("harness retrieval attestation schema is invalid")
    if not isinstance(value.get("service_id"), str) or _SERVICE_ID.fullmatch(value["service_id"]) is None:
        raise ResourceVerificationError("harness retrieval attestation service identity is invalid")
    if not isinstance(value.get("client_id"), str) or _CLIENT_ID.fullmatch(value["client_id"]) is None:
        raise ResourceVerificationError("harness retrieval attestation client identity is invalid")
    if not isinstance(value.get("run_id"), str) or _RUN_ID.fullmatch(value["run_id"]) is None:
        raise ResourceVerificationError("harness retrieval attestation run identity is invalid")
    if not isinstance(value.get("role"), str) or not value["role"]:
        raise ResourceVerificationError("harness retrieval attestation role is invalid")
    if not isinstance(value.get("execution_identity"), str) or not value["execution_identity"]:
        raise ResourceVerificationError("harness retrieval attestation execution identity is invalid")
    if not isinstance(value.get("attestation_key_id"), str) or _KEY_ID.fullmatch(value["attestation_key_id"]) is None:
        raise ResourceVerificationError("harness retrieval attestation key identity is invalid")
    for field in ("card_hash", "handoff_hash", "resource_receipt_hash"):
        if not _is_hash(value.get(field)):
            raise ResourceVerificationError("harness retrieval attestation hash is invalid")
    resources = value.get("resources")
    if not isinstance(resources, Mapping) or set(resources) != CARD_RESOURCE_NAMES:
        raise ResourceVerificationError("harness retrieval attestation resources are invalid")
    normalized_resources = {name: dict(resources[name]) for name in sorted(CARD_RESOURCE_NAMES)}
    for name, binding in normalized_resources.items():
        _binding(binding, name)
    return {
        "schema": HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
        "service_id": value["service_id"],
        "client_id": value["client_id"],
        "run_id": value["run_id"],
        "card_hash": value["card_hash"],
        "role": value["role"],
        "execution_identity": value["execution_identity"],
        "handoff_hash": value["handoff_hash"],
        "resource_receipt_hash": value["resource_receipt_hash"],
        "resources": normalized_resources,
        "attestation_key_id": value["attestation_key_id"],
    }


def issue_harness_retrieval_attestation(
    value: Mapping[str, Any], *, signing_private_key: Ed25519PrivateKey | str | bytes,
) -> dict[str, Any]:
    """Issue a canonical Ed25519 retrieval attestation.

    The caller supplies only the signed payload, including the catalog-pinned
    ``attestation_key_id``.  ``attestation_hash`` addresses that payload and
    the signature authenticates the payload plus its hash.  The helper is used
    by the service and by explicit test seams; it never discovers key material
    from source or a catalog.
    """

    payload = _attestation_payload(value)
    attestation_hash = _hash(payload)
    signed = {**payload, "attestation_hash": attestation_hash}
    signature = _private_key(signing_private_key).sign(_canonical(signed))
    return {**signed, "signature": base64.b64encode(signature).decode("ascii")}


def validate_harness_retrieval_attestation(
    attestation: Mapping[str, Any], *, expected: Mapping[str, Any] | None = None,
    attestation_key_id: str | None = None, attestation_public_key: str | None = None,
) -> dict[str, Any]:
    """Validate a durable service retrieval record before it can become evidence.

    A bare hash is not an attestation.  The record carries the service and run
    identities plus every content-addressed card binding.  Its hash covers the
    signatureless payload and the Ed25519 signature covers that payload plus
    the hash.  ``expected`` lets the dispatch and candidate paths bind it to
    independently held card and handoff identities.  Passing both a public key
    and key id authenticates it against the catalog; callers that merely parse
    an artifact may omit both, but must not treat that as trusted evidence.
    """
    required = {
        "schema", "service_id", "client_id", "run_id", "card_hash", "role", "execution_identity",
        "handoff_hash", "resource_receipt_hash", "resources", "attestation_key_id",
        "attestation_hash", "signature",
    }
    if not isinstance(attestation, Mapping) or set(attestation) != required:
        raise ResourceVerificationError("harness retrieval attestation is invalid")
    payload = _attestation_payload(
        {key: value for key, value in attestation.items() if key not in {"attestation_hash", "signature"}}
    )
    claimed = attestation.get("attestation_hash")
    if not _is_hash(claimed) or claimed != _hash(payload):
        raise ResourceVerificationError("harness retrieval attestation hash mismatch")
    signature = _base64(attestation.get("signature"), label="harness retrieval attestation signature", length=64)
    if (attestation_key_id is None) != (attestation_public_key is None):
        raise ResourceVerificationError("harness retrieval attestation trust binding is invalid")
    if attestation_key_id is not None:
        if not isinstance(attestation_key_id, str) or _KEY_ID.fullmatch(attestation_key_id) is None:
            raise ResourceVerificationError("harness retrieval attestation trust binding is invalid")
        if payload["attestation_key_id"] != attestation_key_id:
            raise ResourceVerificationError("harness retrieval attestation key identity mismatch")
        try:
            _public_key(str(attestation_public_key)).verify(
                signature, _canonical({**payload, "attestation_hash": claimed})
            )
        except InvalidSignature as exc:
            raise ResourceVerificationError("harness retrieval attestation signature is invalid") from exc
    if expected is not None:
        for key, value in expected.items():
            if payload.get(key) != value:
                raise ResourceVerificationError("harness retrieval attestation binding mismatch")
    return {**payload, "attestation_hash": claimed, "signature": attestation["signature"]}


def validate_resource_service_descriptor(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the sole portable resource-service descriptor contract."""

    if not isinstance(descriptor, Mapping) or set(descriptor) != _RESOURCE_SERVICE_DESCRIPTOR_FIELDS:
        raise ResourceVerificationError("registered resource service descriptor is invalid")
    if descriptor["schema"] != RESOURCE_SERVICE_SCHEMA:
        raise ResourceVerificationError("registered resource service descriptor schema is invalid")
    service_id = descriptor["id"]
    client_id = descriptor["client_id"]
    endpoint = descriptor["endpoint"]
    timeout_seconds = descriptor["timeout_seconds"]
    credential_env = descriptor["credential_env"]
    if not isinstance(service_id, str) or _SERVICE_ID.fullmatch(service_id) is None:
        raise ResourceVerificationError("registered resource service id is invalid")
    if not isinstance(client_id, str) or _CLIENT_ID.fullmatch(client_id) is None:
        raise ResourceVerificationError("registered resource service client identity is invalid")
    if not isinstance(endpoint, str):
        raise ResourceVerificationError("registered resource service endpoint is invalid")
    parsed = urlsplit(endpoint)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.scheme == "http" and not loopback)
    ):
        raise ResourceVerificationError("registered resource service endpoint must use HTTPS or loopback HTTP")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise ResourceVerificationError("registered resource service timeout is invalid")
    if credential_env is not None and (
        not isinstance(credential_env, str) or _ENV_NAME.fullmatch(credential_env) is None
    ):
        raise ResourceVerificationError("registered resource service credential environment is invalid")
    return {
        "schema": RESOURCE_SERVICE_SCHEMA,
        "id": service_id,
        "client_id": client_id,
        "endpoint": endpoint.rstrip("/"),
        "credential_env": credential_env,
        "timeout_seconds": timeout_seconds,
    }


def resource_service_descriptor_hash(descriptor: Mapping[str, Any]) -> str:
    """Hash the normalized service descriptor that a card must bind."""

    return _hash(validate_resource_service_descriptor(descriptor))


def validate_resource_service_catalog(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Validate an immutable Plan-bound qualified resource-service catalog.

    The catalog is a separate, content-addressed execution input.  A descriptor
    alone only tells a runner where to connect; it cannot qualify that endpoint
    or substitute for the Plan binding carried by a governed execution card.
    """

    if not isinstance(catalog, Mapping) or set(catalog) != {"schema", "catalog_ref", "plan_commit", "services"}:
        raise ResourceVerificationError("registered resource service catalog is invalid")
    if catalog["schema"] != RESOURCE_SERVICE_CATALOG_SCHEMA:
        raise ResourceVerificationError("registered resource service catalog schema is invalid")
    if not isinstance(catalog["catalog_ref"], str) or not catalog["catalog_ref"]:
        raise ResourceVerificationError("registered resource service catalog reference is invalid")
    if not isinstance(catalog["plan_commit"], str) or _GIT_COMMIT.fullmatch(catalog["plan_commit"]) is None:
        raise ResourceVerificationError("registered resource service catalog Plan commit is invalid")
    services = catalog["services"]
    if not isinstance(services, list) or not services:
        raise ResourceVerificationError("registered resource service catalog is empty")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in services:
        if not isinstance(item, Mapping) or set(item) != {
            "id", "client_id", "descriptor_hash", "capabilities", "attestation_key_id", "attestation_public_key",
        }:
            raise ResourceVerificationError("registered resource service catalog entry is invalid")
        service_id = item["id"]
        client_id = item["client_id"]
        descriptor_hash = item["descriptor_hash"]
        capabilities = item["capabilities"]
        attestation_key_id = item["attestation_key_id"]
        attestation_public_key = item["attestation_public_key"]
        service_identity = (service_id, client_id)
        if (
            not isinstance(service_id, str)
            or _SERVICE_ID.fullmatch(service_id) is None
            or not isinstance(client_id, str)
            or _CLIENT_ID.fullmatch(client_id) is None
            or service_identity in seen
        ):
            raise ResourceVerificationError("registered resource service catalog identity is invalid")
        if not _is_hash(descriptor_hash):
            raise ResourceVerificationError("registered resource service catalog descriptor hash is invalid")
        if not isinstance(attestation_key_id, str) or _KEY_ID.fullmatch(attestation_key_id) is None:
            raise ResourceVerificationError("registered resource service catalog attestation key identity is invalid")
        _public_key(attestation_public_key)
        if (
            not isinstance(capabilities, list)
            or not all(isinstance(value, str) and value for value in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise ResourceVerificationError("registered resource service catalog capabilities are invalid")
        seen.add(service_identity)
        normalized.append(
            {
                "id": service_id,
                "client_id": client_id,
                "descriptor_hash": descriptor_hash,
                "capabilities": sorted(capabilities),
                "attestation_key_id": attestation_key_id,
                "attestation_public_key": attestation_public_key,
            }
        )
    return {
        "schema": RESOURCE_SERVICE_CATALOG_SCHEMA,
        "catalog_ref": catalog["catalog_ref"],
        "plan_commit": catalog["plan_commit"],
        "services": normalized,
    }


def resource_service_catalog_hash(catalog: Mapping[str, Any]) -> str:
    """Return the immutable identity that a card must carry for its catalog."""

    return _hash(validate_resource_service_catalog(catalog))


def load_resource_service_catalog(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        value = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceVerificationError("registered resource service catalog is unreadable") from exc
    return validate_resource_service_catalog(value)


def resource_service_attestation_key(
    catalog: Mapping[str, Any], service_id: str, client_id: str,
) -> dict[str, str]:
    """Return the immutable catalog-pinned trust key for one service identity."""

    normalized = validate_resource_service_catalog(catalog)
    entry = next(
        (item for item in normalized["services"] if item["id"] == service_id and item["client_id"] == client_id), None,
    )
    if entry is None:
        raise ResourceVerificationError("registered resource service is absent from qualified catalog")
    return {
        "attestation_key_id": entry["attestation_key_id"],
        "attestation_public_key": entry["attestation_public_key"],
    }


def verify_resource_service_registration(
    catalog: Mapping[str, Any], descriptor: Mapping[str, Any], *, resolver: "HTTPRegisteredResourceResolver | None" = None,
) -> dict[str, Any]:
    """Require exact catalog identity, capabilities, descriptor and live health."""

    normalized_catalog = validate_resource_service_catalog(catalog)
    normalized_descriptor = validate_resource_service_descriptor(descriptor)
    entry = next(
        (
            item for item in normalized_catalog["services"]
            if item["id"] == normalized_descriptor["id"] and item["client_id"] == normalized_descriptor["client_id"]
        ),
        None,
    )
    if entry is None:
        raise ResourceVerificationError("registered resource service is absent from qualified catalog")
    if entry["descriptor_hash"] != resource_service_descriptor_hash(normalized_descriptor):
        raise ResourceVerificationError("registered resource service descriptor is not catalog-bound")
    missing = RESOURCE_SERVICE_CAPABILITIES - set(entry["capabilities"])
    if missing:
        raise ResourceVerificationError(
            "registered resource service lacks capabilities: " + ", ".join(sorted(missing))
        )
    if resolver is not None:
        resolver.check_health(attestation_key_id=entry["attestation_key_id"])
    return normalized_descriptor


def verify_card_resource_service(
    card: Mapping[str, Any], descriptor: Mapping[str, Any], catalog: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Check that descriptor and qualified catalog are both card-bound."""

    normalized_catalog = verify_card_resource_service_catalog(card, catalog)
    binding = card["resource_service"]
    normalized = validate_resource_service_descriptor(descriptor)
    if (
        binding["id"] != normalized["id"]
        or binding["client_id"] != normalized["client_id"]
        or binding["descriptor_hash"] != resource_service_descriptor_hash(normalized)
    ):
        raise ResourceVerificationError("card resource service binding mismatch")
    return normalized, normalized_catalog


def verify_card_resource_service_catalog(
    card: Mapping[str, Any], catalog: Mapping[str, Any],
) -> dict[str, Any]:
    """Require the card's service identity to resolve inside its exact catalog."""

    binding = card.get("resource_service") if isinstance(card, Mapping) else None
    if not isinstance(binding, Mapping) or set(binding) != {
        "id", "client_id", "descriptor_hash", "catalog_ref", "catalog_hash",
    }:
        raise ResourceVerificationError("card resource service binding is invalid")
    normalized_catalog = validate_resource_service_catalog(catalog)
    if (
        binding["catalog_ref"] != normalized_catalog["catalog_ref"]
        or binding["catalog_hash"] != resource_service_catalog_hash(normalized_catalog)
        or normalized_catalog["plan_commit"] != card.get("plan_commit")
        or not isinstance(binding["client_id"], str)
        or _CLIENT_ID.fullmatch(binding["client_id"]) is None
    ):
        raise ResourceVerificationError("card resource service catalog binding mismatch")
    entry = next(
        (
            item for item in normalized_catalog["services"]
            if item["id"] == binding["id"] and item["client_id"] == binding["client_id"]
        ),
        None,
    )
    if entry is None or entry["descriptor_hash"] != binding["descriptor_hash"]:
        raise ResourceVerificationError("card resource service is not qualified by its catalog")
    return normalized_catalog


@dataclass(frozen=True)
class RegisteredResource:
    """One resource exposed by a registered environment service.

    ``digest`` is evaluated for every fetch.  Most resources use
    :meth:`from_bytes`; a provider with a native content hash (for example a
    source-tree snapshot service) may register its own deterministic digest.
    """

    value: Any
    digest: Callable[[Any], str]

    @classmethod
    def from_bytes(cls, value: bytes | str) -> "RegisteredResource":
        content = value.encode() if isinstance(value, str) else value
        if not isinstance(content, bytes):
            raise ResourceVerificationError("registered byte resource is invalid")
        return cls(content, lambda item: content_hash(item))

    @classmethod
    def from_json(cls, value: Any) -> "RegisteredResource":
        return cls(_canonical(value), lambda item: content_hash(item))

    def content_hash(self) -> str:
        claimed = self.digest(self.value)
        if not _is_hash(claimed):
            raise ResourceVerificationError("registered resource produced an invalid content hash")
        return claimed


class ResourceResolver(Protocol):
    """Provider-neutral registered environment service used at dispatch time."""

    def fetch(self, ref: str) -> RegisteredResource:
        """Fetch the current registered resource for ``ref``."""


class RegisteredResourceResolver:
    """Small in-process registry suitable for environment-service adapters/tests."""

    def __init__(self, resources: Mapping[str, RegisteredResource | bytes | str | Any]):
        normalized: dict[str, RegisteredResource] = {}
        for ref, resource in resources.items():
            if not isinstance(ref, str) or not ref:
                raise ResourceVerificationError("registered resource reference is invalid")
            if isinstance(resource, RegisteredResource):
                normalized[ref] = resource
            elif isinstance(resource, (bytes, str)):
                normalized[ref] = RegisteredResource.from_bytes(resource)
            else:
                normalized[ref] = RegisteredResource.from_json(resource)
        self._resources = normalized

    def fetch(self, ref: str) -> RegisteredResource:
        try:
            return self._resources[ref]
        except KeyError as exc:
            raise ResourceVerificationError(f"registered resource is unavailable: {ref}") from exc


class HarnessRunResolver:
    """A harness-scoped view that causes the service to audit each fetch."""

    def __init__(self, service: "HTTPRegisteredResourceResolver", run_id: str) -> None:
        self._service = service
        self._run_id = run_id

    def fetch(self, ref: str) -> RegisteredResource:
        return self._service._fetch(ref, harness_run_id=self._run_id)


class HTTPRegisteredResourceResolver:
    """Retrieve raw content from one explicitly registered HTTP resource service.

    The service has no authority to assert a content hash: the resolver
    computes it from the returned bytes and compares it to the immutable card.
    This adapter accepts only the small versioned response contract below and
    rejects redirects, unregistered service identities, malformed base64, and
    transport failures before a launcher can be reached.
    """

    def __init__(
        self, *, service_id: str, client_id: str, endpoint: str, credential: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        normalized = validate_resource_service_descriptor(
            {
                "schema": RESOURCE_SERVICE_SCHEMA,
                "id": service_id,
                "client_id": client_id,
                "endpoint": endpoint,
                "credential_env": None,
                "timeout_seconds": timeout_seconds,
            }
        )
        if credential is not None and (not isinstance(credential, str) or not credential):
            raise ResourceVerificationError("registered resource service credential is invalid")
        self._service_id = normalized["id"]
        self._client_id = normalized["client_id"]
        self._endpoint = normalized["endpoint"]
        self._credential = credential
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_descriptor(
        cls, descriptor: Mapping[str, Any], *, environment: Mapping[str, str] | None = None,
    ) -> "HTTPRegisteredResourceResolver":
        normalized = validate_resource_service_descriptor(descriptor)
        credential_env = normalized["credential_env"]
        credential = None
        if credential_env is not None:
            if not isinstance(credential_env, str) or _ENV_NAME.fullmatch(credential_env) is None:
                raise ResourceVerificationError("registered resource service credential environment is invalid")
            values = os.environ if environment is None else environment
            credential = values.get(credential_env)
            if not credential:
                raise ResourceVerificationError("registered resource service credential is unavailable")
        return cls(
            service_id=normalized["id"],
            client_id=normalized["client_id"],
            endpoint=normalized["endpoint"],
            credential=credential,
            timeout_seconds=normalized["timeout_seconds"],
        )

    def check_health(self, *, attestation_key_id: str) -> None:
        """Fail closed unless the catalog-selected service reports its signing identity."""

        if not isinstance(attestation_key_id, str) or _KEY_ID.fullmatch(attestation_key_id) is None:
            raise ResourceVerificationError("registered resource service attestation key identity is invalid")

        target = self._endpoint + "/v1/health"
        request = Request(target, method="GET")
        request.add_header("Accept", "application/json")
        if self._credential is not None:
            request.add_header("Authorization", f"Bearer {self._credential}")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec: catalog-bound endpoint
                if response.geturl() != target or response.headers.get_content_type() != "application/json":
                    raise ResourceVerificationError("registered resource service health response is invalid")
                payload = response.read(16 * 1024)
        except ResourceVerificationError:
            raise
        except (HTTPError, URLError, OSError) as exc:
            raise ResourceVerificationError("registered resource service health check failed") from exc
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResourceVerificationError("registered resource service health response is not JSON") from exc
        if value != {
            "schema": RESOURCE_SERVICE_HEALTH_SCHEMA,
            "service_id": self._service_id,
            "attestation_key_id": attestation_key_id,
            "status": "healthy",
        }:
            raise ResourceVerificationError("registered resource service health identity is invalid")

    def _json_request(
        self, target: str, *, method: str, value: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        data = None if value is None else _canonical(value)
        request = Request(target, data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self._credential is not None:
            request.add_header("Authorization", f"Bearer {self._credential}")
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec: registered endpoint
                if response.geturl() != target or response.headers.get_content_type() != "application/json":
                    raise ResourceVerificationError("registered resource service attestation response is invalid")
                payload = response.read(128 * 1024)
        except ResourceVerificationError:
            raise
        except (HTTPError, URLError, OSError) as exc:
            raise ResourceVerificationError("registered resource service attestation request failed") from exc
        try:
            result = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResourceVerificationError("registered resource service attestation response is not JSON") from exc
        if not isinstance(result, Mapping):
            raise ResourceVerificationError("registered resource service attestation response is invalid")
        return result

    def begin_harness_run(
        self, *, card_hash: str, role: str, execution_identity: str, handoff_hash: str,
        resource_receipt_hash: str, resources: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Ask the service to open an auditable harness-specific retrieval run."""
        if not all(_is_hash(value) for value in (card_hash, handoff_hash, resource_receipt_hash)):
            raise ResourceVerificationError("harness retrieval run hash binding is invalid")
        if not isinstance(role, str) or not role or not isinstance(execution_identity, str) or not execution_identity:
            raise ResourceVerificationError("harness retrieval run identity is invalid")
        expected_resources = {name: resources[name] for name in sorted(CARD_RESOURCE_NAMES)} if set(resources) == CARD_RESOURCE_NAMES else None
        if expected_resources is None:
            raise ResourceVerificationError("harness retrieval run resources are invalid")
        payload = {
            "schema": HARNESS_RUN_SCHEMA, "service_id": self._service_id,
            "client_id": self._client_id,
            "card_hash": card_hash, "role": role, "execution_identity": execution_identity,
            "handoff_hash": handoff_hash, "resource_receipt_hash": resource_receipt_hash,
            "resources": expected_resources,
        }
        result = dict(self._json_request(self._endpoint + "/v1/harness-runs", method="POST", value=payload))
        required = set(payload) | {"run_id"}
        if set(result) != required or any(result[key] != payload[key] for key in payload):
            raise ResourceVerificationError("registered resource service harness run binding is invalid")
        if not isinstance(result["run_id"], str) or _RUN_ID.fullmatch(result["run_id"]) is None:
            raise ResourceVerificationError("registered resource service harness run id is invalid")
        return result

    def for_harness_run(self, run: Mapping[str, Any]) -> HarnessRunResolver:
        if not isinstance(run, Mapping) or run.get("schema") != HARNESS_RUN_SCHEMA:
            raise ResourceVerificationError("harness retrieval run is invalid")
        if run.get("service_id") != self._service_id or run.get("client_id") != self._client_id:
            raise ResourceVerificationError("harness retrieval run client binding is invalid")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ResourceVerificationError("harness retrieval run id is invalid")
        return HarnessRunResolver(self, run_id)

    def complete_harness_run(self, run: Mapping[str, Any]) -> dict[str, Any]:
        """Close a run after the harness fetched every bound source itself."""
        if not isinstance(run, Mapping) or run.get("schema") != HARNESS_RUN_SCHEMA:
            raise ResourceVerificationError("harness retrieval run is invalid")
        if run.get("service_id") != self._service_id or run.get("client_id") != self._client_id:
            raise ResourceVerificationError("harness retrieval run client binding is invalid")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ResourceVerificationError("harness retrieval run id is invalid")
        result = dict(self._json_request(
            self._endpoint + "/v1/harness-runs/" + quote(run_id, safe="") + "/complete",
            method="POST", value={"schema": HARNESS_RUN_SCHEMA, "run_id": run_id},
        ))
        verified = validate_harness_retrieval_attestation(result)
        if verified.get("service_id") != self._service_id or verified.get("run_id") != run_id:
            raise ResourceVerificationError("registered resource service retrieval attestation binding is invalid")
        return verified

    def verify_harness_retrieval_attestation(
        self, attestation: Mapping[str, Any], *, card_hash: str, role: str,
        execution_identity: str, handoff_hash: str, resource_receipt_hash: str,
        resources: Mapping[str, Any], attestation_key_id: str,
        attestation_public_key: str,
    ) -> dict[str, Any]:
        """Read back the service record; a runner cannot self-attest by echoing."""
        local = validate_harness_retrieval_attestation(
            attestation,
            attestation_key_id=attestation_key_id,
            attestation_public_key=attestation_public_key,
        )
        run_id = local.get("run_id")
        if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
            raise ResourceVerificationError("harness retrieval attestation run id is invalid")
        recorded = dict(self._json_request(
            self._endpoint + "/v1/harness-runs/" + quote(run_id, safe="") + "/attestation",
            method="GET",
        ))
        if recorded != local:
            raise ResourceVerificationError("harness retrieval attestation was not issued by the service")
        expected = {
            "schema": HARNESS_RETRIEVAL_ATTESTATION_SCHEMA, "service_id": self._service_id,
            "client_id": self._client_id,
            "card_hash": card_hash, "role": role, "execution_identity": execution_identity,
            "handoff_hash": handoff_hash, "resource_receipt_hash": resource_receipt_hash,
            "resources": {name: resources[name] for name in sorted(CARD_RESOURCE_NAMES)},
        }
        return validate_harness_retrieval_attestation(
            recorded,
            expected=expected,
            attestation_key_id=attestation_key_id,
            attestation_public_key=attestation_public_key,
        )

    def fetch(self, ref: str) -> RegisteredResource:
        raise ResourceVerificationError("registered resource service retrieval requires a harness run")

    def _fetch(self, ref: str, *, harness_run_id: str | None = None) -> RegisteredResource:
        if not isinstance(ref, str) or not ref:
            raise ResourceVerificationError("registered resource reference is invalid")
        target = self._endpoint + "/v1/resources/" + quote(ref, safe="")
        request = Request(target, method="GET")
        request.add_header("Accept", "application/json")
        if self._credential is not None:
            request.add_header("Authorization", f"Bearer {self._credential}")
        if harness_run_id is not None:
            request.add_header("X-TGW-Harness-Run", harness_run_id)
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # nosec: descriptor is an explicit registered service
                if response.geturl() != target:
                    raise ResourceVerificationError("registered resource service redirected request")
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise ResourceVerificationError("registered resource service response type is invalid")
                payload = response.read(_MAX_RESOURCE_RESPONSE_BYTES + 1)
        except ResourceVerificationError:
            raise
        except HTTPError as exc:
            if exc.code == 404:
                raise ResourceVerificationError(f"registered resource is unavailable: {ref}") from exc
            raise ResourceVerificationError("registered resource service request failed") from exc
        except (URLError, OSError) as exc:
            raise ResourceVerificationError("registered resource service request failed") from exc
        if len(payload) > _MAX_RESOURCE_RESPONSE_BYTES:
            raise ResourceVerificationError("registered resource service response is too large")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResourceVerificationError("registered resource service response is not JSON") from exc
        required = {"schema", "service_id", "ref", "content_base64"}
        if not isinstance(value, Mapping) or set(value) != required:
            raise ResourceVerificationError("registered resource service response contract is invalid")
        if value["schema"] != RESOURCE_RESPONSE_SCHEMA or value["service_id"] != self._service_id:
            raise ResourceVerificationError("registered resource service response identity is invalid")
        if value["ref"] != ref or not isinstance(value["content_base64"], str):
            raise ResourceVerificationError("registered resource service response binding is invalid")
        try:
            content = base64.b64decode(value["content_base64"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ResourceVerificationError("registered resource service content encoding is invalid") from exc
        return RegisteredResource.from_bytes(content)


def _binding(value: Any, name: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"ref", "hash"}:
        raise ResourceVerificationError(f"card binding {name} must contain only ref and hash")
    ref, expected_hash = value["ref"], value["hash"]
    if not isinstance(ref, str) or not ref:
        raise ResourceVerificationError(f"card binding {name} reference is invalid")
    if not _is_hash(expected_hash):
        raise ResourceVerificationError(f"card binding {name} content hash is invalid")
    return ref, expected_hash


def card_resource_receipt(card: Mapping[str, Any]) -> dict[str, Any]:
    """Build the compact receipt that binds a card to its declared resources.

    This receipt deliberately does not certify a controller-side resource
    fetch.  The resource service forbids bare reads, so content verification
    occurs only inside the receiver's authenticated harness run and is proven
    by the resulting signed retrieval attestation.
    """

    if not isinstance(card, Mapping):
        raise ResourceVerificationError("execution card is invalid")
    if not isinstance(card.get("plan_commit"), str) or not card["plan_commit"]:
        raise ResourceVerificationError("card Plan commit is invalid")
    if not isinstance(card.get("card_hash"), str) or not _is_hash(card["card_hash"]):
        raise ResourceVerificationError("card hash is invalid")
    unsigned = dict(card)
    claimed_card_hash = unsigned.pop("card_hash")
    if claimed_card_hash != _hash(unsigned):
        raise ResourceVerificationError("card hash mismatch")
    bindings = card.get("bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != CARD_RESOURCE_NAMES:
        raise ResourceVerificationError("card bindings must contain the exact required resource set")
    plan_graph_ref, _ = _binding(bindings["plan_graph"], "plan_graph")
    codegraph_ref, _ = _binding(bindings["codegraph_snapshot"], "codegraph_snapshot")
    if plan_graph_ref == codegraph_ref:
        raise ResourceVerificationError("Plan Graph and CodeGraph must use distinct registered references")
    resources = {
        name: {"ref": _binding(bindings[name], name)[0], "hash": _binding(bindings[name], name)[1]}
        for name in sorted(CARD_RESOURCE_NAMES)
    }
    receipt_unsigned = {
        "schema": RESOURCE_RECEIPT_SCHEMA,
        "card_hash": card["card_hash"],
        "plan_commit": card["plan_commit"],
        "resources": resources,
    }
    return {**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}


def verify_card_resources(
    card: Mapping[str, Any], resolver: ResourceResolver
) -> dict[str, Any]:
    """Verify resource bytes fetched within a caller-provided bound resolver.

    An HTTP resolver only supplies such a resolver through
    :meth:`HTTPRegisteredResourceResolver.for_harness_run`; bare service reads
    are denied.  In-process resolvers remain a narrow unit-test seam.
    """

    expected_receipt = card_resource_receipt(card)
    bindings = expected_receipt["resources"]

    resolved: dict[str, dict[str, str]] = {}
    for name in sorted(CARD_RESOURCE_NAMES):
        ref, expected_hash = _binding(bindings[name], name)
        try:
            resource = resolver.fetch(ref)
        except ResourceVerificationError:
            raise
        except Exception as exc:  # a service error must not open a launch path
            raise ResourceVerificationError(f"registered resource fetch failed: {name}") from exc
        if not isinstance(resource, RegisteredResource):
            raise ResourceVerificationError(f"registered resource {name} is invalid")
        try:
            actual_hash = resource.content_hash()
        except ResourceVerificationError:
            raise
        except Exception as exc:
            raise ResourceVerificationError(
                f"registered resource {name} content hash could not be computed"
            ) from exc
        if actual_hash != expected_hash:
            raise ResourceVerificationError(f"registered resource {name} content hash mismatch")
        if name == "plan_commit":
            try:
                bound_commit = resource.value.decode() if isinstance(resource.value, bytes) else str(resource.value)
            except UnicodeDecodeError as exc:
                raise ResourceVerificationError("registered Plan commit is not text") from exc
            if bound_commit != card["plan_commit"]:
                raise ResourceVerificationError("registered Plan commit does not match card")
        resolved[name] = {"ref": ref, "hash": actual_hash}
    if resolved != expected_receipt["resources"]:
        raise ResourceVerificationError("registered resource receipt binding mismatch")
    return expected_receipt
