import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    worker.owner = "owner-1"
    worker.config = {"itemdata_root": tmp_path / "items",
                     "data_root": tmp_path, "archive_root": tmp_path / "archive",
                     "item_mutation_journal_root": tmp_path / "journal",
                     "sqlite_catalog_path": tmp_path / "catalog.db",
                     "workflow_migration": {"ebay_sync_targeted": "workflow"}}
    return worker, path


def _activate_consumer(worker):
    worker.config["workflow_migration"]["ebay_sync_targeted_consumer"] = "workflow"


def _job():
    return {"job_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "lease_token": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "queue_name": "ebay_sync", "entity_type": "item", "entity_id": "SKU-1", "payload_json": {
        "payload_schema_id": "ebay-sync-targeted/v1",
        "sku": "SKU-1", "entity_id": "SKU-1", "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1", "graph_id": "graph-1",
        "goal_profile_id": "tgw.ebay_reconciled", "goal_profile_version": "1",
        "object_generation": "generation-1", "condition_hash": "condition-1",
        "provider_effect_id": "effect-1", "provider_identity": "ebay:account",
        "expected_offer_id": "OFF-1",
        "source_operation": "stage-draft",
    }}


@pytest.fixture(autouse=True)
def _durable_checkpoint(monkeypatch):
    monkeypatch.setattr(
        "tgw.workers.ebay_sync.state_machine.checkpoint_running_job",
        lambda _job_id, _owner, _token, checkpoint: checkpoint,
    )


def _source_effect_ok():
    return patch("tgw.provider_effects.lookup_succeeded_provider_effect",
                 return_value=(SimpleNamespace(operation="stage-draft"), "OFF-1"))


def _provider_offer(offer):
    return patch("tgw.workers.ebay_sync.ebay_get",
                 return_value={"offers": [] if offer is None else [offer]})


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
    with _source_effect_ok(), _provider_offer({"offerId": "OFF-1"}):
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
    job = _job()
    if offer is None:
        job["payload_json"]["sync_retry"] = 3
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), _provider_offer(offer), \
         pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "reconciliation_required"
    assert caught.value.result["evidence"]["reason_code"] == reason


def test_timeout_and_projection_failure_are_truthful_non_success(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get",
                                    side_effect=requests.Timeout("offline")):
        job = _job()
        wait = worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert wait["outcome"] == "transient_backoff"
    assert wait["timer"]["payload"]["sync_retry"] == 1
    assert "effect-1:ebay-sync:1" in wait["timer"]["dedupe_key"]
    assert _waiting_treatment_receipt_error(wait, job) is None
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: (_ for _ in ()).throw(OSError("disk")))
    with _source_effect_ok(), _provider_offer({"offerId": "OFF-1"}), \
         pytest.raises(TreatmentFailure) as caught:
        job = _bound_job(tmp_path / "items" / "SKU-1" / "SKU-1.json")
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "repair_required"


def test_timeout_wait_receipt_completes_atomically_with_timer(
    tmp_path, monkeypatch,
):
    worker, _ = _worker(tmp_path)
    _activate_consumer(worker)
    worker.owner = "test-owner"
    worker.queue_name = "ebay_sync"
    job = _job()
    job.update(job_id="11111111-1111-4111-8111-111111111111",
               attempt_count=1, max_attempts=3)
    monkeypatch.setattr(
        worker, "handle",
        lambda claimed: worker._handle_governed_targeted(
            claimed["payload_json"], "SKU-1", claimed,
        ),
    )
    completed = []
    monkeypatch.setattr("tgw.queue.state_machine.mark_running", lambda *_: True)
    monkeypatch.setattr(
        "tgw.queue.state_machine.complete_treatment_and_schedule_timer",
        lambda job_id, owner, receipt: completed.append((job_id, owner, receipt))
        or "timer-1",
    )
    monkeypatch.setattr(
        "tgw.queue.state_machine.mark_succeeded",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("wait receipt marked succeeded without timer")
        ),
    )
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get",
                                    side_effect=requests.Timeout("offline")):
        worker._process(job)

    assert len(completed) == 1
    job_id, owner, receipt = completed[0]
    assert (job_id, owner) == (
        "11111111-1111-4111-8111-111111111111", "test-owner",
    )
    assert receipt["receipt_schema_id"] == "treatment-wait-receipt/v1"
    assert receipt["outcome"] == "transient_backoff"
    assert receipt["timer"]["payload"]["sync_retry"] == 1


