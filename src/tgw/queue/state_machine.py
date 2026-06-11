from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Generator, List, Optional

import psycopg2
import psycopg2.extras

# Module-level DSN — set by init() before any worker starts.
_DSN: str = 'dbname=state_machine user=tgw'


def init(dsn: str) -> None:
    """Set the PostgreSQL DSN for all state-machine operations."""
    global _DSN
    _DSN = dsn


@contextmanager
def _conn() -> Generator:
    """Open a short-lived autocommit-off connection."""
    con = psycopg2.connect(_DSN)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

STATES = {
    'queued',
    'leased',
    'running',
    'retry_wait',
    'succeeded',
    'failed',
    'dead_letter',
    'cancelled',
}

ALLOWED_TRANSITIONS = {
    'queued': {'leased', 'cancelled'},
    'leased': {'running', 'queued', 'cancelled'},
    'running': {'succeeded', 'retry_wait', 'failed', 'queued', 'cancelled'},
    'retry_wait': {'queued', 'cancelled', 'dead_letter'},
    'failed': {'dead_letter', 'queued', 'cancelled'},
    'succeeded': set(),
    'dead_letter': {'queued', 'cancelled'},
    'cancelled': {'queued'},
}

@dataclass(frozen=True)
class TransitionRule:
    old_state: str
    new_state: str
    requires_worker: bool = False
    terminal: bool = False

RULES = {
    ('queued', 'leased'): TransitionRule('queued', 'leased', requires_worker=True),
    ('leased', 'running'): TransitionRule('leased', 'running', requires_worker=True),
    ('leased', 'queued'): TransitionRule('leased', 'queued'),
    ('running', 'succeeded'): TransitionRule('running', 'succeeded', requires_worker=True, terminal=True),
    ('running', 'retry_wait'): TransitionRule('running', 'retry_wait', requires_worker=True),
    ('running', 'failed'): TransitionRule('running', 'failed', requires_worker=True, terminal=True),
    ('running', 'queued'): TransitionRule('running', 'queued'),
    ('retry_wait', 'queued'): TransitionRule('retry_wait', 'queued'),
    ('retry_wait', 'dead_letter'): TransitionRule('retry_wait', 'dead_letter', terminal=True),
    ('failed', 'dead_letter'): TransitionRule('failed', 'dead_letter', terminal=True),
    ('failed', 'queued'): TransitionRule('failed', 'queued'),
    ('dead_letter', 'queued'): TransitionRule('dead_letter', 'queued'),
    ('queued', 'cancelled'): TransitionRule('queued', 'cancelled', terminal=True),
    ('leased', 'cancelled'): TransitionRule('leased', 'cancelled', terminal=True),
    ('running', 'cancelled'): TransitionRule('running', 'cancelled', terminal=True),
    ('retry_wait', 'cancelled'): TransitionRule('retry_wait', 'cancelled', terminal=True),
    ('failed', 'cancelled'): TransitionRule('failed', 'cancelled', terminal=True),
    ('dead_letter', 'cancelled'): TransitionRule('dead_letter', 'cancelled', terminal=True),
    ('cancelled', 'queued'): TransitionRule('cancelled', 'queued'),
}


def can_transition(old_state: str, new_state: str) -> bool:
    if old_state not in STATES or new_state not in STATES:
        return False
    return new_state in ALLOWED_TRANSITIONS.get(old_state, set())


def validate_transition(old_state: str, new_state: str, worker_id: Optional[str] = None) -> TransitionRule:
    if not can_transition(old_state, new_state):
        raise ValueError(f'invalid transition: {old_state} -> {new_state}')
    rule = RULES[(old_state, new_state)]
    if rule.requires_worker and not worker_id:
        raise ValueError(f'transition {old_state} -> {new_state} requires worker_id')
    return rule


def next_failure_state(attempt_count: int, max_attempts: int) -> str:
    return 'failed' if attempt_count >= max_attempts else 'retry_wait'


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def enqueue_job(
    queue_name: str,
    payload: Dict[str, Any],
    *,
    entity_type: str = 'generic',
    entity_id: str = '',
    operation: str = 'run',
    handler_family: str = '',
    priority: int = 100,
    not_before: Optional[float] = None,
    dedupe_key: Optional[str] = None,
    max_attempts: int = 5,
) -> str:
    """Insert a new queued job. Returns the new job_id (UUID string)."""
    handler_family = handler_family or queue_name
    entity_id = entity_id or queue_name
    nb = None
    if not_before is not None:
        from datetime import datetime, timezone
        nb = datetime.fromtimestamp(not_before, tz=timezone.utc)
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO queue_jobs
                    (queue_name, entity_type, entity_id, operation,
                     handler_family, priority, payload_json,
                     not_before, dedupe_key, max_attempts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING job_id::text
                """,
                (queue_name, entity_type, entity_id, operation,
                 handler_family, priority, json.dumps(payload),
                 nb, dedupe_key, max_attempts),
            )
            return cur.fetchone()[0]


def claim_queue_jobs(
    queue_name: str,
    lease_owner: str,
    lease_seconds: int = 300,
    limit: int = 1,
) -> List[Dict[str, Any]]:
    """Lease up to `limit` queued jobs. Returns list of row dicts."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT * FROM claim_queue_jobs(%s, %s, %s, %s)',
                (lease_owner, queue_name, limit, lease_seconds),
            )
            return [dict(r) for r in cur.fetchall()]


