from unittest.mock import MagicMock, patch

import pytest

from tgw.queue import state_machine

TOKEN = "66666666-6666-4666-8666-666666666666"


def test_live_schema_terminal_functions_reject_expired_leases():
    schema = (
        __import__("pathlib").Path(__file__).parents[1]
        / "src/tgw/queue/live_schema.sql"
    ).read_text()
    for function in ("fail_job", "succeed_job"):
        body = schema.split(f"CREATE FUNCTION public.{function}", 1)[1].split(
            "$$;", 1
        )[0]
        assert "lease_expires_at IS NOT NULL" in body
        assert "lease_expires_at > clock_timestamp()" in body


def _database(*, row=None, rowcount=0):
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = row
    cursor.rowcount = rowcount
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    return connection, cursor


def test_same_owner_old_token_cannot_complete_reclaimed_attempt():
    connection, cursor = _database(row=None)
    with patch.object(state_machine, "_conn", return_value=connection), \
         pytest.raises(RuntimeError, match="lost running lease"):
        state_machine.complete_treatment_and_enqueue_evaluation(
            "job-1", "same-owner", TOKEN, {"outcome": "satisfied"},
        )
    sql, params = cursor.execute.call_args.args
    assert "lease_token = %s::uuid" in sql
    assert "lease_expires_at > NOW()" in sql
    assert params[-1] == TOKEN
    assert cursor.execute.call_count == 1


@pytest.mark.parametrize(
    "transition,args",
    [
        (state_machine.mark_running, ("job-1", "owner", TOKEN)),
        (state_machine.mark_succeeded, ("job-1", "owner", TOKEN)),
        (state_machine.mark_dead_letter,
         ("job-1", "owner", TOKEN, "hard failure")),
        (state_machine.requeue_with_backoff,
         ("job-1", "owner", TOKEN, 60, "transient")),
    ],
)
def test_wrong_token_or_expired_lease_cannot_transition(transition, args):
    connection, cursor = _database(rowcount=0)
    with patch.object(state_machine, "_conn", return_value=connection), \
         pytest.raises(RuntimeError, match="lost .*lease|lost leased job"):
        transition(*args)
    sql = cursor.execute.call_args.args[0]
    assert "lease_token = %s::uuid" in sql
    assert "lease_expires_at > NOW()" in sql


def test_mark_failed_rejects_expired_or_wrong_token_before_state_choice():
    connection, cursor = _database(row=None)
    with patch.object(state_machine, "_conn", return_value=connection), \
         pytest.raises(RuntimeError, match="lost running lease"):
        state_machine.mark_failed("job-1", "owner", TOKEN, "failure")
    sql, params = cursor.execute.call_args.args
    assert "FOR UPDATE" in sql
    assert "lease_token = %s::uuid" in sql
    assert "lease_expires_at > NOW()" in sql
    assert params == ("job-1", "owner", TOKEN)


def test_claim_envelope_is_lease_fenced_and_cannot_overwrite_existing_fields():
    connection, cursor = _database(row=None)
    envelope = {"location": {"worktree": "/exact"}}
    with patch.object(state_machine, "_conn", return_value=connection):
        assert state_machine.record_claim_envelope(
            "job-1", "owner", TOKEN, envelope,
        ) is None

    sql, params = cursor.execute.call_args.args
    assert "lease_expires_at IS NOT NULL" in sql
    assert "lease_expires_at > NOW()" in sql
    assert "jsonb_each" in sql
    assert "IS DISTINCT FROM" in sql
    assert params == (
        __import__("json").dumps(envelope), "job-1", "owner", TOKEN,
        __import__("json").dumps(envelope),
    )


def test_timer_insert_failure_rolls_back_token_fenced_completion():
    connection, cursor = _database(row=("item", "SKU-1"))
    cursor.execute.side_effect = [None, RuntimeError("timer insert failed")]
    receipt = {
        "timer": {
            "queue_name": "ebay_sync", "not_before": 1100.0,
            "payload": {"sku": "SKU-1"}, "dedupe_key": "timer-1",
            "max_attempts": 3,
        },
    }
    with patch.object(state_machine, "_conn", return_value=connection), \
         patch.object(state_machine.time, "time", return_value=1000.0), \
         pytest.raises(RuntimeError, match="timer insert failed"):
        state_machine.complete_treatment_and_schedule_timer(
            "job-1", "owner", TOKEN, receipt,
        )
    first_sql = cursor.execute.call_args_list[0].args[0]
    assert "lease_token = %s::uuid" in first_sql
    assert "lease_expires_at > NOW()" in first_sql
    assert connection.__exit__.call_args.args[0] is RuntimeError


def test_cancel_does_not_overwrite_concurrent_success_receipt():
    succeeded = {"job_id": "job-1", "state": "succeeded", "payload_json": {}}
    connection, cursor = _database(row=succeeded)

    with patch.object(state_machine, "_conn", return_value=connection):
        result = state_machine.cancel_job(
            "job-1", "operator stop", {"outcome": "stopped"},
        )

    assert result == succeeded
    cursor.execute.assert_any_call(
        "SELECT * FROM queue_jobs WHERE job_id = %s FOR UPDATE", ("job-1",)
    )