def test_lost_lease_during_timeout_timer_completion_is_not_reported_success(
    tmp_path, monkeypatch,
):
    worker, _ = _worker(tmp_path)
    _activate_consumer(worker)
    worker.owner = "test-owner"
    worker.queue_name = "ebay_sync"
    job = _job()
    job.update(job_id="22222222-2222-4222-8222-222222222222",
               attempt_count=1, max_attempts=3)
    monkeypatch.setattr(
        worker, "handle",
        lambda claimed: worker._handle_governed_targeted(
            claimed["payload_json"], "SKU-1", claimed,
        ),
    )
    monkeypatch.setattr("tgw.queue.state_machine.mark_running", lambda *_: True)
    monkeypatch.setattr(
        "tgw.queue.state_machine.complete_treatment_and_schedule_timer",
        lambda *_: None,
    )
    monkeypatch.setattr(
        "tgw.queue.state_machine.mark_succeeded",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("lost lease reported success")
        ),
    )
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get",
                                    side_effect=requests.Timeout("offline")), \
         pytest.raises(RuntimeError, match="lost lease scheduling treatment timer"):
        worker._process(job)


def test_activated_consumer_preserves_conflict_and_reconciliation_receipts(
    tmp_path,
):
    worker, _ = _worker(tmp_path)
    _activate_consumer(worker)
    job = _job()
    with patch("tgw.item_mutation.item_generation", return_value="new-generation"), \
         patch("tgw.workers.ebay_sync.ebay_get") as provider_read, \
         pytest.raises(TreatmentFailure) as conflict:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert conflict.value.result["outcome"] == "conflict"
    provider_read.assert_not_called()

    from tgw.provider_effects import ProviderEffectConflict
    with patch("tgw.item_mutation.item_generation", return_value="generation-1"), \
         patch("tgw.provider_effects.lookup_succeeded_provider_effect",
               side_effect=ProviderEffectConflict("contradiction")), \
         patch("tgw.workers.ebay_sync.ebay_get") as provider_read, \
         pytest.raises(TreatmentFailure) as reconciliation:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert reconciliation.value.result["outcome"] == "reconciliation_required"
    assert reconciliation.value.result["evidence"]["reason_code"] == "SOURCE_EFFECT_INVALID"
    provider_read.assert_not_called()


