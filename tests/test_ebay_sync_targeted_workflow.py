import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests

from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import (
    HardFailure,
    _treatment_receipt_error,
    _waiting_treatment_receipt_error,
)
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
                     "data_root": tmp_path, "archive_root": tmp_path / "archive",
                     "item_mutation_journal_root": tmp_path / "journal",
                     "sqlite_catalog_path": tmp_path / "catalog.db",
                     "workflow_migration": {"ebay_sync_targeted": "workflow"}}
    return worker, path


def _job():
    return {"queue_name": "ebay_sync", "entity_type": "item", "entity_id": "SKU-1", "payload_json": {
        "payload_schema_id": "ebay-sync-targeted/v1",
        "sku": "SKU-1", "entity_id": "SKU-1", "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1", "graph_id": "graph-1",
        "goal_profile_id": "tgw.ebay_reconciled", "goal_profile_version": "1",
        "object_generation": "generation-1", "condition_hash": "condition-1",
        "provider_effect_id": "effect-1", "provider_identity": "ebay:account",
        "expected_offer_id": "OFF-1",
        "source_operation": "stage-draft",
    }}


def _source_effect_ok():
    return patch("tgw.provider_effects.lookup_succeeded_provider_effect",
                 return_value=(SimpleNamespace(operation="stage-draft"), "OFF-1"))


def _bound_job(path):
    from tgw.item_mutation import item_generation

    job = _job()
    job["payload_json"]["object_generation"] = item_generation(
        json.loads(path.read_text())
    )
    return job


def test_workflow_targeted_success_is_fully_bound(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    monkeypatch.setattr(
        worker, "_sync_one",
        lambda *args: (_ for _ in ()).throw(AssertionError("legacy path called")),
    )
    monkeypatch.setattr(
        "tgw.workers.ebay_sync.state_machine.enqueue_job",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("repush enqueued")),
    )
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    with _source_effect_ok(), \
         patch("tgw.ebay.sync._find_offer", return_value={"offerId": "OFF-1"}):
        job = _bound_job(path)
        receipt = worker._handle_governed_targeted(
            job["payload_json"], "SKU-1", job,
        )
    assert receipt["outcome"] == "satisfied"
    assert receipt["established_conditions"] == ["provider_projection_current"]
    assert receipt["evidence"]["provider_effect_id"] == "effect-1"
    from tgw.item_mutation import item_generation
    assert receipt["evidence"]["resulting_generation"] == item_generation(
        json.loads(path.read_text())
    )


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
                                    side_effect=requests.Timeout("offline")):
        job = _job()
        wait = worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert wait["outcome"] == "transient_backoff"
    assert wait["timer"]["payload"]["sync_retry"] == 1
    assert "effect-1:ebay-sync:1" in wait["timer"]["dedupe_key"]
    assert _waiting_treatment_receipt_error(wait, job) is None
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    return_value={"offerId": "OFF-1"}), \
         pytest.raises(TreatmentFailure) as caught:
        job = _bound_job(tmp_path / "items" / "SKU-1" / "SKU-1.json")
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
    assert "consumer is not admitted" in str(caught.value)
    provider_read.assert_not_called()


def test_internal_handler_rejects_treatment_and_entity_before_provider_read(tmp_path):
    worker, _ = _worker(tmp_path)
    job = _job()
    job["payload_json"]["treatment_id"] = "ebay-publish"
    with patch("tgw.ebay.sync._find_offer") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()


@pytest.mark.parametrize("value", [-1, True, "1", 4, 1.0])
def test_invalid_retry_count_is_rejected_before_ledger_or_provider(tmp_path, value):
    worker, _ = _worker(tmp_path)
    job = _job()
    job["payload_json"]["sync_retry"] = value
    with patch("tgw.provider_effects.lookup_succeeded_provider_effect") as ledger, \
         patch("tgw.ebay.sync._find_offer") as provider_read, \
         pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    ledger.assert_not_called()
    provider_read.assert_not_called()
    job = _job()
    job["entity_id"] = "OTHER"
    with patch("tgw.ebay.sync._find_offer") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()


def test_success_receipt_passes_queue_worker_binding_validation(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    job = _bound_job(path)
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    return_value={"offerId": "OFF-1"}):
        receipt = worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert _treatment_receipt_error(receipt, job) is None


def test_exact_observation_on_current_projection_is_semantic_noop(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    projections = []
    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: projections.append(args) or {"ok": True},
    )
    offer = {"offerId": "OFF-1", "status": "UNPUBLISHED"}
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer", return_value=offer):
        first = _bound_job(path)
        worker._handle_governed_targeted(first["payload_json"], "SKU-1", first)
        second = _bound_job(path)
        second["payload_json"]["graph_id"] = "graph-2"
        receipt = worker._handle_governed_targeted(
            second["payload_json"], "SKU-1", second,
        )
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["changed"] is False
    assert receipt["evidence"]["resulting_generation"] == (
        second["payload_json"]["object_generation"]
    )
    assert len(projections) == 2


def test_noop_with_missing_sqlite_projection_is_durable_repair_required(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    offer = {"offerId": "OFF-1", "status": "UNPUBLISHED"}
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer", return_value=offer):
        first = _bound_job(path)
        worker._handle_governed_targeted(first["payload_json"], "SKU-1", first)

    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: (_ for _ in ()).throw(OSError("catalog unavailable")),
    )
    second = _bound_job(path)
    second["payload_json"]["graph_id"] = "graph-noop-repair"
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer", return_value=offer), \
         pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed_targeted(second["payload_json"], "SKU-1", second)

    assert caught.value.result["outcome"] == "repair_required"
    assert caught.value.result["evidence"]["reason_code"] == (
        "ITEM_MUTATION_REPAIR_REQUIRED"
    )
    operation_id = caught.value.result["evidence"]["operation_id"]
    receipt_path = (
        tmp_path / "journal" / "operations" / operation_id[:2] /
        operation_id / "receipt.json"
    )
    durable = json.loads(receipt_path.read_text())
    assert durable["status"] == "REPAIR_REQUIRED"
    assert durable["changed"] is False


def test_generation_advance_after_provider_read_conflicts_without_projection(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _bound_job(path)

    def observe_then_advance(*args):
        document = json.loads(path.read_text())
        document["operator_note"] = "newer"
        path.write_text(json.dumps(document))
        return {"offerId": "OFF-1"}

    projection = []
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: projection.append(args) or {"ok": True})
    with _source_effect_ok(), patch("tgw.ebay.sync._find_offer",
                                    side_effect=observe_then_advance), \
         pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "conflict"
    assert projection == []
    assert json.loads(path.read_text())["operator_note"] == "newer"


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
