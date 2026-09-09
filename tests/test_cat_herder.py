"""cat_herder daemon — the autonomous coding loop (Todo 2003).

The queue is real PostgreSQL; harness_cli.dispatch is stubbed (a real dispatch
runs a model session). Skips when no PostgreSQL with queue_jobs is reachable.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from tgw.development import cat_herder, harness_cli, harness_orchestrator, harness_queue

_CANDIDATE_DSNS = (
    os.environ.get("TGW_TEST_STATE_MACHINE_DSN"),
    "dbname=tgw_lib_dev_state_machine user=tgw_coding",
    "dbname=state_machine_test user=tgw",
)


def _reachable_dsn() -> str | None:
    for dsn in _CANDIDATE_DSNS:
        if not dsn:
            continue
        try:
            with psycopg2.connect(dsn) as con:
                with con.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.queue_jobs')")
                    if cur.fetchone()[0] is None:
                        continue
            return dsn
        except Exception:
            continue
    return None


DSN = _reachable_dsn()
pytestmark = pytest.mark.skipif(DSN is None, reason="no PostgreSQL with queue_jobs reachable")

_BASE = 910_000_000


@pytest.fixture
def ids():
    harness_queue.init(DSN)
    got: list[int] = []
    yield got
    if got:
        keys = [f"coding:todo:{i}" for i in got]
        with psycopg2.connect(DSN) as con:
            with con.cursor() as cur:
                cur.execute("DELETE FROM queue_jobs WHERE dedupe_key = ANY(%s)", (keys,))


def _new_id(got: list[int]) -> int:
    tid = _BASE + int(uuid.uuid4().int % 10_000_000)
    got.append(tid)
    return tid


def _herder() -> cat_herder.CatHerder:
    return cat_herder.CatHerder(
        {"postgres_dsn": DSN, "coding": {"repository_root": "/opt/TGW/tgw-lib/src/trader-grims-warehouse",
                                         "worktree_root": "/opt/TGW/var/worktrees"}},
        poll_seconds=0.1, lease_seconds=60,
    )


def _state(job_id: str) -> tuple[str, str | None]:
    with psycopg2.connect(DSN) as con:
        with con.cursor() as cur:
            cur.execute("SELECT state, error_code FROM queue_jobs WHERE job_id = %s", (job_id,))
            return cur.fetchone()


def test_landed_job_is_marked_succeeded(ids, monkeypatch):
    tid = _new_id(ids)
    seen = {}

    def fake_dispatch(target, **kw):
        seen["target"] = target
        seen["kw"] = kw
        return {"outcome": "landed", "commit": "abc123", "rounds": 1}

    monkeypatch.setattr(harness_cli, "dispatch", fake_dispatch)
    job = harness_queue.enqueue(tid, "make it so", executor_preference=("stub",))
    assert _herder().run_once() == 0

    assert seen["target"] == f"todo-{tid}"
    assert seen["kw"]["message"] == "make it so"
    assert seen["kw"]["coder_user"] == "tgw-coder"
    assert seen["kw"]["executor_preference"] == ("stub",)
    assert _state(job["job_id"])[0] == "succeeded"


def test_blocked_job_is_parked(ids, monkeypatch):
    tid = _new_id(ids)
    monkeypatch.setattr(harness_cli, "dispatch", lambda target, **kw: {
        "outcome": "blocked",
        "supervisor_handoff": {"next_operator_action": "grant prod read access"},
    })
    job = harness_queue.enqueue(tid, "needs access")
    _herder().run_once()
    state, code = _state(job["job_id"])
    assert state == "cancelled" and code == "BLOCKED"


def test_rebind_required_goes_to_retry_wait(ids, monkeypatch):
    tid = _new_id(ids)
    monkeypatch.setattr(harness_cli, "dispatch",
                        lambda target, **kw: {"outcome": "rebind_required", "detail": "base moved"})
    job = harness_queue.enqueue(tid, "rebase me")
    _herder().run_once()
    assert _state(job["job_id"])[0] == "retry_wait"


def test_dispatch_exception_is_retried_not_fatal(ids, monkeypatch):
    tid = _new_id(ids)

    def boom(target, **kw):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(harness_cli, "dispatch", boom)
    job = harness_queue.enqueue(tid, "explodes", max_attempts=2)
    _herder().run_once()  # must not raise
    assert _state(job["job_id"])[0] == "retry_wait"


def test_orchestrator_busy_is_retried(ids, monkeypatch):
    tid = _new_id(ids)

    def busy(target, **kw):
        raise harness_orchestrator.OrchestratorBusy("someone else owns the cursor")

    monkeypatch.setattr(harness_cli, "dispatch", busy)
    job = harness_queue.enqueue(tid, "contended")
    _herder().run_once()
    assert _state(job["job_id"])[0] == "retry_wait"


def test_unusable_payload_is_parked(ids):
    tid = _new_id(ids)
    harness_queue.init(DSN)
    # enqueue a job with a payload missing todo_id, straight into the table
    with psycopg2.connect(DSN) as con:
        con.autocommit = True
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO queue_jobs (dedupe_key, entity_type, entity_id, operation,
                    handler_family, queue_name, priority, payload_json, max_attempts)
                VALUES (%s,'todo',%s,'implement','harness','coding',100,'{}'::jsonb,3)
                RETURNING job_id
                """,
                (f"coding:todo:{tid}", str(tid)),
            )
            job_id = str(cur.fetchone()[0])
    _herder().run_once()
    state, code = _state(job_id)
    assert state == "cancelled" and code == "BLOCKED"


def test_singleton_second_herder_does_nothing(ids, monkeypatch):
    tid = _new_id(ids)
    calls = []
    monkeypatch.setattr(harness_cli, "dispatch",
                        lambda target, **kw: calls.append(target) or {"outcome": "landed"})
    job = harness_queue.enqueue(tid, "one herder only")

    holder = _herder()
    assert holder._acquire_singleton() is True
    try:
        assert _herder().run_once() == 0
        assert calls == []  # the second herder never claimed
        assert _state(job["job_id"])[0] == "queued"
    finally:
        holder._release_singleton()