def test_default_selector_preserves_legacy_targeted_behavior(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    worker.config["workflow_migration"] = {}
    monkeypatch.setattr(worker, "_sync_one", lambda *_: 0)
    with patch("tgw.ebay.sync._find_offer", return_value=None):
        assert worker.handle({"payload_json": {"sku": "SKU-1"}}) is None


def test_workflow_selector_fails_closed_before_provider_read(tmp_path):
    worker, _ = _worker(tmp_path)
    with patch("tgw.workers.ebay_sync.ebay_get") as provider_read, pytest.raises(HardFailure) as caught:
        worker.handle(_job())
    assert "consumer is not admitted" in str(caught.value)
    provider_read.assert_not_called()


def test_workflow_selector_remains_fail_closed_for_governed_payload(tmp_path, monkeypatch):
    worker, _ = _worker(tmp_path)
    _activate_consumer(worker)
    job = _job()
    governed = []
    monkeypatch.setattr(
        worker, "_handle_governed_targeted",
        lambda *args: governed.append(args),
    )
    monkeypatch.setattr(
        worker, "_sync_one",
        lambda *_: (_ for _ in ()).throw(AssertionError("legacy path called")),
    )

    with patch("tgw.workers.ebay_sync.ebay_get") as provider_read, \
         pytest.raises(HardFailure, match="consumer is not admitted"):
        worker.handle(job)
    assert governed == []
    provider_read.assert_not_called()


@pytest.mark.parametrize("status", [429, 500, 503])
def test_governed_provider_transient_http_becomes_bounded_wait(tmp_path, status):
    worker, _ = _worker(tmp_path)
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError(f"HTTP {status}", response=response)
    with patch("tgw.workers.ebay_sync.ebay_get", side_effect=error):
        receipt = worker._find_offer_governed(_job()["payload_json"], "SKU-1")
    assert receipt["outcome"] == "transient_backoff"
    assert receipt["timer"]["payload"]["sync_retry"] == 1


@pytest.mark.parametrize("status", [401, 403])
def test_governed_provider_auth_failure_is_definitive(tmp_path, status):
    worker, _ = _worker(tmp_path)
    response = requests.Response()
    response.status_code = status
    error = requests.HTTPError(f"HTTP {status}", response=response)
    with patch("tgw.workers.ebay_sync.ebay_get", side_effect=error), \
         pytest.raises(TreatmentFailure) as caught:
        worker._find_offer_governed(_job()["payload_json"], "SKU-1")
    assert caught.value.result["outcome"] == "failed"
    assert caught.value.result["evidence"]["reason_code"] == (
        "PROVIDER_AUTHORIZATION_FAILED"
    )


def test_governed_provider_404_waits_then_requires_reconciliation(tmp_path):
    worker, _ = _worker(tmp_path)
    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError("HTTP 404", response=response)
    payload = _job()["payload_json"]
    with patch("tgw.workers.ebay_sync.ebay_get", side_effect=error):
        assert worker._find_offer_governed(payload, "SKU-1")["outcome"] == (
            "transient_backoff"
        )
        payload["sync_retry"] = 3
        with pytest.raises(TreatmentFailure) as caught:
            worker._find_offer_governed(payload, "SKU-1")
    assert caught.value.result["outcome"] == "reconciliation_required"
    assert caught.value.result["evidence"]["reason_code"] == "PROVIDER_OFFER_ABSENT"


def test_governed_empty_offer_result_waits_then_requires_reconciliation(tmp_path):
    worker, _ = _worker(tmp_path)
    payload = _job()["payload_json"]
    with patch("tgw.workers.ebay_sync.ebay_get", return_value={"offers": []}):
        assert worker._find_offer_governed(payload, "SKU-1")["outcome"] == (
            "transient_backoff"
        )
        payload["sync_retry"] = 3
        with pytest.raises(TreatmentFailure) as caught:
            worker._find_offer_governed(payload, "SKU-1")
    assert caught.value.result["outcome"] == "reconciliation_required"
    assert caught.value.result["evidence"]["reason_code"] == "PROVIDER_OFFER_ABSENT"


@pytest.mark.parametrize(
    "response,reason",
    [({}, "PROVIDER_RESPONSE_MALFORMED"),
     ({"offers": "not-a-list"}, "PROVIDER_RESPONSE_MALFORMED"),
     ({"offers": [{"offerId": "A"}, {"offerId": "B"}]},
      "PROVIDER_RESPONSE_AMBIGUOUS"),
     ({"offers": ["not-an-object"]}, "PROVIDER_RESPONSE_AMBIGUOUS")],
)
def test_governed_provider_malformed_or_ambiguous_is_reconciliation(
    tmp_path, response, reason,
):
    worker, _ = _worker(tmp_path)
    with patch("tgw.workers.ebay_sync.ebay_get", return_value=response), \
         pytest.raises(TreatmentFailure) as caught:
        worker._find_offer_governed(_job()["payload_json"], "SKU-1")
    assert caught.value.result["outcome"] == "reconciliation_required"
    assert caught.value.result["evidence"]["reason_code"] == reason


@pytest.mark.parametrize("pending,expected_enqueues", [(False, 1), (True, 0)])
def test_startup_checks_exact_periodic_pending_identity_not_total_depth(
    tmp_path, monkeypatch, pending, expected_enqueues,
):
    worker, _ = _worker(tmp_path)
    worker.owner = "test-owner"
    worker._stop = True
    checked = []
    enqueued = []
    monkeypatch.setattr(worker, "install_signal_handlers", lambda: None)
    monkeypatch.setattr(
        "tgw.queue.state_machine.has_pending_job_with_payload",
        lambda queue, dedupe, payloads: checked.append((queue, dedupe, payloads))
        or pending,
    )
    monkeypatch.setattr(
        "tgw.queue.state_machine.enqueue_job",
        lambda **kwargs: enqueued.append(kwargs),
    )
    monkeypatch.setattr(
        "tgw.queue.state_machine.queue_depths",
        lambda: (_ for _ in ()).throw(
            AssertionError("aggregate queue depth consulted")
        ),
    )
    worker.run()
    assert checked == [("ebay_sync", "ebay_sync:pending",
                        [{"reason": "startup"}, {"reason": "scheduled"}])]
    assert len(enqueued) == expected_enqueues


def test_pending_periodic_lookup_covers_every_active_chain_state():
    from tgw.queue import state_machine

    cursor = MagicMock()
    cursor.fetchone.return_value = (True,)
    connection = MagicMock()
    connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
    with patch("tgw.queue.state_machine._conn", return_value=connection):
        assert state_machine.has_pending_job_with_payload(
            "ebay_sync", "ebay_sync:pending",
            [{"reason": "startup"}, {"reason": "scheduled"}],
        ) is True
    sql, params = cursor.execute.call_args.args
    assert "'queued', 'retry_wait', 'leased', 'running'" in sql
    assert params[:2] == ("ebay_sync", "ebay_sync:pending")
    assert json.loads(params[2]) == [
        {"reason": "startup"}, {"reason": "scheduled"},
    ]


@pytest.mark.parametrize("mode", ["off", "workflow"])
def test_consumer_selector_does_not_reinterpret_legacy_or_periodic(
    tmp_path, monkeypatch, mode,
):
    worker, _ = _worker(tmp_path)
    worker.config["workflow_migration"]["ebay_sync_targeted_consumer"] = mode
    governed = []
    monkeypatch.setattr(
        worker, "_handle_governed_targeted",
        lambda *args: governed.append(args),
    )
    monkeypatch.setattr(worker, "_sync_one", lambda *_: 0)
    with patch("tgw.ebay.sync._find_offer", return_value={"offerId": "OFF-1"}):
        assert worker.handle({"payload_json": {"sku": "SKU-1"}}) is None
    monkeypatch.setattr(
        "tgw.workers.ebay_sync.fetch_all_offers",
        lambda *_: (_ for _ in ()).throw(RuntimeError("periodic reached")),
    )
    with pytest.raises(RuntimeError, match="periodic reached"):
        worker.handle({"payload_json": {"reason": "scheduled"}})
    assert governed == []


def test_internal_handler_rejects_treatment_and_entity_before_provider_read(tmp_path):
    worker, _ = _worker(tmp_path)
    job = _job()
    job["payload_json"]["treatment_id"] = "ebay-publish"
    with patch("tgw.workers.ebay_sync.ebay_get") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()


@pytest.mark.parametrize("value", [-1, True, "1", 4, 1.0])
def test_invalid_retry_count_is_rejected_before_ledger_or_provider(tmp_path, value):
    worker, _ = _worker(tmp_path)
    job = _job()
    job["payload_json"]["sync_retry"] = value
    with patch("tgw.provider_effects.lookup_succeeded_provider_effect") as ledger, \
         patch("tgw.workers.ebay_sync.ebay_get") as provider_read, \
         pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    ledger.assert_not_called()
    provider_read.assert_not_called()
    job = _job()
    job["entity_id"] = "OTHER"
    with patch("tgw.workers.ebay_sync.ebay_get") as provider_read, pytest.raises(HardFailure):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider_read.assert_not_called()


def test_success_receipt_passes_queue_worker_binding_validation(tmp_path, monkeypatch):
    worker, path = _worker(tmp_path)
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    job = _bound_job(path)
    with _source_effect_ok(), _provider_offer({"offerId": "OFF-1"}):
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
    with _source_effect_ok(), _provider_offer(offer):
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
    with _source_effect_ok(), _provider_offer(offer):
        first = _bound_job(path)
        worker._handle_governed_targeted(first["payload_json"], "SKU-1", first)

    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: (_ for _ in ()).throw(OSError("catalog unavailable")),
    )
    second = _bound_job(path)
    second["payload_json"]["graph_id"] = "graph-noop-repair"
    with _source_effect_ok(), _provider_offer(offer), \
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
    with _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get",
                                    side_effect=lambda *args, **kwargs: {
                                        "offers": [observe_then_advance()]
                                    }), \
         pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "conflict"
    assert projection == []
    assert json.loads(path.read_text())["operator_note"] == "newer"


