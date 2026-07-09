Todo #1244 — retroactive record. Code-review (session 48, conventions angle)
caught that this change was made without its own todo/inbox note, unlike
every sibling change this session (#1234/#1235/#1242/#1243) — a real gap
against CLAUDE.md's "log the work first" rule. Filing this now to close the
recoverability gap.

What happened: Dave asked why multi_intake.py directly patches an existing
ItemData record (strip stale 'Item number', set source_sku) when a derived
child SKU collides with one, instead of routing through the normal photo-
intake folder. Investigation (live eBay pull, exhaustive photo-size search
across all of ItemData/NewItems, git blame on the branch) found:
- The one production case this branch ever touched (tgw202604130911246) is
  a real, currently-Active eBay listing — not test data, not corrupted, but
  also never actually verified safe at the time it was written.
- No sibling children exist to corroborate the "split a combined lot
  listing" story the original code comment claimed.
- bundle_intake._write_item_json()/_copy_images() already no-op safely on
  an existing SKU (idempotent retry handling, unrelated original purpose)
  — dropping the stub into newitems_dir (which multi_intake already does
  unconditionally) is sufficient; the direct patch was redundant AND
  unverified AND fence-bypassing.

Change made: removed the direct atomic_write_json patch of the existing
record entirely. Replaced with log.warning + tgw_logging.log_event +
notify() on collision, relying on bundle_intake's existing safe handling.
Updated the PP-FENCE-001 gap comment (multi_intake.py now has only 1
documented gap, not 2) and tests/test_invariants_items_fence.py's
_FENCE_GAPS comment to match.

Known consequence (surfaced by later code review, session 48): a colliding
SKU that still carries a legacy 'Item number' field now stays gated in
ebay_stage.py's operator-review path (checks legacy_item_number and
routes to check_legacy_duplicate_listing, session 43's P10 design) forever,
since nothing auto-clears it anymore. This is the safe/intended fail-closed
behavior (never auto-resolve an ambiguous duplicate-listing risk), not a
new corruption risk — but the notify() text doesn't currently tell the
operator that specific next step is needed. Follow-up: improve the notify
message, or leave as-is since ebay_stage's own log/log_event already
surfaces the skip reason when it's encountered there.

Evidence: tests/test_multi_intake.py (2 tests) added same session, full
suite passed at time of change (see DONE-1235-atomic-write-sweep.md for the
broader session's test run), worker restarted and confirmed healthy.
