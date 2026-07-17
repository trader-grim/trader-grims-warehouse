---
title: TGW HTTP API (tgw-http)
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-07-17
---

# TGW HTTP API

## Overview
- FastAPI service on port 7373
- Bearer auth: `Authorization: Bearer <api_key>` for `/api/*` (all carry
  `dependencies=[AUTH]`); `/form/*` pages use a session cookie instead
  (`_SESSION_COOKIE`, set via `/login`) — "no Bearer auth" on a `/form/*`
  page means no `Authorization` header, not no auth at all; the session
  middleware still redirects unauthenticated hits on `/form/*` to `/login`
- Key: `secrets_root/tgw-api-key.json → api_key`
- All `/api/*` responses: `{"ok": true, ...}` (error responses: FastAPI's
  standard `{"detail": "..."}` + non-2xx status)
- Source: `src/tgw/http_server.py` (9,940 lines as of 2026-07-17 — one file,
  route table + embedded HTML/JS templates)
- **This pass (PP-UIUX-001 Phase 1, 2026-07-17) is a live-verified refresh** —
  every endpoint below was confirmed against the actual `@app.get/post/patch/
  delete` decorator table in the running source, not against the prior
  version of this doc. The 2026-06-04 version documented 14 of the (now) 79
  routes; nothing documented then had been *removed* — the gap was entirely
  routes added since without a doc update. See
  `reference/UI-Inventory-PP-UIUX-001.md` for the full UI-surface → endpoint
  mapping this pass also produced.

## Endpoints

