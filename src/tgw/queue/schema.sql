-- TGW queue state machine schema
-- PostgreSQL-first, SKIP LOCKED-friendly, lease-based distributed processing

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'queue_job_state') THEN
        CREATE TYPE queue_job_state AS ENUM (
            'queued',
            'leased',
            'running',
            'retry_wait',
            'succeeded',
            'failed',
            'dead_letter',
            'cancelled'
        );
    END IF;
END$$;

CREATE TABLE IF NOT EXISTS queue_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dedupe_key TEXT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    handler_family TEXT NOT NULL,
    queue_name TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    state queue_job_state NOT NULL DEFAULT 'queued',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    run_mode TEXT NOT NULL DEFAULT 'immediate',
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    not_before TIMESTAMPTZ,
    remaining_runs INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    lease_owner TEXT,
    lease_token UUID,
    lease_expires_at TIMESTAMPTZ,
    last_heartbeat_at TIMESTAMPTZ,
    error_code TEXT,
    error_detail TEXT,
    parent_job_id UUID REFERENCES queue_jobs(job_id) ON DELETE SET NULL,
    trace_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CHECK (max_attempts >= 0),
    CHECK (attempt_count >= 0),
    CHECK (remaining_runs IS NULL OR remaining_runs >= 0),
    CHECK (run_mode IN ('immediate', 'scheduled', 'repeat'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_jobs_dedupe_key_active
    ON queue_jobs(dedupe_key)
    WHERE dedupe_key IS NOT NULL
      AND state NOT IN ('succeeded','failed','dead_letter','cancelled');

-- todo #1618 / PP-STATEMACHINE-001: independent DB-level backstop —
-- "at most one queued/retry_wait row per dedupe_key". NOT used as an
-- ON CONFLICT arbiter: enqueue_job()'s debounce=True path does NOT use
-- INSERT ... ON CONFLICT at all (see state_machine.py's enqueue_job()
-- docstring, "Fix actually used" — a real Postgres 17 arbiter-inference
-- gotcha, verified live, made that approach unworkable: ON CONFLICT
-- accepts any unique index whose predicate is *implied by* the specified
-- WHERE clause as an eligible arbiter, so the broad index above stayed
-- eligible too and kept silently corrupting a self-rescheduling worker's
-- own in-flight leased/running row regardless of which index the ON
-- CONFLICT clause named). The debounce path instead does an explicit
-- pg_advisory_xact_lock-guarded read-then-write in Python: look up any
-- existing queued/retry_wait row and UPDATE it if found, else INSERT a
-- fresh row (with dedupe_key=NULL if a leased/running row already holds
-- the real key, so it can't collide with the broad index above). This
-- index exists purely as a real DB-level safety net for that same
-- invariant, independent of the application logic. Do NOT widen this to
-- match the broad index above. See uq_queue_jobs_dedupe_key_active.
CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_jobs_dedupe_key_pending
    ON queue_jobs(dedupe_key)
    WHERE dedupe_key IS NOT NULL
      AND state IN ('queued', 'retry_wait');

CREATE INDEX IF NOT EXISTS idx_queue_jobs_runnable
    ON queue_jobs(queue_name, priority, run_at, created_at)
    WHERE state = 'queued';

CREATE INDEX IF NOT EXISTS idx_queue_jobs_retry_wait
    ON queue_jobs(not_before, priority, created_at)
    WHERE state = 'retry_wait';

CREATE INDEX IF NOT EXISTS idx_queue_jobs_leases
    ON queue_jobs(state, lease_expires_at)
    WHERE state IN ('leased', 'running');

CREATE INDEX IF NOT EXISTS idx_queue_jobs_entity
    ON queue_jobs(entity_type, entity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_queue_jobs_trace
    ON queue_jobs(trace_id)
    WHERE trace_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS operator_authorities (
    authority_id UUID PRIMARY KEY,
    operator_identity TEXT NOT NULL, surface TEXT NOT NULL,
    entity_id TEXT NOT NULL, goal_profile_id TEXT NOT NULL,
    goal_profile_version TEXT NOT NULL, object_generation TEXT NOT NULL,
    pre_authority_condition_hash TEXT NOT NULL, content_identity TEXT NOT NULL,
    provider_identity TEXT NOT NULL, scopes TEXT[] NOT NULL,
    issued_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
    superseded_at TIMESTAMPTZ, superseded_by TEXT,
    CHECK (expires_at > issued_at), CHECK (cardinality(scopes) > 0)
);

CREATE INDEX IF NOT EXISTS idx_operator_authorities_entity
    ON operator_authorities(entity_id, issued_at DESC);

CREATE TABLE IF NOT EXISTS provider_effects (
    effect_id TEXT PRIMARY KEY CHECK (effect_id ~ '^[0-9a-f]{64}$'),
    provider TEXT NOT NULL, operation TEXT NOT NULL,
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    object_generation TEXT NOT NULL, graph_id TEXT NOT NULL,
    treatment_id TEXT NOT NULL, treatment_version TEXT NOT NULL,
    condition_hash TEXT NOT NULL,
    request_json JSONB NOT NULL, authority_json JSONB NOT NULL,
    state TEXT NOT NULL CHECK (state IN
        ('reserved','dispatched','succeeded','rejected','ambiguous','reconciliation_required')),
    result_json JSONB, error_detail TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    UNIQUE (provider, operation, entity_type, entity_id, object_generation)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_provider_effects_unresolved_entity
    ON provider_effects(provider, operation, entity_type, entity_id)
    WHERE state IN ('reserved','dispatched','ambiguous','reconciliation_required');

CREATE TABLE IF NOT EXISTS queue_workers (
    worker_id TEXT PRIMARY KEY,
    node_class TEXT NOT NULL,
    host_name TEXT,
    pid INTEGER,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    queues JSONB NOT NULL DEFAULT '[]'::jsonb,
    handler_families JSONB NOT NULL DEFAULT '[]'::jsonb,
    max_parallel INTEGER NOT NULL DEFAULT 1,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    heartbeat_interval_seconds INTEGER NOT NULL DEFAULT 15,
    last_heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (max_parallel > 0),
    CHECK (heartbeat_interval_seconds > 0)
);

CREATE TABLE IF NOT EXISTS queue_job_history (
    history_id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES queue_jobs(job_id) ON DELETE CASCADE,
    old_state queue_job_state,
    new_state queue_job_state NOT NULL,
    transition TEXT NOT NULL,
    worker_id TEXT,
    message TEXT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_queue_jobs_updated_at ON queue_jobs;
CREATE TRIGGER trg_queue_jobs_updated_at
BEFORE UPDATE ON queue_jobs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_queue_workers_updated_at ON queue_workers;
CREATE TRIGGER trg_queue_workers_updated_at
BEFORE UPDATE ON queue_workers
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE OR REPLACE FUNCTION queue_record_history()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.state IS DISTINCT FROM OLD.state THEN
        INSERT INTO queue_job_history(job_id, old_state, new_state, transition, worker_id, message, details)
        VALUES (
            NEW.job_id,
            OLD.state,
            NEW.state,
            COALESCE(current_setting('tgw.queue_transition', true), 'state_change'),
            COALESCE(current_setting('tgw.worker_id', true), NULL),
            COALESCE(current_setting('tgw.transition_message', true), NULL),
            '{}'::jsonb
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_queue_jobs_history ON queue_jobs;
CREATE TRIGGER trg_queue_jobs_history
AFTER UPDATE ON queue_jobs
FOR EACH ROW EXECUTE FUNCTION queue_record_history();

-- Ops-query convenience views.
CREATE OR REPLACE VIEW v_dead_letters AS
SELECT j.job_id, j.queue_name, j.entity_type, j.entity_id, j.operation,
       j.error_code, j.error_detail, j.payload_json,
       j.attempt_count, j.max_attempts, j.created_at, j.finished_at
  FROM queue_jobs j
 WHERE j.state = 'dead_letter';

CREATE OR REPLACE VIEW v_job_history AS
SELECT h.history_id, h.job_id,
       j.queue_name, j.entity_type, j.entity_id, j.operation,
       j.state AS current_state,
       h.old_state, h.new_state, h.transition, h.worker_id, h.message, h.details,
       j.error_code, j.error_detail, j.payload_json,
       h.created_at
  FROM queue_job_history h
  JOIN queue_jobs j USING (job_id);

-- PP-QUEUESTATS-001: real date-scoped per-queue outcome stats, sourced from
-- queue_job_history (an append-only, never-reset ledger of every state
-- transition — unlike queue_jobs.finished_at, which requeue_with_backoff()
-- clears back to NULL on retry, so it cannot answer "how many succeeded
-- *today*" once a job has been retried). Day boundary is midnight
-- America/Los_Angeles, matching quota.py's existing eBay-reset convention.
-- Grouped by hour (not collapsed to a single daily number) so this can serve
-- as the baseline data for future per-queue surge/anomaly detection
-- (Dave, 2026-07-14) without a schema change when that's built — see
-- TGW-Master-Plan.md PP-QUEUESTATS-001. Only terminal-ish outcome states
-- (succeeded/failed/dead_letter) are counted; queued/leased/running/
-- retry_wait are current-state counts (queue_status()), not historical
-- events, and stay out of this view on purpose.
CREATE INDEX IF NOT EXISTS idx_queue_job_history_created_at
    ON queue_job_history (created_at);

CREATE OR REPLACE VIEW queue_daily_stats AS
SELECT
    j.queue_name,
    (h.created_at AT TIME ZONE 'America/Los_Angeles')::date            AS stat_date,
    date_trunc('hour', h.created_at AT TIME ZONE 'America/Los_Angeles') AS stat_hour,
    h.new_state                                                         AS state,
    COUNT(*)::bigint                                                    AS job_count
  FROM queue_job_history h
  JOIN queue_jobs j ON j.job_id = h.job_id
 WHERE h.new_state IN ('succeeded', 'failed', 'dead_letter')
 GROUP BY j.queue_name, stat_date, stat_hour, h.new_state;

-- Claim next runnable jobs using FOR UPDATE SKIP LOCKED.
CREATE OR REPLACE FUNCTION claim_queue_jobs(
    p_worker_id TEXT,
    p_queue_name TEXT,
    p_limit INTEGER DEFAULT 1,
    p_lease_seconds INTEGER DEFAULT 300
)
RETURNS SETOF queue_jobs AS $$
BEGIN
    RETURN QUERY
    WITH candidates AS (
        SELECT q.job_id
        FROM queue_jobs q
        WHERE q.queue_name = p_queue_name
          AND q.state = 'queued'
          AND q.run_at <= NOW()
          AND (q.not_before IS NULL OR q.not_before <= NOW())
        ORDER BY q.priority ASC, q.run_at ASC, q.created_at ASC
        LIMIT p_limit
        FOR UPDATE SKIP LOCKED
    )
    UPDATE queue_jobs q
       SET state = 'leased',
           lease_owner = p_worker_id,
           lease_token = gen_random_uuid(),
           lease_expires_at = NOW() + make_interval(secs => p_lease_seconds),
           last_heartbeat_at = NOW(),
           started_at = COALESCE(started_at, NOW()),
           attempt_count = attempt_count + 1
      FROM candidates c
     WHERE q.job_id = c.job_id
     RETURNING q.*;
END;
$$ LANGUAGE plpgsql;

-- Requeue expired leases and promote mature retry_wait jobs.
CREATE OR REPLACE FUNCTION recover_expired_jobs()
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
    v_retry INTEGER;
BEGIN
    -- Exhausted lease-expired jobs go straight to dead_letter (never rest in
    -- 'failed' — mirrors mark_failed's immediate failed->dead_letter cascade)
    -- otherwise they become invisible zombies: missed by dead_letter_count,
    -- dead-letter CLI/MCP tools, and the stall watchdog.
    UPDATE queue_jobs
       SET state = CASE
            WHEN attempt_count >= max_attempts THEN 'dead_letter'::queue_job_state
            ELSE 'queued'::queue_job_state
           END,
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           last_heartbeat_at = NULL,
           not_before = CASE
               WHEN attempt_count >= max_attempts THEN not_before
               ELSE NOW()
           END,
           error_code = COALESCE(error_code, 'LEASE_EXPIRED'),
           error_detail = COALESCE(error_detail, 'Lease expired before completion'),
           finished_at = CASE
               WHEN attempt_count >= max_attempts THEN NOW()
               ELSE finished_at
           END
     WHERE state IN ('leased', 'running')
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at < NOW();

    GET DIAGNOSTICS v_count = ROW_COUNT;

    -- Promote retry_wait jobs whose not_before has passed back to queued
    UPDATE queue_jobs
       SET state = 'queued'::queue_job_state,
           not_before = NULL
     WHERE state = 'retry_wait'
       AND not_before IS NOT NULL
       AND not_before <= NOW();

    GET DIAGNOSTICS v_retry = ROW_COUNT;

    RETURN v_count + v_retry;
END;
$$ LANGUAGE plpgsql;

-- Multi-agent TODO tracker (PP-TODO-001 / PP-PLANDB-001).
-- id uses SERIAL so the sequence survives pg_restore and tgw-db-init re-runs.
CREATE TABLE IF NOT EXISTS todo_items (
    id          SERIAL PRIMARY KEY,
    agent       TEXT NOT NULL DEFAULT 'claude',
    priority    INTEGER NOT NULL DEFAULT 50,
    body        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'session',
    tags        TEXT[] NOT NULL DEFAULT '{}',
    added_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    done_at     TIMESTAMPTZ,
    pp_ref      TEXT,
    depends_on  INTEGER[] NOT NULL DEFAULT '{}',
    plan_anchor TEXT,
    reasoning   TEXT NOT NULL DEFAULT 'normal'
                CHECK (reasoning IN ('high', 'normal', 'low'))
);

CREATE INDEX IF NOT EXISTS idx_todo_items_open
    ON todo_items(agent, priority, id)
    WHERE done_at IS NULL;

-- AI usage audit log (PP-MULTIMODEL-001).
-- NOTE (PP-AGENTTRACE-001, 2026-07-20): this copy has already drifted from
-- the DDL actually applied at runtime (state_machine.py's _AI_USAGE_DDL /
-- _ensure_ai_usage_table()) — column names/types differ. Nothing keeps the
-- two in sync automatically. Named here rather than silently repeated for
-- agent_runs below.
CREATE TABLE IF NOT EXISTS ai_usage (
    id          SERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    model       TEXT NOT NULL,
    task        TEXT,
    prompt_tokens  INTEGER,
    output_tokens  INTEGER,
    cost_usd    NUMERIC(10,6)
);

-- Agent run trace ledger (PP-AGENTTRACE-001 Phase 1).
-- BOOTSTRAP DOCUMENTATION ONLY — the DDL actually applied at runtime lives
-- in state_machine.py (_AGENT_RUNS_DDL / _ensure_agent_runs_table(), the
-- self-apply pattern). This copy is not kept in sync automatically; if you
-- change one, change the other by hand (same known gap as ai_usage above).
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
);
