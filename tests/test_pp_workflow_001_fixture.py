"""PP-WORKFLOW-001 first mandatory synthetic listing fixture.

This is observation/evaluator proof.  It deliberately performs no provider
effect and does not claim production migration or operator acceptance.
"""

from __future__ import annotations

import json

from tgw.workflow.contracts import TreatmentAttempt
from tgw.workflow.evaluator import evaluate
from tgw.workflow.item_snapshot import build_item_snapshot
from tgw.workflow.profiles import TGW_EBAY_LISTABLE
from tgw.workflow.treatments import TGW_TREATMENTS


def _item(tmp_path, *, sku="PPWF-001", condition="Used", **changes):
    path = tmp_path / sku / "item.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    photo = path.parent / "one.jpg"
    photo.write_bytes(b"synthetic-photo")
    photo_url = "https://example.invalid/one.jpg"
    value = {
        "sku": sku,
        "condition": condition,
        "image": "one.jpg",
        "photo_order": ["one.jpg"],
        "ebay_category_id": "12345",
        "draft_listing": {
            "title": "Synthetic PP workflow fixture",
            "category_id": "12345",
            "price": 20.0,
            "imageUrls": [photo_url],
        },
        "ebay_photos": [{"local": str(photo), "url": photo_url}],
        "ebay_offer": {"offer_id": "offer-fixture"},
        "ebay_listing": {"status": "Active"},
    }
    value.update(changes)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _graph(path, *, attempts=(), ambiguities=()):
    snapshot = build_item_snapshot(
        path,
        TGW_EBAY_LISTABLE,
        treatments=TGW_TREATMENTS,
        external_effect_ambiguities=ambiguities,
    )
    return snapshot, evaluate(
        snapshot=snapshot,
        goal=TGW_EBAY_LISTABLE,
        treatments=TGW_TREATMENTS,
        evaluator_version="pp-workflow-fixture/v1",
        attempts=attempts,
    )


def test_published_list_item_goal_does_not_require_stage_freshness(tmp_path):
    snapshot, graph = _graph(_item(tmp_path))

    assert graph.goal_profile_id == "tgw.ebay_listable"
    assert graph.object_id == snapshot.object_id == "PPWF-001"
    assert set(graph.satisfied_requirements) == set(TGW_EBAY_LISTABLE.required)
    assert graph.unmet_requirements == ()
    assert graph.explicit_requirements == ()
    assert graph.next_event_classes == ("evidence_changed",)
    assert graph.eligible_treatments == ()
    staged = next(
        fingerprint for fingerprint in graph.fingerprints
        if fingerprint.condition_id == "staged_content_current"
    )
    assert staged.result.value == "not_applicable"
    assert staged.reasons == ("published listing supersedes staged content freshness",)


def test_invalid_condition_selects_bounded_local_remediation(tmp_path):
    _snapshot, graph = _graph(_item(tmp_path, condition="pre-owned"))

    assert graph.unmet_requirements == ("valid_condition",)
    assert [item.treatment_id for item in graph.eligible_treatments] == [
        "normalize-condition"
    ]


def test_unknown_condition_is_waiting_not_guessed(tmp_path):
    _snapshot, graph = _graph(_item(tmp_path, condition="mystery grade"))

    assert graph.unmet_requirements == ("valid_condition",)
    assert "normalize-condition" not in {
        item.treatment_id for item in graph.eligible_treatments
    }
    normalize = next(
        item for item in graph.waiting_treatments
        if item.treatment_id == "normalize-condition"
    )
    assert any("condition_normalizable=false" in reason for reason in normalize.reasons)