def test_cancel_receipt_update_is_fenced_to_cancelled_state():
    cancelled = {"job_id": "job-1", "state": "cancelled", "payload_json": {}}
    persisted = {
        **cancelled,
        "payload_json": {"result": {"outcome": "stopped"}},
    }
    connection, cursor = _database()
    active = {**cancelled, "state": "running"}
    cursor.fetchone.side_effect = [active, persisted]

    with patch.object(state_machine, "_conn", return_value=connection):
        result = state_machine.cancel_job(
            "job-1", "operator stop", {"outcome": "stopped"},
        )

    assert result == persisted
    update_sql = cursor.execute.call_args_list[2].args[0]
    assert "state IN ('queued', 'leased', 'running', 'retry_wait')" in update_sql


def test_worker_cancellation_ack_is_single_use_and_worker_owned():
    job_id = "11111111-1111-4111-8111-111111111111"
    acknowledged = {"job_id": job_id, "state": "cancelled"}
    connection, cursor = _database(row=acknowledged)
    evidence = {
        "schema": "tgw-coding-stop-ack/v1", "job_id": job_id,
        "ack_id": "22222222-2222-4222-8222-222222222222", "worker": "worker-1",
        "observed_at": "2026-08-26T07:00:00+00:00", "reason": "stopped",
        "reaped": True, "runner": {
            "schema": "tgw-coding-runner/v2", "job_id": job_id,
            "queue_name": "codex-implement", "lease_owner": "worker-1",
            "lease_token": TOKEN,
        },
    }
    with patch.object(state_machine, "_conn", return_value=connection):
        assert state_machine.acknowledge_cancellation(job_id, evidence) == acknowledged
    sql = cursor.execute.call_args.args[0]
    assert "state = 'cancelled'" in sql
    assert "runner_cancel_requested" in sql
    assert "request_identity" in sql
    assert "jsonb_build_object" in sql
    assert "NOT (payload_json->'result'->'stop_control' ? 'acknowledgement')" in sql


def test_cancel_winner_suppresses_local_receipt_attempt_and_success():
    connection, cursor = _database(row=None)
    published = MagicMock()
    with patch.object(state_machine.psycopg2, "connect", return_value=connection):
        assert not state_machine.close_local_success(
            "job-1", "owner", TOKEN, {"outcome": "satisfied"}, published
        )
    published.assert_not_called()
    assert any("FOR UPDATE" in call.args[0] for call in cursor.execute.call_args_list)


def test_completion_winner_publishes_under_lock_before_success_update():
    connection, cursor = _database(row=(1,), rowcount=1)
    order = []
    cursor.execute.side_effect = lambda sql, *_a: order.append(
        "lock" if "FOR UPDATE" in sql else "success" if "UPDATE queue_jobs" in sql else "control"
    )
    with patch.object(state_machine.psycopg2, "connect", return_value=connection):
        assert state_machine.close_local_success(
            "job-1", "owner", TOKEN, {"outcome": "satisfied"},
            lambda register: (register(lambda: order.append("undo")), order.append("publish")),
        )
    publish_index = order.index("publish")
    assert order.index("lock") < order.index("success") < publish_index
    assert "lock" in order[order.index("success") + 1:publish_index]
    assert "success" in order[publish_index + 1:]


def test_publication_is_undone_before_database_rollback_releases_lock():
    connection, cursor = _database(row=(1,), rowcount=0)
    order = []
    connection.rollback.side_effect = lambda: order.append("rollback")
    with patch.object(state_machine.psycopg2, "connect", return_value=connection), \
         pytest.raises(RuntimeError, match="lost locked local success closure"):
        state_machine.close_local_success(
            "job-1", "owner", TOKEN, {"outcome": "satisfied"},
            lambda register: register(lambda: order.append("undo")),
        )
    assert order == ["undo", "rollback"]


def test_unprovable_publication_cleanup_is_terminally_fenced_under_lock():
    connection, cursor = _database(row=(1,), rowcount=0)
    with patch.object(
        state_machine.psycopg2, "connect", return_value=connection
    ), pytest.raises(RuntimeError, match="requires reconciliation"):
        state_machine.close_local_success(
            "job-1", "owner", TOKEN, {"outcome": "satisfied"},
            lambda register: register(
                lambda: (_ for _ in ()).throw(OSError("unlink failed"))
            ),
        )
    fence_sql = next(
        call.args[0] for call in cursor.execute.call_args_list
        if "PUBLICATION_RECONCILIATION_REQUIRED" in call.args[0]
    )
    assert "PUBLICATION_RECONCILIATION_REQUIRED" in fence_sql
    connection.rollback.assert_called_once_with()
    assert connection.commit.call_count == 2


def _rollback_fixture(connection, on_rollback):
    from contextlib import contextmanager

    @contextmanager
    def fixture():
        try:
            yield connection
        except Exception:
            on_rollback()
            connection.rollback()
            raise
    return fixture()
