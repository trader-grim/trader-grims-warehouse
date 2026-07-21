from __future__ import annotations

import json
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

# Module-level DSN — set by init() before any worker starts.
_DSN: str = 'dbname=state_machine user=tgw'

# PP-STATEMACHINE-001 Phase 2 (todo #1608) — queue-priority config, same
# 'defaults'/'use_default' shape/convention as tgw-models.json. Loaded lazily
# and cached; missing file is NOT an error (every lookup just falls back to
# the 'normal' tier, today's implicit hardcoded default) since this file is
# a live-only deploy artifact (like tgw-models.json, not git-tracked) that
# may not exist yet in every environment.
_QUEUE_PRIORITIES_PATH = Path('/opt/TGW/config/tgw-queue-priorities.json')
_queue_priorities_cache: Optional[Dict[str, Any]] = None


def _load_queue_priorities() -> Dict[str, Any]:
    global _queue_priorities_cache
    if _queue_priorities_cache is not None:
        return _queue_priorities_cache
    cfg: Dict[str, Any] = {}
    try:
        if _QUEUE_PRIORITIES_PATH.exists():
            raw = json.loads(_QUEUE_PRIORITIES_PATH.read_text(encoding='utf-8'))
            cfg = {k: v for k, v in raw.items() if not k.startswith('_')}
    except Exception:
        log.warning('failed to load %s — falling back to normal priority for '
                     'every queue', _QUEUE_PRIORITIES_PATH, exc_info=True)
        cfg = {}
    _queue_priorities_cache = cfg
    return cfg


def _reset_queue_priorities_cache() -> None:
    """Test hook — clear the module-level cache so a test can point at a
    fresh fixture path / monkeypatched file content."""
    global _queue_priorities_cache
    _queue_priorities_cache = None


def resolve_priority(queue_name: str, operation: str) -> int:
    """Resolve the priority tier for queue_name:operation from
    tgw-queue-priorities.json. Falls back to 100 ('normal') if the file is
    missing, the key isn't present, or the entry is malformed — an
    unmapped queue is not an error (see PP-STATEMACHINE-001)."""
    cfg = _load_queue_priorities()
    key = f'{queue_name}:{operation}'
    entry = cfg.get(key)
    if entry is None:
        return 100
    if isinstance(entry, int):
        return entry
    if isinstance(entry, dict) and 'use_default' in entry:
        default_name = entry['use_default']
        tier_value = cfg.get('defaults', {}).get(default_name)
        if isinstance(tier_value, int):
            return tier_value
        log.warning("tgw-queue-priorities.json[%r]['use_default'] names %r, "
                    "which has no int entry in 'defaults' — falling back to "
                    "normal", key, default_name)
        return 100
    log.warning('tgw-queue-priorities.json[%r] is malformed (%r) — falling '
                'back to normal', key, entry)
    return 100


def init(dsn: str) -> None:
    """Set the PostgreSQL DSN for all state-machine operations."""
    global _DSN, _ai_usage_table_ready
    _DSN = dsn
    _ai_usage_table_ready = False


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
    'leased': {'running', 'queued', 'cancelled', 'dead_letter'},
    'running': {'succeeded', 'retry_wait', 'failed', 'queued', 'cancelled', 'dead_letter'},
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
    ('leased', 'dead_letter'): TransitionRule('leased', 'dead_letter', terminal=True),
    ('running', 'succeeded'): TransitionRule('running', 'succeeded', requires_worker=True, terminal=True),
    ('running', 'retry_wait'): TransitionRule('running', 'retry_wait', requires_worker=True),
    ('running', 'failed'): TransitionRule('running', 'failed', requires_worker=True, terminal=True),
    ('running', 'dead_letter'): TransitionRule('running', 'dead_letter', terminal=True),
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

class MissingManifestFieldError(ValueError):
    """PP-STATEMACHINE-001 Phase 4 (todo #1608) — raised by enqueue_job() when
    a call is missing a required manifest field (dedupe_key, or entity_id for
    an entity_type='item' job) and hasn't declared an explicit, named
    opt-out. See invariant E16."""


