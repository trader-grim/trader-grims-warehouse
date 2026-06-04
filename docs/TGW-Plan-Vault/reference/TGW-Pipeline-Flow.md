---
title: TGW Worker Pipeline Flow
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# TGW Worker Pipeline Flow

## Infrastructure Workers
### token_refresh
- Trigger: self-scheduling; fires when token expires within 30 min
- Does: OAuth token refresh via eBay API
- Writes: `secrets_root/ebay-token.json`
- On hard fail: dead_letter + notify
- Next: none (standalone)

### pm_intake
- Trigger: scans `docs/TGW-Plan-Vault/inbox/*.md` on startup loop
- Does: reads note → Ollama Qwen2.5 classifies → patches Master Plan → archives file
- Writes: `TGW-Master-Plan.md`
- Next: none (standalone)

### catalog_rebuild
- Trigger: enqueued by almost every other worker; dedupe_key=`catalog_rebuild:pending`; 30s not_before (coalesces rapid writes)
- Does: `build_all_catalogs()` — JSON catalog + SQLite + location tree
- Writes: `search-catalog.json`, `tgwcatalog.db`, location tree
- Next: none

### thumbnail_gen
- Trigger: enqueued per-SKU after intake
- Does: generates `{sku}.jpg` thumbnail from primary photo (Pillow)
- Writes: `catalog_root/thumbnails/{sku}.jpg`
- Next: none

---

## Intake Pipeline

### bundle_intake
- Trigger: polls `incoming/newitems/` on startup loop; stability gate (30s unmodified)
- Detects
  - Dir `newitems/<SKU>/` → bundle job
  - Zip `newitems/<SKU>.zip` → bundle job
  - Dir `newitems/multi/<SKU>/` → multi_intake job
- Does: moves photos to `ItemData/<SKU>/`; writes stub `<SKU>.json`
- Writes: `ItemData/<SKU>/<SKU>.json` + photos
- Next: `catalog_rebuild` (30s) + `thumbnail_gen` + `ai_identify`

### multi_intake
- Trigger: enqueued by bundle_intake for multi-item bundles
- Does: splits bundle into child SKUs (parent SKU + sequential increment)
- Strips `Item number` from child JSON if present
- Writes `source_sku` field on child items
- Next: (same as bundle_intake downstream per child SKU)

---

## Identification Pipeline

### ai_identify
- Trigger: enqueued by bundle_intake; also manual via `tgw hint` or HTTP action
- Skip condition: `ai_identified: true` AND no `ai_reidentify` flag
- Does
  - Checks `upc` / `isbn` field → (future: PP-LOOKUP-001 product lookup)
  - Checks `ai_hint` → prepends "I know this item is: {hint}" to prompt
  - Resizes primary photo to 512px → sends to Ollama `qwen2.5vl:7b`
  - Parses JSON response: title, category, description, condition
- Writes: title, category, description, condition, `ai_identified: true`, clears `ai_reidentify`
- Next: `ebay_draft` + `catalog_rebuild`

---

## eBay Listing Pipeline

### ebay_draft
- Trigger: enqueued by ai_identify
- Skip condition: `draft_listing` already present (idempotent)
- Does
  - Taxonomy API: getCategorySuggestions → `ebay_category_id`
  - Taxonomy API: getItemAspectsForCategory → aspects list
  - Ollama Qwen2.5: fills SELECTION_ONLY + FREE_TEXT aspects
  - Conditions: `best_condition()` → `condition_id`, `condition_label`, `condition_enum`
  - Builds `draft_listing` block (title, categoryId, condition, format, qty, price=null, item_specifics, description)
  - (future PP-QUALITY-001: compute quality score here)
- Writes: `draft_listing` block in item JSON
- Next: `catalog_rebuild` + `ebay_price` + `ebay_upload`

### ebay_upload
- Trigger: enqueued by ebay_draft
- Skip condition: all photos already in `ebay_photos` list
- Does: Trading API UploadSiteHostedPictures per photo → EPS permanent URLs
- Writes: `ebay_photos: [{local, url}]`, `draft_listing.imageUrls`
- Next: `catalog_rebuild`

