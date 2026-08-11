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
from .standalone_plan import (
    PlanDocument,
    PlanValidationError,
    canonical_hash,
    compile_plan,
    completion_candidate,
    parse_plan,
    validate_evidence,
    validate_plan,
)
from .standalone_plan import (
    status as plan_status,
)

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
    "PlanDocument",
    "PlanValidationError",
    "canonical_hash",
    "compile_plan",
    "completion_candidate",
    "parse_plan",
    "plan_status",
    "validate_evidence",
    "validate_plan",
]
