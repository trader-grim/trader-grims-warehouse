# Result: #1265 ebay_draft 402/429 dead-letter bulk requeue
Status: blocked (no action taken — live pre-flight found the fix already applied)
Todo: #1265   PP: PP-COHESION-001

## Pre-flight verification (step 3, mandatory before any --apply)

1. **Re-confirmed counts still accurate today (2026-07-17, vs the todo's
   2026-07-14 numbers):**
   - `queue_jobs WHERE state='dead_letter' AND queue_name='ebay_draft' AND error_detail LIKE '%402 Client Error%'`
     → **2658** rows, dated 2026-07-02 → 07-04.
   - `... AND error_detail LIKE '%429%'` → **12** rows, all
     `commerce/taxonomy/v1/category_tree` 429s, dated 2026-07-01 → 07-02.
   - Total 2670 — matches todo's figure exactly, not stale.

2. **Read todo #1250's incident** (`invariants.md` E9,
   `DONE-1249-diagnose-dead-letters.md`): `requeue_ebay_draft_402_dead_letters.py`
   had silently been run more than once (no `announce_script_run` logging
   at the time) and created 6,607 requeue jobs against an expected ~2,689
   — the exact failure mode this task was told to avoid repeating.

3. **Went one step further than the packet asked and checked outcome, not
   just count** — this is what changed the plan. Found:
   - `/opt/TGW/var/run/requeue_ebay_draft_402_dead_letters.done.json` (the
     OLD script's marker) already lists **all 2658** of today's 402
     dead-letter `job_id`s as previously-used requeue *sources*
     (100% overlap, verified by set intersection).
   - `queue_jobs` has **9187 succeeded** + **90 dead_letter** ebay_draft
     rows carrying `retried_from_job`/`bulk_requeue_reason` pointing back
     at these same 2658 source job_ids (9265 total child attempts for
     2658 unique sources — consistent with the #1250 storm's
     over-firing, but the *outcome* was mostly success).
   - Direct `ItemData/<SKU>/<SKU>.json` scan of **all 2413 unique SKUs**
     behind the 2670 dead-letter rows (2658 402-bucket + 12 429-bucket,
     deduped): **every single one already has `draft_listing` populated**
     (2413/2413, 0 missing, 0 empty). Sampled and manually inspected
     3 SKUs from the 402 bucket and 8 from the 429 bucket — all confirmed
     with real `draft_listing` content (title/category/condition/price
     etc.), not stub data.
   - Conclusion: **the underlying work this todo exists to fix is already
     done.** The 2670 dead-letter rows are stale historical records of
     jobs whose source data was already successfully reprocessed (almost
     certainly as an unintended side effect of the #1250 storm itself,
     which over-fired but also mostly succeeded once the 07-08
     direct-API fix landed). Dead-letter rows are never auto-purged
     (by design — permanent record), so their continued presence in
     `queue_jobs` does not mean the SKUs are unresolved.

## Decision: stopped before any --apply run

Per this contract's step 3 ("If any assumption fails: STOP, do not
silently adapt the spec to the new reality... report as blocked") —
the todo's premise ("very likely fixable by requeue now") is **directly
contradicted** by live evidence: there is nothing left to fix. Running
`--apply` at any batch size would have been a pure-waste bulk-billing
operation — real OpenRouter LLM calls and eBay Taxonomy API calls spent
regenerating drafts that already exist, with zero benefit and non-zero
risk of clobbering already-good draft data. Ran only a `--limit 5`
**dry-run** (no `--apply`, read-only) to confirm the new
`scripts/requeue_deadletter.py` tool works correctly against the live
worktree/DB before deciding not to use it further; that dry-run made
zero API calls and zero writes.

## Files touched
None in the worktree beyond this result manifest + inbox breadcrumb
(no code changes — the generic tool from #1402 was verified as-is, not
modified).

## Live evidence
- `psql` counts: 2658 402-bucket, 12 429-bucket dead_letter rows,
  date ranges confirmed pre-2026-07-08 fix (verbatim above).
- Overlap check: `set(marker_job_ids) & set(current_402_dead_letter_ids)`
  = 2658/2658 (100%).
- `queue_jobs` lineage: 9187 succeeded + 90 dead_letter children tagged
  `bulk_requeue_reason` referencing these exact source job_ids.
- ItemData scan: 2413/2413 unique target SKUs have non-empty
  `draft_listing` (script output: `has_draft 2413 / no_draft 0 /
  missing_file 0`).
- `scripts/requeue_deadletter.py --queue ebay_draft --error-like
  '%402 Client Error%' --limit 5` (dry-run, no `--apply`) ran cleanly
  against the worktree DB connection, confirming the tool itself is
  usable if a genuinely-unresolved bucket turns up later.

## Batches run
None. Zero jobs requeued, zero paid API calls made (OpenRouter or eBay
Taxonomy) beyond the pre-flight `psql`/file-scan verification work,
which is free/local.

## Deviations from spec
Did not proceed to the requeue batches the packet otherwise authorized,
because pre-flight verification (mandatory per this contract's step 3)
found the spec's premise no longer holds — this is the explicit
"assumption fails → stop, report blocked" path, not a silent
substitution. Flagging per Prime Directive 3.

## Out-of-scope findings filed
- #1501 — recommend deciding whether the 2670 now-redundant dead-letter
  rows should be annotated as historically-resolved so future audits
  stop re-flagging them, or left as-is (permanent record, no code
  change required either way).
- #1502 — the 90 dead_letter children of the #1250 storm's own retries
  (tagged `bulk_requeue_reason`) are still dead_letter after a retry
  attempt — a genuinely distinct, still-unresolved bucket worth a
  #1249-style diagnosis pass (separate from this todo's now-moot
  2670-row bucket).