def enqueue_job(
    queue_name: str,
    payload: Dict[str, Any],
    *,
    entity_type: str = 'generic',
    entity_id: str = '',
    operation: str = 'run',
    handler_family: str = '',
    priority: Optional[int] = None,
    not_before: Optional[float] = None,
    dedupe_key: Optional[str] = None,
    max_attempts: int = 5,
    debounce: bool = False,
    supersede: bool = False,
    dedupe_key_exempt: bool = False,
) -> str:
    """Insert a new queued job. Returns the job_id (UUID string).

    debounce=True changes dedupe_key collision handling from reject (the
    default — raises psycopg2.errors.UniqueViolation, correct for per-SKU
    pipeline dedup like `ebay_stage:{sku}` where a second enqueue must be a
    no-op, never a delay) to extend: a colliding insert instead pushes the
    existing pending job's not_before forward via GREATEST(). Use this only
    for batch-coalescing keys (e.g. catalog_rebuild:pending) where the goal
    is one job fired after writes go quiet, not one fired at a fixed offset
    from the first write in a sustained burst. See uq_queue_jobs_dedupe_key_active.

    entity_id — ALWAYS pass this explicitly (entity_type='item', entity_id=sku)
    for any per-item job. The `entity_id or queue_name` fallback below exists
    only for genuinely queue-level jobs (self-rescheduling maintenance runs
    with no single entity, e.g. token_refresh/ebay_sync's own reschedule).
    Forgetting to pass entity_id on a per-SKU cross-enqueue silently breaks
    `tgw queue-history --sku <sku>` (job_history()'s `WHERE entity_id = %s`)
    with no error — this exact bug shipped for ~300k rows (todo #1406,
    PP-DEADLETTER-001) before every internal pipeline enqueue_job() caller
    was audited and fixed to pass entity_id=sku. Do not let a new caller
    regress it.

    priority — PP-STATEMACHINE-001 Phase 2 (todo #1608): if omitted (None),
    resolved from tgw-queue-priorities.json via resolve_priority(queue_name,
    operation), falling back to 100 ('normal') if no config entry exists. An
    explicit priority= argument always overrides the config lookup.

    supersede=True (PP-STATEMACHINE-001 Phase 3, todo #1608) — for the
    "force this job eligible right now" case (e.g. `tgw restart-ebay-token`)
    that debounce=True's GREATEST() can't express (it can only push a
    pending job's not_before LATER, never earlier). Only meaningful together
    with a dedupe_key: before inserting, atomically cancels (state →
    'cancelled') any existing non-terminal row under the same dedupe_key,
    then inserts the fresh row with plain reject semantics (no ON CONFLICT
    needed — the collision was just cleared in the same transaction).
    supersede=True with debounce=True is not a supported combination — pick
    one collision-handling mode.

    Enforcement (PP-STATEMACHINE-001 Phase 4, todo #1608, invariant E16):
    every call must supply a non-empty dedupe_key, unless it explicitly
    passes dedupe_key_exempt=True (a code comment at the call site must
    explain why — this is a deliberate, reviewed exception, not a way to
    silently dodge the manifest contract). Any entity_type='item' call must
    supply a non-empty entity_id — no opt-out for this one; the 2026-07-20
    codebase-wide re-audit found no genuine holdout that needs it, and a
    per-item job's entity_id is always knowable at the call site.
    """
    if debounce and supersede:
        raise ValueError(
            "enqueue_job: debounce=True and supersede=True are mutually "
            "exclusive collision-handling modes — pick one."
        )
    if entity_type == 'item' and not entity_id:
        raise MissingManifestFieldError(
            f"enqueue_job(queue_name={queue_name!r}): entity_type='item' "
            "requires a non-empty entity_id (see invariant E16 / "
            "PP-STATEMACHINE-001) — pass entity_id=<sku> explicitly."
        )
    if not dedupe_key and not dedupe_key_exempt:
        raise MissingManifestFieldError(
            f"enqueue_job(queue_name={queue_name!r}): missing dedupe_key "
            "(see invariant E16 / PP-STATEMACHINE-001) — pass an explicit "
            "dedupe_key=, or dedupe_key_exempt=True with a code comment "
            "explaining why this call is a genuine, reviewed exception."
        )
    handler_family = handler_family or queue_name
    entity_id = entity_id or queue_name
    if priority is None:
        priority = resolve_priority(queue_name, operation)
    nb = None
    if not_before is not None:
        from datetime import datetime, timezone
        nb = datetime.fromtimestamp(not_before, tz=timezone.utc)
    with _conn() as con:
        with con.cursor() as cur:
            if supersede and dedupe_key:
                # Only preempt rows that are genuinely still *pending* —
                # 'queued' (incl. future not_before) and 'retry_wait' are the
                # cases debounce=True's GREATEST() can only push later, plus
                # 'failed'/'dead_letter' (a stalled prior attempt under this
                # key that would otherwise just sit there). Deliberately
                # excludes 'leased'/'running': a job actively being worked
                # right now is not superseded by a fresh enqueue, it's left
                # to finish (or dead-letter/retry on its own).
                cur.execute(
                    """
                    UPDATE queue_jobs
                    SET state = 'cancelled', updated_at = NOW()
                    WHERE dedupe_key = %s
                      AND state IN ('queued', 'retry_wait', 'failed', 'dead_letter')
                    """,
                    (dedupe_key,),
                )
            if debounce:
                cur.execute(
                    """
                    INSERT INTO queue_jobs
                        (queue_name, entity_type, entity_id, operation,
                         handler_family, priority, payload_json,
                         not_before, dedupe_key, max_attempts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (dedupe_key)
                        WHERE dedupe_key IS NOT NULL
                          AND state NOT IN ('succeeded','failed','dead_letter','cancelled')
                    DO UPDATE SET
                        not_before = GREATEST(queue_jobs.not_before, EXCLUDED.not_before),
                        payload_json = EXCLUDED.payload_json,
                        updated_at = NOW()
                    RETURNING job_id::text
                    """,
                    (queue_name, entity_type, entity_id, operation,
                     handler_family, priority, json.dumps(payload),
                     nb, dedupe_key, max_attempts),
                )
            else:
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


