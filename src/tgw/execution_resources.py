"""Registered, content-addressed inputs for governed execution cards.

The runner never receives a second, copied execution context.  It receives a
Promptcraft handoff whose resource receipt proves that the card's registered
references were fetched and checked before the handoff was created.
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

RESOURCE_RECEIPT_SCHEMA = "tgw-execution-resource-receipt/v1"
RESOURCE_SERVICE_SCHEMA = "tgw-registered-resource-service/v1"
RESOURCE_SERVICE_CATALOG_SCHEMA = "tgw-registered-resource-service-catalog/v1"
RESOURCE_SERVICE_HEALTH_SCHEMA = "tgw-registered-resource-health/v1"
RESOURCE_RESPONSE_SCHEMA = "tgw-registered-resource/v1"
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
_MAX_RESOURCE_RESPONSE_BYTES = 4 * 1024 * 1024
_RESOURCE_SERVICE_DESCRIPTOR_FIELDS = {
    "schema", "id", "endpoint", "credential_env", "timeout_seconds",
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


def validate_resource_service_descriptor(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize the sole portable resource-service descriptor contract."""

    if not isinstance(descriptor, Mapping) or set(descriptor) != _RESOURCE_SERVICE_DESCRIPTOR_FIELDS:
        raise ResourceVerificationError("registered resource service descriptor is invalid")
    if descriptor["schema"] != RESOURCE_SERVICE_SCHEMA:
        raise ResourceVerificationError("registered resource service descriptor schema is invalid")
    service_id = descriptor["id"]
    endpoint = descriptor["endpoint"]
    timeout_seconds = descriptor["timeout_seconds"]
    credential_env = descriptor["credential_env"]
    if not isinstance(service_id, str) or _SERVICE_ID.fullmatch(service_id) is None:
        raise ResourceVerificationError("registered resource service id is invalid")
    if not isinstance(endpoint, str):
        raise ResourceVerificationError("registered resource service endpoint is invalid")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ResourceVerificationError("registered resource service endpoint is invalid")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or not 1 <= timeout_seconds <= 60:
        raise ResourceVerificationError("registered resource service timeout is invalid")
    if credential_env is not None and (
        not isinstance(credential_env, str) or _ENV_NAME.fullmatch(credential_env) is None
    ):
        raise ResourceVerificationError("registered resource service credential environment is invalid")
    return {
        "schema": RESOURCE_SERVICE_SCHEMA,
        "id": service_id,
        "endpoint": endpoint.rstrip("/"),
        "credential_env": credential_env,
        "timeout_seconds": timeout_seconds,
    }


def resource_service_descriptor_hash(descriptor: Mapping[str, Any]) -> str:
    """Hash the normalized service descriptor that a card must bind."""

    return _hash(validate_resource_service_descriptor(descriptor))


