import json
from unittest.mock import patch

import pytest
import requests

from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import HardFailure, _treatment_receipt_error
from tgw.workers.ebay_sync import EbaySyncWorker
from tgw.workflow.evaluator import evaluate
from tgw.workflow.item_snapshot import build_item_snapshot
from tgw.workflow.profiles import TGW_EBAY_RECONCILED
from tgw.workflow.treatments import EBAY_SYNC_TARGETED


def _worker(tmp_path):
    path = tmp_path / "items" / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"sku": "SKU-1", "ebay_offer": {"offer_id": "OFF-1"}}))
    worker = object.__new__(EbaySyncWorker)
    worker.config = {"itemdata_root": tmp_path / "items",
                     "workflow_migration": {"ebay_sync_targeted": "workflow"}}
    return worker, path


def _job():
    return {"entity_type": "item", "entity_id": "SKU-1", "payload_json": {
        "sku": "SKU-1", "entity_id": "SKU-1", "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1", "graph_id": "graph-1",
        "goal_profile_id": "tgw.ebay_reconciled", "goal_profile_version": "1",
        "object_generation": "generation-1", "condition_hash": "condition-1",
        "provider_effect_id": "effect-1", "provider_identity": "ebay:account",
        "expected_offer_id": "OFF-1",
    }}


def _source_effect_ok():
    return patch("tgw.provider_effects.lookup_succeeded_provider_effect",
                 return_value=(object(), "OFF-1"))


def test_workflow_targeted_success_is_fully_bound(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    monkeypatch.setattr(worker, "_sync_one", lambda offer, sku: 1)
    with patch("tgw.item_mutation.item_generation", side_effect=["generation-1", "generation-2"]), \
         _source_effect_ok(), \
         patch("tgw.ebay.sync._find_offer", return_value={"offerId": "OFF-1"}):
        job = _job()
        receipt = worker._handle_governed_targeted(
            job["payload_json"], "SKU-1", job,
        )
    assert receipt["outcome"] == "satisfied"
    assert receipt["established_conditions"] == ["provider_projection_current"]
    assert receipt["evidence"]["provider_effect_id"] == "effect-1"
    assert receipt["evidence"]["resulting_generation"] == "generation-2"


@pytest.mark.parametrize(
    "offer,reason",
    [(None, "PROVIDER_OFFER_ABSENT"),
     ({"offerId": "OTHER"}, "PROVIDER_OFFER_CONTRADICTION")],
)
def test_missing_or_contradictory_offer_is_not_success(tmp_path, offer, reason):
    worker, _ = _worker(tmp_path)
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.ebay.sync._find_offer", return_value=offer), \
         pytest.raises(TreatmentFailure) as caught:
        job = _job()
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "reconciliation_required"
    assert caught.value.result["evidence"]["reason_code"] == reason


def test_timeout_and_projection_failure_are_truthful_non_success(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    side_effect=requests.Timeout("offline")), \
         pytest.raises(TreatmentFailure) as caught:
        job = _job()
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "transient_backoff"
    monkeypatch.setattr(worker, "_sync_one", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    return_value={"offerId": "OFF-1"}), \
         pytest.raises(TreatmentFailure) as caught:
        job = _job()
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "repair_required"


def test_default_selector_preserves_legacy_targeted_behavior(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    worker.config["workflow_migration"] = {}
    monkeypatch.setattr(worker, "_sync_one", lambda *_: 0)
    with patch("tgw.ebay.sync._find_offer", return_value=None):
        assert worker.handle({"payload_json": {"sku": "SKU-1"}}) is None


def test_workflow_selector_fails_closed_before_provider_read(tmp_path):
    worker, _ = _worker(tmp_path)
    with patch("tgw.ebay.sync._find_offer") as provider_read, pytest.raises(HardFailure) as caught:
        worker.handle(_job())
    assert "projection CAS pending" in str(caught.value)
    provider_read.assert_not_called()


def test_internal_handler_rejects_treatment_and_entity_before_provider_read(tmp_path):
    worker, _ = _worker(tmp_path)
    job = _job()
    job["payload_json"]["treatment_id"] = "ebay-publish"
    with patch("tgw.ebay.sync._find_offer") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()
    job = _job()
    job["entity_id"] = "OTHER"
    with patch("tgw.ebay.sync._find_offer") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()


def test_success_receipt_passes_queue_worker_binding_validation(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    monkeypatch.setattr(worker, "_sync_one", lambda *_: 0)
    job = _job()
    with patch("tgw.item_mutation.item_generation", side_effect=["generation-1", "generation-2"]), \
         _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    return_value={"offerId": "OFF-1"}):
        receipt = worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert _treatment_receipt_error(receipt, job) is None


def test_evaluator_sequences_source_effect_to_projection_receipt(tmp_path):
    _, path = _worker(tmp_path)
    source = {"provider_effect_id": "effect-1", "outcome": "source_succeeded"}
    snapshot = build_item_snapshot(
        path, TGW_EBAY_RECONCILED, treatments=(EBAY_SYNC_TARGETED,),
        provider_projection_receipt=source,
    )
    graph = evaluate(snapshot=snapshot, goal=TGW_EBAY_RECONCILED,
                     treatments=(EBAY_SYNC_TARGETED,), evaluator_version="test/v1")
    assert [item.treatment_id for item in graph.eligible_treatments] == [
        "ebay-sync-targeted"
    ]
    completed = build_item_snapshot(
        path, TGW_EBAY_RECONCILED, treatments=(EBAY_SYNC_TARGETED,),
        provider_projection_receipt={
            "provider_effect_id": "effect-1", "outcome": "satisfied",
            "resulting_generation": snapshot.generation,
        },
    )
    done = evaluate(snapshot=completed, goal=TGW_EBAY_RECONCILED,
                    treatments=(EBAY_SYNC_TARGETED,), evaluator_version="test/v1")
    assert done.satisfied_requirements == ("provider_projection_current",)
    assert done.eligible_treatments == ()
