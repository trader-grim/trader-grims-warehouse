"""Real PostgreSQL race proofs for the local coding Stop channel.

Set ``TGW_TEST_STATE_MACHINE_DSN`` to a disposable database with
``src/tgw/queue/live_schema.sql`` applied.  Tests use unique UUID rows and
delete only those rows; they never truncate shared test state.
"""

from __future__ import annotations

import os
import threading
import uuid

import psycopg2
import pytest

from tgw.queue import state_machine

DSN = os.environ.get(
    "TGW_TEST_STATE_MACHINE_DSN", "dbname=state_machine_test user=tgw"
)


def _available() -> bool:
    try:
        with psycopg2.connect(DSN) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1 FROM queue_jobs LIMIT 0")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _available(), reason="disposable PostgreSQL queue schema unavailable")


@pytest.fixture
def job_rows():
    ids: list[str] = []
    state_machine.init(DSN)
    yield ids
    if ids:
        with psycopg2.connect(DSN) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM queue_jobs WHERE job_id = ANY(%s::uuid[])", (ids,))


def _insert(ids: list[str], *, state: str = "running", queue: str = "codex-implement"):
    job_id, token = str(uuid.uuid4()), str(uuid.uuid4())
    ids.append(job_id)
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO queue_jobs
                   (job_id, entity_type, entity_id, operation, handler_family,
                    queue_name, state, lease_owner, lease_token, lease_expires_at)
                   VALUES (%s, 'todo', %s, 'coding', 'coding', %s, %s,
                           'worker:race', %s, NOW() + interval '5 minutes')""",
                (job_id, job_id, queue, state, token),
            )
    return job_id, token


def _row(job_id: str):
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT state::text, payload_json FROM queue_jobs WHERE job_id=%s", (job_id,))
            return cursor.fetchone()


def _failure_trigger(job_id: str, *, deferred: bool):
    suffix = uuid.uuid4().hex
    function = f"tgw_stop_fail_{suffix}"
    trigger = f"tgw_stop_trigger_{suffix}"
    timing = "CONSTRAINT TRIGGER" if deferred else "TRIGGER"
    deferral = "DEFERRABLE INITIALLY DEFERRED" if deferred else ""
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'injected stop closure failure'; END $$;
                    CREATE {timing} {trigger} AFTER UPDATE ON queue_jobs
                    {deferral}
                    FOR EACH ROW WHEN (NEW.job_id = '{job_id}'::uuid
                                       AND NEW.state::text = 'succeeded')
                    EXECUTE FUNCTION {function}()"""
            )
    return trigger, function


def _drop_failure_trigger(trigger: str, function: str):
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER IF EXISTS {trigger} ON queue_jobs")
            cursor.execute(f"DROP FUNCTION IF EXISTS {function}()")


