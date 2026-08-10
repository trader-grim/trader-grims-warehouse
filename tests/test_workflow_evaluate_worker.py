import json
from unittest.mock import MagicMock, patch

import pytest

from tgw.errors import TreatmentFailure
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.workers.workflow_evaluate import evaluate_event


def _event(item_root, **changes):
    payload = {
        "entity_id": "SKU-1",
        "origin_job_id": "origin-1",
        "origin_receipt": {"outcome": "satisfied", "graph_id": "old-graph"},
        "goal_profile_id": "tgw.ebay_listable",
        "goal_profile_version": "1",
        "prior_graph_id": "old-graph",
        "prior_object_generation": "old-generation",
    }
    payload.update(changes)
    return {"job_id": "event-1", "entity_type": "item", "entity_id": "SKU-1",
            "payload_json": payload}, {"itemdata_root": item_root}


def _item(tmp_path):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"sku": "SKU-1", "condition": "Used", "image": "a.jpg"}),
                    encoding="utf-8")
    return root


def test_rebuilds_new_generation_and_dispatches_evaluator_selected_local_treatment(tmp_path):
    job, cfg = _event(_item(tmp_path))
    enqueue = MagicMock(return_value="next-job")
    receipt = evaluate_event(job, cfg, enqueue_fn=enqueue)
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["object_generation"] != "old-generation"
    assert receipt["evidence"]["dispatch"] == "enqueued"
    assert enqueue.call_count == 1
    assert enqueue.call_args.kwargs["payload"]["graph_id"] == receipt["evidence"]["graph_id"]


def test_external_next_treatment_is_reported_but_not_dispatched(tmp_path):
    root = tmp_path / "items"
    path = root / "SKU-1" / "SKU-1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "sku": "SKU-1", "condition": "Used", "image": "a.jpg",
        "product_lookup": {"title": "known"}, "ebay_category_id": "123",
        "draft_listing": {"title": "Ready", "category_id": "123", "price": 10},
        "ebay_offer": {"offer_id": "offer-1"},
    }), encoding="utf-8")
    job, cfg = _event(root)
    enqueue = MagicMock()
    receipt = evaluate_event(job, cfg, enqueue_fn=enqueue)
    assert receipt["evidence"]["dispatch"] == "held_external"
    assert receipt["evidence"]["next_treatment"] in {"ebay-publish", "ebay-upload"}
    enqueue.assert_not_called()


@pytest.mark.parametrize(
    "changes, reason",
    [
        ({"goal_profile_id": "coding.deployed"}, "PROFILE_MISMATCH"),
        ({"goal_profile_id": "not.registered"}, "UNKNOWN_PROFILE"),
        ({"origin_receipt": {"outcome": "failed"}}, "INVALID_ORIGIN_RECEIPT"),
        ({"prior_graph_id": "different"}, "ORIGIN_GRAPH_MISMATCH"),
    ],
)
def test_invalid_event_is_structured_hard_failure(tmp_path, changes, reason):
    job, cfg = _event(_item(tmp_path), **changes)
    with pytest.raises(TreatmentFailure) as caught:
        evaluate_event(job, cfg, enqueue_fn=MagicMock())
    assert caught.value.result["outcome"] == "failed"
    assert caught.value.result["evidence"]["reason_code"] == reason


def test_worker_completion_uses_atomic_outbox_only_for_bound_success_receipt():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    receipt = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "outcome": "satisfied",
               "receipt_schema_id": "treatment-receipt/v1"}
    job = {"job_id": "job-1", "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": {"treatment_id": "normalize-condition", "treatment_version": "1",
                            "graph_id": "graph-1", "object_generation": "gen-1",
                            "goal_profile_id": "tgw.ebay_listable",
                            "goal_profile_version": "1", "entity_id": "SKU-1",
                            "object_id": "SKU-1"}}
    worker.handle = MagicMock(return_value=receipt)
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary:
        worker._process(job)
    atomic.assert_called_once_with("job-1", "owner", receipt)
    ordinary.assert_not_called()


def test_unrecognized_dict_keeps_backward_compatible_completion():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "echo"
    worker.owner = "owner"
    worker.config = {}
    worker.handle = MagicMock(return_value={"ok": True})
    job = {"job_id": "job-1", "entity_type": "system", "payload_json": {}}
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary:
        worker._process(job)
    atomic.assert_not_called()
    ordinary.assert_called_once_with("job-1", "owner", result={"ok": True})