def enqueue_catalog_rebuild(reason: str, delay_seconds: float = 30.0) -> str:
    """No-op as of PP-CATALOG-INCR-001 CI-4 (2026-07-18).

    Was: coalesced per-write catalog_rebuild enqueue — the one place that
    pattern lived, debounced so a write burst extended one pending job's
    not_before instead of each write re-arming a fresh one (PP-NIXOS-001
    2026-07-12 incident). Superseded: CI-2's synchronous per-item SQLite
    upsert (sqlite_catalog.upsert_catalog_row, called from every fence write
    in http_server.py's _apply_patch/_apply_ebay_write) already keeps the
    SQLite catalog — the one the inventory webui and every operator-facing
    query reads — live-accurate on every write, through every caller
    (workers included: all worker writes route through tgw.apis.fence's
    HTTP client into the same two fence functions). The remaining 3
    artifacts (full_catalog/search_catalog JSON, location_tree) are still
    full-rebuild-only (CI-5 deferred) and are now refreshed by an hourly
    systemd timer instead of a per-write trigger — see
    docs/TGW-Plan-Vault/plan/pp/PP-CATALOG-INCR-001.md. `tgw build-all` /
    `tgw catalog-rebuild` remain available for an on-demand full rebuild.

    Kept as a no-op function (not deleted) so none of this codebase's ~35
    call sites need editing — every one of them still calls this safely,
    it now simply does nothing. Returns '' instead of a job_id since no job
    is created.
    """
    return ''


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


