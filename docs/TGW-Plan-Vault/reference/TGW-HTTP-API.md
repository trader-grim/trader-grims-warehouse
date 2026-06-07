---
title: TGW HTTP API (tgw-http)
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# TGW HTTP API

## Overview
- FastAPI service on port 7373
- Auth: `Authorization: Bearer <api_key>`
- Key: `secrets_root/tgw-api-key.json → api_key`
- All responses: `{"ok": true, ...}`
- Source: `src/tgw/http_server.py`

## Endpoints

### Items

#### GET /api/items — search catalog
- Source: SQLite `tgwcatalog.db`
- Query params
  - `search` — title OR sku LIKE match
  - `location` — exact location match
  - `status_filter` — exact status match
  - `date_from` / `date_to` — YYYYMMDD, matches SKU date prefix
  - `limit` (default 200) / `offset`
- Returns `{ok, count, items: [{sku, title, location, status, price, qty, image}]}`

#### GET /api/items/{sku} — full item detail
- Source: ItemData JSON + media scan + PostgreSQL queue jobs
- Returns full item JSON plus
  - `_images` — list of image filenames in SKU folder
  - `_videos` — list of video filenames in SKU folder
  - `_queue_jobs` — last 50 queue jobs for this SKU (queue_name, state, attempt_count, timestamps, error)
- 404 if SKU folder/JSON not found

#### PATCH /api/items/{sku} — update fields
- Body: `{"fields": {"key": value, ...}}`
- `sku` field is immutable — 400 if included
- `location` handled specially: updates location tree via `locationupdate()`
- All other fields merged atomically into item JSON
- Enqueues coalesced `catalog_rebuild` (30s delay, dedupe key)
- Returns `{ok, sku, updated: [list of keys changed]}`

#### GET /api/items/{sku}/thumbnail — serve thumbnail image
- Source: `catalog_root/thumbnails/{sku}.jpg`
- Returns JPEG; 404 if not yet generated

#### POST /api/items/{sku}/action — enqueue pipeline stage
- Body: `{"action": "<name>", "options": {}}`
- Valid actions
  - `ai_identify` — sets `ai_reidentify: true` on item JSON, then enqueues
  - `ebay_draft`
  - `ebay_upload`
  - `ebay_price`
  - `ebay_stage`
  - `ebay_publish`
  - `catalog_rebuild` — dedupe key, 5s delay; SKU not required
  - `thumbnail_gen`
- Duplicate job returns `{ok, status: "already_queued"}` (no error)
- Returns `{ok, sku, action, job_id}`

### Queue

#### GET /api/queue/status — job counts per queue+state
- Source: PostgreSQL `queue_jobs`
- Returns `{ok, queues: {queue_name: {state: count}}}`
- States: `queued`, `claimed`, `succeeded`, `failed`, `retry_wait`, `dead_letter`

### eBay

#### GET /api/ebay/aspects/{category_id} — aspects for offer form
- Delegates to `apis/ebay/specifics.get_aspects()`
- Returns `{ok, category_id, aspects: [...]}`
- Used by Flutter app offer form (SELECTION_ONLY → dropdowns, FREE_TEXT → fields)

### Catalog

#### GET /api/locations — distinct locations
- Source: SQLite `tgwcatalog.db`
- Returns `{ok, locations: ["A1", "B2", ...]}`
- Excludes empty-string locations

#### GET /api/category-groups — template list for intake form (PP-INTAKE-001 P2)
- Source: `config/category-groups.json` (`category_groups_path` config key)
- Returns `{ok, count, groups: [{key, name, size_class, ai_hint, floor, typical_used}]}`
- 24 groups; drives template picker chips in intake form

#### POST /api/items/{sku}/set-template — apply category-group template (PP-INTAKE-001 P2)
- Body: `{"template_key": "books"}`
- Applies same logic as `tgw set-template`: writes `category_group`, `size_class`, `ai_hint` (prepended), `ebay_category_id` (if not set)
- Enqueues coalesced `catalog_rebuild`
- Returns `{ok, sku, template_key, applied: {fields written}, group_name}`
- 400 if unknown template_key

#### GET /api/items/{sku}/hint-trail — identification history (PP-HINT-001 trail)
- Returns `{ok, sku, count, history: [{ts, event, ...}]}`
- Events: `ai_identify` (round, model, prompt_type, hint, lookup_source, title, category, condition, ebay_category_id) and `hint_set` (hint, prev_hint, by)
- Empty list if item has never been through ai_identify since trail was added

### Forms

#### GET /form/intake/{sku} — mobile intake form (PP-INTAKE-001 P2)
- **No Bearer auth** — intended for internal network / Tailscale use from phone/tablet
- Mobile-first HTML form: 24 template chips, weight (oz), barcode, ai_hint, condition
- Pre-fills from current item JSON values
- On submit: calls `POST /api/items/{sku}/set-template` (if template changed) then `PATCH /api/items/{sku}`
- Dark theme, large touch targets, no keyboard required for template selection

### Webhooks

#### POST /webhooks/ebay/notification — eBay push (no Bearer auth)
- eBay FixedPriceTransaction sold events
- Auth: SOAP signature verification (not Bearer — eBay can't send it)
- Always returns `{"ack": "Success"}` to prevent eBay retry storms
- On valid sold event
  - Looks up listing_id in 10-min cached listing index
  - Calls `_mark_item_sold()` (shared with ebay_legacy_sync polling)
  - Enqueues coalesced `catalog_rebuild`
- Infrastructure deployment pending (PP-SOLD-001 Tier 4)

## Auth notes
- No public endpoints except `/webhooks/ebay/notification` (signature-verified instead)
- API key stored in `secrets_root/tgw-api-key.json` as `{"api_key": "..."}`
- Flutter app and MC copyin both use Bearer token

## Service
- systemd: `tgw-http.service`
- Start: `systemctl start tgw-http`
- Logs: `journalctl -u tgw-http -f`
