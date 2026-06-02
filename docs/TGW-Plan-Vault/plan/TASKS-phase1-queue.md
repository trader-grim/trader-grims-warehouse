---
title: Phase 1 Execution Tasks — Queue Foundation
for: Sonnet / Haiku execution sessions
prereq: PostgreSQL installed, schema applied, smoke-tested (confirmed)
---

# Phase 1 — Queue foundation: execution tasks

Each task below is one execution session. Hand the executor model this file
plus the named source files. Tasks are ordered; do not skip ahead.

## Conventions for the executor
- All paths come from `load_config()` in `tgw.config` — never hardcode.
- Atomic writes only (temp file + `os.replace()`).
- Every CLI path returns one JSON object with an `ok` key; exit code mirrors it.
- Call `setup_logging(component)` once at startup; `getLogger(__name__)` elsewhere.
- Dry-run / safe-default for anything that touches data.
- After each task: run the verification block before declaring done.

---

## TASK 1.0 — secrets_root migration (do this before anything else)
**Goal:** single canonical secrets directory; fix the health-check path bug before
health becomes part of the verification suite for subsequent tasks.

**Background:** secrets are currently scattered across at least three locations —
token state at `state_root/ebay_token_state.json`, refresh tokens described as
`runtime/secrets/`, and health probing `config/secrets/ebay-token.json`. eBay
`app_id` / `cert_id` live inside `tgw-api-config.json`, making the config file
itself a secret. None of this is resolved from config.

**Build:**
1. Add `secrets_root` key to `tgw-api-config.json` (e.g. `/opt/TGW/secrets/`).
   Have `get_tgw_paths()` auto-create it alongside the other `*_root` directories.
2. Create `/opt/TGW/secrets/` on disk: `chmod 700`, owned by `tgw`.
3. Move existing secret files into `secrets_root`; set each to `chmod 600`.
4. Update `get_access_token.py` / `refresh_access_token.py` to resolve token path
   from `secrets_root` in config — no hardcoded paths.
5. Update `health.py` to probe the token file via `secrets_root` — same path the
   token manager writes.
6. Add `secrets/` to `.gitignore` (belt-and-suspenders — directory is already
   outside the repo tree, but guard anyway).

**Files to read first:** `src/tgw/config.py`, `src/tgw/health.py`,
`get_access_token.py`, `refresh_access_token.py`, `tgw-api-config.json`

**Verify:**
- `tgw health` reports token status green (reads the same file the manager writes)
- `grep -r 'config/secrets\|runtime/secrets\|ebay_token_state' src/` returns nothing
- No secret value appears in `tgw-api-config.json` (app_id / cert_id moved out)
- `ls -la /opt/TGW/secrets/` shows `drwx------` and files `-rw-------`

---

## TASK 1.1 — QueueWorker base class
**Goal:** one shared base class that owns all PostgreSQL queue interaction, so
no individual worker ever writes raw SQL.

**Build** `src/tgw/queue/worker_base.py` with a `QueueWorker` class:
- `__init__(self, queue_name, config)` — store config, set up logging
- `run(self)` — the forever loop:
  1. `claim()` a job (lease it) via `state_machine.claim_queue_jobs(queue_name, owner, lease_seconds)`
  2. if none: sleep `poll_interval`, loop
  3. else: call `self.handle(job)` (subclasses implement this)
  4. on success: `state_machine.mark_succeeded(job_id)`
  5. on exception: log, `state_machine.mark_failed(job_id, error)` (respects max_attempts → dead_letter)
- `handle(self, job)` — abstract; raise NotImplementedError
- graceful shutdown on SIGTERM (finish current job, then exit)
- a periodic `recover_expired_jobs()` call (either here on an interval, or note it for a separate recovery task)

**Files to read first:** `src/tgw/queue/state_machine.py`, `src/tgw/queue/schema.sql`, `src/tgw/config.py`

**Verify:**
- Unit test with a fake `handle()` that succeeds → job ends `succeeded`
- Unit test where `handle()` raises → job ends `failed`, attempt_count incremented
- Use `tempfile`-style DB fixture or a test schema; no live data

---

## TASK 1.2 — Echo worker (the reference implementation)
**Goal:** prove the whole loop end to end with zero business risk. This worker
becomes the template every future worker is copied from.

**Build** `src/tgw/workers/echo.py`:
- subclass `QueueWorker`
- `handle(self, job)` reads `job.payload`, logs it, writes it to the result/notes
  field, returns. Does nothing else.
- console entry or `python -m tgw.workers.echo --config ...`

**Verify (this is the acceptance gate for the queue foundation):**
- Insert a job into the `echo` queue (payload `{"msg": "hello"}`)
- Start the echo worker; confirm it leases, logs "hello", marks `succeeded`
- Insert a job, start worker, `kill -9` it mid-lease
- Run `recover_expired_jobs()` after lease expiry; confirm job returns to `queued`
- Restart worker; confirm it reclaims and completes
- Insert 3 jobs, start 2 workers; confirm no job is processed twice (SKIP LOCKED holds)

---

## TASK 1.3 — Process liveness: launcher vs systemd template
**Goal:** decide and implement how worker processes stay alive.

**Investigate, then pick one:**
- Option i: keep a slimmed launcher whose ONLY job is spawning/restarting N workers per queue
- Option ii: systemd templated units `tgw-worker@<queue>.service` + a target that starts the set

**Recommendation to evaluate first:** systemd templated units. systemd already
does restart-on-failure, ordering, and logging to journald. If it covers the
need, the custom launcher retires entirely (less code to own).

**Whichever wins, implement:**
- startup ordering: `After=postgresql.service`, `Requires=postgresql.service`
- restart policy on worker crash
- one echo worker running under the chosen mechanism, surviving a kill

**Verify:** reboot the box (or restart the units); echo worker comes up after
Postgres, leases a test job, completes it.

---

## TASK 1.4 — Health + logging integration
**Goal:** make the queue observable.

- Extend `tgw.health.check_all()` with: Postgres reachable, queue depth per
  queue, count of `dead_letter` jobs, oldest `queued` job age.
- Extend `tgw.health.check_all()` with catalog artifact checks: SQLite catalog
  exists at `sqlite_catalog_path`, report row count and last-modified time;
  thumbnail cache exists at `thumbnail_root`, report image count.
- Confirm `QueueWorker` base logs every claim/complete/fail via `tgw.logging`
  (structured `log_event` so the PM assistant can later parse it).

**Verify:** `tgw health` shows Postgres ok and per-queue depths; kill Postgres,
confirm health reports it down (not a crash). `tgw health` shows SQLite catalog
row count and thumbnail count.

---

## TASK 1.5 — Retire the old path
**Goal:** remove the superseded filesystem queue so there is one system.

- Remove filesystem `.job.json` discovery/processing from the launcher code.
- Remove dead `.queue_worker` / `.queue_worker_config` symlink discovery if the
  systemd-template path was chosen.
- Delete or archive the retired launcher logic; update systemd units.
- Update `tgw health` to stop checking the retired launcher.

**Verify:** no code path reads `.job.json`; echo + recovery still pass; `tgw health` green.

**Acceptance for Phase 1 complete:** all of 1.2's verification gate passes under
the production liveness mechanism, `tgw health` is green, and grep finds no
remaining `.job.json` references in `src/`.