def mark_failed(job_id: str, lease_owner: str, error: str) -> str:
    """
    Transition running → retry_wait or failed → dead_letter.

    Respects max_attempts: if attempt_count >= max_attempts, goes straight
    to dead_letter. Otherwise goes to retry_wait with exponential backoff.

    Returns 'retry_wait' or 'dead_letter' — the caller needs this to know
    whether the job chain just ended (audit#1143 #1165+#1166: callers must
    be able to detect a terminal failure to alert on it / restart a
    self-rescheduling chain; silently returning None hid that signal).
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT attempt_count, max_attempts FROM queue_jobs WHERE job_id = %s',
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                # audit#1143 #1234/#1243 follow-up: the row is gone — nothing
                # will ever retry this job_id, so this is terminal, not
                # 'retry_wait'. Report it as 'dead_letter' so the caller
                # alerts/reschedules instead of silently doing nothing.
                return 'dead_letter'
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
                transitioned = cur.rowcount > 0
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
                # audit#1143 #1246: only promote failed → dead_letter if the
                # running → failed UPDATE above actually matched this job's
                # row under our lease — otherwise (lease race) some other
                # owner's row may legitimately already be sitting in
                # 'failed' for a reason that has nothing to do with us, and
                # this promotion would wrongly claim/fast-forward it.
                transitioned = cur.rowcount > 0
                if transitioned:
                    cur.execute(
                        """
                        UPDATE queue_jobs SET state = 'dead_letter'
                         WHERE job_id = %s AND state = 'failed'
                        """,
                        (job_id,),
                    )

            if not transitioned:
                # audit#1143 #1246 (deferred #1245 finding): the lease-guarded
                # UPDATE above matched zero rows — this caller lost a lease
                # race (e.g. recover_expired_jobs() already reclaimed the
                # lease between the SELECT above and this UPDATE) and did NOT
                # actually perform the transition it's about to claim.
                # Re-query the row's real current state instead of blindly
                # returning new_state, so the caller's terminal-failure
                # handling (alerting / restarting a self-rescheduling chain)
                # reflects what's actually true in the DB.
                log.warning(
                    'mark_failed: lease race for job %s (lease_owner=%s) — '
                    '0 rows matched state=running; re-checking actual state',
                    job_id, lease_owner,
                )
                cur.execute('SELECT state FROM queue_jobs WHERE job_id = %s', (job_id,))
                actual = cur.fetchone()
                if actual is None:
                    return 'dead_letter'
                actual_state = actual['state']
                return 'dead_letter' if actual_state in ('failed', 'dead_letter') else 'retry_wait'

            return 'retry_wait' if new_state == 'retry_wait' else 'dead_letter'


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


def queue_state_summary() -> Dict[str, int]:
    """Return total job counts across all queues by broad state bucket.

    Returns {queued, processing, dead_letter} — one DB round-trip.
    ``processing`` = running + leased (actively being worked on).
    """
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE state = 'queued')                    AS queued,
                    COUNT(*) FILTER (WHERE state IN ('running', 'leased'))      AS processing,
                    COUNT(*) FILTER (WHERE state = 'dead_letter')               AS dead_letter
                  FROM queue_jobs
                """
            )
            row = cur.fetchone()
            return {'queued': row[0], 'processing': row[1], 'dead_letter': row[2]}


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


def retry_wait_breakdown() -> List[Dict[str, Any]]:
    """PP-PHOTOSYNC-001 P2: per-queue retry_wait count + oldest not_before age,
    for the ops-digest pending-liability line. A pile of retry_wait jobs with
    an old not_before is a stuck worker, not a job "waiting its turn"."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT queue_name, COUNT(*) AS n,
                       EXTRACT(EPOCH FROM (NOW() - MIN(not_before))) / 3600 AS oldest_age_hours
                  FROM queue_jobs
                 WHERE state = 'retry_wait'
                 GROUP BY queue_name
                 ORDER BY n DESC, queue_name
                """
            )
            return [{'queue_name': r['queue_name'], 'count': r['n'],
                      'oldest_age_hours': round(r['oldest_age_hours'], 1)}
                    for r in cur.fetchall()]


def morning_exposure(cutoff_hour: int = 6, tz_name: str = 'America/Los_Angeles') -> List[Dict[str, Any]]:
    """PP-PHOTOSYNC-001 P2: per-queue count of queued/retry_wait jobs whose
    not_before falls before <cutoff_hour>:00 in <tz_name> tomorrow — the
    "landmine view" of what's about to fire on fresh quota before anyone's awake."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT queue_name, COUNT(*) AS n
                  FROM queue_jobs
                 WHERE state IN ('queued', 'retry_wait')
                   AND (not_before IS NULL
                        OR not_before <= (
                            date_trunc('day', NOW() AT TIME ZONE %s)
                            + interval '1 day' + (%s || ' hours')::interval
                        ) AT TIME ZONE %s)
                 GROUP BY queue_name
                 ORDER BY n DESC, queue_name
                """,
                (tz_name, cutoff_hour, tz_name),
            )
            return [{'queue_name': r['queue_name'], 'count': r['n']} for r in cur.fetchall()]


