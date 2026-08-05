"""Structured treatment receipts — workers return these after handling.

Phase 3 spine: receipt schema and validation. Phase 4: the item_pipeline
runner reads receipts and uses them for re-evaluation to pick the next
eligible treatment.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TreatmentReceipt:
    """A receipt emitted by a worker after completing a treatment.

    Carried through QueueWorker._process() after handle() returns.
    The scheduler reads this to re-evaluate the item and enqueue the
    next eligible treatment.
    """

    treatment_id: str
    treatment_version: str
    graph_id: str | None = None
    outcome: str = "satisfied"
    established_conditions: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    receipt_schema_id: str = "treatment-receipt/v1"

    @property
    def fingerprint(self) -> str:
        """Deterministic receipt fingerprint."""
        payload = {
            "treatment_id": self.treatment_id,
            "treatment_version": self.treatment_version,
            "graph_id": self.graph_id,
            "outcome": self.outcome,
            "established_conditions": sorted(self.established_conditions),
            "artifacts": sorted(self.artifacts),
            "receipt_schema_id": self.receipt_schema_id,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "treatment_id": self.treatment_id,
            "treatment_version": self.treatment_version,
            "graph_id": self.graph_id,
            "outcome": self.outcome,
            "established_conditions": list(self.established_conditions),
            "artifacts": list(self.artifacts),
            "evidence": self.evidence,
            "receipt_schema_id": self.receipt_schema_id,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_worker_return(cls, data: dict[str, Any]) -> TreatmentReceipt:
        """Construct a receipt from a worker's return dict.

        Workers return a dict with keys like treatment_id, outcome,
        established_conditions, etc. This normalizes it into a
        TreatmentReceipt.
        """
        return cls(
            treatment_id=str(data.get("treatment_id", "")),
            treatment_version=str(data.get("treatment_version", "1")),
            graph_id=data.get("graph_id"),
            outcome=str(data.get("outcome", "satisfied")),
            established_conditions=tuple(data.get("established_conditions", ())),
            artifacts=tuple(data.get("artifacts", ())),
            evidence=data.get("evidence", {}),
            receipt_schema_id=str(
                data.get("receipt_schema_id", "treatment-receipt/v1")
            ),
        )


# Outcomes
OUTCOME_SATISFIED = "satisfied"
OUTCOME_FAILED = "failed"
OUTCOME_PARTIAL = "partial"
OUTCOME_CONFLICT = "conflict"
