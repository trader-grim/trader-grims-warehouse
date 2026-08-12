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
    TreatmentContract,
    TreatmentDisposition,
)
from .evaluator import evaluate
from .plan_bridge import CompiledPlanRuntime, ResolutionHold, compile_solution_runtime

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
    "TreatmentContract",
    "TreatmentDisposition",
    "evaluate",
    "CompiledPlanRuntime",
    "ResolutionHold",
    "compile_solution_runtime",
]
