---
title: eBay Bulk Audit Sources — cost/coverage ranking
updated: 2026-07-03
---

# eBay Bulk Audit Sources (PP-PHOTOSYNC-001 P9)

Dave, s43: "there is likely for us to do a bulk, maybe even whole site audit for
less api cost if we look hard through all of our scopes." Per-SKU offer pulls
(R1.8's current method) cost ~19,486 calls for full coverage. This note ranks every
candidate reachable with our LOCKED scopes (`sell.inventory`, `sell.account`,
`sell.marketing`, Trading IAF — never request new; a blocked candidate stays
blocked, it does not become a scope-expansion request).

## Ranking (cheapest + most useful first)

### 1. Inventory API bulk `GET /sell/inventory/v1/inventory_item` (paged) — WINNER
- **Scope:** `sell.inventory` (already have)
- **Cost:** ~98 calls for all 19,486 items at `limit=200` — live-verified 2026-07-03
- **Coverage:** ALL SKUs (confirmed `total: 19486` in the live response)
- **Live-verified finding:** the bulk LIST response already includes full
  `product.imageUrls` and `availability.shipToLocationAvailability.quantity` per
  item — no separate per-SKU GET needed for photo-count or quantity truth. Sample:
  ```
  tgw202606021133367 -> imageUrls: 24, availability: 1
  tgw202605131827555 -> imageUrls: 8,  availability: 1
  tgw202606021107459 -> imageUrls: 5,  availability: 1
  ```
- **What it does NOT give:** offer status/price/staged state — that still needs
  the per-SKU offer GET (R1.8's current approach) if that data is the goal. For
  P7's specific need (live photo-count truth to replace the local-mirror
  comparison in `photos_short_on_ebay`), this bulk list is sufficient on its own.
- **Recommendation:** this is the source PP-PHOTOSYNC-001's next micro-packet
  should point `photos_short_on_ebay` at, instead of `draft_listing.imageUrls`
  (our own local record, which can itself be stale/wrong — exactly the class of
  bug this whole track exists to catch). ~200x cheaper than a full R1.8-style
  per-offer snapshot for this specific comparison.

### 2. Trading `GetMyeBaySelling` — ALREADY LIVE, but narrower than assumed
- **Scope:** Trading IAF token (already have)
- **Cost:** ~98 calls/day at `EntriesPerPage=200` (5,000/day Trading pool — plenty
  of headroom; today's spend was 502/5,000 from this + GetOrders)
- **Already running**: `ebay_legacy_sync` worker, daily (`SYNC_INTERVAL_S = 24h`),
  via `tgw.ebay.pull.sync_active_listings` / `tgw.apis.ebay.trading.get_my_ebay_selling`
- **Coverage gap found**: `_apply_active_listing()` explicitly SKIPS any listing
  where `ebay_listing.api == 'inventory'` (`src/tgw/ebay/pull.py:426-428`) — i.e.
  it only ever updates the ~10,000 legacy/Trading-native listings, never the
  ~9,403 modern Inventory-API items. It is NOT currently a whole-site source
  despite pulling "every active listing" per its own docstring.
- **Extraction gap found**: `_item_from_xml()` (`src/tgw/apis/ebay/trading.py:154`)
  parses `ItemID/Title/SKU/CustomLabel/ListingStatus/CurrentPrice/ViewItemURL/
  Quantity/QuantitySold` but never `PictureDetails`/`GalleryURL`, even though
  `DetailLevel=ReturnAll` means the raw XML almost certainly carries it (Trading
  API schema). The raw XML itself IS captured via `capture_response` (E7) — the
  data isn't lost, just never extracted into anything usable today.
- **Recommendation:** low priority to extend (source #1 already covers photo
  truth for the harder cohort — inventory-API items). If ever needed for the
  legacy cohort specifically, extracting `PictureDetails` from the already-
  captured raw XML is a parsing change, not a new API call.

### 3. Feed API `ACTIVE_INVENTORY_REPORT` — BLOCKED ON SCOPE, do not request
- **Scope required:** `sell.item.feed` — **NOT** in our granted scope set
  (confirmed against `reference/eBay-API-Landscape.md` "✅ Have": sell.inventory,
  sell.account, sell.marketing only). The packet's original hypothesis ("sell.
  inventory scope family") was wrong — Feed API is its own scope, separate from
  Inventory API.
- Per the standing rule (scopes LOCKED, broke OAuth 2026-06-05): this candidate
  is **blocked, not requested**. Recorded here so no future session re-discovers
  and re-proposes it without checking first.

### 4. R1.8's current per-SKU offer pull — keep for what it uniquely provides
- Still the only source for offer-level detail (price, staged status, listing
  policies) at full fidelity. Not being replaced — #1 above is specifically a
  cheaper substitute for the PHOTO-TRUTH comparison, not a replacement for R1.8's
  broader snapshot purpose.

## Bottom line

A whole-site photo-truth audit costs ~98 calls (Inventory API bulk list), not
19,486 (per-SKU offers) and not requiring any new scope. `GetMyeBaySelling` is
already running daily but doesn't cover the modern-pipeline cohort or extract
photo fields — low-priority gaps, not blockers, since source #1 already covers
the higher-value cohort. Feed API is a dead end under current scopes.
