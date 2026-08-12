"""Generation-bound integration surface for queue and workflow foundations.

Queue leasing and atomic dedupe remain owned by ``queue.state_machine``.  This
module binds the objects crossing that boundary and classifies worker receipts;
it does not add another queue or state authority.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contracts import RuntimeWorkGraph, TreatmentReceipt


class GenerationConflict(ValueError):
    """A claimed job or receipt belongs to a different object generation."""


class ReceiptDisposition(str, Enum):
    ACCEPTED = "accepted"
    RETRY = "retry"
    AMBIGUOUS = "ambiguous"
    TERMINAL_FAILURE = "terminal_failure"
    CONFLICT = "conflict"
    STALE_GENERATION = "stale_generation"


@dataclass(frozen=True)
class FoundationDispatchBinding:
    graph_id: str
    object_id: str
    object_generation: str
    treatment_id: str
    treatment_version: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], graph: RuntimeWorkGraph) -> "FoundationDispatchBinding":
        required = ("graph_id", "object_id", "object_generation", "treatment_id", "treatment_version")
        if any(not isinstance(payload.get(key), str) or not payload[key] for key in required):
            raise ValueError("dispatch payload lacks an immutable runtime binding")
        if payload["graph_id"] != graph.graph_id or payload["object_id"] != graph.object_id:
            raise GenerationConflict("dispatch graph/object identity does not match current runtime graph")
        if payload["object_generation"] != graph.object_generation:
            raise GenerationConflict("dispatch object generation is stale")
        return cls(*(str(payload[key]) for key in required))

    @property
    def dedupe_key(self) -> str:
        return self.graph_id

    @property
    def run_identity(self) -> str:
        data = {
            "graph_id": self.graph_id,
            "object_id": self.object_id,
            "object_generation": self.object_generation,
            "treatment_id": self.treatment_id,
            "treatment_version": self.treatment_version,
        }
        return "sha256:" + hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class FoundationIntegrationReceipt:
    schema: str
    graph_id: str
    object_generation: str
    run_identity: str
    dedupe_key: str
    treatment_receipt_fingerprint: str
    disposition: ReceiptDisposition


def classify_receipt(receipt: TreatmentReceipt, binding: FoundationDispatchBinding) -> ReceiptDisposition:
    """Classify terminal evidence without silently accepting stale work."""

    if receipt.graph_id != binding.graph_id or receipt.treatment_id != binding.treatment_id or receipt.treatment_version != binding.treatment_version:
        return ReceiptDisposition.STALE_GENERATION
    outcomes = {
        "satisfied": ReceiptDisposition.ACCEPTED,
        "partial": ReceiptDisposition.RETRY,
        "retry": ReceiptDisposition.RETRY,
        "ambiguous": ReceiptDisposition.AMBIGUOUS,
        "failed": ReceiptDisposition.TERMINAL_FAILURE,
        "conflict": ReceiptDisposition.CONFLICT,
    }
    return outcomes.get(receipt.outcome, ReceiptDisposition.CONFLICT)


def integration_receipt(
    *,
    graph: RuntimeWorkGraph,
    dispatch_payload: Mapping[str, Any],
    receipt: TreatmentReceipt,
) -> FoundationIntegrationReceipt:
    """Emit one immutable receipt proving the queue/runtime handoff binding."""

    binding = FoundationDispatchBinding.from_payload(dispatch_payload, graph)
    return FoundationIntegrationReceipt(
        schema="tgw-foundation-integration-receipt/v1",
        graph_id=graph.graph_id,
        object_generation=graph.object_generation,
        run_identity=binding.run_identity,
        dedupe_key=binding.dedupe_key,
        treatment_receipt_fingerprint=receipt.fingerprint,
        disposition=classify_receipt(receipt, binding),
    )