def mark_running(job_id: str, lease_owner: str) -> None:
    """Transition leased → running."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = 'running'
                 WHERE job_id = %s AND state = 'leased' AND lease_owner = %s
                """,
                (job_id, lease_owner),
            )


def mark_succeeded(job_id: str, lease_owner: str, result: Optional[Dict[str, Any]] = None) -> None:
    """Transition running → succeeded."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = 'succeeded',
                       finished_at = NOW(),
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL,
                       error_code = NULL,
                       error_detail = NULL
                 WHERE job_id = %s AND state = 'running' AND lease_owner = %s
                """,
                (job_id, lease_owner),
            )


def mark_failed(job_id: str, lease_owner: str, error: str) -> None:
    """
    Transition running → retry_wait or failed → dead_letter.

    Respects max_attempts: if attempt_count >= max_attempts, goes straight
    to dead_letter. Otherwise goes to retry_wait with exponential backoff.
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT attempt_count, max_attempts FROM queue_jobs WHERE job_id = %s',
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            attempt = row['attempt_count']
            max_att = row['max_attempts']
            new_state = next_failure_state(attempt, max_att)

            if new_state == 'retry_wait':
                backoff = min(30 * (2 ** (attempt - 1)), 3600)
                from datetime import datetime, timedelta, timezone
                nb = datetime.now(tz=timezone.utc) + timedelta(seconds=backoff)
                cur.execute(
                    """
                    UPDATE queue_jobs
                       SET state = 'retry_wait',
                           not_before = %s,
                           error_code = 'WORKER_EXCEPTION',
                           error_detail = %s,
                           finished_at = NOW(),
                           lease_owner = NULL,
                           lease_token = NULL,
                           lease_expires_at = NULL
                     WHERE job_id = %s AND state = 'running' AND lease_owner = %s
                    """,
                    (nb, error[:2000], job_id, lease_owner),
                )
            else:
                cur.execute(
                    """
                    UPDATE queue_jobs
                       SET state = 'failed',
                           error_code = 'WORKER_EXCEPTION',
                           error_detail = %s,
                           finished_at = NOW(),
                           lease_owner = NULL,
                           lease_token = NULL,
                           lease_expires_at = NULL
                     WHERE job_id = %s AND state = 'running' AND lease_owner = %s
                    """,
                    (error[:2000], job_id, lease_owner),
                )
                # Immediately promote failed → dead_letter
                cur.execute(
                    """
                    UPDATE queue_jobs SET state = 'dead_letter'
                     WHERE job_id = %s AND state = 'failed'
                    """,
                    (job_id,),
                )


def mark_dead_letter(job_id: str, lease_owner: str, error: str) -> None:
    """Transition running → dead_letter immediately, bypassing retry logic."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = 'dead_letter',
                       error_code = 'HARD_FAILURE',
                       error_detail = %s,
                       finished_at = NOW(),
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL
                 WHERE job_id = %s AND state = 'running' AND lease_owner = %s
                """,
                (error[:2000], job_id, lease_owner),
            )


def recover_expired_jobs() -> int:
    """Requeue jobs whose leases have expired. Returns count of recovered jobs."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute('SELECT recover_expired_jobs()')
            return cur.fetchone()[0]


def queue_depths() -> Dict[str, int]:
    """Return count of queued jobs per queue_name."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT queue_name, COUNT(*) as n
                  FROM queue_jobs
                 WHERE state = 'queued'
                 GROUP BY queue_name
                """
            )
            return {row[0]: row[1] for row in cur.fetchall()}


def active_depths() -> Dict[str, int]:
    """Count jobs in any active state (queued/running/leased/retry_wait) per queue.

    Used by `tgw quiet-check` (PP-CAPTURE-001) to decide whether the pipeline is
    genuinely idle — queue_depths() counts only 'queued', missing in-flight work.
    """
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT queue_name, COUNT(*) as n
                  FROM queue_jobs
                 WHERE state IN ('queued', 'running', 'leased', 'retry_wait')
                 GROUP BY queue_name
                """
            )
            return {row[0]: row[1] for row in cur.fetchall()}


def dead_letter_count() -> int:
    """Return total count of dead_letter jobs."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM queue_jobs WHERE state = 'dead_letter'")
            return cur.fetchone()[0]


def dead_letter_breakdown() -> Dict[str, int]:
    """Return per-queue count of dead_letter jobs (excludes queues with 0)."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT queue_name, COUNT(*) as n
                  FROM queue_jobs
                 WHERE state = 'dead_letter'
                 GROUP BY queue_name
                 ORDER BY n DESC, queue_name
                """
            )
            return {row[0]: row[1] for row in cur.fetchall()}


