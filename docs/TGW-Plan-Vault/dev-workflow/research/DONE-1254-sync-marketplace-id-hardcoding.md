# DONE #1254 — sync.py hardcoded marketplaceId=EBAY_US on offer create+lookup

## Investigation (live, read-only, zero risk)

**Confirmed the exact bug mechanism** using the real known Motors item
`tgw20160122242616788` (offerId 262214442018):
- `_find_offer`'s EBAY_US-filtered GET → **404 Not Found** for this real,
  live Motors offer.
- Same GET with `marketplace_id=EBAY_MOTORS` → found correctly.
- Same GET with **no marketplace_id filter at all** → also found correctly
  (a bare `sku=` query returns the one offer regardless of marketplace) —
  the simplest, safest fix for the read side.

**Corrected an earlier wrong assumption** (mine, from the #1255 discussion):
eBay Motors does NOT share the EBAY_US category tree. Verified live:
- `get_default_category_tree_id(marketplace_id=EBAY_MOTORS)` → 400 (not a
  valid value for that endpoint).
- Real Motors category IDs (100449, 14769, 172517 from actual known Motors
  items) → 404 against EBAY_US's tree (ID 0).
- `get_default_category_tree_id(marketplace_id=EBAY_MOTORS_US)` → tree ID
  **100**, and the same three category IDs resolve correctly there.
- Confirms eBay uses **different marketplaceId spellings across API
  families**: Sell/Inventory API's offer.marketplaceId uses `EBAY_MOTORS`;
  Taxonomy API's marketplace_id param uses `EBAY_MOTORS_US`. Both refer to
  the same marketplace.
- Public documentation research (developer.ebay.com via search) confirms
  eBay does NOT auto-override a mismatched marketplaceId/categoryId
  combination on createOffer — the caller must supply the correct
  marketplaceId or the category is rejected as invalid. This means the old
  hardcoded EBAY_US would likely make offer creation FAIL OUTRIGHT for any
  genuinely new item in a Motors-tree category — a live-blocking bug for
  the ongoing acquisition Dave described, not just a historical curiosity.

## Fix (src/tgw/ebay/sync.py)
1. `_find_offer()`: dropped the `marketplace_id` filter entirely — finds
   the SKU's real offer regardless of which marketplace it's actually on.
2. New `_is_motors_category(cfg, category_id)`: one live Taxonomy API call
   per distinct category_id per process lifetime (cached), checking
   membership in Motors' tree (ID 100). Fails closed to False (today's
   existing EBAY_US behavior) on any error — never blocks a draft push.
3. `_build_offer_bodies()`: `offer_body['marketplaceId']` is now
   `EBAY_MOTORS` when `_is_motors_category()` says so, else `EBAY_US` (was:
   always hardcoded `EBAY_US`).
4. `stage_draft()`'s UPDATE path (existing offer found): overrides the
   category-based guess with the **existing offer's own live
   marketplaceId** — ground truth always wins over a guess.

## Tests (tests/test_ebay_sync.py, +16 new cases)
`_is_motors_category` (found/not-found/cached/empty-id-short-circuits),
`_build_offer_bodies` marketplaceId selection (default vs Motors),
`_find_offer` query shape (no marketplace_id key), `stage_draft` update
path overriding to ground truth, `stage_draft` create path using the
category-based guess. Existing tests' shared fixture now stubs
`_is_motors_category` to False so pre-existing tests stay deterministic
and offline.

pytest -q: 1926 passed (was 1917), same 2 pre-existing unrelated failures
(test_invariants_pricing.py, confirmed via git stash predating this work).

## Live verification (real data, zero writes)
- `_find_offer(cfg, 'tgw20160122242616788')` → correctly returns
  offerId=262214442018, marketplaceId=EBAY_MOTORS (previously would have
  returned None due to the EBAY_US filter bug).
- `_is_motors_category(cfg, '100449')` → True (real Motors category).
- `_is_motors_category(cfg, '12345')` → False, fails closed cleanly on the
  live 400 (arbitrary/generic category not in the Motors tree).
- `ebay_taxonomy` quota: 11/5,000 spent across the whole investigation —
  negligible.

## Follow-up still open (#1255)
Proactive Motors detection at draft time (before an item is ever staged)
still needs its own tree-100 cache analogous to the existing tree-0 cache
in `tgw.apis.ebay.taxonomy`, since that module only caches tree 0. The
per-category live check added here is a correct, low-cost stopgap
(one call per NEW category encountered), not a full local cache — fine for
today's ~200-item Motors footprint, worth reconsidering if that grows.