### Items — `/api/items*`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/items` | Search catalog (SQLite `tgwcatalog.db`); params `search`, `location`, `status_filter`, `date_from`/`date_to` (YYYYMMDD), `limit` (default 200), `offset` |
| POST | `/api/items` | Create a new item JSON; body `{sku, data}`; 400 on bad SKU format, 409 if SKU exists; enqueues `catalog_rebuild` |
| GET | `/api/items/pending-revision` | Items with an unapplied eBay revision draft awaiting operator review |
| GET | `/api/items/review-queue` | Items flagged for AI-draft review (used by `/form/drafts` and Flutter's review screen) |
| GET | `/api/items/{sku}` | Full item detail — ItemData JSON + `_images`/`_videos` (media scan) + `_queue_jobs` (last 50 PostgreSQL queue jobs); 404 if SKU folder/JSON missing |
| PATCH | `/api/items/{sku}` | Update fields; body `{"fields": {...}}`; `sku` is immutable (400 if included); `location` routed through `locationupdate()`; enqueues coalesced `catalog_rebuild` (30s delay); returns `{ok, sku, updated: [...]}` |
| DELETE | `/api/items/{sku}` | Delete an item |
| GET | `/api/items/{sku}/thumbnail` | Serve `catalog_root/thumbnails/{sku}.jpg`; 404 if not generated |
| POST | `/api/items/{sku}/action` | Enqueue a pipeline stage; body `{"action": "<name>", "options": {}}` — see **Pipeline actions** below for the current valid set (this is where the 2026-06-04 doc was most stale) |
| POST | `/api/items/{sku}/append` | Append-only field write (list/array fields — doesn't replace the whole field) |
| POST | `/api/items/{sku}/ebay-write` | Shared write path for anything that also needs to update local `ebay_listing`/`ebay_offer` truth after an eBay-side call (used internally by several actions, e.g. `ebay_end_listing`) |
| POST | `/api/items/{sku}/set-template` | Apply a category-group template (PP-INTAKE-001 P2); body `{"template_key": "..."}`; writes `category_group`/`size_class`/`ai_hint`/`ebay_category_id`; 400 on unknown key |
| POST | `/api/items/{sku}/photo-order` | Persist operator-set photo display order |
| GET | `/api/items/{sku}/inventory-diff` | Diff between `item_attributes` (Set A) and `draft_listing.item_specifics` (Set B) — PP-FIELDCOMPLETE-001/field-set-boundary work |
| POST | `/api/items/{sku}/inventory-diff/apply` | Apply an inventory-diff resolution (the "+ Add to listing" action, #1475) |
| GET | `/api/items/{sku}/category-aspect-migration` | Preview an eBay-category-change aspect remap |
| POST | `/api/items/{sku}/category-aspect-migration/apply` | Apply the aspect remap |
| GET | `/api/items/{sku}/assets` | List media assets (photos/videos) for a SKU |
| DELETE | `/api/items/{sku}/assets/{filename}` | Delete one media asset |
| POST | `/api/items/{sku}/remove-comp` | Remove a comp/reference item from the item's comp list |
| GET | `/api/items/{sku}/hint-trail` | AI-identify + hint-change history (PP-HINT-001) |
| POST | `/api/items/{sku}/revision/apply` | Apply a pending eBay listing-revision draft live |
| DELETE | `/api/items/{sku}/revision` | Discard a pending revision draft |

#### Pipeline actions (`POST /api/items/{sku}/action`, live-verified 2026-07-17)

`PIPELINE_ACTIONS` (http_server.py:143) currently:
`ai_identify`, `ebay_draft`, `ebay_upload`, `ebay_price`, `ebay_stage`,
`ebay_publish`, `ebay_end_listing`, `ebay_update`, `accept_proposals`,
`dismiss_proposals`, `catalog_rebuild`, `thumbnail_gen`, `approve`,
`archive`, `migrate_unblock`.

The 2026-06-04 doc only listed the first 6 plus `catalog_rebuild`/
`thumbnail_gen` — 7 actions (`ebay_end_listing`, `ebay_update`,
`accept_proposals`, `dismiss_proposals`, `approve`, `archive`,
`migrate_unblock`) existed in code but were undocumented. Notable ones:
- `approve` — sets `status: "Ready"` directly, no job enqueued
- `archive` — sets `status: "archived"`, no job enqueued
- `ebay_end_listing` — withdraws the Inventory-API offer (or ends the
  legacy Trading-API listing if no offer_id), then writes local
  `ebay_listing.status: "Ended"` / `ebay_offer.status: "UNPUBLISHED"` —
  this is the fix from the Session 42 one-at-a-time incident (local truth
  used to stay `PUBLISHED` after a live withdraw)
- `migrate_unblock` — clears `sku_migrate_blocked`/`sku_migrate_skip` and
  removes the SKU from `/opt/TGW/var/migrate-blocked.json`

Duplicate job requests return `{ok, status: "already_queued"}`, not an
error.

### Bulk — `/api/bulk/*`

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/bulk/preview` | Dry-run a bulk edit against a selector (skus/location/status/search); no writes |
| POST | `/api/bulk/apply` | Apply the previewed bulk field edit |
| POST | `/api/bulk/action` | Apply a bulk pipeline action across a selector; valid actions (`_BULK_VALID_ACTIONS`, line 1233): `ai_identify`, `ebay_price`, `ebay_draft`, `ebay_stage`, `ebay_upload`, `set_ready`, `mark_sold`, `delete`, `approve`, `list_now`, `ebay_end_listing`, `archive` |

### Queue / jobs — `/api/queue/*`, `/api/jobs/*`, `/api/pipeline/*`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/queue/status` | Job counts per queue+state (PostgreSQL `queue_jobs`); states: `queued`, `claimed`, `succeeded`, `failed`, `retry_wait`, `dead_letter` |
| GET | `/api/pipeline/jobs` | Recent job rows across all queues (feeds `/form/pipeline` dead-letter manager + Flutter's pipeline job sheet) |
| POST | `/api/jobs/{job_id}/requeue` | Re-enqueue a dead-lettered/failed job with a fresh dedupe key |
| POST | `/api/jobs/{job_id}/cancel` | Cancel a queued/claimed job |
| GET | `/api/migrate/blocked` | SKU-migration blocked registry (`/opt/TGW/var/migrate-blocked.json`) |
| GET | `/api/review` | Items with `review_block.ready=false` (PP-REVIEW-001 P1 / PP-UI-INTEGRITY-001 P3, backs `/form/needs-review`) |

### System — `/api/system/*`, `/api/health`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Service health summary |
| GET | `/api/system/workers` | systemd active/sub state for all `tgw-worker@*` units + `tgw-http` (via `systemctl show`) |
| GET | `/api/system/info` | Fuller system info page backing `/form/system` |
| POST | `/api/system/workers/{unit}/restart` | Restart one worker unit from the system dashboard |

### eBay — `/api/ebay/*`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/ebay/aspects/{category_id}` | Category aspects for the offer/draft form (delegates to `apis/ebay/specifics.get_aspects()`); used by both the web item-detail page and Flutter's edit screen |
| GET | `/api/ebay/category-context/{category_id}` | Full category context (path, required aspects, etc.) |
| GET | `/api/ebay/category-search` | Free-text category search (`?q=`) |
| GET | `/api/ebay/category-node/{category_id}` | One category node's detail |
| GET | `/api/ebay/category-children` | Child categories under a parent (`?parent_id=`) |
| GET | `/api/ebay/store-categories` | Seller's eBay store category list |

### Catalog / reference data

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/locations` | Distinct locations (SQLite `tgwcatalog.db`), empty-string excluded |
| GET | `/api/catalog/snapshot` | Full/partial catalog snapshot (offline-sync source for the Flutter app) |
| GET | `/api/category-groups` | Template list for intake (PP-INTAKE-001 P2); 24 groups: `{key, name, size_class, ai_hint, floor, typical_used}` |
| GET | `/api/dashboard` | Home-dashboard summary (health strip, action cards) — backs `/form/home` |
| GET | `/api/activity` | Recent activity feed — backs `/form/home` |

### Offers — `/api/offers*`

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/offers` | Pending Best Offers |
| POST | `/api/offers/{offer_id}/respond` | Accept/counter/decline an offer |
| GET | `/api/offers/limits` | Per-listing offer floor/limit context |

### PM chat — `/api/pm/*`

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/pm/chat` | Chat message to the PM-intake-adjacent assistant surfaced on `/form/home`/`/form/system` |
| POST | `/api/pm/action` | Structured action triggered from that chat surface |

### Suggest / inbox

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/suggest` | JSON suggestion entry (`{text}`) — used by the nav popup overlay embedded across `/form/*` pages; appends to `SUGGESTIONS.md` via `cmd_suggest()`. Distinct from the plain-HTML `/form/suggest` below (same backend function, different transport) |
| POST | `/api/inbox/upload` | Upload a file (≤512 KB, intended for `.md` notes) into the plan inbox — used by the Flutter app's inbox-upload feature |

### Auth / media

| Method | Path | Purpose |
|---|---|---|
| GET/POST | `/login` | Session-cookie login gate for `/form/*` pages |
| GET | `/media/{sku}/{filename}` | Serve a photo/video file from a SKU's ItemData folder |
| GET | `/thumb/{sku}` | Serve a SKU's thumbnail (alternate path to `/api/items/{sku}/thumbnail`, no Bearer auth — used by `<img>` tags in server-rendered pages) |
| GET | `/docs`, `/docs/{path}` | Static docs mount |

### Forms — `/form/*` (session-cookie gated, no Bearer header)

See `reference/UI-Inventory-PP-UIUX-001.md` for the full page-by-page
inventory and per-page API mapping. Route table (all live-verified
2026-07-17):

| Path | Page |
|---|---|
| `/form/intake` | Intake landing — SKU/barcode entry, recent intakes |
| `/form/intake/{sku}` | Mobile intake form — template chips, weight, barcode, condition |
| `/form/bulk` | Tablet bulk editor |
| `/form/todos` | Read-only open-todo dashboard (`tgw todo`, grouped by agent) |
| `/form/history/{sku_old}` | Historical-catalog lookup by old SKU |
| `/form/suggest` (GET/POST) | Plain-HTML suggestion entry, no JS |
| `/form/items` | Inventory browse — card grid, search/filter |
| `/form/items/{sku}` | Item detail — photos, fields, revision diff, pipeline actions |
| `/form/offers` | Best Offers management |
| `/form/revisions` | Revision-draft review queue |
| `/form/drafts` | Post-draft human-QA review queue (the real page) |
| `/form/review` | **Redirect only** — renamed to `/form/drafts`, kept for back-compat |
| `/form/needs-review` | Blocked-items dashboard (`review_block.ready=false`) |
| `/form/pipeline` | Pipeline monitor + dead-letter manager |
| `/form/system` | Full system health page |
| `/form/home` | Home dashboard — health strip, action cards, quick intake, activity feed |
| `/form/links` | External links hub (eBay/AI/infra/research) — static, no API calls |

### Webhooks

#### POST /webhooks/ebay/notification — eBay push (no Bearer auth)
- eBay FixedPriceTransaction sold events
- Auth: SOAP signature verification (not Bearer — eBay can't send it)
- Always returns `{"ack": "Success"}` to prevent eBay retry storms
- On valid sold event: looks up listing_id in a 10-min cached listing
  index, calls `_mark_item_sold()` (shared with `ebay_legacy_sync`
  polling), enqueues coalesced `catalog_rebuild`
- Infrastructure deployment status: unverified this pass — out of scope
  for a Phase 1 inventory pass; re-check under its own PP if touched next

## Auth notes
- No public endpoints except `/webhooks/ebay/notification` (signature-verified instead)
- API key stored in `secrets_root/tgw-api-key.json` as `{"api_key": "..."}`
- Flutter app and MC copyin both use Bearer token against `/api/*`
- `/form/*` pages use the session cookie (`/login`), not Bearer — several
  page docstrings say "no Bearer auth" which is accurate but easy to
  misread as "no auth at all"; the session middleware still gates them

## Service
- systemd: `tgw-http.service`
- Start: `systemctl start tgw-http`
- Logs: `journalctl -u tgw-http -f`
