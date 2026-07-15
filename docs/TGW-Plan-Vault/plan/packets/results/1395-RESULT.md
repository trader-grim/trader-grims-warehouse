# Result: 1395 ebay-stage-non-leaf-category
Status: done
Todo: #1395   PP: PP-DEADLETTER-001
Files touched:
- src/tgw/workers/ebay_stage.py
- tests/test_invariants_stage_guards.py (2 new tests)

Live evidence:
- Confirmed live cause via `psql state_machine`: all 17 `ebay_stage`
  dead_letter jobs with error text "The category selected is not a leaf
  category" (2026-07-05 19:51 batch) — sampled `tgw201501021970513`,
  `tgw201501062426172`, `tgw201501071306485`, `tgw201501082021595`,
  `tgw201412241655220` and confirmed via item JSON:
  `draft_listing.category_id == '99'` on the sampled SKU
  (`tgw201501021970513`), matching the packet's §Spec cause 2 (the `'99'
  Everything Else` fallback in `ebay_draft.py` line ~326), not cause 3
  (`best_category()` returning a non-leaf ID — no evidence of that; these
  items never had `ebay_category_id` resolved at all, so they hit the
  `'99'` fallback path directly).
- Fix: added a guard in `ebay_stage.py` (before `stage_draft()` is called,
  same location/shape as the existing title-length and no-price guards)
  that HardFails locally when `draft_listing.category_id == '99'` and
  persists a durable `pipeline_error` finding
  (`code: category_not_leaf`) via `fence_patch_item` — invariant C11:
  operator-queryable finding instead of a silent dead-letter loop, and no
  more wasted/guaranteed-failing eBay API call for this class of item.
- Tests: `pytest -q tests/test_invariants_stage_guards.py` → 25 passed
  (23 existing + 2 new: `test_fallback_category_99_never_staged_no_api_call`
  reproduces the real dead-letter shape and asserts `stage_draft` is never
  called + the finding is persisted; `test_real_leaf_category_stages_normally`
  is the control, confirming normal leaf categories are unaffected).
  Verified against the worktree copy (`tgw.workers.ebay_stage.__file__`
  resolved under the worktree path, not the shared checkout) with
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src`.
- Full offline suite: `pytest -q` → 2212 passed, 1 skipped, 0 failed —
  zero regressions.

Deviations from spec: none. Chose option (a) from §Spec item 2 (refuse to
queue/stage + durable finding) rather than (b) (resolve a real fallback
leaf category) — the packet explicitly scopes (b)'s category-accuracy
judgment call as out of scope ("Any category *content* mapping decisions
... don't touch PP-CATPICK-001's broader design"), and (a) is the
mechanical leaf/non-leaf validity fix the packet asks for.

Out-of-scope findings filed: none — no new adjacent bugs found. (Per
packet's "Out of scope" note, the 17 already-dead-lettered jobs were not
requeued and `ebay_category_id`/`category_ids` on the 17 affected items
were not bulk-rewritten; that's a separate operator/requeue step after
merge.)
