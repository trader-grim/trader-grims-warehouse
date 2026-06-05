---
title: TGW eBay Error Code Reference
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-05
maintained_by: Claude (session 6)
---

# eBay Error Code Reference

## How to read this file
- Open in Obsidian with the **Markmap** plugin for the mind map view
- `→ HANDLED`: TGW has explicit code to deal with this error
- `→ DEAD-LETTER`: TGW has no recovery; job goes to dead_letter and needs manual requeue
- `→ RETRY`: QueueWorker base class retries automatically (transient/network errors)
- `→ SKIP`: error is caught and silently ignored (missing scope, non-critical lookup)
- `Source`: file(s) where the handling lives

## Inventory API (sell.inventory)

### errorId 25021 — Condition not supported by category
- **Meaning**: The category only accepts `conditionId 3000` ("Used") — granular conditions like
  `USED_VERY_GOOD` or `USED_GOOD` are rejected
- **Observed in**: categories 32852 (Board Games), 183050 (Toys), many "Used" categories
- **Action → HANDLED**: retry inventory_item PUT with `condition: USED_EXCELLENT`
  (conditionId 3000, universally accepted for used-item categories)
- **Source**: `ebay/sync.py` `stage_draft()` + `workers/ebay_publish.py`

### errorId 25709 — Missing Content-Language header ✅ CLOSED ISS
- **Meaning**: All Inventory API PUT/POST calls require `Content-Language: en-US`
- **Action → FIXED**: header added globally to all Inventory API calls in `apis/ebay/client.py`
  equivalent — via `extra_headers={'Content-Language': 'en-US'}` on every PUT/POST call
- **Source**: `ebay/sync.py`, `workers/ebay_price_reducer.py`, `workers/ebay_sku_migrate.py`

### errorId 25002 — Item.Country not set ⚠ OPEN ISS-001
- **Meaning**: Some categories require `Item.Country` explicitly (not just fulfillment policy coverage)
- **Affected categories observed**: 34032 (Jewelry), 14027, 13916
- **Current behaviour → DEAD-LETTER**: `shipToLocations.regionIncluded` was added to offer body
  permanently but the error still fires for these categories; job dead-letters
- **Next step**: investigate whether `Item.Country` must be in the *inventory item* body rather
  than the offer body for these specific categories
- **Source**: `ebay/sync.py` `_build_offer_bodies()`

### errorId 25500-series — Strikethrough pricing rejected
- **Meaning**: `originalRetailPrice` in pricingSummary rejected — account not approved for
  Strikethrough Pricing program
- **Action → DEAD-LETTER** (if `strikethrough_enabled=true` but account not approved)
- **Prevention**: verify Seller Hub access before enabling `ebay.strikethrough_enabled` in config
  (`tgw strikethrough-check`); leave `false` until confirmed
- **Source**: `ebay/sync.py` `_build_offer_bodies()`

## HTTP Status Codes

### HTTP 204 — No Content (success, empty body)
- **Action → HANDLED**: treat as success; return `{}` (not an error)
- **Source**: `apis/ebay/client.py`

### HTTP 400 / 422 — Client validation error
- **At staging** (`ebay_stage`): parse errors, check for known errorIds; unknown → `HardFailure`
  → dead-letter
- **At publish** (`ebay_publish`): same; known 25021 → retry with condition fallback
- **At price update** (`ebay_price_reducer`): log error, count failure, continue batch
  (no dead-letter; item skipped this cycle, retried next run)
- **At fetch_all_offers** (`fetch_all_offers`): eBay returns 400 when seller has no Inventory
  API offers → treat as empty list, not error
- **Source**: `workers/ebay_stage.py`, `workers/ebay_publish.py`, `workers/ebay_price_reducer.py`,
  `ebay/sync.py`

### HTTP 401 / 403 — Auth / scope error
- **EPID lookup** (`apis/ebay/catalog.py`): `commerce.catalog.readonly` not granted →
  `→ SKIP` silently; EPID field left null
- **All other calls** → propagated; QueueWorker base class retries (token_refresh handles expiry)
- **Source**: `apis/ebay/catalog.py`