def dead_letter_errors() -> List[Dict[str, Any]]:
    """Return (queue_name, error_detail) for every dead_letter job.

    Classification into TRANSIENT vs HARD_FAILURE happens in the caller
    (worker_base.classify_dead_letter — imported here it would be circular).
    """
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT queue_name, COALESCE(error_detail, '')
                  FROM queue_jobs
                 WHERE state = 'dead_letter'
                """
            )
            return [{'queue_name': r[0], 'error_detail': r[1]} for r in cur.fetchall()]


def zero_work_queues(stall_hours: float) -> List[Dict[str, Any]]:
    """PP-DEADLETTER-001 zero-work watchdog: queues where a worker is alive and
    eligible jobs have waited > stall_hours, yet nothing succeeded in that window
    (the ebay_sku_migrate silent-stall pattern: worker up, queue full, zero output).

    Eligibility excludes future-scheduled jobs (not_before > now) so
    self-rescheduling workers (velocity_stats, ebay_dole) don't false-positive.
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                WITH eligible AS (
                    SELECT queue_name, COUNT(*) AS waiting, MIN(created_at) AS oldest
                      FROM queue_jobs
                     WHERE state = 'queued'
                       AND (not_before IS NULL OR not_before <= NOW())
                     GROUP BY queue_name
                ), alive AS (
                    SELECT DISTINCT jsonb_array_elements_text(queues) AS queue_name
                      FROM queue_workers
                     WHERE enabled
                       AND last_heartbeat_at > NOW() - interval '10 minutes'
                ), completions AS (
                    SELECT j.queue_name, MAX(h.created_at) AS last_done
                      FROM queue_job_history h
                      JOIN queue_jobs j USING (job_id)
                     WHERE h.new_state = 'succeeded'
                     GROUP BY j.queue_name
                )
                SELECT e.queue_name,
                       e.waiting,
                       ROUND(EXTRACT(EPOCH FROM (NOW() - e.oldest)) / 3600, 1) AS oldest_wait_h,
                       ROUND(EXTRACT(EPOCH FROM (NOW() - c.last_done)) / 3600, 1) AS hours_since_done
                  FROM eligible e
                  JOIN alive a USING (queue_name)
                  LEFT JOIN completions c USING (queue_name)
                 WHERE e.oldest < NOW() - (%s * interval '1 hour')
                   AND (c.last_done IS NULL OR c.last_done < NOW() - (%s * interval '1 hour'))
                 ORDER BY e.queue_name
                """,
                (stall_hours, stall_hours),
            )
            return [dict(r) for r in cur.fetchall()]


def cancel_queued(queue_name: str) -> int:
    """Cancel all 'queued' jobs for a given queue_name — for orphan queues with
    no worker consuming them (PP-PHOTOSYNC-001 P6, todo #1121). Returns the
    number of rows affected."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """UPDATE queue_jobs SET state = 'cancelled'
                   WHERE queue_name = %s AND state = 'queued'""",
                (queue_name,),
            )
            return cur.rowcount


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


# ---------------------------------------------------------------------------
# AI usage ledger (PP-MULTIMODEL-001 / Phase 5 #2)
# ---------------------------------------------------------------------------

_AI_USAGE_DDL = """
CREATE TABLE IF NOT EXISTS ai_usage (
    id                BIGSERIAL PRIMARY KEY,
    recorded_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    task              TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    input_chars       INTEGER,
    output_chars      INTEGER,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    duration_ms       INTEGER NOT NULL,
    success           BOOLEAN NOT NULL DEFAULT true,
    error_msg         TEXT,
    sku               TEXT
)
"""

_AI_USAGE_SKU_MIGRATION = """
ALTER TABLE ai_usage ADD COLUMN IF NOT EXISTS sku TEXT
"""

_ai_usage_table_ready = False


def _ensure_ai_usage_table() -> None:
    global _ai_usage_table_ready
    if _ai_usage_table_ready:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(_AI_USAGE_DDL)
            cur.execute(_AI_USAGE_SKU_MIGRATION)
    _ai_usage_table_ready = True


def record_ai_usage(
    task: str,
    provider: str,
    model: str,
    duration_ms: int,
    *,
    input_chars: int = 0,
    output_chars: int = 0,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    success: bool = True,
    error_msg: Optional[str] = None,
    sku: Optional[str] = None,
) -> None:
    """Record one AI/LLM call. Never raises — fail-soft so callers are never blocked."""
    try:
        _ensure_ai_usage_table()
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ai_usage
                        (task, provider, model, input_chars, output_chars,
                         prompt_tokens, completion_tokens, total_tokens,
                         duration_ms, success, error_msg, sku)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (task, provider, model, input_chars, output_chars,
                     prompt_tokens, completion_tokens, total_tokens,
                     duration_ms, success, error_msg, sku or None),
                )
    except Exception as exc:
        log.warning("record_ai_usage failed: %s", exc)


def query_ai_usage(since_days: int = 7) -> List[Dict[str, Any]]:
    """Aggregate AI usage by day / task / provider / model.

    Returns rows sorted by day desc, calls desc. Each row::

        {day, task, provider, model, calls, total_ms,
         prompt_tokens, completion_tokens, total_tokens,
         input_chars, output_chars, errors}
    """
    _ensure_ai_usage_table()
    sql = """
        SELECT
            date_trunc('day', recorded_at AT TIME ZONE 'UTC')::date AS day,
            task,
            provider,
            model,
            COUNT(*)                                  AS calls,
            SUM(duration_ms)                          AS total_ms,
            SUM(prompt_tokens)                        AS prompt_tokens,
            SUM(completion_tokens)                    AS completion_tokens,
            SUM(total_tokens)                         AS total_tokens,
            SUM(input_chars)                          AS input_chars,
            SUM(output_chars)                         AS output_chars,
            COUNT(*) FILTER (WHERE NOT success)       AS errors
        FROM ai_usage
        WHERE recorded_at >= now() - (%s || ' days')::interval
        GROUP BY day, task, provider, model
        ORDER BY day DESC, calls DESC
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (str(since_days),))
            return [dict(r) for r in cur.fetchall()]


