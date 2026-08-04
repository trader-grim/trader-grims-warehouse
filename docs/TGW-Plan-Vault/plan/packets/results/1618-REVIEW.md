# Review: 1618 debounce-selfcollision-fix

Status: cleared
Reviewer: Claude (same session as dispatcher — no separate /tgw-packet spec
file exists for this todo; original dispatch prompt in-session serves as
the de facto spec, same recurring process gap as #1615, tracked under #1617)
Todo: #1618   PP: PP-STATEMACHINE-001

## Checked

- **Spec (goal)**: self-rescheduling workers' debounce reschedule must not
  corrupt/orphan their own in-flight (`leased`/`running`) job. Confirmed
  via diff read of `src/tgw/queue/state_machine.py`'s `enqueue_job()`
  debounce branch — explicit `pg_advisory_xact_lock`-guarded read-then-
  write, correctly handles: (1) existing pending row → UPDATE/coalesce
  (unchanged behavior), (2) existing active row → INSERT with
  `dedupe_key=NULL` (avoids the broad index entirely, cannot collide), (3)
  neither → plain INSERT with real key (unchanged first-schedule case).
- **Deviation, major**: packet specified a narrower partial index as the
  `ON CONFLICT` arbiter; executor found via live Postgres 17 testing that
  this doesn't work (arbiter-implication gotcha — a narrower predicate that
  logically implies a broader existing index's predicate doesn't exclude
  that broader index as an eligible arbiter). Verified the failure live,
  verified the rejected alternative (disjoint indexes) live too before
  discarding it, then built and live-verified the shipped mechanism. This
  is exactly the kind of judgment call the packet's "the fix itself must
  be correct... not rushed" acceptance criterion was asking for — resolved,
  not an open trigger.
- **Deviation, minor (fix-attempt 1 of 2, resolved)**: schema.sql/
  live_schema.sql comments initially still described the new index as the
  active ON CONFLICT arbiter, inconsistent with the actual shipped
  mechanism. Sent back, corrected in commit `4768395` — verified accurate
  now, index definitions themselves untouched.
- **Out of scope**: broad `uq_queue_jobs_dedupe_key_active` index
  untouched; plain reject-INSERT path untouched; `supersede` logic
  untouched — confirmed via diff (`state_machine.py`'s `if debounce:`
  branch is the only modified branch; `else:` insert path unchanged).
- **Live evidence**: real, not simulated. Bug reproduced on a live
  PostgreSQL 17.10 instance (throwaway `state_machine_test` DB, never
  touched production), confirmed unfixed code produces the exact tonight's-
  incident symptom (same job_id, orphaned row), confirmed the packet's
  literal proposed fix still reproduces it, confirmed the shipped fix
  resolves it (distinct job_id, original `mark_succeeded()` doesn't affect
  the new pending row), confirmed coalescing still works, confirmed the
  reject-path's `UniqueViolation` still fires correctly.
- **Invariants**: E16 (job manifest enforcement) unaffected — `dedupe_key`
  is still required for every non-exempt call; this fix changes only how a
  *satisfied* dedupe_key is resolved against existing rows, not whether one
  is required. No invariant violated.
- **Tests**: `tests/test_statemachine_manifest.py` updated for the new
  mechanism (coalescing + self-collision cases), 16/16 pass.
  `tests/test_debounce_selfcollision_live.py` (new, DB-reachability-gated,
  skips cleanly offline) — 3/3 pass with fix, confirmed fails on reverted
  code (regression test genuinely catches the original bug). Full suite:
  2742 passed, 4 skipped, 2 pre-existing unrelated failures (same
  `test_invariant_c12_field_set_accessors.py` line-drift as #1615's run).
- **New infra flagged appropriately**: throwaway `state_machine_test` DB
  created for live reproduction — did not touch production, keep/drop
  decision correctly deferred to review/Dave rather than assumed, filed as
  #1619 (also covers documenting the Postgres arbiter-implication gotcha
  for future schema work). Worktree/`flake.lock`-symlink pytest permission
  friction noted but correctly not double-filed (already covered by
  #1322/PP-NIXOS-001).

## Trigger check

None fired (unresolved). The major deviation was flagged, justified with
live evidence, and resolved within scope — not an open/unresolved
deviation. The minor doc-inconsistency nit was caught and fixed in one
attempt (well under the 2-attempt cap). No invariant violation, no
out-of-scope files, no live/production write attempted (throwaway DB only,
explicitly not production), pp_ref matches, manifest sanity check passed.

## Not yet done — explicitly deferred to stitch, not a gap

The new `uq_queue_jobs_dedupe_key_pending` index exists only in this
branch's schema files, not on the running production `state_machine`
database. Applying it live is the stitch step's responsibility per the
packet's own scope (agents don't run DDL against production). Whoever
merges this must also apply the index live — see stitch notes.

## Summary

Thorough, well-verified fix for tonight's live incident. The deviation from
the original packet's proposed mechanism is a genuine improvement, not
scope drift — the packet's literal approach was proven not to work before
being replaced. Cleared for stitch.