def test_checkpoint_recovers_after_projection_before_queue_receipt(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    worker.owner = "owner-1"
    monkeypatch.setattr("tgw.sqlite_catalog.upsert_catalog_row",
                        lambda *args: {"ok": True})
    job = _bound_job(path)
    job.update(
        job_id="11111111-1111-4111-8111-111111111111",
        lease_token="11111111-1111-1111-1111-111111111111",
    )
    saved = []

    def checkpoint(_job_id, _owner, _token, value):
        saved.append(value)
        return value

    monkeypatch.setattr(
        "tgw.workers.ebay_sync.state_machine.checkpoint_running_job", checkpoint,
    )
    original = worker._project_governed_offer

    def project_then_die(**kwargs):
        original(**kwargs)
        raise RuntimeError("process died before queue receipt")

    monkeypatch.setattr(worker, "_project_governed_offer", project_then_die)
    with _source_effect_ok(), _provider_offer({"offerId": "OFF-1"}), \
         pytest.raises(RuntimeError, match="process died"):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)

    retry = _job()
    retry["payload_json"]["object_generation"] = job["payload_json"][
        "object_generation"
    ]
    retry["payload_json"]["observation_checkpoint"] = saved[0]
    monkeypatch.setattr(worker, "_project_governed_offer", original)
    with _source_effect_ok(), patch(
        "tgw.workers.ebay_sync.ebay_get",
        side_effect=AssertionError("provider reread during recovery"),
    ):
        receipt = worker._handle_governed_targeted(
            retry["payload_json"], "SKU-1", retry,
        )
    assert receipt["outcome"] == "satisfied"
    assert receipt["evidence"]["operation_id"] == saved[0]["operation_id"]


