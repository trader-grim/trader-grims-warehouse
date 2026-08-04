import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow import (  # noqa: E402
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    Requirement,
    TreatmentContract,
    evaluate,
)


def test_all_six_fingerprint_results_remain_distinct():
    results = {
        FingerprintResult.TRUE,
        FingerprintResult.FALSE,
        FingerprintResult.UNKNOWN,
        FingerprintResult.STALE,
        FingerprintResult.CONTRADICTORY,
        FingerprintResult.NOT_APPLICABLE,
    }

    assert len(results) == 6


def _reference(identity="observation-1"):
    return EvidenceReference(
        identity=identity,
        source_class="authoritative_observation",
        source_generation="7",
    )


def _assertion(condition_id, result, *reasons, reference=None):
    return EvidenceAssertion(
        condition_id=condition_id,
        result=result,
        reasons=reasons,
        evidence=(reference or _reference(),),
    )


def _goal(*conditions):
    return GoalProfile(identity="ready", version="1", required=conditions)


def _treatment(
    identity,
    condition_id,
    accepted_results,
    ownership,
    *,
    effect_class=EffectClass.LOCAL,
    version="1",
):
    return TreatmentContract(
        identity=identity,
        version=version,
        requires=(Requirement(condition_id, accepted_results),),
        may_establish=(condition_id,),
        must_preserve=("audit_evidence",),
        ownership=(ownership,),
        effect_class=effect_class,
        receipt_schema_id="receipt.example/v1",
    )


def _evaluate(assertions, treatments, *, generation="3", ambiguities=()):
    return evaluate(
        snapshot=ObjectSnapshot(
            object_id="object-17",
            generation=generation,
            assertions=assertions,
            external_effect_ambiguities=ambiguities,
        ),
        goal=_goal("valid", "documented"),
        treatments=treatments,
        evaluator_version="condition-evaluator/v1",
    )


def test_invalid_object_selects_local_remediation_and_skips_satisfied_requirement():
    graph = _evaluate(
        (
            _assertion("valid", FingerprintResult.FALSE, "format invalid"),
            _assertion("documented", FingerprintResult.TRUE),
        ),
        (
            _treatment("repair", "valid", (FingerprintResult.FALSE,), "record.format"),
            _treatment("document", "documented", (FingerprintResult.FALSE,), "record.docs"),
        ),
    )

    assert graph.satisfied_requirements == ("documented",)
    assert graph.unmet_requirements == ("valid",)
    assert [item.treatment_id for item in graph.eligible_treatments] == ["repair"]
    assert graph.eligible_treatments[0].reasons == ("valid=false: format invalid",)
    assert [item.treatment_id for item in graph.waiting_treatments] == ["document"]


def test_missing_evidence_is_unknown_and_waits_without_retry_request():
    graph = _evaluate(
        (_assertion("documented", FingerprintResult.TRUE),),
        (_treatment("repair", "valid", (FingerprintResult.FALSE,), "record.format"),),
    )

    assert graph.explicit_requirements == (("valid", FingerprintResult.UNKNOWN),)
    assert graph.waiting_treatments[0].reasons == ("valid=unknown: no evidence",)
    assert graph.retry_requested is False
    assert graph.next_event_classes == ("evidence_changed",)


def test_duplicate_goal_requirements_are_reported_once_in_sorted_order():
    graph = evaluate(
        snapshot=ObjectSnapshot(
            object_id="object-17",
            generation="3",
            assertions=(
                _assertion("valid", FingerprintResult.FALSE),
                _assertion("documented", FingerprintResult.TRUE),
                _assertion("reviewed", FingerprintResult.STALE),
            ),
        ),
        goal=_goal("valid", "documented", "reviewed", "valid", "documented", "reviewed"),
        treatments=(),
        evaluator_version="condition-evaluator/v1",
    )

    assert graph.satisfied_requirements == ("documented",)
    assert graph.unmet_requirements == ("valid",)
    assert graph.explicit_requirements == (("reviewed", FingerprintResult.STALE),)