def test_atomic_completion_writes_receipt_then_durable_event():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [
        ("SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                   "graph_id": "graph-1", "object_generation": "gen-1"}),
        ("event-job",),
    ]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    receipt = {"outcome": "satisfied", "graph_id": "graph-1"}
    with patch.object(state_machine, "_conn", return_value=connection):
        event_id = state_machine.complete_treatment_and_enqueue_evaluation(
            "origin-job", "owner", receipt,
        )
    assert event_id == "event-job"
    assert cursor.execute.call_count == 2
    inserted = json.loads(cursor.execute.call_args_list[1].args[1][2])
    assert inserted["origin_receipt"] == receipt
    assert inserted["prior_object_generation"] == "gen-1"


def test_atomic_completion_raises_when_lease_guard_loses():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = None
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        with pytest.raises(RuntimeError, match="lost running lease"):
            state_machine.complete_treatment_and_enqueue_evaluation(
                "origin-job", "wrong-owner", {"outcome": "satisfied"},
            )
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize(
    ("receipt_change", "payload_change", "job_change", "reason"),
    [
        ({"graph_id": ""}, {}, {}, "INVALID_RECEIPT_IDENTITY"),
        ({"graph_id": "wrong"}, {}, {}, "GRAPH_ID_MISMATCH"),
        ({}, {"object_generation": ""}, {}, "INVALID_OBJECT_GENERATION"),
        ({}, {"goal_profile_version": ""}, {}, "INVALID_GOAL_PROFILE_VERSION"),
        ({}, {"entity_id": "OTHER"}, {}, "ENTITY_ID_MISMATCH"),
        ({}, {"object_id": "OTHER"}, {}, "OBJECT_ID_MISMATCH"),
        ({}, {}, {"entity_type": "system"}, "INVALID_ENTITY_TYPE"),
    ],
)
def test_candidate_treatment_receipt_with_bad_binding_is_terminal_not_success(
    receipt_change, payload_change, job_change, reason,
):
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    receipt = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "outcome": "satisfied",
               "receipt_schema_id": "treatment-receipt/v1"}
    receipt.update(receipt_change)
    payload = {"treatment_id": "normalize-condition", "treatment_version": "1",
               "graph_id": "graph-1", "object_generation": "gen-1",
               "goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
               "entity_id": "SKU-1", "object_id": "SKU-1"}
    payload.update(payload_change)
    job = {"job_id": "job-1", "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": payload}
    job.update(job_change)
    worker.handle = MagicMock(return_value=receipt)
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.mark_dead_letter") as dead, \
         patch("tgw.queue.worker_base.state_machine.mark_succeeded") as ordinary, \
         patch("tgw.queue.worker_base.state_machine.complete_treatment_and_enqueue_evaluation") as atomic:
        worker._process(job)
    ordinary.assert_not_called()
    atomic.assert_not_called()
    result = dead.call_args.kwargs["result"]
    assert result["outcome"] == "failed"
    assert result["evidence"]["reason_code"] == reason


def test_atomic_insert_exception_propagates_to_transaction_context():
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = (
        "SKU-1", {"goal_profile_id": "tgw.ebay_listable", "goal_profile_version": "1",
                  "graph_id": "graph-1", "object_generation": "gen-1"},
    )
    cursor.execute.side_effect = [None, RuntimeError("insert failed")]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(state_machine, "_conn", return_value=connection):
        with pytest.raises(RuntimeError, match="insert failed"):
            state_machine.complete_treatment_and_enqueue_evaluation(
                "origin-job", "owner", {"outcome": "satisfied"},
            )
    # The exception leaves the `_conn` transaction context; psycopg rolls it back.
    assert connection.__exit__.call_args.args[0] is RuntimeError


def test_treatment_failure_persists_structured_result_as_dead_letter():
    worker = object.__new__(QueueWorker)
    worker.queue_name = "normalize_condition"
    worker.owner = "owner"
    worker.config = {}
    failed = {"outcome": "failed", "evidence": {"reason_code": "CONFLICT"}}
    worker.handle = MagicMock(side_effect=TreatmentFailure("conflict", failed))
    job = {"job_id": "job-1", "entity_type": "item", "entity_id": "SKU-1",
           "payload_json": {}}
    with patch("tgw.queue.worker_base.state_machine.mark_running"), \
         patch("tgw.queue.worker_base.state_machine.mark_dead_letter") as dead, \
         patch("tgw.notify.notify"):
        worker._process(job)
    dead.assert_called_once_with(
        "job-1", "owner", "TreatmentFailure('conflict')", result=failed,
    )
