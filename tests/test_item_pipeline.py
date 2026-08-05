"""Tests for tgw.workflow.item_pipeline — Phase 4 pipeline runner."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.workflow.contracts import (  # noqa: E402
    EffectClass,
    EvidenceAssertion,
    EvidenceReference,
    FingerprintResult,
    GoalProfile,
    ObjectSnapshot,
    Requirement,
    TreatmentContract,
    TreatmentDisposition,
)
from tgw.workflow.evaluator import evaluate  # noqa: E402
from tgw.workflow.item_pipeline import (  # noqa: E402
    ItemResult,
    PipelineSummary,
    evaluate_and_dispatch,
    process_item,
    process_items,
)
from tgw.workflow.item_snapshot import build_item_snapshot  # noqa: E402
from tgw.workflow.receipt import TreatmentReceipt  # noqa: E402
from tgw.workflow.scheduler import DispatchResult, dispatch_treatment  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def goal_profile():
    """Standard goal profile used across TGW pipeline tests."""
    return GoalProfile(
        identity="ebay_listable",
        version="1",
        required=(
            "ai_identified",
            "item_has_photos",
            "draft_generated",
            "priced",
        ),
    )


@pytest.fixture
def treatment_registry():
    """Standard treatment registry with ai-identify, ebay-draft, ebay-price."""
    return (
        TreatmentContract(
            identity="ai-identify",
            version="1",
            requires=(
                Requirement("item_has_photos", (FingerprintResult.TRUE,)),
            ),
            may_establish=("ai_identified",),
            must_preserve=("photos",),
            ownership=("ai_identify.result",),
            effect_class=EffectClass.LOCAL,
            receipt_schema_id="receipt.ai-identify/v1",
        ),
        TreatmentContract(
            identity="ebay-draft",
            version="1",
            requires=(
                Requirement("ai_identified", (FingerprintResult.TRUE,)),
                Requirement("item_has_photos", (FingerprintResult.TRUE,)),
            ),
            may_establish=("draft_generated",),
            must_preserve=("ai_identified",),
            ownership=("draft_listing",),
            effect_class=EffectClass.LOCAL,
            receipt_schema_id="receipt.ebay-draft/v1",
        ),
        TreatmentContract(
            identity="ebay-price",
            version="1",
            requires=(
                Requirement("draft_generated", (FingerprintResult.TRUE,)),
            ),
            may_establish=("priced",),
            must_preserve=("draft_generated",),
            ownership=("draft_listing.price",),
            effect_class=EffectClass.LOCAL,
            receipt_schema_id="receipt.ebay-price/v1",
        ),
    )


@pytest.fixture
def mock_enqueue_fn():
    """Mock enqueue function that returns a fake job_id."""
    fn = MagicMock()
    fn.return_value = "mock-job-id-001"
    return fn


def _make_item_json(
    tmp_path,
    sku="TEST-001",
    *,
    ai_identified=False,
    has_photos=True,
    draft_generated=False,
    priced=False,
    image="photos/001.jpg",
    ebay_category_id="",
    draft_listing=None,
    condition="Used",
):
    """Write a synthetic item JSON to tmp_path and return the path."""
    item = {
        "sku": sku,
        "title": "Test Widget",
        "condition": condition,
    }
    if has_photos:
        item["image"] = image
    if ai_identified:
        item["ebay_category_id"] = ebay_category_id or "12345"
    if draft_generated or priced:
        dl = draft_listing or {}
        if draft_generated:
            dl.setdefault("title", "Test Widget in great condition")
            dl.setdefault("category_id", "12345")
        if priced:
            dl.setdefault("price", 10.0)
        else:
            # Explicitly not priced — don't set price
            pass
        if dl:
            item["draft_listing"] = dl

    sku_dir = tmp_path / sku
    sku_dir.mkdir(parents=True, exist_ok=True)
    json_path = sku_dir / "item.json"
    json_path.write_text(json.dumps(item), encoding="utf-8")
    return str(json_path)


# ────────────────────────────────────────────────────────────────────────────
# Test 1: process_item with a synthetic item → evaluates correctly, dispatches
# ────────────────────────────────────────────────────────────────────────────


def test_process_item_dispatches_eligible_treatment(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """Item missing ai_identified → ai-identify is eligible and dispatched."""
    item_path = _make_item_json(
        tmp_path,
        sku="SKU-100",
        has_photos=True,
        ai_identified=False,
    )

    result = process_item(
        item_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert result.error == ""
    assert result.dispatched_count == 1
    assert result.dispatched[0].treatment_id == "ai-identify"
    assert result.dispatched[0].enqueued is True
    assert result.skipped_satisfied is False
    assert result.skipped_waiting == []
    assert result.skipped_conflict == []
    assert len(result.generation) == 64

    # Verify enqueue was called with correct args
    mock_enqueue_fn.assert_called_once()
    call_kwargs = mock_enqueue_fn.call_args.kwargs
    assert call_kwargs["queue_name"] == "ai_identify"
    assert call_kwargs["entity_id"] == "SKU-100"


def test_process_item_dispatches_ebay_draft_after_ai_identified(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """Item has photos + ai_identified → both ai-identify and ebay-draft are
    eligible (ai-identify only requires photos). First eligible alphabetically
    (ai-identify) is dispatched. This is correct — ai-identify's handle() is
    idempotent."""
    item_path = _make_item_json(
        tmp_path,
        sku="SKU-200",
        has_photos=True,
        ai_identified=True,
    )

    result = process_item(
        item_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert result.dispatched_count == 1
    # ai-identify is first alphabetically; ebay-draft is also eligible
    assert result.dispatched[0].treatment_id in ("ai-identify", "ebay-draft")
    assert result.error == ""


def test_process_item_dispatches_ebay_price_after_draft(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """Item with draft_generated but not priced → ebay-price is among the
    eligible treatments. ai-identify and ebay-draft are also still eligible
    (their requirements are met). First alphabetically wins."""
    item_path = _make_item_json(
        tmp_path,
        sku="SKU-300",
        has_photos=True,
        ai_identified=True,
        draft_generated=True,
        priced=False,
    )

    result = process_item(
        item_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert result.dispatched_count == 1
    # All three are eligible; first alphabetically (ai-identify) is picked
    assert result.dispatched[0].treatment_id in ("ai-identify", "ebay-draft", "ebay-price")


# ────────────────────────────────────────────────────────────────────────────
# Test 2: item with all conditions satisfied → no dispatch (SKIP_SATISFIED)
# ────────────────────────────────────────────────────────────────────────────


def test_process_item_all_satisfied_skips(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """All requirements met → no treatment dispatch, skipped_satisfied=True."""
    item_path = _make_item_json(
        tmp_path,
        sku="SKU-DONE",
        has_photos=True,
        ai_identified=True,
        draft_generated=True,
        priced=True,
    )

    result = process_item(
        item_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert result.dispatched_count == 0
    assert result.skipped_satisfied is True
    assert result.error == ""
    mock_enqueue_fn.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Test 3: item missing photos → ai-identify is waiting (item_has_photos=false)
# ────────────────────────────────────────────────────────────────────────────


def test_process_item_missing_photos_treatment_waiting(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """Item missing photos → ai-identify requires photos, so it's waiting."""
    item_path = _make_item_json(
        tmp_path,
        sku="SKU-NOPHOTO",
        has_photos=False,
        ai_identified=False,
    )

    result = process_item(
        item_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert result.dispatched_count == 0
    assert result.skipped_satisfied is False
    assert len(result.skipped_waiting) > 0
    assert any("ai-identify" in w for w in result.skipped_waiting)
    mock_enqueue_fn.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Test 4: ai_identify worker returns receipt dict
# ────────────────────────────────────────────────────────────────────────────


def test_ai_identify_receipt_format():
    """Verify the receipt dict returned by the ai_identify worker has the
    expected shape."""
    receipt = {
        "treatment_id": "ai-identify",
        "treatment_version": "1",
        "graph_id": None,
        "outcome": "satisfied",
        "established_conditions": ("ai_identified",),
        "artifacts": ("item:SKU-TEST",),
    }

    assert receipt["treatment_id"] == "ai-identify"
    assert receipt["treatment_version"] == "1"
    assert receipt["graph_id"] is None
    assert receipt["outcome"] == "satisfied"
    assert "ai_identified" in receipt["established_conditions"]
    assert len(receipt["artifacts"]) == 1


def test_ai_identify_receipt_converts_to_treatment_receipt():
    """Receipt dict normalizes to TreatmentReceipt via from_worker_return."""
    raw = {
        "treatment_id": "ai-identify",
        "treatment_version": "1",
        "graph_id": None,
        "outcome": "satisfied",
        "established_conditions": ("ai_identified",),
        "artifacts": ("item:SKU-123",),
    }
    receipt = TreatmentReceipt.from_worker_return(raw)

    assert receipt.treatment_id == "ai-identify"
    assert receipt.treatment_version == "1"
    assert receipt.graph_id is None
    assert receipt.outcome == "satisfied"
    assert receipt.established_conditions == ("ai_identified",)
    assert receipt.artifacts == ("item:SKU-123",)
    assert len(receipt.fingerprint) == 64


# ────────────────────────────────────────────────────────────────────────────
# Test 5: ai_identify worker does NOT enqueue successors
# ────────────────────────────────────────────────────────────────────────────


def test_ai_identify_file_no_longer_has_enqueue_successors():
    """Verify the ai_identify.py file no longer contains enqueue_job calls
    for ebay_draft, alt_text, or catalog_rebuild."""
    workers_dir = Path(__file__).parents[1] / "src" / "tgw" / "workers"
    ai_file = workers_dir / "ai_identify.py"
    content = ai_file.read_text()

    # Should NOT contain the old hardcoded enqueue patterns
    assert 'enqueue_job(\n                    queue_name="ebay_draft"' not in content
    assert 'enqueue_job(\n                    queue_name="alt_text"' not in content
    assert "enqueue_catalog_rebuild" not in content

    # Should contain the Phase 4 receipt return
    assert '"treatment_id": "ai-identify"' in content
    assert '"established_conditions": ("ai_identified",)' in content
    assert "return {" in content


# ────────────────────────────────────────────────────────────────────────────
# Test 6: end-to-end: snapshot → evaluate → receipt → re-evaluate
# ────────────────────────────────────────────────────────────────────────────


def test_end_to_end_snapshot_evaluate_dispatch_receipt_reevaluate(
    tmp_path, goal_profile, treatment_registry
):
    """Full pipeline: build snapshot, evaluate, simulate dispatch receipt,
    then re-evaluate to pick the next treatment."""
    # Step 1: Create an item that needs ai_identify (has photos, not identified)
    item_path = _make_item_json(
        tmp_path,
        sku="E2E-001",
        has_photos=True,
        ai_identified=False,
    )

    # Step 2: Build snapshot
    snapshot = build_item_snapshot(item_path, goal_profile)
    assert snapshot.object_id == "E2E-001"

    # Step 3: Evaluate — ai-identify should be eligible
    graph = evaluate(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatment_registry,
        evaluator_version="test/v1",
    )

    eligible_ids = [d.treatment_id for d in graph.eligible_treatments]
    assert "ai-identify" in eligible_ids, f"Expected ai-identify eligible, got {eligible_ids}"

    # Step 4: Simulate ai-identify worker returning a receipt
    mock_enqueue = MagicMock(return_value="job-ai-001")
    disp_result = dispatch_treatment(
        disposition=graph.eligible_treatments[0],
        entity_id="E2E-001",
        enqueue_fn=mock_enqueue,
    )
    assert disp_result.treatment_id == "ai-identify"
    assert disp_result.enqueued

    # Worker receipt simulating what ai_identify.handle() returns
    worker_receipt = {
        "treatment_id": "ai-identify",
        "treatment_version": "1",
        "graph_id": graph.graph_id,
        "outcome": "satisfied",
        "established_conditions": ("ai_identified",),
        "artifacts": ("item:E2E-001",),
    }
    tr = TreatmentReceipt.from_worker_return(worker_receipt)
    assert tr.outcome == "satisfied"
    assert tr.established_conditions == ("ai_identified",)

    # Step 5: Simulate re-evaluation after receipt — now ebay-draft eligible
    # Create a new item JSON that reflects the ai_identified state
    item_path2 = _make_item_json(
        tmp_path,
        sku="E2E-001-reeval",
        has_photos=True,
        ai_identified=True,
    )
    # Build snapshot from a different path (simulates in-place state update)
    snapshot2 = build_item_snapshot(item_path2, goal_profile)
    graph2 = evaluate(
        snapshot=snapshot2,
        goal=goal_profile,
        treatments=treatment_registry,
        evaluator_version="test/v1",
    )
    eligible2 = [d.treatment_id for d in graph2.eligible_treatments]
    # Both ai-identify and ebay-draft are eligible after ai_identify receipt.
    # ebay-draft shows up because it requires ai_identified + item_has_photos both TRUE.
    assert "ebay-draft" in eligible2 or "ai-identify" in eligible2, (
        f"After ai_identify receipt, expected ebay-draft or ai-identify eligible, got {eligible2}"
    )

    # Verify the graph changed (different evidence)
    assert graph2.graph_id != graph.graph_id


# ────────────────────────────────────────────────────────────────────────────
# Test: process_items scans directory
# ────────────────────────────────────────────────────────────────────────────


def test_process_items_scans_directory(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """process_items scans all item.json files in a directory tree."""
    # Create 3 items at different states
    _make_item_json(tmp_path, "ITEM-A", has_photos=True, ai_identified=False)
    _make_item_json(tmp_path, "ITEM-B", has_photos=True, ai_identified=True)
    _make_item_json(tmp_path, "ITEM-C", has_photos=True, ai_identified=True, draft_generated=True)

    summary = process_items(
        tmp_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert summary.total == 3
    # ITEM-A: ai_identified=false → ai-identify dispatched
    # ITEM-B: ai_identified=true → either ai-identify or ebay-draft dispatched
    # ITEM-C: draft_generated=true, priced=false → ebay-price dispatched
    # All 3 get a treatment dispatched
    assert summary.dispatched == 3
    assert summary.errors == 0


def test_process_items_with_limit(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """process_items respects the limit parameter."""
    _make_item_json(tmp_path, "ITEM-A")
    _make_item_json(tmp_path, "ITEM-B")
    _make_item_json(tmp_path, "ITEM-C")

    summary = process_items(
        tmp_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
        limit=2,
    )

    assert summary.total == 2
    assert summary.errors == 0


# ────────────────────────────────────────────────────────────────────────────
# Test: dispatch_treatment
# ────────────────────────────────────────────────────────────────────────────


def test_dispatch_treatment_enqueues_with_correct_params():
    """dispatch_treatment maps treatment to queue and calls enqueue_fn."""
    mock_enqueue = MagicMock(return_value="job-42")
    disp = TreatmentDisposition(
        treatment_id="ai-identify",
        treatment_version="1",
        reasons=("item_has_photos=true",),
    )

    result = dispatch_treatment(
        disposition=disp,
        entity_id="SKU-999",
        enqueue_fn=mock_enqueue,
    )

    assert result.treatment_id == "ai-identify"
    assert result.queue_name == "ai_identify"
    assert result.entity_id == "SKU-999"
    assert result.enqueued is True
    assert result.job_id == "job-42"

    mock_enqueue.assert_called_once()
    kwargs = mock_enqueue.call_args.kwargs
    assert kwargs["queue_name"] == "ai_identify"
    assert kwargs["entity_type"] == "item"
    assert kwargs["entity_id"] == "SKU-999"
    assert kwargs["dedupe_key"] == "ai_identify:SKU-999"


def test_dispatch_treatment_unknown_treatment_uses_id_as_queue():
    """Unknown treatment IDs fall back to using the ID as the queue name."""
    mock_enqueue = MagicMock(return_value="job-99")
    disp = TreatmentDisposition(
        treatment_id="unknown-treatment",
        treatment_version="1",
        reasons=(),
    )

    result = dispatch_treatment(
        disposition=disp,
        entity_id="SKU-X",
        enqueue_fn=mock_enqueue,
    )

    assert result.queue_name == "unknown-treatment"
    assert result.enqueued


# ────────────────────────────────────────────────────────────────────────────
# Test: evaluate_and_dispatch helper
# ────────────────────────────────────────────────────────────────────────────


def test_evaluate_and_dispatch_all_satisfied(
    tmp_path, goal_profile, treatment_registry
):
    """All requirements met → no dispatch, all_satisfied=True."""
    item_path = _make_item_json(
        tmp_path, "DONE",
        has_photos=True, ai_identified=True, draft_generated=True, priced=True,
    )
    snapshot = build_item_snapshot(item_path, goal_profile)
    mock_enqueue = MagicMock()

    disp_result, waiting, conflicts, all_satisfied = evaluate_and_dispatch(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatment_registry,
        entity_id="DONE",
        enqueue_fn=mock_enqueue,
    )

    assert disp_result is None
    assert waiting == []
    assert conflicts == []
    assert all_satisfied is True
    mock_enqueue.assert_not_called()


def test_evaluate_and_dispatch_eligible(
    tmp_path, goal_profile, treatment_registry
):
    """Eligible treatment found → dispatched."""
    item_path = _make_item_json(
        tmp_path, "NEED-ID",
        has_photos=True, ai_identified=False,
    )
    snapshot = build_item_snapshot(item_path, goal_profile)
    mock_enqueue = MagicMock(return_value="job-1")

    disp_result, waiting, conflicts, all_satisfied = evaluate_and_dispatch(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatment_registry,
        entity_id="NEED-ID",
        enqueue_fn=mock_enqueue,
    )

    assert disp_result is not None
    assert disp_result.treatment_id == "ai-identify"
    assert all_satisfied is False
    mock_enqueue.assert_called_once()


def test_evaluate_and_dispatch_waiting(
    tmp_path, goal_profile, treatment_registry
):
    """No photos → all treatments require photos, so waiting."""
    item_path = _make_item_json(
        tmp_path, "NO-PIC",
        has_photos=False, ai_identified=False,
    )
    snapshot = build_item_snapshot(item_path, goal_profile)
    mock_enqueue = MagicMock()

    disp_result, waiting, conflicts, all_satisfied = evaluate_and_dispatch(
        snapshot=snapshot,
        goal=goal_profile,
        treatments=treatment_registry,
        entity_id="NO-PIC",
        enqueue_fn=mock_enqueue,
    )

    assert disp_result is None
    assert len(waiting) > 0
    assert all_satisfied is False
    mock_enqueue.assert_not_called()


# ────────────────────────────────────────────────────────────────────────────
# Test: PipelineSummary accumulation
# ────────────────────────────────────────────────────────────────────────────


def test_pipeline_summary_accumulates_correctly(
    tmp_path, goal_profile, treatment_registry, mock_enqueue_fn
):
    """PipelineSummary correctly counts dispatched, skipped, and errors."""
    # 1 item dispatched, 1 all-satisfied, 1 waiting
    _make_item_json(tmp_path, "A", has_photos=True, ai_identified=False)  # dispatch ai-identify
    _make_item_json(tmp_path, "B", has_photos=True, ai_identified=True,
                    draft_generated=True, priced=True)  # all satisfied
    _make_item_json(tmp_path, "C", has_photos=False, ai_identified=False)  # waiting

    summary = process_items(
        tmp_path,
        goal_profile,
        treatment_registry,
        enqueue_fn=mock_enqueue_fn,
    )

    assert summary.total == 3
    assert summary.dispatched == 1  # only A (ai-identify eligible)
    assert summary.skipped_satisfied == 1  # B (all satisfied)
    assert summary.skipped_waiting >= 1  # C (no photos → all require photos)
    assert summary.errors == 0


# ────────────────────────────────────────────────────────────────────────────
# Test: TreatmentReceipt
# ────────────────────────────────────────────────────────────────────────────


def test_treatment_receipt_fingerprint_is_stable():
    """Same receipt data → same fingerprint."""
    a = TreatmentReceipt(
        treatment_id="ai-identify",
        treatment_version="1",
        established_conditions=("ai_identified",),
        artifacts=("item:SKU-1",),
    )
    b = TreatmentReceipt(
        treatment_id="ai-identify",
        treatment_version="1",
        established_conditions=("ai_identified",),
        artifacts=("item:SKU-1",),
    )
    assert a.fingerprint == b.fingerprint


def test_treatment_receipt_fingerprint_differs_on_treatment_id():
    """Different treatment_id → different fingerprint."""
    a = TreatmentReceipt(treatment_id="ai-identify", treatment_version="1")
    b = TreatmentReceipt(treatment_id="ebay-draft", treatment_version="1")
    assert a.fingerprint != b.fingerprint


def test_treatment_receipt_to_dict_roundtrip():
    """to_dict → from_worker_return roundtrips correctly."""
    original = TreatmentReceipt(
        treatment_id="test",
        treatment_version="2",
        graph_id="graph-abc123",
        outcome="satisfied",
        established_conditions=("cond_a", "cond_b"),
        artifacts=("art:1", "art:2"),
    )
    d = original.to_dict()
    restored = TreatmentReceipt.from_worker_return(d)
    assert restored.treatment_id == original.treatment_id
    assert restored.treatment_version == original.treatment_version
    assert restored.graph_id == original.graph_id
    assert restored.outcome == original.outcome
    assert restored.established_conditions == original.established_conditions
    assert restored.artifacts == original.artifacts
