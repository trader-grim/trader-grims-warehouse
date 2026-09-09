"""The coding-work queue — LEAF-11-1 residual (Todo 2003, `cat_herder`).

`tgw coding start <todo>` is synchronous: a session runs it and waits out the
whole implement -> test -> review -> land loop. The `cat_herder` daemon makes
the loop autonomous, and this module is the hand-off point between them.

Substrate: the one shared PostgreSQL ``queue_jobs`` table (``queue.durable-claims@1``
/ W02) — the same database ``harness_ledger`` already uses. This is a thin,
coding-specific adapter over it (raw psycopg2, exactly like ``harness_ledger``);
it deliberately does NOT pull in ``tgw.queue.worker_base`` / ``state_machine``,
which carry the eCommerce platform's NATS / quota / eBay-environment machinery
that the apparatus-free coding workflow was built to avoid.

One logical queue, ``coding``. One job per Todo: ``dedupe_key`` is
``coding:todo:<id>`` so re-enqueueing a Todo that already has a live job is a
no-op that returns the existing job id. A job carries only what
``harness_cli.dispatch`` needs — the Todo id, the commit subject, an optional
executor preference and round budget — in ``payload_json``.

Job state (the ``queue_job_state`` enum) maps to coding outcomes as:

    queued / leased / running   in flight
    succeeded                   landed (or already-satisfied / already-done)
    cancelled                   parked — blocked on something a human/plan owns
    retry_wait                  transient failure, will retry after not_before
    dead_letter                 gave up after max_attempts
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator

import psycopg2
import psycopg2.errors
import psycopg2.extras

QUEUE_NAME = "coding"
HANDLER_FAMILY = "harness"
OPERATION = "implement"
ENTITY_TYPE = "todo"

# A coding job that keeps failing outright should stop, not spin. Three real
# attempts (each a full bounded implement/review budget) is plenty before a
# human looks.
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_PRIORITY = 100

_DSN: str = "dbname=tgw_lib_dev_state_machine user=tgw_coding"


class HarnessQueueError(RuntimeError):
    """The coding queue cannot be used as asked."""


def init(dsn: str) -> None:
    """Bind the PostgreSQL DSN (the same database as harness_ledger / the queue)."""
    global _DSN
    _DSN = dsn


@contextmanager
def _conn() -> Generator[Any, None, None]:
    con = psycopg2.connect(_DSN)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _dedupe_key(todo_id: int) -> str:
    return f"coding:todo:{int(todo_id)}"


def _dump(value: Any) -> str:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------------- #
# enqueue
# --------------------------------------------------------------------------- #

def enqueue(
    todo_id: int,
    message: str,
    *,
    body: str | None = None,
    executor_preference: tuple[str, ...] = (),
    max_rounds: int | None = None,
    priority: int = DEFAULT_PRIORITY,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    origin: str = "operator",
) -> dict[str, Any]:
    """Put one job on the coding queue for the daemon to pick up.

    ``todo_id`` addresses the job and dedupes it (one live job per id). When
    ``body`` is given it is the task text verbatim (an ad-hoc spec, or a
    solved plan/PP work unit); otherwise the daemon reads Todo ``todo_id``'s
    body from the store at dispatch time.

    Idempotent: if a non-terminal job for this id already exists its job_id is
    returned with ``created=False`` and nothing new is enqueued.
    """
    todo_id = int(todo_id)
    if not isinstance(message, str) or not message.strip():
        raise HarnessQueueError("a non-empty commit message is required to enqueue")
    payload: dict[str, Any] = {
        "todo_id": todo_id,
        "message": message.strip(),
        "origin": origin,
    }
    if body is not None:
        if not body.strip():
            raise HarnessQueueError("body, when given, must be non-empty")
        payload["body"] = body
    if executor_preference:
        payload["executor_preference"] = list(executor_preference)
    if max_rounds is not None:
        payload["max_rounds"] = int(max_rounds)

    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # An advisory lock over the Todo makes the check-then-insert atomic
            # against a second enqueuer without relying on catching the unique
            # violation (which aborts the transaction).
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_dedupe_key(todo_id),))
            cur.execute(
                """
                SELECT job_id, state FROM queue_jobs
                 WHERE dedupe_key = %s
                   AND state NOT IN ('succeeded','failed','dead_letter','cancelled')
                 ORDER BY created_at DESC
                 LIMIT 1
                """,
                (_dedupe_key(todo_id),),
            )
            existing = cur.fetchone()
            if existing is not None:
                return {"job_id": str(existing["job_id"]), "state": existing["state"],
                        "created": False, "todo_id": todo_id}
            cur.execute(
                """
                INSERT INTO queue_jobs
                    (dedupe_key, entity_type, entity_id, operation, handler_family,
                     queue_name, priority, payload_json, max_attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING job_id, state
                """,
                (_dedupe_key(todo_id), ENTITY_TYPE, str(todo_id), OPERATION,
                 HANDLER_FAMILY, QUEUE_NAME, int(priority), _dump(payload),
                 int(max_attempts)),
            )
            row = cur.fetchone()
            return {"job_id": str(row["job_id"]), "state": row["state"],
                    "created": True, "todo_id": todo_id}


# --------------------------------------------------------------------------- #
# claim / heartbeat / finish  (the daemon side)
# --------------------------------------------------------------------------- #

def claim(owner: str, *, lease_seconds: int) -> dict[str, Any] | None:
    """Lease the next runnable coding job, or None. Uses the shared
    ``claim_queue_jobs`` SKIP LOCKED function so a second herder never double-runs
    a job."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM claim_queue_jobs(%s, %s, %s, %s)",
                (owner, QUEUE_NAME, 1, int(lease_seconds)),
            )
            row = cur.fetchone()
    if row is None:
        return None
    job = dict(row)
    job["job_id"] = str(job["job_id"])
    if job.get("lease_token") is not None:
        job["lease_token"] = str(job["lease_token"])
    return job


