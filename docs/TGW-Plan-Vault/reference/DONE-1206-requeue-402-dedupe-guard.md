# DONE — todo #1206 (audit#1143)

`scripts/requeue_ebay_draft_402_dead_letters.py` (a one-time bulk-requeue
for the 2026-07-02 OpenRouter 402 billing gap, ~2,582 rows) generated its
dedupe key from a fresh millisecond timestamp on every invocation
(`f'ebay_draft:{sku}:requeue:{int(time.time() * 1000)}'`), with no run-once
guard. Re-running `--apply` (operator confusion, an accidental re-run, a
cron mistake) would requeue every matching dead-letter row again, burning a
second full round of billed AI-drafting cost for items that had already
been successfully requeued — with no flag, per the bulk-AI-cost standing
rule.

## Investigation: why a job_id-derived dedupe key alone isn't enough
Checked `queue/schema.sql`: `uq_queue_jobs_dedupe_key_active` is a **partial**
unique index — `WHERE dedupe_key IS NOT NULL AND state NOT IN ('succeeded',
'failed','dead_letter','cancelled')`. It only blocks a duplicate dedupe key
while the first job is still active. Once the first requeued job reaches a
terminal state (succeeded, or dies again into dead_letter) — very plausibly
hours later for ~2,600 billed AI-drafting jobs — the constraint no longer
protects against a second bulk run. A deterministic dedupe key closes the
narrower *concurrent*-invocation race but not the realistic *sequential*
one, so it can't be the sole guard.

## Fix
- Added a persistent JSON marker file
  (`/opt/TGW/var/run/requeue_ebay_draft_402_dead_letters.done.json`,
  path overridable via `--marker` for testing) recording every `job_id`
  already requeued by a completed `--apply` run. Loaded at the start of
  every `--apply` run; any row whose `job_id` is already recorded is
  skipped (counted separately as `already_done` in the summary line), and
  the marker is rewritten (atomic tmp+rename) after each run with the
  updated set. This is the durable run-once guard — it survives past any
  single job's terminal-state transition.
- Also made the dedupe key deterministic
  (`f'ebay_draft:{sku}:requeue:{job_id}'` instead of a timestamp), as
  defense-in-depth for the narrower concurrent-invocation race the partial
  index does still catch.
- A job that fails to enqueue (caught by the existing `except Exception`)
  is correctly NOT recorded in the marker, so a genuinely failed requeue
  attempt can still be retried on a later run.

## Tests
New `tests/test_requeue_ebay_draft_402_dead_letters.py` (script had zero
prior coverage; all `state_machine` DB calls mocked, no real Postgres
connection made):
- `--apply` requeues all matched rows and writes the marker
- a second `--apply` run over the *same* rows skips all of them (the
  regression case for #1206)
- genuinely new dead-letter rows (not in the marker) are still requeued
  on a later run — the guard is per-`job_id`, not a blanket "never run
  again"
- dedupe key is deterministic (job_id-based), not timestamp-based
- dry-run (no `--apply`) never writes the marker
- a failed enqueue attempt is not recorded in the marker

`pytest -q tests/test_requeue_ebay_draft_402_dead_letters.py`: 6/6 pass.
Full suite: 2008 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (read-only only — no --apply run against production)
Per the standing bulk-AI-cost rule, I did **not** run `--apply` against
real data (2,658 real dead-letter rows currently match the 402 pattern —
would have billed a full round of AI-drafting for real). Instead:
- Ran the real (unmodified) SELECT query against the live `state_machine`
  Postgres DB (as the `tgw` user, read-only): confirmed 2,658 real
  dead-letter `ebay_draft` rows still match the 402 pattern today (up
  slightly from the docstring's original 2,582 — more have accumulated
  since).
- Confirmed the marker file does not yet exist in production
  (`/opt/TGW/var/run/requeue_ebay_draft_402_dead_letters.done.json`) —
  this script has never been run with `--apply` for real, so the bug
  described in this todo has been dormant (not yet caused an actual
  double-bill), but the fix closes it before this script is ever run.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no billed AI-drafting jobs were enqueued during this work.
