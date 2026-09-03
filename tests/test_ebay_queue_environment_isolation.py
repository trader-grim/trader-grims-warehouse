"""Same-DSN production/sandbox queue isolation for eBay workflows."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker, _waiting_treatment_receipt_error

LEASE_TOKEN = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _restore_queue_environment():
    original = state_machine._EBAY_QUEUE_ENVIRONMENT
    state_machine._EBAY_QUEUE_ENVIRONMENT = "production"
    yield
    state_machine._EBAY_QUEUE_ENVIRONMENT = original


def _mock_connection(*, fetchone=None, fetchall=None):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = [] if fetchall is None else fetchall
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection, cursor


def _enqueue_params(environment: str, payload: dict[str, object]):
    state_machine._EBAY_QUEUE_ENVIRONMENT = environment
    connection, cursor = _mock_connection(fetchone=("job-id",))
    with patch.object(state_machine, "_conn", return_value=connection):
        state_machine.enqueue_job(
            queue_name="ebay_stage",
            payload=payload,
            entity_type="item",
            entity_id="SKU-1",
            dedupe_key="treatment:ebay_stage:item:SKU-1:gen-1",
        )
    return cursor.execute.call_args.args[1]


def test_same_sku_production_and_sandbox_use_distinct_active_identities():
    production = _enqueue_params(
        "production", {"sku": "SKU-1", "ebay_environment": "production"},
    )
    sandbox = _enqueue_params(
        "sandbox", {"sku": "SKU-1", "ebay_environment": "sandbox"},
    )

    assert production[0] == "ebay_stage"
    assert sandbox[0] == "ebay_stage@sandbox"
    assert production[8] == "treatment:ebay_stage:item:SKU-1:gen-1"
    assert sandbox[8] == (
        "ebay-environment:sandbox:treatment:ebay_stage:item:SKU-1:gen-1"
    )
    assert production[8] != sandbox[8]
    assert json.loads(sandbox[6])["ebay_environment"] == "sandbox"


def test_sandbox_process_stamps_unbound_legacy_ebay_manifest():
    sandbox = _enqueue_params("sandbox", {"sku": "SKU-1"})
    assert sandbox[0] == "ebay_stage@sandbox"
    assert json.loads(sandbox[6])["ebay_environment"] == "sandbox"


def test_explicit_environment_mismatch_fails_before_database_write():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "production"
    with patch.object(state_machine, "_conn") as connection, pytest.raises(
        state_machine.EbayQueueEnvironmentError, match="configured for 'production'",
    ):
        state_machine.enqueue_job(
            queue_name="ebay_sync",
            payload={"sku": "SKU-1", "ebay_environment": "sandbox"},
            entity_type="item",
            entity_id="SKU-1",
            dedupe_key="ebay_sync:SKU-1",
        )
    connection.assert_not_called()


def test_non_ebay_queue_is_unchanged_in_sandbox_process():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"
    connection, cursor = _mock_connection(fetchone=("job-id",))
    with patch.object(state_machine, "_conn", return_value=connection):
        state_machine.enqueue_job(
            queue_name="thumbnail_gen",
            payload={"sku": "SKU-1"},
            entity_type="item",
            entity_id="SKU-1",
            dedupe_key="thumbnail_gen:SKU-1",
        )
    params = cursor.execute.call_args.args[1]
    assert params[0] == "thumbnail_gen"
    assert params[8] == "thumbnail_gen:SKU-1"
    assert "ebay_environment" not in json.loads(params[6])


def test_worker_claim_routes_are_disjoint_on_the_same_dsn():
    production_connection, production_cursor = _mock_connection(fetchall=[])
    with patch.object(
        state_machine, "_conn", return_value=production_connection,
    ):
        assert state_machine.claim_queue_jobs(
            "ebay_sync", "prod-worker", ebay_environment="production",
        ) == []
    sandbox_connection, sandbox_cursor = _mock_connection(fetchall=[])
    with patch.object(
        state_machine, "_conn", return_value=sandbox_connection,
    ):
        assert state_machine.claim_queue_jobs(
            "ebay_sync", "sandbox-worker", ebay_environment="sandbox",
        ) == []

    assert production_cursor.execute.call_args.args[1][1] == "ebay_sync"
    assert sandbox_cursor.execute.call_args.args[1][1] == "ebay_sync@sandbox"


def test_worker_uses_exact_normalized_config_for_claim_route():
    with patch("tgw.queue.worker_base.state_machine.init") as initialize, patch(
        "tgw.queue.worker_base.tgw_logging.setup_logging",
    ):
        worker = QueueWorker(
            "ebay_upload",
            {"postgres_dsn": "same-dsn", "ebay_environment": "sandbox"},
        )

    assert worker.ebay_environment == "sandbox"
    assert worker.claim_queue_name == "ebay_upload@sandbox"
    initialize.assert_called_once_with("same-dsn", "sandbox")


def test_atomic_evaluation_continuation_preserves_sandbox_route_and_binding():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"
    origin_payload = {
        "goal_profile_id": "tgw.ebay_listable",
        "goal_profile_version": "1",
        "graph_id": "graph-1",
        "object_generation": "gen-1",
        "ebay_environment": "sandbox",
        "ebay_endpoint": "https://api.sandbox.ebay.com",
    }
    connection, cursor = _mock_connection()
    cursor.fetchone.side_effect = [
        ("SKU-1", origin_payload),
        ("event-job",),
    ]
    receipt = {"outcome": "satisfied", "graph_id": "graph-1"}
    with patch.object(state_machine, "_conn", return_value=connection):
        assert state_machine.complete_treatment_and_enqueue_evaluation(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        ) == "event-job"

    params = cursor.execute.call_args_list[1].args[1]
    event_payload = json.loads(params[3])
    assert params[0] == "workflow_evaluate@sandbox"
    assert params[4] == "ebay-environment:sandbox:workflow-evaluate:origin-job"
    assert event_payload["ebay_environment"] == "sandbox"
    assert event_payload["ebay_endpoint"] == "https://api.sandbox.ebay.com"


def test_atomic_timer_stays_on_sandbox_route_and_logical_receipt_contract():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"
    payload = {
        "sku": "SKU-1",
        "entity_id": "SKU-1",
        "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1",
        "graph_id": "graph-1",
        "object_generation": "gen-1",
        "condition_hash": "condition-1",
        "ebay_environment": "sandbox",
        "ebay_endpoint": "https://api.sandbox.ebay.com",
    }
    receipt = {
        "receipt_schema_id": "treatment-wait-receipt/v1",
        "treatment_id": "ebay-sync-targeted",
        "treatment_version": "1",
        "graph_id": "graph-1",
        "outcome": "transient_backoff",
        "timer": {
            "queue_name": "ebay_sync",
            "not_before": 1100.0,
            "payload": dict(payload),
            "dedupe_key": "workflow-timer:graph-1:ebay-sync:1",
        },
    }
    job = {
        "queue_name": "ebay_sync@sandbox",
        "entity_type": "item",
        "entity_id": "SKU-1",
        "payload_json": payload,
    }
    with patch("tgw.queue.worker_base.time.time", return_value=1000.0):
        assert _waiting_treatment_receipt_error(receipt, job) is None
        assert _waiting_treatment_receipt_error(
            receipt, {**job, "queue_name": "ebay_sync"},
        ) == "TIMER_QUEUE_MISMATCH"

    connection, cursor = _mock_connection()
    cursor.fetchone.side_effect = [("item", "SKU-1"), ("timer-job",)]
    with patch.object(state_machine, "_conn", return_value=connection), patch.object(
        state_machine.time, "time", return_value=1000.0,
    ):
        assert state_machine.complete_treatment_and_schedule_timer(
            "origin-job", "owner", LEASE_TOKEN, receipt,
        ) == "timer-job"

    params = cursor.execute.call_args_list[1].args[1]
    assert params[0] == "ebay_sync@sandbox"
    assert params[3] == "ebay_sync"
    assert params[7] == (
        "ebay-environment:sandbox:workflow-timer:graph-1:ebay-sync:1"
    )
    assert json.loads(params[5])["ebay_environment"] == "sandbox"


def test_sandbox_startup_and_upstream_queries_use_only_sandbox_rows():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"
    depth_connection, depth_cursor = _mock_connection(
        fetchall=[
            ("ebay_sync", 2),
            ("ebay_sync@sandbox", 3),
            ("thumbnail_gen", 4),
        ],
    )
    with patch.object(state_machine, "_conn", return_value=depth_connection):
        assert state_machine.queue_depths() == {
            "ebay_sync": 3,
            "thumbnail_gen": 4,
        }

    pending_connection, pending_cursor = _mock_connection(fetchone=(True,))
    with patch.object(state_machine, "_conn", return_value=pending_connection):
        assert state_machine.has_pending_job_with_payload(
            "ebay_sync",
            "ebay_sync:pending",
            [{"reason": "startup"}, {"reason": "scheduled"}],
        ) is True
    pending_params = pending_cursor.execute.call_args.args[1]
    assert pending_params[0] == "ebay_sync@sandbox"
    assert pending_params[1] == "ebay-environment:sandbox:ebay_sync:pending"
    assert all(
        item["ebay_environment"] == "sandbox"
        for item in json.loads(pending_params[2])
    )

    active_connection, active_cursor = _mock_connection(
        fetchall=[("ebay_upload@sandbox",)],
    )
    with patch.object(state_machine, "_conn", return_value=active_connection):
        assert state_machine.active_jobs_for_sku(
            "SKU-1", ["ebay_draft", "ebay_upload"],
        ) == ["ebay_upload"]
    assert active_cursor.execute.call_args.args[1] == (
        "SKU-1", ["ebay_draft@sandbox", "ebay_upload@sandbox"],
    )


def test_retry_updates_the_claimed_row_without_changing_its_route():
    connection, cursor = _mock_connection()
    cursor.rowcount = 1
    with patch.object(state_machine, "_conn", return_value=connection):
        state_machine.requeue_with_backoff(
            "sandbox-job", "sandbox-worker", LEASE_TOKEN, 120, "provider busy",
        )
    update_sql = cursor.execute.call_args.args[0]
    assert "SET queue_name" not in update_sql
    assert "WHERE job_id = %s" in update_sql


def test_sandbox_queue_administration_routes_and_filters_shared_rows():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"

    for operation in (state_machine.cancel_queued, state_machine.clear_dead_letter):
        connection, cursor = _mock_connection()
        cursor.rowcount = 2
        with patch.object(state_machine, "_conn", return_value=connection):
            assert operation("ebay_sync") == 2
        assert cursor.execute.call_args.args[1] == ("ebay_sync@sandbox",)

    jobs_connection, _ = _mock_connection(fetchall=[
        {
            "job_id": "prod", "queue_name": "ebay_sync",
            "payload_json": {}, "error_detail": "prod", "attempt_count": 1,
            "max_attempts": 3, "created_at": None, "finished_at": None,
        },
        {
            "job_id": "sandbox", "queue_name": "ebay_sync@sandbox",
            "payload_json": {"ebay_environment": "sandbox"},
            "error_detail": "sandbox", "attempt_count": 1,
            "max_attempts": 3, "created_at": None, "finished_at": None,
        },
        {
            "job_id": "generic", "queue_name": "thumbnail_gen",
            "payload_json": {}, "error_detail": "generic", "attempt_count": 1,
            "max_attempts": 3, "created_at": None, "finished_at": None,
        },
    ])
    with patch.object(state_machine, "_conn", return_value=jobs_connection):
        jobs = state_machine.dead_letter_jobs(limit=10)
    assert [(job["job_id"], job["queue_name"]) for job in jobs] == [
        ("sandbox", "ebay_sync"),
        ("generic", "thumbnail_gen"),
    ]


def test_dead_letter_requeue_cannot_cross_environment_and_preserves_route():
    state_machine._EBAY_QUEUE_ENVIRONMENT = "sandbox"
    other_connection, _ = _mock_connection(fetchone={
        "job_id": "prod-job", "queue_name": "ebay_stage", "payload_json": {},
        "entity_type": "item", "entity_id": "SKU-1", "operation": "run",
        "priority": 100, "max_attempts": 3,
    })
    with patch.object(state_machine, "_conn", return_value=other_connection), pytest.raises(
        ValueError, match="different eBay environment",
    ):
        state_machine.requeue_dead_letter_job("prod-job")

    sandbox_connection, sandbox_cursor = _mock_connection()
    sandbox_cursor.fetchone.side_effect = [
        {
            "job_id": "sandbox-job", "queue_name": "ebay_stage@sandbox",
            "payload_json": {"sku": "SKU-1", "ebay_environment": "sandbox"},
            "entity_type": "item", "entity_id": "SKU-1", "operation": "run",
            "priority": 100, "max_attempts": 3,
        },
        ("new-job",),
    ]
    with patch.object(state_machine, "_conn", return_value=sandbox_connection):
        assert state_machine.requeue_dead_letter_job("sandbox-job") == "new-job"
    insert_params = sandbox_cursor.execute.call_args_list[2].args[1]
    assert insert_params[0] == "ebay_stage@sandbox"
    assert insert_params[5] == "ebay_stage"