def query_ai_usage_by_sku(sku: str, since_days: int = 30) -> List[Dict[str, Any]]:
    """Aggregate AI usage for a single SKU, grouped by task / provider / model.

    Returns rows sorted by calls desc. Each row::

        {sku, task, provider, model, calls, total_ms,
         prompt_tokens, completion_tokens, total_tokens,
         input_chars, output_chars, errors}
    """
    _ensure_ai_usage_table()
    sql = """
        SELECT
            sku,
            task,
            provider,
            model,
            COUNT(*)                                  AS calls,
            SUM(duration_ms)                          AS total_ms,
            SUM(prompt_tokens)                        AS prompt_tokens,
            SUM(completion_tokens)                    AS completion_tokens,
            SUM(total_tokens)                         AS total_tokens,
            SUM(input_chars)                          AS input_chars,
            SUM(output_chars)                         AS output_chars,
            COUNT(*) FILTER (WHERE NOT success)       AS errors
        FROM ai_usage
        WHERE sku = %s
          AND recorded_at >= now() - (%s || ' days')::interval
        GROUP BY sku, task, provider, model
        ORDER BY calls DESC
    """
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (sku, str(since_days)))
            return [dict(r) for r in cur.fetchall()]


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


def active_jobs_for_sku(sku: str, queue_names: List[str]) -> List[str]:
    """Queue names with an active (queued/running/leased) job for this SKU.

    Session 42: lets order-sensitive workers (ebay_stage, ebay_publish) wait for
    in-flight upstream stages instead of racing them — 'List on eBay' used to
    publish the OLD staged offer while the fresh draft was still generating."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """SELECT DISTINCT queue_name FROM queue_jobs
                    WHERE payload_json->>'sku' = %s
                      AND queue_name = ANY(%s)
                      AND state IN ('queued', 'running', 'leased')""",
                (sku, queue_names),
            )
            return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Agent run trace ledger (PP-AGENTTRACE-001 Phase 1)
# ---------------------------------------------------------------------------
#
# Metadata index for every agent run (Claude Code session/subagent, tgw-coder,
# nix-flake-maintainer, Aider, ...). Raw transcripts are archived separately
# under /opt/TGW/var/agent-traces/ (see tgw.logging.archive_transcript) — this
# table is the derived/queryable index per the Data Charter raw/derived split.
#
# NOTE on schema.sql drift (same known gap as ai_usage above, named explicitly
# here instead of silently repeated): this in-code DDL is the one actually
# applied at runtime (self-apply, guarded by _agent_runs_table_ready). The
# schema.sql copy of this table is bootstrap documentation only and is NOT
# kept in sync automatically — if you change one, remember to change the
# other by hand, same as the pre-existing ai_usage drift.

_AGENT_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS agent_runs (
    run_id          TEXT PRIMARY KEY,
    parent_run_id   TEXT REFERENCES agent_runs(run_id),
    agent_type      TEXT NOT NULL,
    todo_id         INTEGER,
    pp_ref          TEXT,
    host            TEXT,
    git_branch      TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at        TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'killed', 'escalated')),
    summary         TEXT,
    transcript_path TEXT
)
"""