def clear_dead_letter(queue_name: str) -> int:
    """Cancel all dead_letter jobs for a given queue. Returns the number of rows affected."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """UPDATE queue_jobs SET state = 'cancelled'
                   WHERE queue_name = %s AND state = 'dead_letter'""",
                (queue_name,),
            )
            return cur.rowcount


def dead_letter_jobs(queue_name: str = '', limit: int = 100) -> List[Dict[str, Any]]:
    """Return dead_letter jobs as dicts, optionally filtered by queue_name."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if queue_name:
                cur.execute(
                    """
                    SELECT job_id::text, queue_name, payload_json, error_detail,
                           attempt_count, max_attempts, created_at, finished_at
                      FROM queue_jobs
                     WHERE state = 'dead_letter' AND queue_name = %s
                     ORDER BY finished_at DESC NULLS LAST
                     LIMIT %s
                    """,
                    (queue_name, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT job_id::text, queue_name, payload_json, error_detail,
                           attempt_count, max_attempts, created_at, finished_at
                      FROM queue_jobs
                     WHERE state = 'dead_letter'
                     ORDER BY queue_name, finished_at DESC NULLS LAST
                     LIMIT %s
                    """,
                    (limit,),
                )
            return [dict(r) for r in cur.fetchall()]


def job_history(
    sku: str = '',
    queue_name: str = '',
    job_id: str = '',
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return v_job_history rows filtered by SKU, queue, or job_id.

    Exactly one of sku/queue_name/job_id should be supplied.
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if job_id:
                cur.execute(
                    """
                    SELECT history_id, job_id::text, queue_name, entity_type, entity_id,
                           operation, current_state, old_state, new_state, transition,
                           worker_id, message, error_code, error_detail, payload_json, created_at
                      FROM v_job_history
                     WHERE job_id = %s::uuid
                     ORDER BY history_id ASC
                    """,
                    (job_id,),
                )
            elif sku:
                cur.execute(
                    """
                    SELECT history_id, job_id::text, queue_name, entity_type, entity_id,
                           operation, current_state, old_state, new_state, transition,
                           worker_id, message, error_code, error_detail, payload_json, created_at
                      FROM v_job_history
                     WHERE entity_id = %s
                     ORDER BY job_id, history_id ASC
                     LIMIT %s
                    """,
                    (sku, limit),
                )
            else:
                cur.execute(
                    """
                    SELECT history_id, job_id::text, queue_name, entity_type, entity_id,
                           operation, current_state, old_state, new_state, transition,
                           worker_id, message, error_code, error_detail, payload_json, created_at
                      FROM v_job_history
                     WHERE (%s = '' OR queue_name = %s)
                     ORDER BY history_id DESC
                     LIMIT %s
                    """,
                    (queue_name, queue_name, limit),
                )
            return [dict(r) for r in cur.fetchall()]


def requeue_dead_letter_job(job_id: str) -> str:
    """Re-enqueue a dead_letter job by cloning its payload into a fresh queued job.

    The dead_letter job is cancelled (not deleted). Returns the new job_id.
    Raises ValueError if the job is not found or not in dead_letter state.
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id::text, queue_name, payload_json, entity_type,
                       entity_id, operation, priority, max_attempts
                  FROM queue_jobs
                 WHERE job_id = %s AND state = 'dead_letter'
                """,
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(f'job {job_id!r} not found or not in dead_letter state')

        with con.cursor() as cur:
            # Cancel the dead_letter job
            cur.execute(
                "UPDATE queue_jobs SET state = 'cancelled' WHERE job_id = %s",
                (job_id,),
            )
            # Insert a fresh queued job — no dedupe_key so it bypasses the unique constraint
            cur.execute(
                """
                INSERT INTO queue_jobs (queue_name, payload_json, entity_type, entity_id,
                                        operation, handler_family, priority, max_attempts,
                                        state, error_code, error_detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', NULL, NULL)
                RETURNING job_id::text
                """,
                (
                    row['queue_name'],
                    json.dumps(dict(row['payload_json'])),
                    row['entity_type'],
                    row['entity_id'],
                    row['operation'],
                    row['queue_name'],
                    row['priority'],
                    row['max_attempts'],
                ),
            )
            new_id = cur.fetchone()[0]
    return new_id


def requeue_with_backoff(job_id: str, lease_owner: str, delay_seconds: int, error_detail: str = '') -> None:
    """Transition running → retry_wait with a custom delay, resetting attempt_count.

    Used for transient errors classified by classify_dead_letter() that should be
    retried after a longer delay rather than dying permanently.
    """
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE queue_jobs
                   SET state = 'retry_wait',
                       not_before = NOW() + (%s || ' seconds')::interval,
                       attempt_count = 1,
                       error_code = 'TRANSIENT',
                       error_detail = %s,
                       finished_at = NULL,
                       lease_owner = NULL,
                       lease_token = NULL,
                       lease_expires_at = NULL
                 WHERE job_id = %s AND state = 'running' AND lease_owner = %s
                """,
                (str(delay_seconds), error_detail[:2000], job_id, lease_owner),
            )
