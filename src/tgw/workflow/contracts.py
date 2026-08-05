"""Immutable contracts for pure workflow convergence evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FingerprintResult(str, Enum):
    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"
    STALE = "stale"
    CONTRADICTORY = "contradictory"
    NOT_APPLICABLE = "not_applicable"


class EffectClass(str, Enum):
    LOCAL = "local"
    EXTERNAL = "external"


@dataclass(frozen=True)
class EvidenceReference:
    identity: str
    source_class: str
    source_generation: str
    freshness_identity: str = ""
    supersession_identity: str = ""


@dataclass(frozen=True)
class EvidenceAssertion:
    condition_id: str
    result: FingerprintResult
    reasons: tuple[str, ...] = ()
    evidence: tuple[EvidenceReference, ...] = ()


@dataclass(frozen=True)
class ObjectSnapshot:
    object_id: str
    generation: str
    assertions: tuple[EvidenceAssertion, ...]
    external_effect_ambiguities: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoalProfile:
    identity: str
    version: str
    required: tuple[str, ...]


@dataclass(frozen=True)
class Requirement:
    condition_id: str
    accepted_results: tuple[FingerprintResult, ...]


@dataclass(frozen=True)
class TreatmentContract:
    identity: str
    version: str
    requires: tuple[Requirement, ...]
    may_establish: tuple[str, ...]
    must_preserve: tuple[str, ...]
    ownership: tuple[str, ...]
    effect_class: EffectClass
    receipt_schema_id: str


@dataclass(frozen=True)
class Fingerprint:
    condition_id: str
    result: FingerprintResult
    reasons: tuple[str, ...]
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class TreatmentDisposition:
    treatment_id: str
    treatment_version: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeWorkGraph:
    schema_version: str
    graph_id: str
    object_id: str
    object_generation: str
    goal_profile_id: str
    goal_profile_version: str
    evaluator_version: str
    evidence_set_hash: str
    condition_hash: str
    treatment_registry_hash: str
    fingerprints: tuple[Fingerprint, ...]
    satisfied_requirements: tuple[str, ...]
    unmet_requirements: tuple[str, ...]
    explicit_requirements: tuple[tuple[str, FingerprintResult], ...]
    eligible_treatments: tuple[TreatmentDisposition, ...]
    waiting_treatments: tuple[TreatmentDisposition, ...]
    ownership_conflicts: tuple[tuple[str, str, tuple[str, ...]], ...]
    reconciliation_gates: tuple[str, ...]
    next_event_classes: tuple[str, ...]
    retry_requested: bool = False


# ── Phase 3+4: TreatmentReceipt ───────────────────────────────────────────

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
    error_detail: str = ""
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
            "error_detail": self.error_detail,
            "evidence": self.evidence,
            "receipt_schema_id": self.receipt_schema_id,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_worker_return(cls, data: dict[str, Any]) -> TreatmentReceipt:
        """Construct a receipt from a worker's return dict."""
        return cls(
            treatment_id=str(data.get("treatment_id", "")),
            treatment_version=str(data.get("treatment_version", "1")),
            graph_id=data.get("graph_id"),
            outcome=str(data.get("outcome", "satisfied")),
            established_conditions=tuple(data.get("established_conditions", ())),
            artifacts=tuple(data.get("artifacts", ())),
            error_detail=str(data.get("error_detail", "")),
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