_agent_runs_table_ready = False


def _ensure_agent_runs_table() -> None:
    global _agent_runs_table_ready
    if _agent_runs_table_ready:
        return
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(_AGENT_RUNS_DDL)
    _agent_runs_table_ready = True


def _enqueue_agent_run_render() -> None:
    """Coalesced Obsidian re-render on any agent_runs mutation (PP-AGENTTRACE-001
    Phase 2). Same pattern as todo.py's _enqueue_plan_render(): dedupe_key +
    30s not_before so rapid successive start/end calls collapse into one
    render. Never lets a queue problem break the actual trace-recording
    operation (start_agent_run/end_agent_run)."""
    try:
        enqueue_job(
            queue_name='agent_run_render',
            payload={'reason': 'agent_run_mutation'},
            dedupe_key='agent_run_render:pending',
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass


def start_agent_run(
    agent_type: str,
    *,
    parent_run_id: Optional[str] = None,
    todo_id: Optional[int] = None,
    pp_ref: Optional[str] = None,
    host: Optional[str] = None,
    git_branch: Optional[str] = None,
) -> str:
    """Record the start of one agent run.

    Generates and returns a new run_id (uuid4 hex string) — the caller needs
    this value before the row exists, e.g. to pass as parent_run_id when
    dispatching a nested subagent. Unlike record_ai_usage(), this does NOT
    fail-soft: a caller that can't record a run start needs to know, since
    the returned run_id is the load-bearing handle for everything that
    follows (tgw trace end, transcript archival, nested runs).
    """
    _ensure_agent_runs_table()
    run_id = uuid.uuid4().hex
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs
                    (run_id, parent_run_id, agent_type, todo_id, pp_ref, host, git_branch)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (run_id, parent_run_id, agent_type, todo_id, pp_ref, host, git_branch),
            )
    _enqueue_agent_run_render()
    return run_id


def end_agent_run(
    run_id: str,
    *,
    status: str,
    summary: Optional[str] = None,
    transcript_path: Optional[str] = None,
) -> None:
    """Record the end of one agent run: sets ended_at=NOW(), status, summary,
    transcript_path. status must be one of the agent_runs CHECK values
    ('running', 'completed', 'failed', 'killed', 'escalated') — an invalid
    value raises psycopg2.errors.CheckViolation, it is not swallowed.

    Raises ValueError if run_id does not exist — an UPDATE that matches zero
    rows must never look like a successful end-of-run (same class of bug as
    invariant C14: a correction that doesn't take effect must be visibly
    reported as failed, never silently accepted)."""
    _ensure_agent_runs_table()
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                UPDATE agent_runs
                   SET ended_at = NOW(),
                       status = %s,
                       summary = %s,
                       transcript_path = %s
                 WHERE run_id = %s
                """,
                (status, summary, transcript_path, run_id),
            )
            if cur.rowcount == 0:
                raise ValueError(f"end_agent_run: no agent_runs row found for run_id={run_id!r}")
    _enqueue_agent_run_render()


def get_agent_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Read back one agent_runs row by run_id, or None if not found.

    Not part of the packet's core "two functions" spec, but a minimal read
    helper is needed for the round-trip unit tests below and is a natural
    building block for Phase 2/3 (Obsidian render, /form/runs UI) — flagged
    here rather than added silently."""
    _ensure_agent_runs_table()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM agent_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
            return dict(row) if row else None


def list_agent_runs(limit: int = 200) -> List[Dict[str, Any]]:
    """List agent_runs rows, most-recently-started first, capped at `limit`.

    This is a "recent activity" view for the Obsidian render (PP-AGENTTRACE-001
    Phase 2), NOT a full historical dump — `limit` defaults to 200 and callers
    should not assume this returns every run ever recorded. If the true row
    count exceeds `limit`, older rows are silently excluded from the returned
    list (by design, not a bug) — a future need for full history should use a
    dedicated paginated/date-ranged query, not a larger limit on this one.
    """
    _ensure_agent_runs_table()
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT %s",
                (limit,),
            )
            return [dict(row) for row in cur.fetchall()]
