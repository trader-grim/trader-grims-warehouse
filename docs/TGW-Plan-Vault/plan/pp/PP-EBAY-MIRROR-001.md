## PP-EBAY-MIRROR-001 — Canonical eBay Data Mirror

**Opened:** 2026-06-28 (session 33)
**Status:** Phase 1 PENDING — unblocks ebay_sku_migrate immediately
**Driver:** Repeated data gaps (photo URLs, title, condition, aspects, marketing state) traced
to the same root cause: `ebay_live` is populated correctly by `sync_inventory_api`, but nothing
propagates those values to canonical per-item fields. Every gap discovered has required a
one-shot backfill script. This PP owns the fix permanently.

### Problem
`ebay_live.inventory_item.product.imageUrls` contains EPS photo URLs for all 2,138 listed items.
`ebay_offer.photo_urls` is `None` for 2,137 of them. Same pattern repeats for title, condition,
aspects, description. `draft_listing` has a key mismatch (`image_urls` vs `imageUrls`).
Marketing state (active markdown, promotions) is never pulled at all despite `sell.marketing`
scope being held.

### Supersedes / retires
- `scripts/ebay_backfill_offers.py` — one-shot; replaced by Phase 2 steady-state propagation
- `scripts/ebay_audit.py` — one-shot audit; Phase 2 makes it redundant
- **PP-EBAY-SNAPSHOT-001 Backfill (todo #894)** — GET inventory_item backfill absorbed here;
  todo #894 closes when Phase 1 complete
- The `_migrate_inventory` → photo_urls failure in `ebay_sku_migrate` is fixed by Phase 1
  (photo_urls populated before migration re-runs; no code change to worker needed)

### Integrates with
- **PP-SYNC-001 (DONE)** — `sync_inventory_api` + `backfill_draft_from_live` in `pull.py` are
  the existing infrastructure; Phase 2 adds the propagation step after every pull
- **PP-EBAY-SNAPSHOT-001 Phase 3** — periodic photo integrity check runs in the same `ebay_sync`
  extension as Phase 2; coordinate so one GET serves both
- **PP-PROMO-001 P3** — Phase 3 `ebay_marketing` block feeds the dead-stock scanner directly;
  P3 unblocks PP-PROMO-001 P3 (`tgw promo apply`) since it can now detect items already
  under markdown and skip them automatically
- **PP-REPRICER-001** — watcher count (Phase 3) is a sell-through signal; feeds future repricer

### Canonical `ebay_offer` fields after Phase 1
After normalization, `ebay_offer` is the single read target for all eBay state:
```
ebay_offer.offer_id, listing_id, listing_url, price, category_id  ← already present
ebay_offer.photo_urls    ← from ebay_live.inventory_item.product.imageUrls
ebay_offer.title         ← from ebay_live.inventory_item.product.title
ebay_offer.condition     ← from ebay_live.inventory_item.condition
ebay_offer.aspects       ← from ebay_live.inventory_item.product.aspects
ebay_offer.description   ← from ebay_live.offer.listingDescription (HTML)
```
`draft_listing.imageUrls` key mismatch (`image_urls` → `imageUrls`) fixed in same pass.

### New `ebay_marketing` block (Phase 3)
```json
"ebay_marketing": {
  "in_markdown": false,
  "markdown_discount_pct": null,
  "markdown_sale_price": null,
  "markdown_ends_at": null,
  "promotions": [],
  "watcher_count": null,
  "watchers_updated_at": null
}
```

### Phases

| Phase | Scope | Status |
|-------|-------|--------|
| P1 | Normalization pass — copy `ebay_live` → `ebay_offer` fields, fix `image_urls`/`imageUrls` key, construct 49 missing `listing_url`s. Script: `scripts/ebay_normalize.py`. No API calls. Directly unblocks `ebay_sku_migrate`. | ✅ DONE 2026-06-28 (session 34) — 19,394 updated, 0 errors |
| P1.5 | Photo push reconciliation — upload missing local photos to EPS, PUT full `imageUrls` to live inventory_item. Script: `scripts/ebay_photo_push.py`. Run after migration. ~1,135 items. | ✅ DONE (script written + dry-run verified; run pending migration completion, todo #1073) |
| P2 | Propagation in `ebay_sync` — after every `apply_ebay_live()` call, run normalization step automatically. Coordinate with PP-EBAY-SNAPSHOT-001 P3 photo integrity check: one `GET inventory_item` serves both. Retires one-shot scripts permanently. | ✅ DONE 2026-06-28 (session 34) — two changes in `_sync_one()` + `_check_photo_integrity()`; worker restarted |
| P3 | Marketing pull — sweep all active/scheduled promotions via `GET /sell/marketing/v1/promotion` (scope: `sell.marketing` ✅); match to items; write `ebay_marketing` block. Add `watcher_count` from Trading API `GetItem` (batch by listing_id). Schedule in `ebay_sync` weekly. | PENDING |

### Execution sequence
P1 runs as a standalone script (no workers needed, safe while workers stopped).
After P1: restart workers + re-run `ebay_sku_migrate` — remaining 2,138 items will now succeed.
After migration: run P1.5 (photo push) to restore full photo sets on 1,019 + 116 items.
P2 built into `ebay_sync` in the same session or next.
P3 is independent — can run after workers are stable.

### P1.5 — Photo push reconciliation (2026-06-28)
**Context:** 1,019 live listings have fewer photos on eBay than exist locally in ItemData.
116 items have no EPS photos at all. Root cause: original listings were submitted with 1 photo
(or few photos); local files were not pushed in full. `ebay_live.inventory_item.product.imageUrls`
now gives us visibility into the gap for the first time.

**Fix:** Script `scripts/ebay_photo_push.py` — for each item where local photo count > eBay EPS count:
  1. Upload missing local photos via `upload_photo()` to EPS (idempotent via `ebay_photos` dedupe)
  2. Collect full EPS URL list from `ebay_photos`
  3. GET current `inventory_item` body from eBay
  4. Update `product.imageUrls` with full set, PUT back to `/sell/inventory/v1/inventory_item/{sku}`
  5. Write updated `ebay_live` + enqueue `catalog_rebuild`

Run AFTER `ebay_sku_migrate` completes — items must be on canonical SKUs first.
Scope: ~1,135 items. Rate: ~5 API calls/item at 0.5s delay = ~20 min total.
Status: **PENDING** (todo #1073)

Also update the phase table above to add P1.5 row.

---

