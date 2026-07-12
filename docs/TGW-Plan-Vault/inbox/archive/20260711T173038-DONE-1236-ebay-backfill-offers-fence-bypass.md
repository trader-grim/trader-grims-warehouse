# DONE #1236 — ebay_backfill_offers.py bypassed the tgw-api fence

## Bug (audit#1143 MERGED #1204+#1205)
Script read/wrote item JSON directly via `atomic_write_json` instead of
`apis.fence.ebay_write`, causing two symptoms from one root cause:
1. Lost-update race: a concurrent ebay_sync/ebay_publish fence write could
   be silently discarded, and the fence's protected-subfield merge logic
   (price_comps, staged_at, photo_verify) was skipped entirely.
2. No catalog_rebuild enqueue (invariant A7) — catalog/thumbnails went
   stale after a fleet-wide run.

## Fix
`scripts/ebay_backfill_offers.py`: replaced the `atomic_write_json` import
and write call with `apis.fence.ebay_write`. Passes ONLY the new
offer_id/listing_id/price/category_id/listing_status/sold_quantity fields —
deliberately NOT a locally pre-merged copy of the existing ebay_offer/
ebay_listing blocks, since sending a stale local merge back would
reintroduce the same lost-update race one layer up. The fence's own
server-side merge (which already preserves protected sub-fields it knows
about) handles reconciliation with whatever the current live state is.
Read path (has_offer_data check, existence check) left as direct JSON
reads — consistent with other scripts/ one-shot tools (recompile_category_
backfill.py, data_scrub_legacy_ebay_fields.py) and not part of the flagged
bug (only the write path was cited).

The fence write path (POST /api/items/{sku}/ebay-write, http_server.py:880)
already calls `_enqueue_catalog_rebuild` — confirmed by reading the endpoint
directly — so this one change closes both symptoms as the todo predicted.

## Tests (tests/test_ebay_backfill_offers.py, new file)
6 cases: writes go through fence_ebay_write with correct field values;
only new fields are passed (not a locally pre-merged block, proven via an
item with an existing protected-looking sub-field that must not appear in
the fence call); already-has-offer-data SKUs are skipped; SKUs with no
local ItemData are skipped; no-offer-found SKUs aren't written; a fence
write failure on one SKU doesn't stop the run from attempting the next.

pytest -q: 1917 passed (was 1911), same 2 pre-existing unrelated failures
(test_invariants_pricing.py, confirmed via git stash predating this work).

## Live verification note
Did NOT run the script live. A local scan shows 45,522/55,419 items missing
offer_id/listing_id, but this number is NOT a live-backlog indicator — the
vast majority are draft/unpublished items that were simply never listed
(expected; matches the earlier "2,089 published listings have offer_id/
listing_id/price" backfill-complete note). The script's actual candidate
set is bounded by eBay's live Inventory API listing page (Step 1), which
requires a live API call to know for certain, and any real run performs
production fence writes at whatever scale it finds. Flagged to Dave rather
than run unprompted — acceptance criteria for this todo was pytest offline,
not a live run.
