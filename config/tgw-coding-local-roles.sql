\set ON_ERROR_STOP on

-- Development-only peer roles for ordinary tgw-lib Unix worker accounts.
-- Run while connected to tgw_lib_dev_state_machine as its owner.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tgw_coding') THEN
        CREATE ROLE tgw_coding NOLOGIN INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'db') THEN
        CREATE ROLE db LOGIN INHERIT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'codex') THEN
        CREATE ROLE codex LOGIN INHERIT;
    END IF;
END
$$;

ALTER ROLE tgw_coding NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE db LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE codex LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

GRANT tgw_coding TO db, codex;
GRANT CONNECT ON DATABASE tgw_lib_dev_state_machine TO tgw_coding;
GRANT USAGE ON SCHEMA public TO tgw_coding;

GRANT SELECT, INSERT, UPDATE
    ON TABLE public.todo_items, public.queue_jobs
    TO tgw_coding;
GRANT SELECT, INSERT
    ON TABLE public.queue_job_history
    TO tgw_coding;
GRANT USAGE, SELECT
    ON SEQUENCE public.todo_items_id_seq, public.queue_job_history_history_id_seq
    TO tgw_coding;

GRANT EXECUTE ON FUNCTION public.claim_queue_jobs(text, text, integer, integer)
    TO tgw_coding;
GRANT EXECUTE ON FUNCTION public.recover_expired_jobs()
    TO tgw_coding;