### HTTP 404 — Not found
- **EPID lookup**: barcode not in eBay catalog → return `None`
- **Offer DELETE** in sku_migrate: offer already gone → treat as OK (idempotent)
- **Offer GET** (find_offer): returns `None` — triggers POST instead of PUT
- **Source**: `apis/ebay/catalog.py`, `workers/ebay_sku_migrate.py`, `ebay/sync.py`

### HTTP 5xx / ConnectionError / Timeout — Transient network/server
- **Action → RETRY**: QueueWorker base class retries up to `max_attempts` with exponential backoff
- **Exception**: `ebay_price_reducer` runs as a long self-scheduling scan; per-item HTTP errors are
  logged and counted without triggering a retry for that item — the item will be re-evaluated next
  run (every 6h)
- **Photo upload** (`ebay_upload`): per-photo network errors collected; if all photos fail →
  `RuntimeError` → RETRY the whole job
- **Source**: `queue/worker_base.py`, `workers/ebay_price_reducer.py`, `workers/ebay_upload.py`

## Trading API

### Ack=Failure — Generic Trading API failure
- **Action**: `RuntimeError` raised with eBay's `LongMessage` concatenated
- **Workers using Trading API**: `ebay_legacy_sync`, `ebay_sku_migrate`, `ebay_sync`
- **Source**: `apis/ebay/trading.py` `trading_call()`

### GetStore failure (no store / seller not a store member)
- **Action → SKIP**: `get_store_categories()` returns `[]`; store category injection
  in `_build_offer_bodies()` silently skipped when no mapping configured
- **Source**: `apis/ebay/trading.py`, `ebay/sync.py`

### GetMyeBaySelling — ActiveList not in response
- **Meaning**: Seller has no active Trading API listings
- **Action → HANDLED**: treated as empty (generator returns immediately, log info)
- **Source**: `apis/ebay/trading.py`

## EPS (Picture Services) — Photo Upload

### All photos fail upload
- **Action → RETRY**: `RuntimeError` raised → QueueWorker retries job
- **Partial failure**: per-photo network errors collected; if any photo succeeds, job completes
  with available photos; remaining can be re-uploaded by requeuing `ebay_upload`
- **Source**: `workers/ebay_upload.py`

## Browse API — Pricing Comps

### Thin or no comps (< 3 results across all fallback stages)
- **Not an API error**: Browse API returns successfully but no matching active listings found
- **Action**: price left `null`; `ebay_price_worker` does not enqueue `ebay_stage`
  (item stalls after `ebay_price` with no price — must be manually priced)
- **Source**: `workers/ebay_price.py`, `ebay/pricing.py`

## Scope Gaps (silent skips, not errors)

| Scope needed | Feature | Current behaviour |
|---|---|---|
| `commerce.catalog.readonly` | EPID association | HTTP 401/403 → skip silently |
| `sell.analytics.readonly` | Listing impressions | `tgw seo-audit` shows N/A |
| `buy.marketplace_insights` | Sold price data | Finding API dead; Browse API used as proxy |
| `sell.stores.readonly` | Store category read via REST | Using Trading API `GetStore` as workaround |
| `sell.fulfillment.readonly` | REST order read | Trading API GetOrders used instead |

## Dead-letter diagnosis guide

When a job lands in dead_letter, check the `error_detail` column:

| Symptom in error_detail | Likely cause | Fix |
|---|---|---|
| `errorId 25021` in message | Condition fallback failed (rare) | Check `conditions.py` for this category |
| `errorId 25002` + category in {34032, 14027, 13916} | Item.Country ISS-001 | Manual Seller Hub publish; track fix in ISS-001 |
| `no price yet` | ebay_price still running | Wait; or `tgw requeue --no-price --run` |
| `already active listing` | Item already live | Run `tgw ebay-pull` to sync status |
| `legacy eBay Item#` | Unresolved legacy listing | Run `tgw resolve-legacy <sku>` |
| `all photo uploads failed` | Network / EPS outage | Requeue: `tgw-http POST /api/items/:sku/action ebay_upload` |
| `no draft_listing` | Pipeline stalled before ebay_draft | Requeue ai_identify |
| HTTP 400 unknown errorId | New unhandled eBay error | Check `error_detail` for full JSON; add handler |
