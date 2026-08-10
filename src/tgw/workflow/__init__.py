"""Pure, domain-neutral workflow convergence kernel."""

from .contracts import (
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    Fingerprint,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    Requirement,
    RuntimeWorkGraph,
    TreatmentAttempt,
    TreatmentContract,
    TreatmentDisposition,
)
from .evaluator import evaluate

__all__ = [
    "EffectClass",
    "EvidenceAssertion",
    "EvidenceReference",
    "Fingerprint",
    "FingerprintResult",
    "GoalProfile",
    "ObjectSnapshot",
    "Requirement",
    "RuntimeWorkGraph",
    "TreatmentAttempt",
    "TreatmentContract",
    "TreatmentDisposition",
    "evaluate",
]
