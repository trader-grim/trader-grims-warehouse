# DONE — todo #1168 (audit#1143)

`src/tgw/workers/ebay_publish.py`'s 25021 condition-rejection fallback
(triggered when eBay rejects `publish_offer()` because the category doesn't
support the item's granular condition) retries with `condition:
USED_EXCELLENT` directly against the inventory_item endpoint and succeeds —
but never wrote the corrected condition back into `draft_listing`. The next
time this SKU went through `ebay_stage` (any edit/re-stage), `stage_draft()`
would rebuild its inventory-item PUT from the still-stale
`draft_listing.condition_enum`, hit the same 25021 rejection again, and
silently re-run its own separate fallback in `sync.py` — an unrecorded
400+fallback round-trip repeating forever, with the local record
permanently disagreeing with what eBay actually has live.

## Fix
After the fallback retry succeeds, `item['draft_listing']` is updated in
place: `condition_id='3000'`, `condition_label='Used'`,
`condition_enum='USED_EXCELLENT'` — matching the same field shape
`ebay_draft.py` produces via `best_condition()`. No new fence-write call was
needed: `ebay_publish.py`'s existing end-of-function `fence_patch_item(...,
draft_listing=item.get('draft_listing'), ...)` call already persists the
whole `draft_listing` dict, so mutating it in-memory at the fallback site is
sufficient — the fix reuses existing infrastructure rather than adding a new
write path.

## Tests
New `tests/test_ebay_publish_condition_fallback.py` (mirrors the existing
`test_ebay_publish_price_drift.py` pattern — all eBay/state_machine/fence
calls mocked, fully offline):
- 25021 fallback succeeds → `draft_listing.condition_id/condition_label/
  condition_enum` are corrected in the persisted `fence_patch_item` call
- a non-25021 rejection error still raises `HardFailure` and records
  `pipeline_error` as before — condition fields untouched (regression guard
  against over-broadly triggering the new write-back)

`pytest -q tests/test_ebay_publish_price_drift.py
tests/test_ebay_publish_condition_fallback.py`: 4/4 pass. Full suite: 1965
passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only, no eBay calls made)
- Grepped `/opt/TGW/var/log/worker_ebay_publish.log.1` for historical 25021
  occurrences (3 found, 2026-06-03) and checked their current item JSON:
  all 3 now show `draft_listing.condition_enum=USED_EXCELLENT` and
  `ebay_listing.status=PUBLISHED` — they eventually succeeded through some
  other path (a later draft regeneration), not through this fallback, so
  they don't currently exhibit the bug.
- Confirmed the actual failure mode is unobservable after the fact: because
  the original bug never persisted anything when the fallback fired, there
  is no durable signal anywhere in the dataset distinguishing "this item hit
  the 25021 fallback" from "this item was never granular in the first
  place." This is itself consistent with the bug report — the fix's value
  is that future occurrences will now be recorded in `draft_listing`, where
  they can be found by future audits/catalog-verify checks.
- No live eBay API calls were made for this verification (the fix's
  behavior was proven via realistic mocked HTTPError/25021 responses in the
  new tests instead, avoiding unnecessary metered eBay API usage).

No deviations from the todo brief. No config/secrets/OAuth scopes touched.
