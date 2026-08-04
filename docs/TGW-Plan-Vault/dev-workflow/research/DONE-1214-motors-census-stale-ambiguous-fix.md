# DONE #1214 — ebay_motors_census.py stale-data + ambiguity auto-resolve bug

## Bug
`--apply` decided "is this a Motors SKU" from whether EBAY_MOTORS EVER
appeared for that SKU across ALL captured `*.jsonl.gz` files (a Python set,
no recency), and then unconditionally patched every such SKU — including
ones the script's own "Cross-marketplace multi-offer SKUs" report section
explicitly flags as ambiguous and "needs human review, not auto-resolution."
Classic C11 stale-flag-vs-live-data class: the apply path silently resolved
the exact ambiguity the report told the operator not to auto-resolve.

## Fix
`scripts/ebay_motors_census.py`:
- `_iter_offer_records()` now also yields the capture file's date (parsed
  from the `YYYY-MM-DD.jsonl.gz` filename — zero extra API calls, matches
  the script's "Zero API calls" design).
- A SKU's Motors status is now decided by its MOST RECENTLY captured
  marketplaceId only (`sku_latest`), not "ever seen as EBAY_MOTORS."
- Any Motors SKU that ALSO appears under a different marketplaceId in ANY
  capture (`ambiguous_motors = motors_skus ∩ multi_marketplace`) is excluded
  from `--apply` entirely — reported separately, never silently patched.
- Report + apply/dry-run summary output updated to show safe vs ambiguous
  counts explicitly.

## Tests (tests/test_ebay_motors_census.py)
- Updated 3 existing tests for `_iter_offer_records()`'s new 4-tuple shape
  (added capture_date).
- 3 new tests: recency overrides an old stale EBAY_MOTORS record; an
  ambiguous cross-marketplace SKU is excluded from the safe-to-patch set
  even when its LATEST record says Motors; an unambiguous Motors SKU stays
  safe (no over-exclusion).
- `pytest -q`: 1892 passed (was 1889), same 2 pre-existing unrelated
  failures (test_invariants_pricing.py).

## Live verification (dry-run against real capture data, no writes)
Ran the script against all 7 real `/opt/TGW/incoming/ebay/*.jsonl.gz` files
(1.16 GB, 95,746 offer records, 19,452 unique SKUs):
- 202 Motors SKUs found, 0 currently ambiguous — the fix doesn't change
  today's real outcome (all 7 days of capture happen to agree for these
  202 SKUs), confirming the fix is behavior-neutral on current data while
  closing the real risk for future drift/ambiguity.
- Cross-checked old ("ever seen") vs new ("most recent") logic directly:
  identical 202-SKU sets on the live dataset — no silent behavior change,
  purely a safety net for when the two diverge.

## Follow-up (Dave, 2026-07-09, PP-EBAY-MOTORS-001)

Dave clarified Motors is ongoing acquisition (automotive parts/accessories,
actively bought and listed), not a historical one-off — upgraded priority
per plan doc steps 2+3.

### #1 — marketplace_id kept current on edit, not just initial backfill
`src/tgw/workers/ebay_sync.py`'s `_sync_one()` now re-derives `marketplace_id`
from the LIVE offer response's `marketplaceId` on every sync pass — both the
first time a newly staged item is ever synced, and every time afterward,
including the `ebay_sync` job `apply_revision()` (http_server.py) already
enqueues right after a live category-change PUT. Never invented locally,
always read from eBay; a missing/malformed field on the offer response
never clears a known-good stored value. New tests:
tests/test_ebay_sync_marketplace_id.py (5 cases).

### #2 — site_id threaded through trading_call()
`src/tgw/apis/ebay/trading.py`: `trading_call()` gained an optional `site_id`
param (default unchanged, EBAY_US). All 9 public wrappers (get_orders,
get_my_ebay_selling, get_store_categories, end_item, revise_item_sku,
revise_item_pictures, get_api_access_rules, get_best_offers,
respond_to_best_offer) now accept `marketplace_id` (e.g. 'EBAY_MOTORS'),
translated internally via `_resolve_site_id()` — callers pass the same
string already used elsewhere (item['marketplace_id']), never need
Trading API's separate numeric SiteID. Fully backward compatible — no
existing call site's behavior changes (all still default to EBAY_US).
New tests: tests/test_trading_site_id.py (14 cases).

### Adjacent findings surfaced, NOT fixed (flagged, filed as new todos)
- **#1254**: `sync.py`'s `stage_draft()`/`_find_offer()` hardcode
  `marketplaceId=EBAY_US` on every offer create AND lookup, regardless of
  category. `_find_offer`'s GET is filtered to `marketplace_id=EBAY_US`, so
  if a SKU's real offer already lives under EBAY_MOTORS, it reads as "not
  found" and `stage_draft` could attempt to create a DUPLICATE offer under
  EBAY_US. All 202 known Motors items are 2015-2016 legacy SKUs (predate
  this pipeline) — never verified against current sync.py flow for a
  genuinely new Motors-category item. Did NOT touch this — it's a live
  offer-creation code path with real business risk, needs Dave's read first.
- **#1255**: proactive Motors detection at draft time via the cached
  EBAY_US taxonomy tree (Motors is a branch of the same tree, not separate)
  + finding where Best Offer actually gets enabled (not in TGW's own
  offer-creation code — likely an eBay account default policy).

pytest -q: 1911 passed (was 1892), same 2 pre-existing unrelated failures.
