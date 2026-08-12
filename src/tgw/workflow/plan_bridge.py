"""Compile immutable Plan solution objects into the convergence runtime.

The bridge describes conditions and treatments.  It does not enqueue work or
encode a procedure: treatment eligibility is derived by the existing pure
evaluator from current evidence on every compilation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from tgw.plan_solver import PlanResolutionError, validate_solution_integrity

from .contracts import (
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    Requirement,
    RuntimeWorkGraph,
    TreatmentContract,
)
from .evaluator import evaluate

BRIDGE_VERSION = "tgw-plan-runtime-bridge/v1"
DISPATCH_CONDITION = "plan.solution-dispatchable"


@dataclass(frozen=True)
class ResolutionHold:
    code: str
    capability: str
    detail: str


@dataclass(frozen=True)
class CompiledPlanRuntime:
    schema_version: str
    solution_hash: str
    closure_hash: str
    plan_commit: str
    runtime_graph: RuntimeWorkGraph
    treatments: tuple[TreatmentContract, ...]
    holds: tuple[ResolutionHold, ...]

    @property
    def dispatchable(self) -> bool:
        return not self.holds and bool(self.runtime_graph.eligible_treatments)


def _hold_result(code: str) -> FingerprintResult:
    if code == "CONTRADICTORY_RESOLUTION":
        return FingerprintResult.CONTRADICTORY
    if code == "UNSATISFIED":
        return FingerprintResult.FALSE
    return FingerprintResult.UNKNOWN


def compile_solution_runtime(
    solution: Mapping[str, object],
    *,
    current_plan_commit: str,
    capability_assertions: Sequence[EvidenceAssertion] = (),
) -> CompiledPlanRuntime:
    """Compile one hash-bound solution and current capability evidence."""

    validate_solution_integrity(solution, current_plan_commit=current_plan_commit)
    solution_hash = str(solution["solution_hash"])
    closure_hash = str(solution.get("closure_hash", ""))
    if not closure_hash:
        raise PlanResolutionError("solution closure_hash is required")

    holds = tuple(
        ResolutionHold(
            code=str(item.get("code", "UNKNOWN_CAPABILITY")),
            capability=str(item.get("capability", "resolver.conformance")),
            detail=str(item.get("reason") or item.get("provider") or item.get("code", "unresolved")),
        )
        for item in solution.get("unresolved", ())
        if isinstance(item, Mapping)
    )
    if not bool(solution.get("conformance_verified")):
        holds += (ResolutionHold("BLOCKED", "resolver.conformance", "resolver conformance is not verified"),)

    supplied = {assertion.condition_id for assertion in capability_assertions}
    assertions = list(capability_assertions)
    reference = EvidenceReference(
        identity=solution_hash,
        source_class="tgw-plan-solution/v1",
        source_generation=closure_hash,
        freshness_identity=current_plan_commit,
    )
    satisfied = {
        str(item["capability"])
        for item in solution.get("satisfied_installed", ())
        if isinstance(item, Mapping) and "capability" in item
    }
    work_capabilities = {
        str(item["capability"])
        for item in solution.get("work_units", ())
        if isinstance(item, Mapping) and "capability" in item
    }
    for capability in sorted(satisfied | work_capabilities):
        if capability not in supplied:
            result = FingerprintResult.TRUE if capability in satisfied else FingerprintResult.FALSE
            assertions.append(EvidenceAssertion(capability, result, ("derived from exact Plan solution",), (reference,)))
    for hold in holds:
        if hold.capability not in supplied and hold.capability not in satisfied and hold.capability not in work_capabilities:
            assertions.append(EvidenceAssertion(hold.capability, _hold_result(hold.code), (f"{hold.code}: {hold.detail}",), (reference,)))
    assertions.append(
        EvidenceAssertion(
            DISPATCH_CONDITION,
            FingerprintResult.TRUE if not holds and solution.get("dispatchable") else FingerprintResult.FALSE,
            (() if not holds else ("solution is held",)),
            (reference,),
        )
    )

    treatments: list[TreatmentContract] = []
    for item in solution.get("work_units", ()):
        if not isinstance(item, Mapping):
            raise PlanResolutionError("solution work_units must be mappings")
        capability = item.get("capability")
        if not isinstance(capability, str) or not capability:
            raise PlanResolutionError("solution work unit lacks typed capability identity")
        dependencies = item.get("requires_capabilities", ())
        if not isinstance(dependencies, Sequence) or isinstance(dependencies, (str, bytes)):
            raise PlanResolutionError("work-unit requires_capabilities must be a sequence")
        treatments.append(
            TreatmentContract(
                identity=str(item["id"]),
                version=closure_hash,
                requires=(
                    Requirement(DISPATCH_CONDITION, (FingerprintResult.TRUE,)),
                    Requirement(capability, (FingerprintResult.FALSE,)),
                )
                + tuple(Requirement(str(dependency), (FingerprintResult.TRUE, FingerprintResult.NOT_APPLICABLE)) for dependency in dependencies),
                may_establish=(capability,),
                must_preserve=(solution_hash, current_plan_commit),
                ownership=(capability,),
                effect_class=EffectClass.LOCAL,
                receipt_schema_id="tgw-plan-work-receipt/v1",
            )
        )

    required = tuple(sorted(satisfied | work_capabilities | {hold.capability for hold in holds}))
    runtime = evaluate(
        snapshot=ObjectSnapshot(
            object_id=solution_hash,
            generation=closure_hash,
            assertions=tuple(assertions),
        ),
        goal=GoalProfile(identity=str(solution.get("root", {}).get("id", "plan")), version=current_plan_commit, required=required),
        treatments=tuple(treatments),
        evaluator_version=BRIDGE_VERSION,
    )
    return CompiledPlanRuntime(
        schema_version="tgw-plan-compiled-runtime/v1",
        solution_hash=solution_hash,
        closure_hash=closure_hash,
        plan_commit=current_plan_commit,
        runtime_graph=runtime,
        treatments=tuple(treatments),
        holds=holds,
    )
