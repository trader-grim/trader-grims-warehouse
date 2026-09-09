"""Real-PostgreSQL proofs for the coding-work queue (Todo 2003 / cat_herder).

harness_queue is a thin coding-specific adapter over the shared queue_jobs
table. These tests use a unique dedupe key per run and delete only their own
rows; they skip when no PostgreSQL with the shared schema is reachable.
"""

from __future__ import annotations

import os
import uuid

import psycopg2
import pytest

from tgw.development import harness_queue

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

# A Todo id space unlikely to collide with a real Todo.
_BASE = 900_000_000


@pytest.fixture
def todo_ids():
    harness_queue.init(DSN)
    ids: list[int] = []
    yield ids
    if ids:
        keys = [f"coding:todo:{i}" for i in ids]
        with psycopg2.connect(DSN) as con:
            with con.cursor() as cur:
                cur.execute("DELETE FROM queue_jobs WHERE dedupe_key = ANY(%s)", (keys,))


def _new_id(ids: list[int]) -> int:
    tid = _BASE + int(uuid.uuid4().int % 10_000_000)
    ids.append(tid)
    return tid


def test_enqueue_is_idempotent_per_todo(todo_ids):
    tid = _new_id(todo_ids)
    a = harness_queue.enqueue(tid, "do the thing")
    assert a["created"] is True and a["state"] == "queued"
    b = harness_queue.enqueue(tid, "do the thing again")
    assert b["created"] is False
    assert b["job_id"] == a["job_id"]


def test_claim_then_succeed(todo_ids):
    tid = _new_id(todo_ids)
    harness_queue.enqueue(tid, "land me", executor_preference=("stub",), max_rounds=2)
    job = harness_queue.claim("test-owner", lease_seconds=60)
    assert job is not None
    assert job["entity_id"] == str(tid)
    assert job["payload_json"]["executor_preference"] == ["stub"]
    assert harness_queue.mark_running(job["job_id"], job["lease_token"]) is True
    assert harness_queue.heartbeat(job["job_id"], job["lease_token"], lease_seconds=60) is True
    assert harness_queue.succeed(job["job_id"], job["lease_token"], {"outcome": "landed"}) is True

    with psycopg2.connect(DSN) as con:
        with con.cursor() as cur:
            cur.execute("SELECT state, payload_json FROM queue_jobs WHERE job_id = %s",
                        (job["job_id"],))
            state, payload = cur.fetchone()
    assert state == "succeeded"
    assert payload["last_result"] == {"outcome": "landed"}


def test_claim_is_exclusive(todo_ids):
    tid = _new_id(todo_ids)
    harness_queue.enqueue(tid, "only once")
    first = harness_queue.claim("owner-a", lease_seconds=60)
    second = harness_queue.claim("owner-b", lease_seconds=60)
    assert first is not None
    # nothing else of ours is runnable; a different queued job could be picked
    # up, but not this one
    assert second is None or second["job_id"] != first["job_id"]


def test_park_is_terminal_and_reenqueue_after_park_works(todo_ids):
    tid = _new_id(todo_ids)
    harness_queue.enqueue(tid, "blocked work")
    job = harness_queue.claim("owner", lease_seconds=60)
    harness_queue.mark_running(job["job_id"], job["lease_token"])
    assert harness_queue.park(job["job_id"], job["lease_token"], "needs an operator") is True

    with psycopg2.connect(DSN) as con:
        with con.cursor() as cur:
            cur.execute("SELECT state, error_code FROM queue_jobs WHERE job_id = %s",
                        (job["job_id"],))
            state, code = cur.fetchone()
    assert state == "cancelled" and code == "BLOCKED"

    # the blocker cleared — a fresh enqueue is allowed again
    again = harness_queue.enqueue(tid, "unblocked now")
    assert again["created"] is True and again["job_id"] != job["job_id"]


def test_retry_later_then_dead_letter_at_max_attempts(todo_ids):
    tid = _new_id(todo_ids)
    harness_queue.enqueue(tid, "flaky", max_attempts=2)

    job = harness_queue.claim("owner", lease_seconds=60)
    harness_queue.mark_running(job["job_id"], job["lease_token"])
    state = harness_queue.retry_later(job["job_id"], job["lease_token"], error_detail="boom",
                                     delay_seconds=0, attempt_count=1, max_attempts=2)
    assert state == "retry_wait"

    harness_queue.recover_expired()  # promote the matured retry_wait row
    job2 = harness_queue.claim("owner", lease_seconds=60)
    assert job2 is not None and job2["job_id"] == job["job_id"]
    harness_queue.mark_running(job2["job_id"], job2["lease_token"])
    state = harness_queue.retry_later(job2["job_id"], job2["lease_token"], error_detail="boom again",
                                     delay_seconds=0, attempt_count=2, max_attempts=2)
    assert state == "dead_letter"


def test_snapshot_reports_our_job(todo_ids):
    tid = _new_id(todo_ids)
    harness_queue.enqueue(tid, "visible")
    snap = harness_queue.snapshot(limit=100)
    assert snap["queue"] == "coding"
    mine = [j for j in snap["jobs"] if j["entity_id"] == str(tid)]
    assert len(mine) == 1 and mine[0]["state"] == "queued"