def validate_resource_service_catalog(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the checked-in qualified service catalog, not just an URL file."""

    if not isinstance(catalog, Mapping) or set(catalog) != {"schema", "services"}:
        raise ResourceVerificationError("registered resource service catalog is invalid")
    if catalog["schema"] != RESOURCE_SERVICE_CATALOG_SCHEMA:
        raise ResourceVerificationError("registered resource service catalog schema is invalid")
    services = catalog["services"]
    if not isinstance(services, list) or not services:
        raise ResourceVerificationError("registered resource service catalog is empty")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in services:
        if not isinstance(item, Mapping) or set(item) != {"id", "descriptor_hash", "capabilities"}:
            raise ResourceVerificationError("registered resource service catalog entry is invalid")
        service_id = item["id"]
        descriptor_hash = item["descriptor_hash"]
        capabilities = item["capabilities"]
        if not isinstance(service_id, str) or _SERVICE_ID.fullmatch(service_id) is None or service_id in seen:
            raise ResourceVerificationError("registered resource service catalog identity is invalid")
        if not _is_hash(descriptor_hash):
            raise ResourceVerificationError("registered resource service catalog descriptor hash is invalid")
        if (
            not isinstance(capabilities, list)
            or not all(isinstance(value, str) and value for value in capabilities)
            or len(set(capabilities)) != len(capabilities)
        ):
            raise ResourceVerificationError("registered resource service catalog capabilities are invalid")
        seen.add(service_id)
        normalized.append(
            {
                "id": service_id,
                "descriptor_hash": descriptor_hash,
                "capabilities": sorted(capabilities),
            }
        )
    return {"schema": RESOURCE_SERVICE_CATALOG_SCHEMA, "services": normalized}


def load_resource_service_catalog(path: str | os.PathLike[str]) -> dict[str, Any]:
    try:
        value = json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError) as exc:
        raise ResourceVerificationError("registered resource service catalog is unreadable") from exc
    return validate_resource_service_catalog(value)


def verify_resource_service_registration(
    catalog: Mapping[str, Any], descriptor: Mapping[str, Any], *, resolver: "HTTPRegisteredResourceResolver | None" = None,
) -> dict[str, Any]:
    """Require exact catalog identity, capabilities, descriptor and live health."""

    normalized_catalog = validate_resource_service_catalog(catalog)
    normalized_descriptor = validate_resource_service_descriptor(descriptor)
    entry = next(
        (item for item in normalized_catalog["services"] if item["id"] == normalized_descriptor["id"]), None
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
        resolver.check_health()
    return normalized_descriptor


def verify_card_resource_service(card: Mapping[str, Any], descriptor: Mapping[str, Any]) -> dict[str, Any]:
    """Check that the portable descriptor passed to a harness is card-bound."""

    binding = card.get("resource_service")
    if not isinstance(binding, Mapping) or set(binding) != {"id", "descriptor_hash"}:
        raise ResourceVerificationError("card resource service binding is invalid")
    normalized = validate_resource_service_descriptor(descriptor)
    if binding["id"] != normalized["id"] or binding["descriptor_hash"] != resource_service_descriptor_hash(normalized):
        raise ResourceVerificationError("card resource service binding mismatch")
    return normalized


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


class HTTPRegisteredResourceResolver:
    """Retrieve raw content from one explicitly registered HTTP resource service.

    The service has no authority to assert a content hash: the resolver
    computes it from the returned bytes and compares it to the immutable card.
    This adapter accepts only the small versioned response contract below and
    rejects redirects, unregistered service identities, malformed base64, and
    transport failures before a launcher can be reached.
    """

    def __init__(
        self, *, service_id: str, endpoint: str, credential: str | None = None,
        timeout_seconds: int = 15,
    ) -> None:
        normalized = validate_resource_service_descriptor(
            {
                "schema": RESOURCE_SERVICE_SCHEMA,
                "id": service_id,
                "endpoint": endpoint,
                "credential_env": None,
                "timeout_seconds": timeout_seconds,
            }
        )
        if credential is not None and (not isinstance(credential, str) or not credential):
            raise ResourceVerificationError("registered resource service credential is invalid")
        self._service_id = normalized["id"]
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
            endpoint=normalized["endpoint"],
            credential=credential,
            timeout_seconds=normalized["timeout_seconds"],
        )

    def check_health(self) -> None:
        """Fail closed unless the catalog-selected service reports its own identity."""

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
            "status": "healthy",
        }:
            raise ResourceVerificationError("registered resource service health identity is invalid")

    def fetch(self, ref: str) -> RegisteredResource:
        if not isinstance(ref, str) or not ref:
            raise ResourceVerificationError("registered resource reference is invalid")
        target = self._endpoint + "/v1/resources/" + quote(ref, safe="")
        request = Request(target, method="GET")
        request.add_header("Accept", "application/json")
        if self._credential is not None:
            request.add_header("Authorization", f"Bearer {self._credential}")
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


def verify_card_resources(
    card: Mapping[str, Any], resolver: ResourceResolver
) -> dict[str, Any]:
    """Fetch every required card resource and issue one hash-bound receipt.

    This is intentionally performed before Promptcraft is launched.  A card
    can name a resource, but only a registered resolver may establish that the
    reference still returns the exact content its card binds.
    """

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
    receipt_unsigned = {
        "schema": RESOURCE_RECEIPT_SCHEMA,
        "card_hash": card["card_hash"],
        "plan_commit": card["plan_commit"],
        "resources": resolved,
    }
    return {**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}
