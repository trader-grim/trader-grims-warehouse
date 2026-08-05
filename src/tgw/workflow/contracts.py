"""Immutable contracts for pure workflow convergence evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


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


@dataclass(frozen=True)
class TreatmentReceipt:
    treatment_id: str
    treatment_version: str
    graph_id: str
    outcome: str
    established_conditions: tuple[str, ...]
    artifacts: tuple[str, ...]
    error_detail: str = ""
