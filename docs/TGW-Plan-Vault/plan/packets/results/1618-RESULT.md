# Result: todo #1618 debounce-selfcollision-fix
Status: done
Todo: #1618   PP: PP-STATEMACHINE-001

## Summary

Fixed the live incident: `enqueue_job(debounce=True, ...)` could corrupt a
worker's own in-flight (`leased`/`running`) job when the worker called it
from inside its own `handle()` to schedule its next run, using the same
`dedupe_key`. `mark_succeeded()` then permanently finalized the corrupted
row, silently killing the self-rescheduling chain (confirmed live tonight:
`token_refresh`'s chain died this way, causing the eBay token expiry).

**Important deviation from the packet's specified mechanism — read before
review.** The packet's proposed fix (add a narrower
`uq_queue_jobs_dedupe_key_pending` partial index covering only `queued`/
`retry_wait`, and point the debounce path's `ON CONFLICT` predicate at it
instead of the existing broad `uq_queue_jobs_dedupe_key_active` index) does
**not work** — verified live against a real PostgreSQL 17.10 instance while
building this fix. Postgres's `ON CONFLICT` arbiter inference accepts *any*
unique index whose predicate is *implied by* the specified `WHERE` clause,
not only an exact match. Since `state IN ('queued','retry_wait')` is always
a subset of (and therefore logically implies) the broad index's `state NOT
IN (terminal-4)` predicate, **both indexes become simultaneous arbiters**
regardless of which one is named in the `ON CONFLICT` clause. A conflict
detected via *either* index — including the caller's own `leased`/`running`
row via the broad index — silently triggers `DO UPDATE`, with no error
raised. This reproduces the exact original bug (same `job_id` returned,
`not_before` corrupted, `mark_succeeded()` orphans it), just with the
narrower index also present but inert.

I also tried making the broad index disjoint (splitting it into a
`pending`-only + `active`-only pair) — that *does* fix the ON CONFLICT
arbiter problem, but breaks the plain (non-debounce) reject-INSERT path's
real invariant: with disjoint indexes, a duplicate `queued` enqueue for a
`dedupe_key` that already has a `leased` row under it (e.g. `ebay_stage:
{sku}` while `ebay_stage` is actively running for that SKU) silently
succeeds instead of raising `UniqueViolation` — a genuine regression the
packet explicitly said to avoid ("the plain ... INSERT path relies on THIS
broad index to reject a genuine duplicate request while one is actively
running"). Verified this regression live too before rejecting the approach.

**Fix actually shipped:** the debounce path no longer uses `INSERT ...
ON CONFLICT` at all. It's explicit read-then-write, serialized per
`dedupe_key` with `pg_advisory_xact_lock` (held for the transaction):
1. Look for an existing pending (`queued`/`retry_wait`) row under the
   `dedupe_key`. If found, `UPDATE` it (`GREATEST(not_before, ...)`,
   fresh payload) — this is the pre-existing, correct coalescing behavior
   (e.g. `catalog_rebuild:pending`'s write-burst collapse), unchanged.
2. Otherwise, check whether a `leased`/`running` row holds this
   `dedupe_key` (the #1618 self-collision case). If so, insert the fresh
   row with `dedupe_key = NULL` — a distinct, valid, immediately claimable
   row that cannot corrupt or be corrupted by the in-flight one. Known,
   documented, accepted limitation: this fresh row won't be found by a
   *later* coalescing debounce call made while the original job is still
   running (rare — a worker normally reschedules itself once per
   `handle()`), since it no longer carries the semantic key. Worst case in
   that rare scenario is one extra distinct future row, never a lost/
   orphaned one — a much smaller failure mode than the bug being fixed.
3. Otherwise (no pending, no active row — the common first-schedule case)
   insert the fresh row with its real `dedupe_key`, fully coalescable
   going forward, exactly as before.

The broad `uq_queue_jobs_dedupe_key_active` index is untouched, exactly as
the packet required — the plain reject-INSERT path and `supersede`'s
cancel-UPDATE are both fully unaffected (verified live, see below). The new
narrower `uq_queue_jobs_dedupe_key_pending` index from the packet's design
is still added to the schema (real, independently-useful DB-level backstop
— "at most one queued/retry_wait row per dedupe_key" — even though it's no
longer used as an `ON CONFLICT` arbiter for anything).

## Files touched

- `src/tgw/queue/schema.sql` — added `uq_queue_jobs_dedupe_key_pending`
  index (queued/retry_wait only), documented rationale + "do not widen"
  note inline.
- `src/tgw/queue/live_schema.sql` — mirrored the same index (pg_dump-style
  block), with an explicit "NOT YET APPLIED to live production" note.
- `src/tgw/queue/state_machine.py` — rewrote `enqueue_job()`'s
  `debounce=True` branch from `INSERT ... ON CONFLICT DO UPDATE` to
  explicit advisory-lock-guarded read-then-write (SELECT pending → UPDATE,
  or SELECT active → INSERT with real or NULL dedupe_key). Extensively
  documented the investigation and rationale in the docstring so a future
  reader doesn't rediscover the ON-CONFLICT dead end from scratch.
- `tests/test_statemachine_manifest.py` — rewrote Phase 1's mocked SQL-shape
  tests for the new non-ON-CONFLICT debounce implementation: coalescing
  case (`test_debounce_reschedule_coalesces_onto_existing_pending_row`) and
  the exact self-collision case
  (`test_debounce_self_collision_creates_distinct_row_with_null_dedupe_key`).
- `tests/test_debounce_selfcollision_live.py` — **new**, live-Postgres
  regression suite (the mocked-DB convention this repo otherwise uses
  cannot reproduce a real partial-index/ON-CONFLICT-arbiter interaction).
  Gated with `pytest.mark.skipif` on DB reachability via
  `TGW_TEST_STATE_MACHINE_DSN` (defaults to `dbname=state_machine_test
  user=tgw`) so `pytest -q` still passes offline everywhere this DB isn't
  configured. Three tests: (1) the exact self-collision reproduction —
  different job_id, fresh row unaffected by `mark_succeeded()` on the
  original; (2) coalescing still works for genuinely-pending rows; (3)
  plain reject-INSERT path still raises `UniqueViolation` while a row is
  actively running under a non-debounce dedupe_key.
- `docs/TGW-Plan-Vault/plan/packets/results/1618-RESULT.md` — this file.

## New infra created during investigation (flagging explicitly)

Created a throwaway PostgreSQL database `state_machine_test` on tgw-prod
(owned by `tgw`, created via `sudo -u postgres`, `schema.sql` applied) to
run the live reproduction and the new live-DB regression tests against —
did **not** touch the real `state_machine` database at any point. This
database did not exist before this task and isn't tracked anywhere;
todo #1619 asks whether to keep it (useful for future live-DB test runs,
matches the new test file's documented default DSN) or drop it — that's a
call for review/Dave, not mine to make unilaterally.

## Live evidence

**Bug reproduction (unfixed code, real Postgres 17.10, `state_machine_test`
DB):**
```
jid1 = enqueue_job(debounce=True, dedupe_key='test:pending')  # -> 0c1c5e52...
claim + mark_running(jid1)
jid2 = enqueue_job(debounce=True, dedupe_key='test:pending', not_before=+far-future)
jid2 == '0c1c5e52...'   # SAME as jid1 — the bug
mark_succeeded(jid1)
SELECT job_id, state, not_before FROM queue_jobs WHERE dedupe_key='test:pending';
  -> ('0c1c5e52...', 'succeeded', datetime(5138, 11, 16, ...))   # orphaned forever