def _reconciliation_failure_trigger(job_id: str):
    suffix = uuid.uuid4().hex
    function = f"tgw_stop_fence_fail_{suffix}"
    trigger = f"tgw_stop_fence_trigger_{suffix}"
    with psycopg2.connect(DSN) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""CREATE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN
                      PERFORM pg_sleep(1);
                      RAISE EXCEPTION 'injected reconciliation fence failure';
                    END $$;
                    CREATE TRIGGER {trigger} AFTER UPDATE ON queue_jobs
                    FOR EACH ROW WHEN (
                      NEW.job_id = '{job_id}'::uuid
                      AND NEW.error_code = 'PUBLICATION_RECONCILIATION_REQUIRED')
                    EXECUTE FUNCTION {function}()"""
            )
    return trigger, function


def test_real_postgresql_cancellation_winner_isolated_from_other_job(job_rows):
    cancelled_id, token = _insert(job_rows)
    other_id, _ = _insert(job_rows)
    stopped = state_machine.cancel_job(
        cancelled_id, result={"stop_control": {
            "schema": "tgw-coding-stop/v1", "kind": "runner_cancel_requested"
        }},
    )
    assert stopped["state"] == "cancelled"
    identity = stopped["payload_json"]["result"]["stop_control"]["request_identity"]
    assert identity == {"job_id": cancelled_id, "queue_name": "codex-implement",
                        "lease_owner": "worker:race", "lease_token": token}
    assert not state_machine.close_local_success(
        cancelled_id, "worker:race", token, {"outcome": "satisfied"},
        lambda register: register(lambda: None),
    )
    assert _row(other_id)[0] == "running"


def test_real_postgresql_success_winner_blocks_stop_and_preserves_exact_job(job_rows):
    job_id, token = _insert(job_rows)
    other_id, _ = _insert(job_rows)
    published = threading.Event()
    release = threading.Event()
    results = {}

    def complete():
        results["success"] = state_machine.close_local_success(
            job_id, "worker:race", token, {"outcome": "satisfied"},
            lambda register: (register(lambda: None), published.set(), release.wait(5)),
        )

    success_thread = threading.Thread(target=complete)
    success_thread.start()
    assert published.wait(5)
    stop_thread = threading.Thread(
        target=lambda: results.setdefault("stop", state_machine.cancel_job(job_id))
    )
    stop_thread.start()
    release.set()
    success_thread.join(5)
    stop_thread.join(5)
    assert results["success"] is True
    assert results["stop"]["state"] == "succeeded"
    assert _row(job_id)[0] == "succeeded"
    assert _row(other_id)[0] == "running"


@pytest.mark.parametrize("deferred", [False, True], ids=["sql-update", "transaction-commit"])
def test_real_postgresql_sql_and_commit_failures_rollback_publication(job_rows, deferred):
    job_id, token = _insert(job_rows)
    evidence = {"exists": False}
    trigger, function = _failure_trigger(job_id, deferred=deferred)
    try:
        with pytest.raises(psycopg2.Error, match="injected stop closure failure"):
            state_machine.close_local_success(
                job_id, "worker:race", token, {"outcome": "satisfied"},
                lambda register: (register(lambda: evidence.update(exists=False)),
                                  evidence.update(exists=True)),
            )
        assert evidence["exists"] is False
        assert _row(job_id)[0] == "running"
    finally:
        _drop_failure_trigger(trigger, function)


def test_real_postgresql_commit_and_cleanup_failure_fences_before_stop(job_rows):
    job_id, token = _insert(job_rows)
    other_id, _ = _insert(job_rows)
    trigger, function = _failure_trigger(job_id, deferred=True)
    cleanup_started = threading.Event()
    cleanup_calls = []
    results = {}

    def cleanup():
        cleanup_calls.append(True)
        cleanup_started.set()
        raise OSError("injected publication cleanup failure")

    def complete():
        try:
            state_machine.close_local_success(
                job_id, "worker:race", token, {"outcome": "satisfied"},
                lambda register: register(cleanup),
            )
        except Exception as exc:
            results["completion_error"] = exc

    completion = threading.Thread(target=complete)
    completion.start()
    assert cleanup_started.wait(5)
    stop = threading.Thread(
        target=lambda: results.setdefault("stop", state_machine.cancel_job(job_id))
    )
    stop.start()
    completion.join(5)
    stop.join(5)
    try:
        assert not completion.is_alive() and not stop.is_alive()
        assert len(cleanup_calls) == 1
        assert "requires reconciliation" in str(results["completion_error"])
        assert results["stop"]["state"] == "failed"
        state, payload = _row(job_id)
        assert state == "failed"
        assert "result" not in (payload or {})
        evidence = payload["publication_reconciliation"]
        assert evidence["schema"] == "tgw-publication-reconciliation/v1"
        assert evidence["job_id"] == job_id
        assert "injected stop closure failure" in evidence["success_error"]
        assert "injected publication cleanup failure" in evidence["cleanup_error"]
        assert _row(other_id)[0] == "running"
    finally:
        _drop_failure_trigger(trigger, function)


def test_real_postgresql_initial_cleanup_and_fence_failure_remains_non_cancellable(
    job_rows,
):
    job_id, token = _insert(job_rows)
    other_id, _ = _insert(job_rows)
    success_trigger, success_function = _failure_trigger(job_id, deferred=True)
    fence_trigger, fence_function = _reconciliation_failure_trigger(job_id)
    cleanup_started = threading.Event()
    cleanup_calls = []
    results = {}

    def cleanup():
        cleanup_calls.append(True)
        cleanup_started.set()
        raise OSError("injected publication cleanup failure")

    def complete():
        try:
            state_machine.close_local_success(
                job_id, "worker:race", token, {"outcome": "satisfied"},
                lambda register: register(cleanup),
            )
        except Exception as exc:
            results["completion_error"] = exc

    completion = threading.Thread(target=complete)
    completion.start()
    assert cleanup_started.wait(5)
    stop = threading.Thread(
        target=lambda: results.setdefault("stop", state_machine.cancel_job(job_id))
    )
    stop.start()
    completion.join(5)
    stop.join(5)
    try:
        assert not completion.is_alive() and not stop.is_alive()
        assert len(cleanup_calls) == 1
        assert "requires reconciliation" in str(results["completion_error"])
        assert results["stop"]["state"] == "running"
        assert results["stop"]["error_code"] == "PUBLICATION_CLOSING"
        state, payload = _row(job_id)
        assert state == "running"
        assert "result" not in (payload or {})
        evidence = payload["publication_reconciliation"]
        assert evidence == {
            "schema": "tgw-publication-reconciliation/v1",
            "job_id": job_id,
            "state": "closing",
        }
        assert _row(other_id)[0] == "running"
    finally:
        _drop_failure_trigger(fence_trigger, fence_function)
        _drop_failure_trigger(success_trigger, success_function)


def test_real_postgresql_ack_is_exact_identity_bound_and_single_use(job_rows):
    job_id, token = _insert(job_rows)
    stopped = state_machine.cancel_job(job_id, result={"stop_control": {
        "schema": "tgw-coding-stop/v1", "kind": "runner_cancel_requested"
    }})
    identity = stopped["payload_json"]["result"]["stop_control"]["request_identity"]
    acknowledgement = {
        "schema": "tgw-coding-stop-ack/v1", "job_id": job_id,
        "ack_id": str(uuid.uuid4()), "worker": identity["lease_owner"],
        "observed_at": "2026-08-26T07:00:00+00:00", "reason": "no_runner",
        "reaped": True, "runner": {"schema": "tgw-coding-runner/v2",
            "kind": "no_runner", **identity},
    }
    assert state_machine.acknowledge_cancellation(job_id, acknowledgement) is not None
    assert state_machine.acknowledge_cancellation(job_id, acknowledgement) is None
    replay = {**acknowledgement, "ack_id": str(uuid.uuid4()),
              "runner": {**acknowledgement["runner"], "lease_token": str(uuid.uuid4())}}
    assert state_machine.acknowledge_cancellation(job_id, replay) is None