### ebay_price
- Trigger: enqueued by ebay_draft
- Skip condition: `draft_listing.price` already set
- Does
  - Browse API search: full title → category+short title → category only
  - Computes `price_comps: {count, min, p25, median, p75, max}`
  - Sets `draft_listing.price` = launch price (110% of max → .99)
  - Sets `ebay_offer.target_price` = p25
  - (future PP-PRICE-003: use product lookup brand/MPN as search query; condition filter)
- Writes: `draft_listing.price`, `ebay_offer.price_comps`, `ebay_offer.target_price`
- Next: `ebay_stage` + `catalog_rebuild`

### ebay_stage
- Trigger: enqueued by ebay_price
- Guards: skips if `ebay_listing.status == Active`; skips if price null; skips if no photos
- Does
  - Inventory API: PUT inventory_item (upsert)
  - Inventory API: POST offer (create UNPUBLISHED offer)
  - Condition retry: if 25021 error → retry with USED_EXCELLENT
- Writes: `ebay_offer.offer_id`, `ebay_offer.status = Unpublished`
- Next: `catalog_rebuild`
- Operator gate: item now visible/editable in Seller Hub; `tgw staged` shows it

### ebay_publish ← manual trigger only
- Trigger: `tgw publish <sku>` CLI (operator approval gate)
- Guards: offer_id must exist; price non-null; photos uploaded
- Does
  - Inventory API: POST offer/{offer_id}/publish
  - Writes `reprice_schedule` to item JSON (launch/retail/move stages with due_at)
- Writes: `ebay_listing` block (listing_id, status=Active, url), `reprice_schedule`
- Next: `catalog_rebuild`

---

## Post-Publish Workers

### ebay_price_reducer
- Trigger: self-scheduling every 6h; also startup enqueue
- Does: scans all items with `reprice_schedule`; applies due stages via Inventory API offer PUT
- Stages: launch (day 0) → retail / p75 (day 3) → move / p25 (day 17)
- Skip: `reprice_skip: true` on item JSON
- Writes: `reprice_schedule[n].done_at`, updates `draft_listing.price`
- Next: self-reschedule

### ebay_sync
- Trigger: self-scheduling every 6h; startup enqueue
- Does: fetches all eBay offers → syncs status back to item JSON
- Writes: `ebay_listing.status`, `ebay_offer.status`
- Next: `catalog_rebuild` (if changed) + self-reschedule

### ebay_legacy_sync
- Trigger: self-scheduling (startup enqueue); also startup-triggered
- Does
  - GetMyeBaySelling → active listings → writes `ebay_listing` block
  - GetOrders (90-day windows, 365-day initial lookback) → marks sold items
  - Sold match: listing_id → item JSON → `status=sold` + `ebay_sale` block
- Writes: `ebay_listing`, `ebay_offer`, `status=sold`, `ebay_sale`
- State file: `runtime/state/ebay-sold-sync-state.json`
- Next: `catalog_rebuild` per changed item

---

## Maintenance Workers

### ebay_sku_migrate
- Trigger: self-scheduling hourly; startup enqueue
- Does: batches Class A live eBay listings; delist (EndItem) → rename SKU → relist (AddFixedPriceItem)
- Rate: configurable N items/hour; start conservative
- Writes: `sku_history` PostgreSQL table; rollback manifest to `var/log/sku-migrate-*.json`
- Remaining: ~8,370 Class A live listings

---

## catalog_rebuild — Enqueue Map
Every worker that writes ItemData enqueues it. Summary:
- bundle_intake → 30s coalesced
- multi_intake → 30s coalesced
- ai_identify → immediate
- ebay_draft → immediate
- ebay_upload → immediate
- ebay_price → immediate
- ebay_stage → immediate
- ebay_publish → immediate
- ebay_sync → on change only
- ebay_legacy_sync → per changed item
- http PATCH /api/items/{sku} → 30s coalesced
- http POST /webhooks/ebay/notification → 30s coalesced
- Dedupe key: `catalog_rebuild:pending` — only one pending job at a time
