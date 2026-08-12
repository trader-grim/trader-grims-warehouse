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
    TreatmentReceipt,
)
from .evaluator import evaluate
from .foundation import (
    FoundationDispatchBinding,
    FoundationIntegrationReceipt,
    GenerationConflict,
    ReceiptDisposition,
    classify_receipt,
    integration_receipt,
)
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
    "TreatmentReceipt",
    "evaluate",
    "FoundationDispatchBinding",
    "FoundationIntegrationReceipt",
    "GenerationConflict",
    "ReceiptDisposition",
    "classify_receipt",
    "integration_receipt",
    "CompiledPlanRuntime",
    "ResolutionHold",
    "compile_solution_runtime",
]
