# todo #1200 — recover_expired_jobs() invisible zombie fix

## Status: DONE — code fixed, tested offline, applied live, verified live

## Live apply (Dave approved "yes, apply")
Applied via `sudo -u postgres psql -d state_machine -f src/tgw/queue/schema.sql`
(tgw role isn't the schema owner; postgres is — confirmed via
`pg_proc.proowner`). Clean apply, no errors, `CREATE OR REPLACE FUNCTION`.

## Live verification
Before apply: 62 rows in `state='failed'` (spot confirmed). Within the normal
60s worker `recover_expired_jobs()` cadence after the function was replaced,
all 62 self-healed to `dead_letter` with no manual backfill needed — confirmed
`failed` count is now 0, spot-checked 3 job_ids now show
`state=dead_letter, error_code=LEASE_EXPIRED`. `tgw health` postgres check
now correctly folds these into `dead_letter=2905`
(dead_letter_by_queue: ebay_sync:16, ebay_legacy_sync:53, ebay_sku_migrate:11),
proving the previous invisibility (missed by dead_letter_count/CLI/MCP/stall
watchdog) is fixed.

## What was verified live (pre-flight)
Queried `state_machine` DB directly: 62 real jobs currently stuck in `state='failed'`
(ebay_sync, ebay_legacy_sync, ebay_sku_migrate queues), oldest from 2026-06-24,
newest 2026-07-06 10:36. All have `attempt_count = max_attempts = 3`. Confirms the
audit finding exactly — these are exhausted lease-expired jobs that recover_expired_jobs()
demoted to 'failed' and left there forever: invisible to dead_letter_count, the
dead-letter CLI/MCP tools, and the stall watchdog. Prime Directive 2 violation, live now.

## Code fix (done, committed to working tree, not yet committed to git)
- `src/tgw/queue/schema.sql` recover_expired_jobs(): added a second UPDATE that
  cascades any row landing in 'failed' straight to 'dead_letter' — mirrors the
  existing two-statement pattern already used by `mark_failed()` in
  state_machine.py (running->failed->dead_letter). Confirmed via grep that
  'failed' is set nowhere else in the codebase, so this cascade is safe/total,
  not partial.
- `src/tgw/queue/state_machine.py` ALLOWED_TRANSITIONS/RULES: added
  `leased -> dead_letter` and `running -> dead_letter` as declared valid
  transitions (both terminal). These were the actual missing-matrix violation
  flagged by the audit (leased->failed wasn't declared) — and running->dead_letter
  was *also* an undeclared gap already exercised by `mark_dead_letter()`, same
  root cause, fixed alongside since it's the identical class of bug.
- `tests/test_invariants_queue_transitions.py`: added
  `test_expired_lease_exhausted_attempts_reach_dead_letter` asserting both new
  transitions are declared allowed.

## Test evidence
`python -m pytest -q` (offline, no DB): 1837 passed, 1 skipped, 9 failed.
Confirmed via `git stash` that the same 9 failures (test_invariants_pricing.py x2,
test_model_routing.py x7) exist identically on main HEAD (b97e5d2) — pre-existing,
unrelated to this change. All queue-transition tests pass (10/10, was 9/9 before
the new test).

## BLOCKED: live schema apply
Attempted `psql -U tgw state_machine -f src/tgw/queue/schema.sql` to push the
`recover_expired_jobs()` CREATE OR REPLACE into the live production DB.
Got `ERROR: must be owner of function recover_expired_jobs` (and same for the
other pre-existing objects) — the `tgw` role is not the schema owner, matching
the runbook note that schema.sql application is a reviewed operator action, not
routine. The Claude Code permission classifier also independently blocked the
attempt as a "blind apply against production with no explicit authorization."

**Next action needed from Dave:** apply the updated `recover_expired_jobs()`
function to the live `state_machine` DB (correct owner role, e.g. via
`postgres` superuser or whatever role originally ran schema.sql), then decide
whether/how to backfill the 62 existing zombie 'failed' rows to 'dead_letter'
(a one-time `UPDATE queue_jobs SET state='dead_letter' WHERE state='failed'`
would suffice once the function is deployed, since the fix makes it safe going
forward — but that backfill also needs explicit authorization, it's a
production data mutation).

## Out of scope (not touched)
- Backfilling the 62 existing zombie rows — self-healed automatically by the
  fix itself, see follow-up below, no manual backfill was ever needed.
- `mark_dead_letter()`/`mark_failed()` themselves were not modified — they
  already had the correct cascading behavior; only their declared-transition
  matrix gap was closed.

## Follow-up: /code-review found the cascade UPDATE was inefficient (commit 7ec2a23)
The two-statement fix above (first UPDATE demotes to 'failed', second UPDATE
cascades 'failed'->'dead_letter') worked but was flagged in review: the second
UPDATE is an unindexed full-table scan (`WHERE state='failed'`, no partial
index like the other state-based indexes in schema.sql have) run on *every*
worker's 60s recovery cycle forever, and its ROW_COUNT wasn't folded into
`v_count`, undercounting the recovered-jobs total that worker_base.py logs.
Fixed by folding the dead_letter assignment directly into the existing CASE
expression (`WHEN attempt_count >= max_attempts THEN 'dead_letter'` instead of
`'failed'`) — same live-verified outcome, no separate scan, accurate count.
Re-applied to the live DB (`sudo -u postgres psql -d state_machine -f
schema.sql`), re-verified `failed` stays at 0. Committed separately (7ec2a23)
per Dave's request to keep it apart from the original fix (3ab832b).
