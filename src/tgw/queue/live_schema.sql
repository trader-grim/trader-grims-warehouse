--
-- PostgreSQL database dump
--

\restrict m7vFfl7xFLdqtbYkAY592oojjlNWeqzesQmubOtcNm6rEMz04wEXfTFX1i64XEd

-- Dumped from database version 17.10 (Debian 17.10-0+deb13u1)
-- Dumped by pg_dump version 17.10 (Debian 17.10-0+deb13u1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: queue_job_state; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.queue_job_state AS ENUM (
    'queued',
    'leased',
    'running',
    'retry_wait',
    'succeeded',
    'failed',
    'dead_letter',
    'cancelled'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: queue_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queue_jobs (
    job_id uuid DEFAULT gen_random_uuid() NOT NULL,
    dedupe_key text,
    entity_type text NOT NULL,
    entity_id text NOT NULL,
    operation text NOT NULL,
    handler_family text NOT NULL,
    queue_name text NOT NULL,
    priority integer DEFAULT 100 NOT NULL,
    state public.queue_job_state DEFAULT 'queued'::public.queue_job_state NOT NULL,
    payload_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    run_mode text DEFAULT 'immediate'::text NOT NULL,
    run_at timestamp with time zone DEFAULT now() NOT NULL,
    not_before timestamp with time zone,
    remaining_runs integer,
    attempt_count integer DEFAULT 0 NOT NULL,
    max_attempts integer DEFAULT 5 NOT NULL,
    lease_owner text,
    lease_token uuid,
    lease_expires_at timestamp with time zone,
    last_heartbeat_at timestamp with time zone,
    error_code text,
    error_detail text,
    parent_job_id uuid,
    trace_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    CONSTRAINT queue_jobs_attempt_count_check CHECK ((attempt_count >= 0)),
    CONSTRAINT queue_jobs_max_attempts_check CHECK ((max_attempts >= 0)),
    CONSTRAINT queue_jobs_remaining_runs_check CHECK (((remaining_runs IS NULL) OR (remaining_runs >= 0))),
    CONSTRAINT queue_jobs_run_mode_check CHECK ((run_mode = ANY (ARRAY['immediate'::text, 'scheduled'::text, 'repeat'::text])))
);

CREATE TABLE public.operator_authorities (
    authority_id uuid PRIMARY KEY,
    operator_identity text NOT NULL, surface text NOT NULL,
    entity_id text NOT NULL, goal_profile_id text NOT NULL,
    goal_profile_version text NOT NULL, object_generation text NOT NULL,
    pre_authority_condition_hash text NOT NULL, content_identity text NOT NULL,
    provider_identity text NOT NULL, scopes text[] NOT NULL,
    issued_at timestamptz NOT NULL, expires_at timestamptz NOT NULL,
    superseded_at timestamptz, superseded_by text,
    CHECK (expires_at > issued_at), CHECK (cardinality(scopes) > 0)
);

CREATE INDEX idx_operator_authorities_entity
    ON public.operator_authorities USING btree (entity_id, issued_at DESC);

CREATE TABLE public.provider_effects (
    effect_id text PRIMARY KEY CHECK (effect_id ~ '^[0-9a-f]{64}$'),
    provider text NOT NULL, operation text NOT NULL,
    entity_type text NOT NULL, entity_id text NOT NULL,
    object_generation text NOT NULL, graph_id text NOT NULL,
    treatment_id text NOT NULL, treatment_version text NOT NULL,
    condition_hash text NOT NULL,
    request_json jsonb NOT NULL, authority_json jsonb NOT NULL,
    state text NOT NULL CHECK (state IN
        ('reserved','dispatched','succeeded','rejected','ambiguous','reconciliation_required')),
    result_json jsonb, error_detail text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    dispatched_at timestamptz, finished_at timestamptz,
    UNIQUE (provider, operation, entity_type, entity_id, object_generation)
);

CREATE UNIQUE INDEX uq_provider_effects_unresolved_entity
    ON public.provider_effects USING btree
    (provider, operation, entity_type, entity_id)
    WHERE (state = ANY (ARRAY['reserved'::text, 'dispatched'::text,
        'ambiguous'::text, 'reconciliation_required'::text]));

CREATE TABLE public.provider_observations (
    observation_id text PRIMARY KEY CHECK (observation_id ~ '^[0-9a-f]{64}$'),
    schema_id text NOT NULL CHECK (schema_id = 'provider-observation/v1'),
    observation_type text NOT NULL,
    provider text NOT NULL, provider_identity text NOT NULL,
    sku text NOT NULL, offer_id text NOT NULL,
    object_generation text NOT NULL, graph_id text NOT NULL,
    condition_hash text NOT NULL, content_identity text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN
        ('corroborated','contradicted','indeterminate')),
    evidence_json jsonb NOT NULL,
    observed_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);


--
-- Name: cancel_job(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.cancel_job(p_job_id uuid, p_message text DEFAULT NULL::text) RETURNS public.queue_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = 'cancelled',
           finished_at = COALESCE(finished_at, NOW()),
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           last_heartbeat_at = NOW(),
           error_code = CASE WHEN p_message IS NULL THEN error_code ELSE 'CANCELLED' END,
           error_detail = COALESCE(p_message, error_detail)
     WHERE job_id = p_job_id
       AND state IN ('queued', 'leased', 'running', 'retry_wait', 'failed', 'dead_letter')
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'cancel_job denied for job %', p_job_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: claim_queue_jobs(text, text, integer, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.claim_queue_jobs(p_worker_id text, p_queue_name text, p_limit integer DEFAULT 1, p_lease_seconds integer DEFAULT 300) RETURNS SETOF public.queue_jobs
    LANGUAGE plpgsql
    AS $$
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
           attempt_count = attempt_count + 1,
           error_code = NULL,
           error_detail = NULL,
           not_before = NULL,
           finished_at = NULL
      FROM candidates c
     WHERE q.job_id = c.job_id
     RETURNING q.*;
END;
$$;


--
-- Name: fail_job(uuid, text, uuid, text, text, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.fail_job(p_job_id uuid, p_worker_id text, p_lease_token uuid, p_error_code text, p_error_detail text, p_retry_delay_seconds integer DEFAULT 60) RETURNS public.queue_jobs
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
       -- NOW() is fixed at transaction start.  A terminal operation that
       -- waits behind a lock must re-check wall clock time at its statement.
       AND lease_expires_at > clock_timestamp()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'fail_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: heartbeat_job(uuid, text, uuid, integer); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.heartbeat_job(p_job_id uuid, p_worker_id text, p_lease_token uuid, p_extend_seconds integer DEFAULT 300) RETURNS public.queue_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET last_heartbeat_at = NOW(),
           lease_expires_at = NOW() + make_interval(secs => p_extend_seconds)
     WHERE job_id = p_job_id
       AND state IN ('leased', 'running')
       AND lease_owner = p_worker_id
       AND lease_token = p_lease_token
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at >= NOW()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'heartbeat_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: queue_record_history(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.queue_record_history() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: recover_expired_jobs(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.recover_expired_jobs() RETURNS integer
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_count INTEGER;
    v_retry INTEGER;
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
           error_detail = COALESCE(error_detail, 'Lease expired before completion'),
           finished_at = CASE
               WHEN attempt_count >= max_attempts THEN NOW()
               ELSE finished_at
           END
     WHERE state IN ('leased', 'running')
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at < NOW();

    GET DIAGNOSTICS v_count = ROW_COUNT;

    UPDATE queue_jobs
       SET state = 'queued'::queue_job_state,
           not_before = NULL
     WHERE state = 'retry_wait'
       AND not_before IS NOT NULL
       AND not_before <= NOW();

    GET DIAGNOSTICS v_retry = ROW_COUNT;

    RETURN v_count + v_retry;
END;
$$;


--
-- Name: requeue_job(uuid, text); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.requeue_job(p_job_id uuid, p_message text DEFAULT NULL::text) RETURNS public.queue_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = 'queued',
           lease_owner = NULL,
           lease_token = NULL,
           lease_expires_at = NULL,
           last_heartbeat_at = NULL,
           not_before = NOW(),
           finished_at = NULL,
           error_code = NULL,
           error_detail = NULL
     WHERE job_id = p_job_id
       AND state IN ('retry_wait', 'failed', 'dead_letter', 'cancelled', 'leased', 'running')
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'requeue_job denied for job %', p_job_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: set_updated_at(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.set_updated_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


--
-- Name: start_job(uuid, text, uuid); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.start_job(p_job_id uuid, p_worker_id text, p_lease_token uuid) RETURNS public.queue_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = 'running',
           last_heartbeat_at = NOW()
     WHERE job_id = p_job_id
       AND state = 'leased'
       AND lease_owner = p_worker_id
       AND lease_token = p_lease_token
       AND lease_expires_at IS NOT NULL
       AND lease_expires_at >= NOW()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'start_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: succeed_job(uuid, text, uuid, jsonb); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.succeed_job(p_job_id uuid, p_worker_id text, p_lease_token uuid, p_result jsonb DEFAULT '{}'::jsonb) RETURNS public.queue_jobs
    LANGUAGE plpgsql
    AS $$
DECLARE
    v_job queue_jobs;
BEGIN
    UPDATE queue_jobs
       SET state = 'succeeded',
           payload_json = COALESCE(payload_json, '{}'::jsonb) || jsonb_build_object('result', COALESCE(p_result, '{}'::jsonb)),
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
       -- See fail_job: the terminal fence must not accept a lease that
       -- expired while this transaction was waiting.
       AND lease_expires_at > clock_timestamp()
     RETURNING * INTO v_job;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'succeed_job denied for job %, worker %', p_job_id, p_worker_id;
    END IF;

    RETURN v_job;
END;
$$;


--
-- Name: queue_job_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queue_job_history (
    history_id bigint NOT NULL,
    job_id uuid NOT NULL,
    old_state public.queue_job_state,
    new_state public.queue_job_state NOT NULL,
    transition text NOT NULL,
    worker_id text,
    message text,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: queue_job_history_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.queue_job_history_history_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: queue_job_history_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.queue_job_history_history_id_seq OWNED BY public.queue_job_history.history_id;


--
-- Name: queue_workers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.queue_workers (
    worker_id text NOT NULL,
    node_class text NOT NULL,
    host_name text,
    pid integer,
    capabilities jsonb DEFAULT '[]'::jsonb NOT NULL,
    queues jsonb DEFAULT '[]'::jsonb NOT NULL,
    handler_families jsonb DEFAULT '[]'::jsonb NOT NULL,
    max_parallel integer DEFAULT 1 NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    heartbeat_interval_seconds integer DEFAULT 15 NOT NULL,
    last_heartbeat_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT queue_workers_heartbeat_interval_seconds_check CHECK ((heartbeat_interval_seconds > 0)),
    CONSTRAINT queue_workers_max_parallel_check CHECK ((max_parallel > 0))
);


--
-- Name: queue_job_history history_id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_job_history ALTER COLUMN history_id SET DEFAULT nextval('public.queue_job_history_history_id_seq'::regclass);


--
-- Name: queue_job_history queue_job_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_job_history
    ADD CONSTRAINT queue_job_history_pkey PRIMARY KEY (history_id);


--
-- Name: queue_jobs queue_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_jobs
    ADD CONSTRAINT queue_jobs_pkey PRIMARY KEY (job_id);


--
-- Name: queue_workers queue_workers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_workers
    ADD CONSTRAINT queue_workers_pkey PRIMARY KEY (worker_id);


--
-- Name: idx_queue_jobs_entity; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_jobs_entity ON public.queue_jobs USING btree (entity_type, entity_id, created_at DESC);


--
-- Name: idx_queue_jobs_leases; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_jobs_leases ON public.queue_jobs USING btree (state, lease_expires_at) WHERE (state = ANY (ARRAY['leased'::public.queue_job_state, 'running'::public.queue_job_state]));


--
-- Name: idx_queue_jobs_retry_wait; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_jobs_retry_wait ON public.queue_jobs USING btree (not_before, priority, created_at) WHERE (state = 'retry_wait'::public.queue_job_state);


--
-- Name: idx_queue_jobs_runnable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_jobs_runnable ON public.queue_jobs USING btree (queue_name, priority, run_at, created_at) WHERE (state = 'queued'::public.queue_job_state);


--
-- Name: idx_queue_jobs_trace; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_queue_jobs_trace ON public.queue_jobs USING btree (trace_id) WHERE (trace_id IS NOT NULL);


--
-- Name: uq_queue_jobs_dedupe_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_queue_jobs_dedupe_key_active ON public.queue_jobs USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL AND state NOT IN ('succeeded','failed','dead_letter','cancelled'));


--
-- Name: uq_queue_jobs_dedupe_key_pending; Type: INDEX; Schema: public; Owner: -
--
-- todo #1618 / PP-STATEMACHINE-001: independent DB-level backstop — "at
-- most one queued/retry_wait row per dedupe_key". NOT used as an ON
-- CONFLICT arbiter — enqueue_job()'s debounce=True path does not use
-- INSERT ... ON CONFLICT at all (a real Postgres arbiter-inference gotcha,
-- verified live, made that approach unworkable — see schema.sql for the
-- full rationale, and state_machine.py's enqueue_job() docstring "Fix
-- actually used"). The debounce path instead does an explicit
-- pg_advisory_xact_lock-guarded read-then-write in Python; this index is
-- just a real DB-level safety net for the same invariant.
-- NOT YET APPLIED to the live production database as of this commit — see
-- the #1618 result manifest; applying it is the stitch/merge step's job,
-- not this commit's.
--

CREATE UNIQUE INDEX uq_queue_jobs_dedupe_key_pending ON public.queue_jobs USING btree (dedupe_key) WHERE (dedupe_key IS NOT NULL AND state IN ('queued', 'retry_wait'));


--
-- Name: queue_jobs trg_queue_jobs_history; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_queue_jobs_history AFTER UPDATE ON public.queue_jobs FOR EACH ROW EXECUTE FUNCTION public.queue_record_history();


--
-- Name: queue_jobs trg_queue_jobs_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_queue_jobs_updated_at BEFORE UPDATE ON public.queue_jobs FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: queue_workers trg_queue_workers_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_queue_workers_updated_at BEFORE UPDATE ON public.queue_workers FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();


--
-- Name: queue_job_history queue_job_history_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_job_history
    ADD CONSTRAINT queue_job_history_job_id_fkey FOREIGN KEY (job_id) REFERENCES public.queue_jobs(job_id) ON DELETE CASCADE;


--
-- Name: queue_jobs queue_jobs_parent_job_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.queue_jobs
    ADD CONSTRAINT queue_jobs_parent_job_id_fkey FOREIGN KEY (parent_job_id) REFERENCES public.queue_jobs(job_id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict m7vFfl7xFLdqtbYkAY592oojjlNWeqzesQmubOtcNm6rEMz04wEXfTFX1i64XEd
