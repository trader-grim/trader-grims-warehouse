# Universal development database binding (tgw_coding)

**Owner:** shared platform
**Applies to:** tgw-lib local development PostgreSQL; never tgw-prod
**Status:** current canonical design for Todo 1921

## Contract

- Exactly one PostgreSQL execution role, `tgw_coding`, serves every ordinary
  Unix member of `tgw-coders`.
- Every ordinary Unix member of `tgw-coders` reaches that role through peer
  authentication: `pg_hba.conf` resolves local connections that request
  `tgw_coding` through the `tgw-coders` pg_ident map
  (`config/environment/postgresql/pg_ident.conf`). The map covers every
  `tgw-coders` member with a real login shell; service accounts with nologin
  shells are not ordinary coding actors and are intentionally absent. Doctor's
  `database.local-coding-peer-auth` check enforces both directions against the
  live group, so onboarding a new harness without updating the map fails
  closed.
- The shared development DSN explicitly names the role:
  `dbname=tgw_lib_dev_state_machine user=tgw_coding`
  (`config/tgw-coding-local.json` and `config/tgw-plan-render-local.json`).
- No `codex`, `claude`, `deepseek`, `model`, or harness-named PostgreSQL login
  role may exist. The obsolete `db`/`codex` login roles are retired only after
  an ownership/dependency inventory proves them safe to drop
  (`config/tgw-coding-local-roles.sql` fails closed otherwise); their legacy
  membership in `tgw_coding` is revoked by the migration itself and is not a
  blocking dependency.
- The separate database owner/admin role (owner of
  `tgw_lib_dev_state_machine` and its schema objects) is preserved untouched.
- Onboarding a new harness updates `tgw-coders` Unix membership and the peer
  map; it never creates another PostgreSQL login role.

## Canonical files

| Purpose | Path |
| --- | --- |
| Role SQL and guarded obsolete-role retirement | `config/tgw-coding-local-roles.sql` |
| Shared coding DSN | `config/tgw-coding-local.json` |
| Shared plan-render DSN | `config/tgw-plan-render-local.json` |
| Canonical pg_ident peer map | `config/environment/postgresql/pg_ident.conf` |
| Canonical pg_hba layout with managed peer line | `config/environment/postgresql/pg_hba.conf` |
| Doctor check | `database.local-coding` + `database.local-coding-peer-auth` |
| Doctor repair | `sudo -n /usr/local/sbin/tgw-coding-bootstrap --commit COMMIT --repair database` |

## How a new harness is onboarded

1. The operator creates the ordinary Unix account and adds it to
   `tgw-coders` (`usermod -a -G tgw-coders ACTOR`).
2. The canonical peer map adds one
   `tgw-coders      ACTOR                   tgw_coding` line in
   `config/environment/postgresql/pg_ident.conf` (committed with the source).
3. `scripts/install_shared_harness_skills.py --harness ACTOR --home /home/ACTOR`
   links the two canonical skills into the harness's native discovery path.
4. Doctor verifies the functional peer connection as that actor through
   `database.local-coding` and, as root, the exact file materialization
   through `database.local-coding-peer-auth`.
5. No PostgreSQL role is created, altered, or named for the actor.

## Host materialization and reload

Doctor's `repair database`:

- pipes the exact committed role SQL through `sudo -u postgres psql`
  (creating/altering `tgw_coding` and retiring obsolete login roles only when
  the ownership/dependency inventory is empty);
- writes the canonical `tgw-coders` map into
  `/etc/postgresql/17/main/pg_ident.conf` while preserving every unrelated
  map and comment byte-for-byte;
- ensures the managed
  `local   all             tgw_coding      peer map=tgw-coders` line exists
  in `/etc/postgresql/17/main/pg_hba.conf` before any broader
  `local ... all ... peer` line, preserving all other lines;
- runs `SELECT pg_reload_conf();` and re-checks both database checks.

Only PostgreSQL configuration is reloaded; no coding service is restarted for
this binding change because the DSN change is picked up from the shared
configuration by the next process start, and the runtime restart path already
handles worker restarts when the coding runtime is cut over.

## Verification

As each ordinary actor (codex, claude, deepseek):

```bash
sudo -n -u ACTOR /usr/local/bin/tgw todo
sudo -n -u ACTOR /usr/local/bin/tgw coding access-status
sudo -n -u ACTOR /usr/local/bin/tgw coding status
```

Each command must identify the Unix actor while the database session uses the
universal `tgw_coding` role. If peer authentication instead asks for a
same-named PostgreSQL role, repair the shared DSN/peer mapping — never create
that same-named role.