def _set_transition(cur: Any, label: str) -> None:
    # The queue_jobs history trigger reads these GUCs; best-effort labelling.
    try:
        cur.execute("SELECT set_config('tgw.queue_transition', %s, true)", (label,))
    except psycopg2.Error:
        pass


def mark_running(job_id: str, lease_token: str) -> bool:
    """leased -> running at the start of a dispatch. False if the lease is gone."""
    with _conn() as con:
        with con.cursor() as cur:
            _set_transition(cur, "cat_herder:running")
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = 'running', started_at = COALESCE(started_at, now())
                 WHERE job_id = %s AND lease_token = %s AND state = 'leased'
                 RETURNING job_id
                """,
                (job_id, lease_token),
            )
            return cur.fetchone() is not None


def heartbeat(job_id: str, lease_token: str, *, lease_seconds: int) -> bool:
    """Extend the lease while a long dispatch runs. False means the lease was
    lost (expired and stolen) — the caller should stop touching the job."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE queue_jobs
                   SET lease_expires_at = now() + make_interval(secs => %s),
                       last_heartbeat_at = now()
                 WHERE job_id = %s AND lease_token = %s
                   AND state IN ('leased', 'running')
                 RETURNING job_id
                """,
                (int(lease_seconds), job_id, lease_token),
            )
            return cur.fetchone() is not None


def _finish(job_id: str, lease_token: str, *, state: str, label: str,
            error_code: str | None, error_detail: str | None,
            result: dict[str, Any] | None, not_before_seconds: int | None) -> bool:
    with _conn() as con:
        with con.cursor() as cur:
            _set_transition(cur, label)
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = %s::queue_job_state,
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL,
                       last_heartbeat_at = NULL,
                       error_code = %s,
                       error_detail = %s,
                       not_before = CASE WHEN %s::int IS NULL THEN NULL
                                         ELSE now() + make_interval(secs => %s::int) END,
                       finished_at = CASE WHEN %s = 'retry_wait' THEN NULL ELSE now() END,
                       payload_json = payload_json || %s::jsonb
                 WHERE job_id = %s AND lease_token = %s
                 RETURNING job_id
                """,
                (state, error_code, error_detail[:4000] if error_detail else None,
                 not_before_seconds, not_before_seconds, state,
                 _dump({"last_result": result} if result is not None else {}),
                 job_id, lease_token),
            )
            return cur.fetchone() is not None


def succeed(job_id: str, lease_token: str, result: dict[str, Any]) -> bool:
    """The task landed (or was already satisfied / already done)."""
    return _finish(job_id, lease_token, state="succeeded", label="cat_herder:landed",
                   error_code=None, error_detail=None, result=result,
                   not_before_seconds=None)


def park(job_id: str, lease_token: str, error_detail: str) -> bool:
    """The task is blocked on something a human or the plan owns (budget
    exhausted with a supervisor hand-off, base ref moved, orchestrator busy).
    Not a transient failure — it will not retry itself; a person re-enqueues it
    once the blocker clears."""
    return _finish(job_id, lease_token, state="cancelled", label="cat_herder:parked",
                   error_code="BLOCKED", error_detail=error_detail, result=None,
                   not_before_seconds=None)


def retry_later(job_id: str, lease_token: str, *, error_detail: str,
                delay_seconds: int, attempt_count: int, max_attempts: int) -> str:
    """A transient failure. Go to retry_wait if attempts remain, else dead_letter."""
    if attempt_count >= max_attempts:
        _finish(job_id, lease_token, state="dead_letter", label="cat_herder:dead_letter",
                error_code="MAX_ATTEMPTS", error_detail=error_detail, result=None,
                not_before_seconds=None)
        return "dead_letter"
    _finish(job_id, lease_token, state="retry_wait", label="cat_herder:retry",
            error_code="TRANSIENT", error_detail=error_detail, result=None,
            not_before_seconds=int(delay_seconds))
    return "retry_wait"


def recover_expired() -> int:
    """Requeue leases whose owner died mid-job and promote matured retry_wait
    jobs. Safe to call from any herder; it is the shared queue's own function."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT recover_expired_jobs()")
            row = cur.fetchone()
            return int(row[0]) if row else 0


# --------------------------------------------------------------------------- #
# read-only views  (the CLI side)
# --------------------------------------------------------------------------- #

def snapshot(*, limit: int = 20) -> dict[str, Any]:
    """State counts and the most recent jobs for the coding queue."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT state, count(*) AS n FROM queue_jobs "
                "WHERE queue_name = %s GROUP BY state",
                (QUEUE_NAME,),
            )
            counts = {r["state"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                """
                SELECT job_id, entity_id, state, priority, attempt_count, max_attempts,
                       error_code, error_detail, payload_json,
                       created_at, updated_at, finished_at, not_before
                  FROM queue_jobs
                 WHERE queue_name = %s
                 ORDER BY created_at DESC
                 LIMIT %s
                """,
                (QUEUE_NAME, int(limit)),
            )
            jobs = []
            for r in cur.fetchall():
                row = dict(r)
                row["job_id"] = str(row["job_id"])
                jobs.append(row)
    return {"queue": QUEUE_NAME, "counts": counts, "jobs": jobs}
