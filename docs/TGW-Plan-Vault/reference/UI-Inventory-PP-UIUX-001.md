---
title: UI Inventory — PP-UIUX-001 Phase 1
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-07-18
---

# UI Inventory — PP-UIUX-001 Phase 1

Produced 2026-07-17 (todo #1483) as the Phase 1 deliverable for
PP-UIUX-001: catalog every operator-facing UI surface (web + Flutter),
map each to the backend it actually calls, live-verified against the
current codebase (invariant C11 — no trusting old doc claims). See
`plan/TGW-Master-Plan.md`'s PP-UIUX-001 entry for the full sequence
(Phase 2: unified spec; Phase 3: hand to a UI/UX-specialist executor
role, not yet defined).

**Re-checked 2026-07-18 (same todo #1483, same-day drift found):** the
route table grew from 79 to 83 in the ~1 day between the first pass and
this re-check — 4 routes (`/form/search`, `/api/search/full-text`,
`/api/queue/daily_stats`, `/api/items/{sku}/inventory-lock`) landed after
the first pass closed but before this doc was re-verified. Added below;
see `TGW-HTTP-API.md`'s "2026-07-18 gaps found" note for the full list.
This is the exact staleness pattern PP-UIUX-001 exists to catch — a
one-time inventory pass goes stale again as soon as the next commit
lands; there is no live-sync mechanism, this is a point-in-time snapshot
re-verified on demand.

Companion doc: `reference/TGW-HTTP-API.md` (endpoint reference, refreshed
in the same pass). This doc is the UI-surface-centric view; that one is
the endpoint-centric view.

## 1. Web UI — `tgw-http`, `src/tgw/http_server.py`

18 pages total (17 as of the 2026-07-17 pass, `/form/search` added
2026-07-18). All are server-rendered HTML with embedded JS except
`/form/links`, `/form/todos`, `/form/history/{sku_old}`, `/form/suggest`,
and `/form/search`, which are static/no-JS (`/form/search` submits a
plain GET form, results render server-side). Most pages are gated by
session cookie (`/login`), not Bearer — the embedded JS holds/injects the
API key itself for its own `fetch()` calls to `/api/*`. **Not all
`/form/*` pages are cookie-gated** — `/form/search`, `/form/todos`,
`/form/intake`, `/form/intake/{sku}`, `/form/bulk`,
`/form/history/{sku_old}`, `/form/suggest` are explicitly no-auth
("network trust" per their own docstrings). Route table + backing HTML
template/function identified by reading `src/tgw/http_server.py` directly
(route line numbers as of 2026-07-18; will drift as the file is edited
further).

| Page | Route → template | Backend calls (live-grepped from embedded JS) |
|---|---|---|
| Intake landing | `/form/intake` → `_INTAKE_LANDING_HTML` | `GET /api/items?limit=20` |
| Intake form | `/form/intake/{sku}` → `_INTAKE_FORM_HTML` | `GET /api/items/{sku}`, `POST /api/items/{sku}/set-template`, `PATCH /api/items/{sku}` |
| Bulk editor | `/form/bulk` → `_BULK_FORM_HTML` | `POST /api/bulk/preview`, `POST /api/bulk/apply` |
| Todos dashboard | `/form/todos` → `_render_todos_html()` | none — server-rendered from `tgw todo` data at request time, no client JS |
| Full-text search | `/form/search` → `_render_search_html()` | none client-side — server-rendered GET form (`?q=`), calls `tgw.search_full.run_full_text_search()` directly in-process (same recoll query as `GET /api/search/full-text` and `tgw search --full-text`); no-auth, network trust |
| History lookup | `/form/history/{sku_old}` | none — server-rendered lookup, no client JS |
| Suggest (plain) | `/form/suggest` (GET/POST) → `_render_suggest_html()` | none — plain HTML form POST to itself (`cmd_suggest()`), not a JS fetch |
| Inventory browse | `/form/items` → `_BROWSE_HTML` | `GET /api/items?...`, `POST /api/items/{sku}/action`, `POST /api/bulk/action` |
| Item detail | `/form/items/{sku}` → `_render_item_detail_html()` | `GET /api/items/{sku}`, `GET /api/jobs/...` requeue, `PATCH /api/items/{sku}`, `POST /api/items/{sku}/action`, `POST /api/items/{sku}/photo-order`, `GET/POST /api/items/{sku}/inventory-diff[/apply]`, `POST /api/items/{sku}/inventory-lock` (padlock toggle, added 2026-07-18), `POST /api/items/{sku}/category-aspect-migration/apply`, `POST /api/items/{sku}/remove-comp`, `GET /api/offers` (badge count) |
| Offers | `/form/offers` → `_OFFERS_HTML` | `GET /api/offers`, `POST /api/offers/{offer_id}/respond`, `GET /api/offers/limits` |
| Revisions | `/form/revisions` → `_REVISIONS_HTML` | `GET /api/items/pending-revision`, `POST /api/items/{sku}/revision/apply`, `DELETE /api/items/{sku}/revision` |
| Drafts (real page) | `/form/drafts` → `_REVIEW_HTML` | `GET /api/items/review-queue`, item PATCH/action calls |
| Review (redirect) | `/form/review` | 303 redirect to `/form/drafts` only — **not a real page**, back-compat alias from an earlier rename |
| Needs-review | `/form/needs-review` → `_NEEDS_REVIEW_HTML` | `GET /api/review` |
| Pipeline monitor | `/form/pipeline` → `_PIPELINE_HTML` | `GET /api/pipeline/jobs`, `GET /api/queue/status`, `GET /api/system/workers`, `POST /api/jobs/{job_id}/requeue`, `POST /api/jobs/{job_id}/cancel` |
| System health | `/form/system` → `_SYSTEM_HTML` | `GET /api/health`, `GET /api/system/info`, `GET /api/system/workers`, `POST /api/system/workers/{unit}/restart`, `GET /api/queue/daily_stats` (Done today/Failed today columns, added 2026-07-18) |
| Home dashboard | `/form/home` → `_HOME_HTML` | `GET /api/dashboard`, `GET /api/activity`, `GET /api/health`, `POST /api/pm/chat`, `POST /api/pm/action` |
| Links hub | `/form/links` → `_LINKS_HTML` | none — static external-link list, no API calls |

Nav-popup overlay (present across most `/form/*` pages, not a separate
route): posts to `POST /api/suggest`.

### Findings (documented, not fixed — Phase 1 is inventory only)

- **`/form/review` is dead weight as a "page"** — it's a pure redirect to
  `/form/drafts`, kept only for back-compat links. Not a bug, just worth
  naming so a future spec pass doesn't double-count it as a distinct
  surface.
- **`POST /api/items/{sku}/action`'s valid-action set had drifted out of
  the doc** by 7 actions (`ebay_end_listing`, `ebay_update`,
  `accept_proposals`, `dismiss_proposals`, `approve`, `archive`,
  `migrate_unblock`) — fixed in this pass's `TGW-HTTP-API.md` refresh,
  not a code change.
- **61 of 79 total `/api/*` + `/form/*` + auth/media routes had zero
  entry in the pre-2026-07-17 `TGW-HTTP-API.md`** — see that doc's
  Overview note. All were live/working code, just undocumented; no
  evidence any of them are dead code from this pass — every one traced
  to a live caller, either a web page's embedded JS, the Flutter API
  client (§2 below), or (for `/api/items/{sku}/append` and
  `/api/items/{sku}/ebay-write`, which have no web-page or Flutter
  caller) the internal `apis/fence.py` client used by workers
  (`append_item()`/`ebay_write()`) — not orphans, just not
  UI-facing at all. No todo needed for these two.
- **2026-07-18 re-check: 4 more routes landed same-day, post-Phase-1**
  (`/form/search`, `/api/search/full-text`, `/api/queue/daily_stats`,
  `/api/items/{sku}/inventory-lock`) — total live route count is now 83,
  not 79. All 4 traced to a real caller (web page or, for
  `/api/search/full-text`, "no Flutter caller yet" — noted, not a bug).
  Confirms the note above: this doc is a point-in-time snapshot, not a
  live-synced artifact — expect the same drift again next re-check.

## 2. Flutter app

**Correction to a standing assumption (memory note "android/ scaffold
exists, never built"):** that description is accurate for
`apps/android/`, `apps/lib/main.dart`, and the `apps/pubspec.yaml`
project — that outer tree is the default unmodified `flutter create`
counter-app stub (`apps/lib/main.dart` only, `description: "A new
Flutter project."`), with **zero** real screens. It is not the real app.

**The real Flutter app is `apps/tgw_app/`** — a distinct, separate
Flutter project (own `pubspec.yaml`: `name: tgw_app`, `description: Trader
Grim's Warehouse Mobile Client`) with 7 real feature screens, an offline
SQLite cache (`sqflite`), an outbox/mutation-queue for offline edits,
and a Riverpod provider layer. Last touched 2026-06-29 per `git log`.
This was NOT distinguished from the unbuilt `apps/android` stub in prior
session memory — worth correcting going forward: **`apps/tgw_app/` is
the real Flutter surface for PP-UIUX-001 Phase 2+, not `apps/android`.**
(SDK/NDK toolchain-missing note from memory may still be accurate for
actually building/running it — out of scope to verify in this
documentation-only pass; not retested here.)

| Screen (`apps/tgw_app/lib/features/...`) | Backend calls (via `repository.dart` / `providers.dart` / direct `api_client.dart`) |
|---|---|
| `home/home_screen.dart` | `queueStatusProvider` → `GET /api/queue/status`; `pipelineJobsProvider` → `GET /api/pipeline/jobs` |
| `home/pipeline_job_sheet.dart` | `pipelineJobsProvider` (same as above); `api.requeueJob()` → `POST /api/jobs/{job_id}/requeue`; `reportJobToAdmin(...)` (local-only, no endpoint — surfaces to in-app admin chat) |
| `browse/browse_screen.dart` | `repo.getItems()` → `GET /api/items`; `repo.bulkAction()` → `POST /api/bulk/action`; reads `offlineDbProvider` (local SQLite cache) and `connectionStatusProvider` for offline fallback |
| `item/item_screen.dart` | `itemDetailProvider` → `repo.getItem()` → `GET /api/items/{sku}`; `api.mediaUrl()` builds `/media/{sku}/{filename}` URLs; offline-DB fallback when disconnected |
| `item/edit_item_screen.dart` | `repo.patchItem()` → `PATCH /api/items/{sku}`; `repo.getEbayAspects()` → `GET /api/ebay/aspects/{category_id}` |
| `review/review_queue_screen.dart` | `repo.getReviewQueue()` → `GET /api/items/review-queue`; `repo.bulkAction()` → `POST /api/bulk/action` |
| `settings/settings_screen.dart` | reads `apiClientProvider`/`connectionStatusProvider`/`offlineDbProvider` for config + sync-status display; no direct new endpoint calls found |

`api_client.dart`'s full endpoint surface (all confirmed present in
`http_server.py`'s route table — no client-side calls to a
now-nonexistent endpoint found):
`GET /api/queue/status`, `GET /api/items`, `GET /api/items/{sku}`,
`GET /api/locations`, `GET /api/category-groups`,
`PATCH /api/items/{sku}`, `POST /api/items/{sku}/action`,
`GET /api/ebay/aspects/{category_id}`,
`POST /api/items/{sku}/set-template`, `GET /api/items/review-queue`,
`GET /api/items/{sku}/hint-trail`, `POST /api/inbox/upload`,
`POST /api/bulk/action`, `GET /api/pipeline/jobs`,
`POST /api/jobs/{job_id}/requeue`, `POST /api/jobs/{job_id}/cancel`,
`POST /api/suggest`, `DELETE /api/items/{sku}`,
`GET /api/catalog/snapshot`.

`catalog_sync_service.dart` and `offline_db.dart`/`outbox_db.dart`
implement the offline-first sync loop (pull via `/api/catalog/snapshot`,
push queued mutations via `flushOutbox()` → `repo.patchItem()` per queued
edit) — not re-verified end-to-end live in this pass (documentation-only
scope; would need a device/emulator run, which the SDK/NDK gap may still
block).

### Web vs Flutter parity note (for PP-UIUX-001 Phase 2/3)

Flutter's `api_client.dart` covers 19 of the 83 total routes — a subset
of what the web UI's item-detail/browse/pipeline pages call
(e.g. no Flutter caller found yet for `/api/items/{sku}/inventory-diff`,
`/api/items/{sku}/inventory-lock`, `/api/items/{sku}/photo-order`,
`/api/items/{sku}/category-aspect-migration`, `/api/offers*`,
`/api/system/*`, `/api/dashboard`, `/api/activity`, `/api/pm/*`,
`/api/queue/daily_stats`, `/api/search/full-text`). This is expected for a mobile-first subset app, not
necessarily a gap — but it is exactly the kind of divergence Phase 2's
"one complete spec covering both surfaces" needs to resolve deliberately
rather than by accretion. Not actioned in this Phase 1 pass per scope.

## Out-of-scope findings filed

- None. The one open question from an earlier draft of this doc
  (whether `/api/items/{sku}/append`/`/api/items/{sku}/ebay-write` were
  orphaned) was resolved live in this same pass — both are the
  `apis/fence.py` worker-write path, not UI-facing, not orphans. No todo
  filed.
