"""Tests for treatment contracts in tgw.workflow.treatments."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow import (
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
from tgw.workflow.treatments import (
    AI_IDENTIFY,
    ALL_TREATMENTS,
    CLAUDE_REVIEW,
    CODEX_IMPLEMENT,
    CODING_TREATMENTS,
    CONTROLLER_VERIFY,
    EBAY_DRAFT,
    EBAY_PUBLISH,
    EBAY_STAGE,
    EBAY_UPLOAD,
    HERMES_STITCH,
    NORMALIZE_CONDITION,
    TGW_TREATMENTS,
)

# ── Known condition universe ───────────────────────────────────────────────

_CODING_CONDITIONS = frozenset({
    "implemented",
    "tested",
    "linted",
    "reviewed",
    "controller_verified",
    "committed",
})

_TGW_CONDITIONS = frozenset({
    "item_has_photos",
    "ai_identified",
    "draft_generated",
    "priced",
    "photos_uploaded",
    "staged",
    "staged_content_current",
    "published",
    "operator_authorized_upload",
    "operator_authorized_stage",
    "operator_authorized_publish",
    "valid_condition",
    "condition_normalizable",
    "valid_category",
    "title_ok",
})

_ALL_KNOWN_CONDITIONS = _CODING_CONDITIONS | _TGW_CONDITIONS

# ── Helpers ────────────────────────────────────────────────────────────────


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


def _evaluate(assertions, treatments, *, generation="3", ambiguities=()):
    return evaluate(
        snapshot=ObjectSnapshot(
            object_id="object-17",
            generation=generation,
            assertions=assertions,
            external_effect_ambiguities=ambiguities,
        ),
        goal=_goal(*[a.condition_id for a in assertions]),
        treatments=treatments,
        evaluator_version="condition-evaluator/v1",
    )


# ── Structural invariants ──────────────────────────────────────────────────


def test_treatment_count():
    """11 total treatments: 4 coding + 7 TGW."""
    assert len(CODING_TREATMENTS) == 4
    assert len(TGW_TREATMENTS) == 7
    assert len(ALL_TREATMENTS) == 11


def test_unique_identity_version_pairs():
    """No two treatments share the same (identity, version) pair."""
    seen = set()
    for t in ALL_TREATMENTS:
        key = (t.identity, t.version)
        assert key not in seen, f"Duplicate (identity, version): {key}"
        seen.add(key)
    assert len(seen) == len(ALL_TREATMENTS)


def test_all_required_conditions_known():
    """Every condition_id referenced in any treatment's requires
    is in the known condition universe."""
    for t in ALL_TREATMENTS:
        for req in t.requires:
            assert req.condition_id in _ALL_KNOWN_CONDITIONS, (
                f"Unknown condition '{req.condition_id}' "
                f"referenced by treatment '{t.identity}'"
            )


def test_all_fingerprint_acceptance_sets_are_explicit():
    """Every Requirement accepts at least one explicit FingerprintResult
    — no empty accepted_results tuples."""
    for t in ALL_TREATMENTS:
        for req in t.requires:
            assert len(req.accepted_results) > 0, (
                f"Treatment '{t.identity}' requires '{req.condition_id}' "
                f"but accepts no results (empty tuple)"
            )


# ── Effect class classification ────────────────────────────────────────────


def test_external_treatments_are_external():
    """Live eBay effects, unlike coding review/verification, are EXTERNAL."""
    external = {
        EBAY_UPLOAD,
        EBAY_STAGE,
        EBAY_PUBLISH,
    }
    for t in external:
        assert t.effect_class == EffectClass.EXTERNAL, (
            f"Treatment '{t.identity}' expected EXTERNAL, got {t.effect_class}"
        )


def test_local_treatments_are_local():
    """codex-implement, claude-review, controller-verify, hermes-stitch,
    ai-identify, ebay-draft must be EffectClass.LOCAL."""
    local = {
        CODEX_IMPLEMENT,
        CLAUDE_REVIEW,
        CONTROLLER_VERIFY,
        HERMES_STITCH,
        AI_IDENTIFY,
        EBAY_DRAFT,
        NORMALIZE_CONDITION,
    }
    for t in local:
        assert t.effect_class == EffectClass.LOCAL, (
            f"Treatment '{t.identity}' expected LOCAL, got {t.effect_class}"
        )


# ── Overlapping ownership detection via evaluate() ─────────────────────────


def test_overlapping_ownership_detected_by_evaluate():
    """When two eligible treatments claim the same ownership domain,
    evaluate() reports the conflict in ownership_conflicts."""
    # Two treatments that both claim "shared.zone" ownership.
    treatment_a = TreatmentContract(
        identity="worker-a",
        version="1",
        requires=(Requirement("ready", (FingerprintResult.TRUE,)),),
        may_establish=("done",),
        must_preserve=("audit_evidence",),
        ownership=("shared.zone", "unique.a"),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt.example/v1",
    )
    treatment_b = TreatmentContract(
        identity="worker-b",
        version="1",
        requires=(Requirement("ready", (FingerprintResult.TRUE,)),),
        may_establish=("done",),
        must_preserve=("audit_evidence",),
        ownership=("shared.zone", "unique.b"),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt.example/v1",
    )

    graph = _evaluate(
        (_assertion("ready", FingerprintResult.TRUE),),
        (treatment_a, treatment_b),
    )

    assert len(graph.ownership_conflicts) == 1
    left, right, overlap = graph.ownership_conflicts[0]
    assert {left, right} == {"worker-a", "worker-b"}
    assert overlap == ("shared.zone",)


def test_disjoint_ownership_no_conflict():
    """When treatments claim disjoint ownership, no conflict is reported."""
    treatment_a = TreatmentContract(
        identity="worker-a",
        version="1",
        requires=(Requirement("ready", (FingerprintResult.TRUE,)),),
        may_establish=("done",),
        must_preserve=("audit_evidence",),
        ownership=("unique.a",),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt.example/v1",
    )
    treatment_b = TreatmentContract(
        identity="worker-b",
        version="1",
        requires=(Requirement("ready", (FingerprintResult.TRUE,)),),
        may_establish=("done",),
        must_preserve=("audit_evidence",),
        ownership=("unique.b",),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt.example/v1",
    )

    graph = _evaluate(
        (_assertion("ready", FingerprintResult.TRUE),),
        (treatment_a, treatment_b),
    )

    assert graph.ownership_conflicts == ()
    assert len(graph.eligible_treatments) == 2


# ── Coding pipeline flow checks ────────────────────────────────────────────


def test_codex_implement_requires_implemented_false():
    """codex-implement accepts only FALSE for 'implemented'."""
    req = CODEX_IMPLEMENT.requires[0]
    assert req.condition_id == "implemented"
    assert FingerprintResult.FALSE in req.accepted_results
    assert FingerprintResult.TRUE not in req.accepted_results


def test_claude_review_requires_three_true_conditions():
    """claude-review requires implemented/tested/linted all TRUE."""
    conditions = {req.condition_id for req in CLAUDE_REVIEW.requires}
    assert conditions == {"implemented", "tested", "linted"}
    for req in CLAUDE_REVIEW.requires:
        assert FingerprintResult.TRUE in req.accepted_results


def test_hermes_stitch_requires_independent_receipts_before_committing():
    """Stitch follows review/verification evidence without a human gate."""
    conditions = {req.condition_id for req in HERMES_STITCH.requires}
    assert conditions == {"reviewed", "controller_verified"}
    assert HERMES_STITCH.may_establish == ("committed",)


# ── TGW pipeline flow checks ───────────────────────────────────────────────


def test_ai_identify_requires_photos():
    """ai-identify requires item_has_photos:TRUE."""
    req = AI_IDENTIFY.requires[0]
    assert req.condition_id == "item_has_photos"
    assert FingerprintResult.TRUE in req.accepted_results


def test_ebay_draft_requires_ai_identified():
    """ebay-draft requires ai_identified:TRUE."""
    req = EBAY_DRAFT.requires[0]
    assert req.condition_id == "ai_identified"
    assert FingerprintResult.TRUE in req.accepted_results


def test_ebay_stage_requires_data_and_operator_authority():
    conditions = {req.condition_id for req in EBAY_STAGE.requires}
    assert conditions == {
        "draft_generated", "priced", "photos_uploaded", "operator_authorized_stage",
    }
    for req in EBAY_STAGE.requires:
        assert FingerprintResult.TRUE in req.accepted_results


def test_ebay_publish_requires_current_stage_and_operator_authority():
    assert {item.condition_id for item in EBAY_PUBLISH.requires} == {
        "staged", "staged_content_current", "operator_authorized_publish",
    }


# ── evaluate() integration with real treatments ────────────────────────────


def test_coding_pipeline_evaluate_with_all_treatments():
    """Approved coding work progresses implement → review/verify → stitch."""
    # Phase 1: not yet implemented — only codex-implement is eligible.
    g1 = _evaluate(
        (_assertion("implemented", FingerprintResult.FALSE),),
        CODING_TREATMENTS,
    )
    assert [e.treatment_id for e in g1.eligible_treatments] == ["codex-implement"]

    # Phase 2: implemented — claude-review, controller-verify eligible.
    g2 = _evaluate(
        (
            _assertion("implemented", FingerprintResult.TRUE),
            _assertion("tested", FingerprintResult.TRUE),
            _assertion("linted", FingerprintResult.TRUE),
        ),
        CODING_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g2.eligible_treatments}
    assert eligible == {"claude-review", "controller-verify"}

    # Phase 3: receipts suppress already-complete review/verify; stitch is
    # eligible directly, without an intermediate approval.
    g3 = _evaluate(
        (
            _assertion("implemented", FingerprintResult.TRUE),
            _assertion("tested", FingerprintResult.TRUE),
            _assertion("linted", FingerprintResult.TRUE),
            _assertion("reviewed", FingerprintResult.TRUE),
            _assertion("controller_verified", FingerprintResult.TRUE),
        ),
        CODING_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g3.eligible_treatments}
    assert eligible == {"hermes-stitch"}


def test_tgw_pipeline_evaluate_with_all_treatments():
    """TGW pipeline: photos → identify → draft → price → upload → stage → publish."""
    # Phase 1: upload is eligible only with exact operator authority.
    g1 = _evaluate(
        (
            _assertion("item_has_photos", FingerprintResult.TRUE),
            _assertion("operator_authorized_upload", FingerprintResult.TRUE),
        ),
        TGW_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g1.eligible_treatments}
    assert eligible == {"ai-identify", "ebay-upload"}

    # Phase 2: ai_identified — ebay-draft eligible.
    g2 = _evaluate(
        (
            _assertion("item_has_photos", FingerprintResult.TRUE),
            _assertion("ai_identified", FingerprintResult.TRUE),
            _assertion("operator_authorized_upload", FingerprintResult.TRUE),
        ),
        TGW_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g2.eligible_treatments}
    assert "ebay-draft" in eligible
    assert "ebay-upload" in eligible

    # Phase 3: draft + priced + photos_uploaded — ebay-stage eligible.
    g3 = _evaluate(
        (
            _assertion("item_has_photos", FingerprintResult.TRUE),
            _assertion("ai_identified", FingerprintResult.TRUE),
            _assertion("draft_generated", FingerprintResult.TRUE),
            _assertion("priced", FingerprintResult.TRUE),
            _assertion("photos_uploaded", FingerprintResult.TRUE),
            _assertion("operator_authorized_stage", FingerprintResult.TRUE),
        ),
        TGW_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g3.eligible_treatments}
    assert "ebay-stage" in eligible
    assert "ebay-draft" not in eligible

    # Phase 4: staged — ebay-publish eligible.
    g4 = _evaluate(
        (
            _assertion("item_has_photos", FingerprintResult.TRUE),
            _assertion("ai_identified", FingerprintResult.TRUE),
            _assertion("draft_generated", FingerprintResult.TRUE),
            _assertion("priced", FingerprintResult.TRUE),
            _assertion("photos_uploaded", FingerprintResult.TRUE),
            _assertion("staged", FingerprintResult.TRUE),
            _assertion("staged_content_current", FingerprintResult.TRUE),
            _assertion("operator_authorized_publish", FingerprintResult.TRUE),
        ),
        TGW_TREATMENTS,
    )
    eligible = {e.treatment_id for e in g4.eligible_treatments}
    assert "ebay-publish" in eligible
    assert "ebay-stage" not in eligible
