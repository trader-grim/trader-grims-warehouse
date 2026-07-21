# In progress: todo #1618 (PP-STATEMACHINE-001) — debounce self-collision fix

Live incident: `enqueue_job(debounce=True, ...)`'s ON CONFLICT arbiter is the
broad `uq_queue_jobs_dedupe_key_active` index, which covers `leased`/`running`
too — a self-rescheduling worker's own in-flight job can collide with its own
reschedule call, corrupting the in-flight row instead of creating a fresh one.
`mark_succeeded()` then finalizes that corrupted row, orphaning the reschedule
and silently killing the worker's chain (confirmed live: token_refresh).

Fix: add a narrower `uq_queue_jobs_dedupe_key_pending` index (queued/retry_wait
only) and repoint the debounce path's ON CONFLICT predicate at it, in both
schema.sql and live_schema.sql. Add regression test with a real Postgres
connection (existing tests in this area are DB-mocked; this bug needs real
partial-index arbiter behavior to reproduce).

Working in worktree: /opt/TGW/var/worktrees/1618-debounce-selfcollision-fix
Branch: todo/1618-debounce-selfcollision-fix

Out of scope: live DDL apply to production DB (stitch step's job).
