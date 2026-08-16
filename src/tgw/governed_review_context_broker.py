"""Privileged context broker for provider-neutral governed reviews.

The review provider can request one exact card-bound context run, but it never
receives the backend resource-service credential or either signing key.  The
broker fetches every resource through its own fixed backend identity, verifies
the backend service attestation, and then issues the provider-bound attestation
which the controller independently reads back from the broker service.

Deployment and secret issuance are deliberately external to this module.
"""

from __future__ import annotations

import threading
from typing import Any, Mapping, Protocol

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.execution_resources import (
    CARD_RESOURCE_NAMES,
    HARNESS_RETRIEVAL_ATTESTATION_SCHEMA,
    HTTPRegisteredResourceResolver,
    ResourceVerificationError,
    issue_harness_retrieval_attestation,
)

BROKER_REQUEST_SCHEMA = "tgw-context-review-broker-request/v1"


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
        self._attestations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _request(value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "schema", "card_hash", "role", "execution_identity",
            "handoff_hash", "resource_receipt_hash", "resources",
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
                    "resource_receipt_hash",
                )
            )
            or not isinstance(value.get("resources"), Mapping)
            or set(value["resources"]) != CARD_RESOURCE_NAMES
        ):
            raise ReviewContextBrokerError("review context broker request is invalid")
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

    def execute(self, value: Mapping[str, Any]) -> dict[str, Any]:
        request = self._request(value)
        try:
            run = self._backend.begin_harness_run(
                card_hash=request["card_hash"], role="independent-review",
                execution_identity=self._backend_execution_identity,
                handoff_hash=request["handoff_hash"],
                resource_receipt_hash=request["resource_receipt_hash"],
                resources=request["resources"],
            )
            resolver: HarnessRunResolver = self._backend.for_harness_run(run)
            for name, binding in request["resources"].items():
                if resolver.fetch(binding["ref"]).content_hash() != binding["hash"]:
                    raise ReviewContextBrokerError(
                        f"review context broker resource differs: {name}"
                    )
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
        with self._lock:
            if len(self._attestations) >= self._max_retained_runs:
                raise ReviewContextBrokerError("review context broker run capacity is exhausted")
            if attestation["run_id"] in self._attestations:
                raise ReviewContextBrokerError("review context broker run identity is duplicated")
            self._attestations[attestation["run_id"]] = attestation
        return dict(attestation)

    def read_attestation(self, run_id: str) -> dict[str, Any]:
        """Controller-only service surface; HTTP/auth binding is deployment-owned."""

        with self._lock:
            attestation = self._attestations.get(run_id)
        if attestation is None:
            raise ReviewContextBrokerError("review context broker run is unavailable")
        return dict(attestation)