def test_failed_attempt_is_preserved_and_not_repeated_until_evidence_changes(tmp_path):
    path = _item(tmp_path, condition="pre-owned")
    snapshot, first = _graph(path)
    failed = TreatmentAttempt(
        treatment_id="normalize-condition",
        treatment_version="1",
        object_generation=snapshot.generation,
        condition_hash=first.condition_hash,
        outcome="failed",
        receipt_id="receipt-failed-1",
    )

    unchanged_snapshot, unchanged = _graph(path, attempts=(failed,))
    assert unchanged_snapshot.generation == snapshot.generation
    assert unchanged.graph_id != first.graph_id
    assert unchanged.eligible_treatments == ()
    assert any(
        "receipt-failed-1" in reason
        for treatment in unchanged.waiting_treatments
        if treatment.treatment_id == "normalize-condition"
        for reason in treatment.reasons
    )

    # A relevant record event creates a new generation.  The same bounded
    # remediation is now a legal successor attempt without manual requeue.
    path.write_text(
        path.read_text(encoding="utf-8").replace("pre-owned", "preowned"),
        encoding="utf-8",
    )
    changed_snapshot, changed = _graph(path, attempts=(failed,))
    assert changed_snapshot.generation != snapshot.generation
    assert [item.treatment_id for item in changed.eligible_treatments] == [
        "normalize-condition"
    ]

    # Successful local repair changes authoritative evidence and converges.
    path.write_text(
        path.read_text(encoding="utf-8").replace("preowned", "Used"),
        encoding="utf-8",
    )
    _ready_snapshot, ready = _graph(path, attempts=(failed,))
    assert ready.unmet_requirements == ()
    assert ready.eligible_treatments == ()


def test_only_authoritative_non_success_attempts_suppress_and_invalidate(tmp_path):
    path = _item(tmp_path, condition="pre-owned")
    snapshot, baseline = _graph(path)

    def attempt(**changes):
        values = {
            "treatment_id": "normalize-condition",
            "treatment_version": "1",
            "object_generation": snapshot.generation,
            "condition_hash": baseline.condition_hash,
            "outcome": "failed",
            "receipt_id": "receipt-1",
        }
        values.update(changes)
        return TreatmentAttempt(**values)

    irrelevant = (
        attempt(outcome="satisfied"),
        attempt(outcome="invented"),
        attempt(treatment_id="unknown-treatment"),
        attempt(treatment_version="obsolete"),
        attempt(receipt_id=""),
    )
    _same_snapshot, unchanged = _graph(path, attempts=irrelevant)
    assert unchanged.graph_id == baseline.graph_id
    assert [item.treatment_id for item in unchanged.eligible_treatments] == [
        "normalize-condition"
    ]

    first = attempt(receipt_id="receipt-a", outcome="partial")
    second = attempt(receipt_id="receipt-b", outcome="conflict")
    _one, ordered = _graph(path, attempts=(first, second))
    _two, reversed_order = _graph(path, attempts=(second, first))
    assert ordered.graph_id == reversed_order.graph_id
    assert ordered.evidence_set_hash == reversed_order.evidence_set_hash
    assert ordered.eligible_treatments == reversed_order.eligible_treatments == ()


def test_disjoint_local_treatments_have_no_ownership_conflict(tmp_path):
    path = _item(
        tmp_path,
        condition="pre-owned",
        ebay_category_id="",
        product_lookup={},
        draft_listing={},
        ebay_offer={},
        ebay_listing={},
        ebay_photos=[],
    )
    _snapshot, graph = _graph(path)
    eligible = {item.treatment_id for item in graph.eligible_treatments}

    assert {"ai-identify", "normalize-condition"}.issubset(eligible)
    assert not any(
        {left, right} == {"ai-identify", "normalize-condition"}
        for left, right, _ownership in graph.ownership_conflicts
    )


def test_ambiguous_external_effect_refuses_automatic_retry(tmp_path):
    path = _item(
        tmp_path,
        ebay_listing={},
    )
    snapshot, graph = _graph(path, ambiguities=("listing.publish",))

    assert snapshot.external_effect_ambiguities == ("listing.publish",)
    assert graph.reconciliation_gates == ("listing.publish",)
    assert "ebay-publish" not in {
        item.treatment_id for item in graph.eligible_treatments
    }
    publish = next(
        item for item in graph.waiting_treatments
        if item.treatment_id == "ebay-publish"
    )
    assert publish.reasons == ("external effect ambiguous: listing.publish",)
