"""Deterministic, read-only construction of runtime work graphs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from .contracts import (
    OUTCOME_CONFLICT,
    OUTCOME_FAILED,
    OUTCOME_PARTIAL,
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    Fingerprint,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    RuntimeWorkGraph,
    TreatmentAttempt,
    TreatmentContract,
    TreatmentDisposition,
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _reference_key(reference: EvidenceReference) -> tuple[str, str, str, str, str]:
    return (
        reference.source_class,
        reference.identity,
        reference.source_generation,
        reference.freshness_identity,
        reference.supersession_identity,
    )


def _assertion_data(assertion: EvidenceAssertion) -> dict[str, Any]:
    return {
        "condition_id": assertion.condition_id,
        "result": assertion.result.value,
        "reasons": sorted(assertion.reasons),
        "evidence": [asdict(item) for item in sorted(assertion.evidence, key=_reference_key)],
    }


def _derive_fingerprint(condition_id: str, assertions: tuple[EvidenceAssertion, ...]) -> Fingerprint:
    matching = tuple(item for item in assertions if item.condition_id == condition_id)
    evidence = tuple(sorted({ref for item in matching for ref in item.evidence}, key=_reference_key))
    if not matching:
        return Fingerprint(condition_id, FingerprintResult.UNKNOWN, ("no evidence",), ())

    results = {item.result for item in matching}
    reasons = tuple(sorted({reason for item in matching for reason in item.reasons}))
    if len(results) != 1:
        detail = ", ".join(sorted(result.value for result in results))
        return Fingerprint(condition_id, FingerprintResult.CONTRADICTORY, (f"conflicting results: {detail}",), evidence)
    return Fingerprint(condition_id, next(iter(results)), reasons, evidence)


def _reason(fingerprint: Fingerprint) -> str:
    suffix = "; ".join(fingerprint.reasons)
    return f"{fingerprint.condition_id}={fingerprint.result.value}" + (f": {suffix}" if suffix else "")


def evaluate(
    *,
    snapshot: ObjectSnapshot,
    goal: GoalProfile,
    treatments: tuple[TreatmentContract, ...],
    evaluator_version: str,
    attempts: tuple[TreatmentAttempt, ...] = (),
) -> RuntimeWorkGraph:
    """Evaluate only supplied values; no ambient state is read or changed."""
    condition_ids = set(goal.required)
    for treatment in treatments:
        condition_ids.update(requirement.condition_id for requirement in treatment.requires)
        condition_ids.update(treatment.may_establish)
    fingerprints = tuple(_derive_fingerprint(item, snapshot.assertions) for item in sorted(condition_ids))
    by_id = {item.condition_id: item for item in fingerprints}
    condition_data = [{"condition_id": item.condition_id, "result": item.result.value, "reasons": list(item.reasons), "evidence": [asdict(ref) for ref in item.evidence]} for item in fingerprints]
    condition_hash = _hash(condition_data)
    registered_treatments = {
        (treatment.identity, treatment.version) for treatment in treatments
    }
    suppressing_outcomes = {OUTCOME_FAILED, OUTCOME_PARTIAL, OUTCOME_CONFLICT}
    relevant_attempts = tuple(
        attempt for attempt in attempts
        if attempt.object_generation == snapshot.generation
        and attempt.condition_hash == condition_hash
        and (attempt.treatment_id, attempt.treatment_version) in registered_treatments
        and attempt.outcome in suppressing_outcomes
        and isinstance(attempt.receipt_id, str)
        and bool(attempt.receipt_id.strip())
    )

    required = tuple(sorted(set(goal.required)))
    satisfied = tuple(item for item in required if by_id[item].result in {FingerprintResult.TRUE, FingerprintResult.NOT_APPLICABLE})
    unmet = tuple(item for item in required if by_id[item].result is FingerprintResult.FALSE)
    explicit_results = {FingerprintResult.UNKNOWN, FingerprintResult.STALE, FingerprintResult.CONTRADICTORY}
    explicit = tuple((item, by_id[item].result) for item in required if by_id[item].result in explicit_results)

    eligible: list[TreatmentDisposition] = []
    waiting: list[TreatmentDisposition] = []
    ambiguities = set(snapshot.external_effect_ambiguities)
    for treatment in sorted(treatments, key=lambda item: (item.identity, item.version)):
        ambiguity = sorted(ambiguities.intersection(treatment.ownership)) if treatment.effect_class is EffectClass.EXTERNAL else []
        unmet_requirements = [requirement for requirement in treatment.requires if by_id[requirement.condition_id].result not in requirement.accepted_results]
        unchanged_attempts = tuple(
            attempt for attempt in relevant_attempts
            if attempt.treatment_id == treatment.identity
            and attempt.treatment_version == treatment.version
        )
        if ambiguity:
            reasons = tuple(f"external effect ambiguous: {item}" for item in ambiguity)
            waiting.append(TreatmentDisposition(treatment.identity, treatment.version, reasons))
        elif unmet_requirements:
            reasons = tuple(_reason(by_id[item.condition_id]) for item in unmet_requirements)
            waiting.append(TreatmentDisposition(treatment.identity, treatment.version, reasons))
        elif unchanged_attempts:
            receipts = ", ".join(sorted(attempt.receipt_id for attempt in unchanged_attempts))
            outcomes = ", ".join(sorted({attempt.outcome for attempt in unchanged_attempts}))
            waiting.append(TreatmentDisposition(
                treatment.identity,
                treatment.version,
                (f"unchanged non-success attempt already recorded ({outcomes}): {receipts}",),
            ))
        elif treatment.may_establish and all(
            by_id[condition_id].result
            in {FingerprintResult.TRUE, FingerprintResult.NOT_APPLICABLE}
            for condition_id in treatment.may_establish
        ):
            waiting.append(
                TreatmentDisposition(
                    treatment.identity,
                    treatment.version,
                    ("all treatment conditions already established",),
                )
            )
        else:
            reasons = tuple(_reason(by_id[item.condition_id]) for item in treatment.requires)
            eligible.append(TreatmentDisposition(treatment.identity, treatment.version, reasons))

    treatment_by_id = {(item.identity, item.version): item for item in treatments}
    conflicts: list[tuple[str, str, tuple[str, ...]]] = []
    for index, left in enumerate(eligible):
        for right in eligible[index + 1 :]:
            left_treatment = treatment_by_id[(left.treatment_id, left.treatment_version)]
            right_treatment = treatment_by_id[(right.treatment_id, right.treatment_version)]
            overlap = tuple(sorted(set(left_treatment.ownership).intersection(right_treatment.ownership)))
            if overlap:
                conflicts.append((left.treatment_id, right.treatment_id, overlap))

    assertion_data = sorted((_assertion_data(item) for item in snapshot.assertions), key=lambda item: _canonical(item))
    attempt_data = sorted((asdict(item) for item in relevant_attempts), key=lambda item: _canonical(item))
    evidence_set_hash = _hash(
        {
            "assertions": assertion_data,
            "attempts": attempt_data,
            "external_effect_ambiguities": sorted(ambiguities),
        }
    )
    treatment_registry_hash = _hash(
        sorted((asdict(item) for item in treatments), key=lambda item: _canonical(item))
    )
    graph_binding = {
        "object_id": snapshot.object_id,
        "object_generation": snapshot.generation,
        "goal_profile_id": goal.identity,
        "goal_profile_version": goal.version,
        "evaluator_version": evaluator_version,
        "evidence_set_hash": evidence_set_hash,
        "condition_hash": condition_hash,
        "treatment_registry_hash": treatment_registry_hash,
    }
    missing_evidence = any(item.result is FingerprintResult.UNKNOWN for item in fingerprints)
    next_events = ("evidence_changed",) if missing_evidence else ()

    return RuntimeWorkGraph(
        schema_version="runtime-work-graph/v1",
        graph_id=_hash(graph_binding),
        object_id=snapshot.object_id,
        object_generation=snapshot.generation,
        goal_profile_id=goal.identity,
        goal_profile_version=goal.version,
        evaluator_version=evaluator_version,
        evidence_set_hash=evidence_set_hash,
        condition_hash=condition_hash,
        treatment_registry_hash=treatment_registry_hash,
        fingerprints=fingerprints,
        satisfied_requirements=satisfied,
        unmet_requirements=unmet,
        explicit_requirements=explicit,
        eligible_treatments=tuple(eligible),
        waiting_treatments=tuple(waiting),
        ownership_conflicts=tuple(conflicts),
        reconciliation_gates=tuple(sorted(ambiguities)),
        next_event_classes=next_events,
    )
