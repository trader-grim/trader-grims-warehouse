# DONE #1255 — proper local Motors category tree cache

Replaces the per-category live-call stopgap `_is_motors_category` (todo
#1254) added directly in sync.py with a real disk+memory cache of the
full Motors tree, mirroring the existing EBAY_US tree cache architecture
in `tgw.apis.ebay.taxonomy`.

## What was built (src/tgw/apis/ebay/taxonomy.py)
- `_MOTORS_TREE_ID = '100'` (hardcoded, confirmed live/stable, same
  pattern as `_EBAY_US_DEFAULT_TREE_ID`).
- `_ensure_motors_tree_index(cfg)` — lazy build/cache of the Motors
  category id → node index, disk-cached to
  `catalog_root/ebay-motors-category-tree.json`, same never-auto-expires
  contract as the EBAY_US tree cache (reuses the existing tree-agnostic
  `_build_index`/`_flatten_tree` helpers — no duplication there).
- `refresh_motors_category_tree_cache(cfg)` — force re-fetch, mirrors
  `refresh_category_tree_cache` for EBAY_US.
- `is_motors_category(cfg, category_id)` — the real membership check,
  backed by the cache (not a live call per category). Fails closed to
  False on any fetch error.

Deliberately a PARALLEL set of functions/caches, not a `tree_id` param
threaded through every existing EBAY_US function — the EBAY_US tree is the
hot path for the whole drafting pipeline (category suggestions, search,
browse UI) and stays completely untouched; this is purely additive.

## sync.py updated
`_is_motors_category()` now delegates to
`taxonomy.is_motors_category()` instead of doing its own ad-hoc
per-category live call + local dict cache — removes the duplicate caching
logic added as a stopgap in #1254.

## Tests
- New `tests/test_motors_category_tree.py` (9 cases): fetch+disk-cache,
  disk-cache-reused-without-live-call, never-auto-expires, refresh forces
  re-fetch, is_motors_category true/false/empty-id/fetch-failure/cached.
  Mirrors `tests/test_category_tree.py`'s proven pattern for the EBAY_US
  tree.
- `tests/test_ebay_sync.py`: replaced 4 tests that exercised the old
  ad-hoc live-call behavior with 1 delegation test (sync._is_motors_category
  calls taxonomy.is_motors_category).

pytest -q: 1932 passed (was 1926), same 2 pre-existing unrelated failures
(test_invariants_pricing.py, confirmed via git stash predating this work).

## Live verification (real data, one full-tree fetch, no writes)
- `_ensure_motors_tree_index()` against production config: **3,287
  categories** loaded in 0.46s.
- All 3 real known Motors category IDs (100449, 14769, 172517) resolve
  True from the cache.
- A generic non-Motors category (12345) resolves False.
- Disk cache written correctly: `ebay-motors-category-tree.json`, 869KB,
  644 perms (matches the sibling EBAY_US tree cache file's permissions).
- `ebay_taxonomy` quota: 12/5,000 spent total across #1254+#1255
  investigation and verification — negligible.

## Best Offer enablement question (from the original #1255 brief)
Still open, unrelated to the tree-cache work — Best Offer isn't set
anywhere in TGW's own offer-creation code (grep confirmed in #1254's
investigation), so it's likely an eBay account-level default listing
policy setting. Not investigated further here; would need Seller Hub
account settings review, not a code search.

## Best Offer clarification (Dave, 2026-07-10)
Corrected: Best Offer is a per-ITEM setting on the Inventory API offer
(`listingPolicies.bestOfferTerms`), not an eBay account-level default as I'd
guessed. It's simply not exposed anywhere in TGW's current intake/draft
form — `_build_offer_bodies()` never sets it, so whatever a listing shows
today is either an eBay category default or a manual Seller Hub change
outside TGW's records (same class of drift invariant C11 exists for).
No form field, no item JSON field, no wiring into the offer payload exists
yet. Not built — would need: (1) a field on draft_listing, (2) exposing it
in the operator-facing intake/draft form, (3) wiring into
`_build_offer_bodies`'s listingPolicies construction. Flagging as a
candidate feature, not filing a todo yet pending Dave's call on priority.
