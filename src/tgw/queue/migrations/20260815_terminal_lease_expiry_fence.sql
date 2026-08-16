-- Queue migration: terminal transitions must not complete an expired lease.
--
-- ``live_schema.sql`` is a pg_dump-style schema snapshot.  It documents the
-- resulting database shape but is not replayable against an installed queue.
-- This is the executable, idempotent migration for deployments that predate
-- the terminal lease-expiry fence.  CREATE OR REPLACE preserves the function
-- identity and grants while replacing only its definition.

CREATE OR REPLACE FUNCTION public.fail_job(
    p_job_id uuid,
    p_worker_id text,
    p_lease_token uuid,
    p_error_code text,
    p_error_detail text,
    p_retry_delay_seconds integer DEFAULT 60
) RETURNS public.queue_jobs
    LANGUAGE plpgsql
AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = CASE
            WHEN attempt_count >= max_attempts THEN 'failed'::queue_job_state
            ELSE 'retry_wait'::queue_job_state
           END,
           finished_at = CASE
            WHEN attempt_count >= max_attempts THEN NOW()
            ELSE finished_at
           END,
           not_before = CASE
            WHEN attempt_count >= max_attempts THEN not_before
            ELSE NOW() + make_interval(secs => p_retry_delay_seconds)
           END,
           last_heartbeat_at = NOW(),
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           error_code = p_error_code,
           error_detail = p_error_detail
     WHERE job_id = p_job_id
       AND state = 'running'
       AND lease_owner = p_worker_id
       AND lease_token = p_lease_token
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at > NOW()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'fail_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;


CREATE OR REPLACE FUNCTION public.succeed_job(
    p_job_id uuid,
    p_worker_id text,
    p_lease_token uuid,
    p_result jsonb DEFAULT '{}'::jsonb
) RETURNS public.queue_jobs
    LANGUAGE plpgsql
AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = 'succeeded',
           payload_json = COALESCE(payload_json, '{}'::jsonb)
               || jsonb_build_object('result', COALESCE(p_result, '{}'::jsonb)),
           finished_at = NOW(),
           last_heartbeat_at = NOW(),
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           error_code = NULL,
           error_detail = NULL,
           not_before = NULL
     WHERE job_id = p_job_id
       AND state = 'running'
       AND lease_owner = p_worker_id
       AND lease_token = p_lease_token
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at > NOW()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'succeed_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;
