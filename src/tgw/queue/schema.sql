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

CREATE UNIQUE INDEX IF NOT EXISTS uq_queue_jobs_dedupe_key
    ON queue_jobs(dedupe_key)
    WHERE dedupe_key IS NOT NULL;

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

-- Requeue expired leases.
CREATE OR REPLACE FUNCTION recover_expired_jobs()
RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE queue_jobs
       SET state = CASE
            WHEN attempt_count >= max_attempts THEN 'failed'::queue_job_state
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
           error_detail = COALESCE(error_detail, 'Lease expired before completion')
     WHERE state IN ('leased', 'running')
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at < NOW();

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;
