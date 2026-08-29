\set ON_ERROR_STOP on

-- TGW universal development database binding.
--
-- One PostgreSQL execution role, ``tgw_coding``, serves every ordinary Unix
-- member of ``tgw-coders``.  Peer authentication binds each Unix identity to
-- that single role through the ``tgw-coders`` pg_ident map in
-- config/environment/postgresql/pg_ident.conf, and the shared development
-- DSNs explicitly name ``user=tgw_coding`` (config/tgw-coding-local.json and
-- config/tgw-plan-render-local.json).  No caller selects a role named after
-- an agent or harness, and no such login role may exist.
--
-- The separate database owner/admin role (the role that owns
-- ``tgw_lib_dev_state_machine`` and its schema objects) is preserved and
-- never altered here.  Run this file while connected to
-- ``tgw_lib_dev_state_machine`` as a privileged admin; Doctor pipes it
-- through ``sudo -u postgres``.
--
-- Onboarding a new harness adds its Unix account to ``tgw-coders`` and to the
-- peer map; it never creates another PostgreSQL login role.

DO $$ BEGIN
    ALTER TABLE public.todo_items ADD COLUMN progress_note TEXT;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tgw_coding') THEN
        CREATE ROLE tgw_coding LOGIN INHERIT;
    END IF;
END
$$;

ALTER ROLE tgw_coding LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;

GRANT CONNECT ON DATABASE tgw_lib_dev_state_machine TO tgw_coding;
GRANT USAGE ON SCHEMA public TO tgw_coding;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON TABLE public.todo_items, public.queue_jobs
    TO tgw_coding;
GRANT SELECT, INSERT
    ON TABLE public.queue_job_history
    TO tgw_coding;
GRANT USAGE, SELECT, UPDATE
    ON SEQUENCE public.todo_items_id_seq, public.queue_job_history_history_id_seq
    TO tgw_coding;

GRANT EXECUTE ON FUNCTION public.claim_queue_jobs(text, text, integer, integer)
    TO tgw_coding;
GRANT EXECUTE ON FUNCTION public.recover_expired_jobs()
    TO tgw_coding;

-- Retire obsolete per-actor login roles only after an ownership/dependency
-- inventory proves they are safe to drop.  This block fails closed: it lists
-- every owned object, membership, grant, or ACL dependency it finds and
-- refuses to drop the role, so the operator can transfer ownership or revoke
-- the dependency explicitly before rerunning the repair.  Membership in the
-- universal ``tgw_coding`` role itself is revoked by this migration and is
-- therefore not a blocking dependency.
DO $$
DECLARE
    obsolete CONSTANT text[] := ARRAY['db', 'codex'];
    r record;
    inventory text;
BEGIN
    FOR r IN
        SELECT rolname, oid FROM pg_roles
        WHERE rolname = ANY(obsolete) AND rolcanlogin
        ORDER BY rolname
    LOOP
        SELECT string_agg(line, E'\n' ORDER BY line)
          INTO inventory
          FROM (
            SELECT 'owns database ' || datname::text AS line
              FROM pg_database WHERE datdba = r.oid
            UNION ALL
            SELECT 'owns ' || c.relkind::text || ' ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE c.relowner = r.oid
            UNION ALL
            SELECT 'owns schema ' || quote_ident(n.nspname)
              FROM pg_namespace n WHERE n.nspowner = r.oid
            UNION ALL
            SELECT 'owns function ' || p.oid::regprocedure::text
              FROM pg_proc p WHERE p.proowner = r.oid
            UNION ALL
            SELECT 'member of ' || g.rolname::text
              FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid
             WHERE m.member = r.oid AND g.rolname <> 'tgw_coding'
            UNION ALL
            SELECT 'granted to ' || g.rolname::text
              FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.member
             WHERE m.roleid = r.oid
            UNION ALL
            SELECT 'ACL reference in table ' || quote_ident(c.relname)
              FROM pg_class c WHERE c.relacl::text LIKE '%' || r.oid::text || '%'
            UNION ALL
            SELECT 'ACL reference in database ' || datname::text
              FROM pg_database WHERE datacl::text LIKE '%' || r.oid::text || '%'
            UNION ALL
            SELECT 'ACL reference in schema ' || quote_ident(n.nspname)
              FROM pg_namespace n WHERE n.nspacl::text LIKE '%' || r.oid::text || '%'
            UNION ALL
            SELECT 'ACL reference in function ' || p.oid::regprocedure::text
              FROM pg_proc p WHERE p.proacl::text LIKE '%' || r.oid::text || '%'
          ) AS d(line);
        IF inventory IS NOT NULL THEN
            RAISE EXCEPTION
                'refusing to retire obsolete login role % with ownership/dependencies:%',
                r.rolname, E'\n' || inventory;
        END IF;
        IF EXISTS (
            SELECT 1 FROM pg_auth_members m
            JOIN pg_roles g ON g.oid = m.roleid
            WHERE m.member = r.oid AND g.rolname = 'tgw_coding'
        ) THEN
            EXECUTE format('REVOKE tgw_coding FROM %I', r.rolname);
        END IF;
        EXECUTE format('DROP ROLE %I', r.rolname);
    END LOOP;
END
$$;