def test_forged_checkpoint_is_rejected_without_provider_or_item_effect(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _bound_job(path)
    checkpoint = worker._observation_checkpoint(
        job["payload_json"], "SKU-1", {"offerId": "OFF-1"},
    )
    checkpoint["offer_fingerprint"] = "0" * 64
    job["payload_json"]["observation_checkpoint"] = checkpoint
    before = path.read_bytes()
    with _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get") as provider, \
         pytest.raises(HardFailure, match="checkpoint identity mismatch"):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider.assert_not_called()
    assert path.read_bytes() == before


def test_checkpoint_with_genuinely_newer_generation_conflicts_without_reread(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _bound_job(path)
    checkpoint = worker._observation_checkpoint(
        job["payload_json"], "SKU-1", {"offerId": "OFF-1"},
    )
    job["payload_json"]["observation_checkpoint"] = checkpoint
    document = json.loads(path.read_text())
    document["operator_note"] = "newer"
    path.write_text(json.dumps(document))
    projections = []
    monkeypatch.setattr(
        "tgw.sqlite_catalog.upsert_catalog_row",
        lambda *args: projections.append(args) or {"ok": True},
    )
    with _source_effect_ok(), patch(
        "tgw.workers.ebay_sync.ebay_get",
        side_effect=AssertionError("provider reread during recovery"),
    ), pytest.raises(TreatmentFailure) as caught:
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    assert caught.value.result["outcome"] == "conflict"
    assert projections == []


@pytest.mark.parametrize(
    "field,value",
    [("job_id", None), ("job_id", "not-a-uuid"),
     ("lease_token", None), ("lease_token", "not-a-uuid")],
)
def test_governed_handler_requires_exact_lease_identity_before_read_or_mutation(
    tmp_path, field, value,
):
    worker, path = _worker(tmp_path)
    job = _bound_job(path)
    job[field] = value
    before = path.read_bytes()
    with patch("tgw.provider_effects.lookup_succeeded_provider_effect") as ledger, \
         patch("tgw.workers.ebay_sync.ebay_get") as provider, \
         pytest.raises(HardFailure, match="running lease identity"):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    ledger.assert_not_called()
    provider.assert_not_called()
    assert path.read_bytes() == before


def test_recovery_lost_lease_stops_without_provider_read_or_mutation(
    tmp_path, monkeypatch,
):
    worker, path = _worker(tmp_path)
    job = _bound_job(path)
    job["payload_json"]["observation_checkpoint"] = worker._observation_checkpoint(
        job["payload_json"], "SKU-1", {"offerId": "OFF-1"},
    )
    before = path.read_bytes()
    monkeypatch.setattr(
        "tgw.workers.ebay_sync.state_machine.checkpoint_running_job",
        lambda *_: (_ for _ in ()).throw(RuntimeError("lost running lease")),
    )
    with _source_effect_ok(), patch("tgw.workers.ebay_sync.ebay_get") as provider, \
         pytest.raises(RuntimeError, match="lost running lease"):
        worker._handle_governed_targeted(job["payload_json"], "SKU-1", job)
    provider.assert_not_called()
    assert path.read_bytes() == before


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