```

**With the packet's literal proposed fix (narrow index only, broad index
untouched) — still reproduces the bug identically** (same job_id, same
corruption) — this is the finding that drove the design change above.

**With the shipped fix, same reproduction:**
```
jid1 != jid2                                    # True
queue_jobs after mark_succeeded(jid1):
  (jid1, 'succeeded', None,                    dedupe_key='test:pending')
  (jid2, 'queued',    datetime(5138, 11, 16..), dedupe_key=NULL)
```
Coalescing case (two debounce calls, no active row) still collapses to one
row via `GREATEST`. Plain reject-INSERT (`ebay_stage:{sku}`-style, non-
debounce) still raises `psycopg2.errors.UniqueViolation` while a `leased`
row exists under the same key — confirmed live, unaffected by this change.

**Automated test evidence:**
- `tests/test_debounce_selfcollision_live.py` — 3/3 pass with the fix.
  Reverted `state_machine.py` locally (kept schema unchanged) and re-ran:
  `test_self_reschedule_while_running_creates_distinct_row` **fails**
  exactly as expected (`assert jid2 != jid1` → `AssertionError: ... same
  job_id`), confirming the test would have caught the original bug.
  Restored the fix afterward; all 3 pass again.
- `tests/test_statemachine_manifest.py` — 16/16 pass (updated Phase 1
  mocked tests + all pre-existing Phase 2/3/4 tests unaffected).
- Full suite: `pytest -q` (run as the worktree-owning user, `sm.__file__`
  confirmed resolving under the worktree, not the shared checkout) →
  **2742 passed, 4 skipped, 2 failed** — the 2 failures are exactly the
  pre-existing known `test_invariant_c12_field_set_accessors.py` failures
  named in the packet's acceptance criteria (line-number drift in
  `ai_identify.py`, unrelated to this change). No new failures.

**Note on running pytest as `tgw` in this worktree:** hit an unrelated
environment permission issue (`worktree/flake.lock` is a symlink to
`/home/db/tgw-flake/flake.lock`, unreadable by the `tgw` OS user, and
pytest's config-file discovery stats it while walking up from cwd) that
blocks running pytest as `tgw` directly inside this worktree. Real-DB
Postgres access requires peer-auth as `tgw` though, so the live-DB test
runs were done from a scratch copy outside the worktree (deleted after
use); the full offline suite ran fine as the worktree-owning user (`db`),
which is sufncient since it doesn't need real DB access. Flagging as
operational friction, not filing a separate todo since #1322/PP-NIXOS-001
(durable systemd-disable gap) already tracks worktree/perm friction in
this area and this is a narrow, easily-worked-around instance of it.

## Deviations from spec

1. **Major, load-bearing (see Summary above):** the packet's literal
   proposed mechanism (narrower index as the sole `ON CONFLICT` arbiter,
   broad index untouched) does not work — verified live. Implemented a
   different, verified-working mechanism (explicit advisory-lock-guarded
   read-then-write) that achieves the packet's stated goal (self-collision
   creates a distinct row instead of corrupting the in-flight one) while
   fully preserving every constraint the packet listed (broad index
   untouched, plain reject-INSERT path unaffected, `supersede` logic
   untouched).
2. Added `pytest.mark.skipif`-gated live-Postgres test file
   (`tests/test_debounce_selfcollision_live.py`) and a throwaway
   `state_machine_test` database, since the repo's established
   fully-mocked test convention cannot reproduce a real partial-index
   arbiter interaction — the packet's "reproduce the actual race" ask
   requires this. This is new test infrastructure beyond a plain code
   change; flagged via todo #1619 for a keep/drop decision on the
   database.
3. Fresh reschedule rows created during a genuine self-collision get
   `dedupe_key = NULL` instead of the semantic key — documented,
   accepted, narrow limitation (loses coalescing only for the rare case of
   a second debounce call arriving for the same key while the first
   reschedule is still queued and the *original* job is still running).

## Out-of-scope findings filed

- todo #1619 (PP-STATEMACHINE-001) — document the Postgres partial-index
  ON-CONFLICT-arbiter-implication gotcha in `reference/` for future schema
  work; decide keep/drop on the `state_machine_test` database.

## NOT done (explicitly out of scope per packet)

- Live DDL apply to production `state_machine` — the new
  `uq_queue_jobs_dedupe_key_pending` index exists only in this branch's
  schema files, not on the running production database. That's the
  stitch/merge step's job.
