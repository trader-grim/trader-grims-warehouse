# Packet: ebay_stage dead-letters on non-leaf eBay category selection

Todo: #1395   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — dispatched alongside 7 other packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/ebay_draft.py` (whole file — where
`category_id` is chosen and written to the item, see line ~297-353) +
whichever module defines `best_category()` (grep it — likely
PP-CATPICK-001's category picker, not yet confirmed which file) +
`src/tgw/workers/ebay_stage.py` (whole file — where staging actually
submits and fails). Nothing else until you've confirmed the real call
chain; don't guess file names.

## Verified live before this packet was written
- 17 `ebay_stage` dead-letters, all `HardFailure`, eBay's rejection text:
  "The eBay listing associated with the inventory item, or the
  unpublished offer has an invalid category ID. The category selected is
  not a leaf category." — confirmed identical text across all 17 sampled
  SKUs (queried live from `queue_jobs` 2026-07-14).
- In `src/tgw/workers/ebay_draft.py` around line 297-353, category
  resolution: `category_id = item.get('ebay_category_id')`; if absent, it
  calls `best_category(...)` (line 306-308); if *that* still returns
  nothing, it falls back to **`category_id = '99'`, explicitly commented
  `# eBay "Everything Else" — non-leaf, eBay will prompt`** (line 326).
  This fallback is a strong lead: `'99'` is knowingly non-leaf by the
  code's own comment. Confirm whether any of the 17 dead-lettered SKUs
  actually have `ebay_category_id == '99'` in their item JSON — if so,
  that's the direct cause and the question becomes why staging wasn't
  already rejecting/handling `'99'` specially before this batch.
  If none of the 17 have `'99'`, the bug is instead in `best_category()`
  itself returning a non-leaf category ID from its lookup — investigate
  that function's source (category tree traversal / `category_groups`
  mapping) for a gap.

## Spec
1. Pull the actual `ebay_category_id` for a sample of the 17 affected SKUs
   (`tgw item get <SKU>` or read the item JSON directly) to determine
   which of the two causes above is real — do not assume, verify live.
2. If it's the `'99'` fallback: `ebay_stage.py` (or `ebay_draft.py` before
   ever queuing to stage) needs to either (a) refuse to queue an item for
   staging with a known-non-leaf category and instead raise a durable,
   queryable finding (invariant C11) for operator resolution, since eBay
   will always reject it, or (b) resolve a *real* leaf category instead of
   falling back to `'99'` — check whether a better fallback leaf category
   exists in the category-groups config before defaulting to
   "Everything Else".
3. If it's `best_category()` returning a genuinely non-leaf ID: fix the
   category-tree lookup to only return leaf nodes (eBay's taxonomy API
   distinguishes leaf vs. non-leaf categories — check whether that flag is
   already available in the cached/fetched tree data and just not being
   checked).
4. Either way: don't let this class of item loop into dead_letter with a
   generic HardFailure and no actionable trail — the fix should either
   prevent the bad category from ever being staged, or surface a clear,
   queryable operator finding when it can't resolve a leaf category.

## Out of scope
- Any category *content* mapping decisions (which category is "correct"
  for a given item type) — this is a leaf/non-leaf mechanical validity
  bug, not a category-accuracy improvement. Don't touch PP-CATPICK-001's
  broader design.
- Requeuing the 17 dead-lettered jobs — separate step after merge.

## Dataset
If the fix changes what `ebay_category_id` gets written for future items,
that's a derived/recomputable field per the Data Charter — fine to change
going forward. Do not bulk-rewrite the 17 already-affected items' stored
`ebay_category_id` as part of this packet; that's an operator/requeue
decision after the code fix is verified.

## Acceptance (live)
1. Reproduce the failure path with a unit test using one of the real
   affected SKUs' data shape (or a synthetic item that would hit the same
   fallback/lookup gap you identified).
2. Confirm the fix either prevents staging with a non-leaf category or
   correctly resolves a leaf category instead.
3. Run the full offline suite — zero regressions.
4. Report in the result manifest which of the two causes (§Spec 2 vs 3)
   was confirmed live, with the SKU/category evidence.

## Quota/risk
Low — no new eBay API calls unless your fix needs one additional taxonomy
lookup per affected item at draft time (acceptable, same call class
already made routinely).