def test_conflicting_assertions_derive_contradictory_fingerprint():
    graph = _evaluate(
        (
            _assertion("valid", FingerprintResult.TRUE, reference=_reference("a")),
            _assertion("valid", FingerprintResult.FALSE, reference=_reference("b")),
            _assertion("documented", FingerprintResult.TRUE),
        ),
        (),
    )

    valid = next(item for item in graph.fingerprints if item.condition_id == "valid")
    assert valid.result is FingerprintResult.CONTRADICTORY
    assert valid.reasons == ("conflicting results: false, true",)
    assert graph.explicit_requirements == (("valid", FingerprintResult.CONTRADICTORY),)


def test_graph_identity_is_generation_bound_order_stable_and_repeatable():
    a = _assertion("valid", FingerprintResult.FALSE, "bad", reference=_reference("a"))
    b = _assertion("documented", FingerprintResult.TRUE, reference=_reference("b"))
    repair = _treatment("repair", "valid", (FingerprintResult.FALSE,), "record.format")

    first = _evaluate((a, b), (repair,))
    reordered = _evaluate((b, a), (repair,))
    repeated = _evaluate((a, b), (repair,))
    regenerated = _evaluate((a, b), (repair,), generation="4")

    assert first.evidence_set_hash == reordered.evidence_set_hash
    assert first.condition_hash == reordered.condition_hash
    assert first.graph_id == reordered.graph_id == repeated.graph_id
    assert regenerated.graph_id != first.graph_id


def test_disjoint_treatments_coexist_and_overlapping_ownership_is_exposed():
    assertions = (
        _assertion("valid", FingerprintResult.FALSE),
        _assertion("documented", FingerprintResult.FALSE),
    )
    repair = _treatment("repair", "valid", (FingerprintResult.FALSE,), "record.format")
    document = _treatment("document", "documented", (FingerprintResult.FALSE,), "record.docs")
    competing = _treatment("competing-repair", "valid", (FingerprintResult.FALSE,), "record.format")

    disjoint = _evaluate(assertions, (document, repair))
    overlapping = _evaluate(assertions, (competing, document, repair))

    assert [item.treatment_id for item in disjoint.eligible_treatments] == ["document", "repair"]
    assert disjoint.ownership_conflicts == ()
    assert overlapping.ownership_conflicts == (("competing-repair", "repair", ("record.format",)),)


def test_ownership_conflicts_use_the_matching_treatment_version():
    assertions = (
        _assertion("valid", FingerprintResult.FALSE),
        _assertion("documented", FingerprintResult.FALSE),
    )
    repair_v1 = _treatment(
        "repair", "valid", (FingerprintResult.FALSE,), "record.shared", version="1"
    )
    repair_v2 = _treatment(
        "repair", "valid", (FingerprintResult.FALSE,), "record.v2", version="2"
    )
    other = _treatment(
        "other", "documented", (FingerprintResult.FALSE,), "record.shared"
    )

    graph = _evaluate(assertions, (repair_v1, repair_v2, other))

    assert graph.ownership_conflicts == (("other", "repair", ("record.shared",)),)


def test_ambiguous_external_effect_blocks_effect_treatment():
    publish = _treatment(
        "publish",
        "valid",
        (FingerprintResult.TRUE,),
        "provider.account",
        effect_class=EffectClass.EXTERNAL,
    )
    graph = _evaluate(
        (
            _assertion("valid", FingerprintResult.TRUE),
            _assertion("documented", FingerprintResult.TRUE),
        ),
        (publish,),
        ambiguities=("provider.account",),
    )

    assert graph.eligible_treatments == ()
    assert graph.waiting_treatments[0].reasons == (
        "external effect ambiguous: provider.account",
    )
    assert graph.reconciliation_gates == ("provider.account",)

    unambiguous = _evaluate(
        (
            _assertion("valid", FingerprintResult.TRUE),
            _assertion("documented", FingerprintResult.TRUE),
        ),
        (publish,),
    )
    assert unambiguous.graph_id != graph.graph_id
