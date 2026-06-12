# Gemini Task 003 — Flutter App Scaffold (PP-EDITOR-001 Phase B + C)

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Expected output:** A complete, buildable Flutter project written to `apps/tgw_app/` on this
machine. Gemini CLI runs locally with filesystem write access — create the files directly.
**Also produce:** `GEMINI-003-result.md` in `docs/TGW-Plan-Vault/inbox/` summarizing what you
built, file tree, any TODOs/assumptions, and exact build commands you verified.

> Why you (Gemini): Claude Code cannot run or visually iterate on a GUI in this environment, and
> a Flutter scaffold is a large, self-contained codegen task that fits your long context and code
> strengths. Build the skeleton; Claude wires the Python/API side and reviews your Dart.

---

## Context — what TGW is

Trader Grim's Warehouse (TGW) is an eBay resale business (~55,000 listings) running a custom
Python inventory + eBay-automation platform. The settled mobile interface is a **Flutter app**
(Linux desktop + Android tablet). The tablet is the PRIMARY operator surface — it must be
**mostly operable without a keyboard** (checkboxes, buttons, dropdowns, chips).

The Python backend (`tgw-http`, FastAPI) is **already built, running, and unit-tested** (30+
tests). Your job is the **Flutter client** that talks to it. Do NOT design the backend — consume
the contract below exactly as given.

## Architecture (settled — do not relitigate)

```
tgw-http (FastAPI, port 7373)   ← all WRITES go here (Bearer auth)
        ↑                ↑
   MC console      Flutter app  (apps/tgw_app/)
                   • sqflite reads tgwcatalog.db directly when OFFLINE
                   • Dio HTTP client for writes/actions when ONLINE
                   • Syncthing syncs tgwcatalog.db + thumbnails to the device
```

- **Online** (API reachable): read + write through tgw-http.
- **Offline** (no API): read-only browse from a local copy of `tgwcatalog.db` via `sqflite`.
- Connection state is a first-class UI concept (banner: ONLINE green / OFFLINE amber).

## Scope for THIS task — Phase B + Phase C only

**Phase B — skeleton**
- Flutter project at `apps/tgw_app/`, build targets **Linux desktop + Android (Android 10+)**.
- `Dio` HTTP client wired to a configurable base URL (default `http://127.0.0.1:7373`) + Bearer
  token loaded from app settings (do NOT hardcode a key; provide a Settings screen field +
  secure storage via `flutter_secure_storage`).
- `sqflite` (use `sqflite_common_ffi` on Linux desktop) opening a configurable path to
  `tgwcatalog.db` for offline reads.
- Bottom navigation shell with tabs: **Home / Browse / Item / Settings** (Item opens from Browse;
  keep it in the shell as a detail route).
- Connection-state provider: pings `GET /api/queue/status`; ONLINE if 200, else OFFLINE.

**Phase C — browse + item view**
- **Browse screen**: thumbnail grid (image from `GET /api/items/{sku}/thumbnail` when online, or
  the synced thumbnail file when offline), title, location chip, status badge. Filters: text
  search, location dropdown (`GET /api/locations`), status filter. Infinite scroll / paging via
  `limit`/`offset`.
- **Item detail screen**: tabbed — **Fields / eBay draft / Offer status**. Read-only in this
  phase (edit is Phase D, later task). Show all of `GET /api/items/{sku}`. Photo gallery inline.
- **Home screen (stub for now)**: show connection state + queue depths from
  `GET /api/queue/status` as cards. (The full status/alert welcome screen is a later phase — just
  lay the tab in and render queue counts.)

**Out of scope (later tasks):** edit/PATCH flows, pipeline action dispatch, eBay offer form,
RBAC/role layouts, push notifications. Stub these tabs/buttons as disabled with a `// TODO Phase D`.

## State management & structure

- Use **Riverpod** (`flutter_riverpod`) for state. Keep it conventional and readable.
- Layered structure: `lib/api/` (Dio client + models), `lib/db/` (sqflite offline reader),
  `lib/models/` (data classes + `fromJson`), `lib/features/<browse|item|home|settings>/`,
  `lib/app.dart`, `lib/main.dart`.
- Models with `fromJson`/`toJson` for every response shape below. Use `json_serializable` OR
  hand-written `fromJson` — your call, but be consistent and don't add a heavy codegen step if
  hand-written is cleaner.
- Provide a `Repository` abstraction that picks API-vs-sqflite based on connection state for
  reads, and always API for writes.

## EXACT API contract (consume verbatim — verified against `src/tgw/http_server.py`)

Base URL `http://<host>:7373`. All `/api/*` routes require header
`Authorization: Bearer <token>`. The output contract: every JSON response has an `ok` key.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/api/items` | query: `search, location, status_filter, date_from, date_to, limit=200, offset=0` |
| GET | `/api/items/{sku}` | full item JSON detail |
| PATCH | `/api/items/{sku}` | (Phase D — not this task) |
| GET | `/api/items/{sku}/thumbnail` | image/jpeg bytes |
| POST | `/api/items/{sku}/action` | (Phase D) enqueue a pipeline stage |
| GET | `/api/queue/status` | per-queue job counts |
| GET | `/api/locations` | distinct locations |
| GET | `/api/category-groups` | the 25 category groups |
| GET | `/api/ebay/aspects/{category_id}` | (Phase E) |
| GET | `/api/items/{sku}/hint-trail` | identification history |

**`GET /api/items` →**
```json
{ "ok": true, "count": 200,
  "items": [ { "sku": "tgw201411151759014", "title": "…", "location": "A-12-3",
               "status": "In Stock", "price": "11.99", "qty": "1", "image": "…filename.jpg" } ] }
```
Note: `price` and `qty` are **strings**. `image` is a filename (thumbnail key is the SKU).

**`GET /api/items/{sku}` →** `{ "ok": true, "item": { …full item JSON… }, "images": [...],
"videos": [...], "queue_jobs": [ … last 50 … ] }`. The item JSON is large and nested; render
defensively (treat every field as optional). Key fields you'll surface:
`title`, `location`, `status`, `condition`, `price`/`target_price`, `ebay_category_id`,
`category_group`, `size_class`, `ai_hint`, `draft_listing` (title/description/aspects),
`ebay_offer`, `ebay_listing` (status/url), `identification_history`.

**`GET /api/queue/status` →** `{ "ok": true, "queues": { "<queue>": { "<state>": <count> } } }`.

**`GET /api/locations` →** `{ "ok": true, "locations": ["A-12-3", …] }`.

**`GET /api/category-groups` →** `{ "ok": true, "groups": { "<key>": { "name", "size_class",
"ebay_categories":[...], "ai_hint", "pricing": {...} } } }`.

Auth failure returns HTTP 401. Catalog-not-built returns 503. Handle both with a friendly banner.

## Offline DB contract (`tgwcatalog.db`, SQLite — for sqflite reads)

```sql
CREATE TABLE catalog (
  sku TEXT PRIMARY KEY, title TEXT, location TEXT, status TEXT,
  price TEXT, qty TEXT, image TEXT, attribute_set TEXT,
  data TEXT NOT NULL,            -- full item JSON as a string; json-decode for detail
  updated_at TEXT
);
CREATE INDEX idx_location ON catalog(location);
CREATE INDEX idx_status   ON catalog(status);
CREATE INDEX idx_title    ON catalog(title);
```
Offline Browse = `SELECT sku,title,location,status,price,qty,image FROM catalog WHERE … ORDER BY
sku DESC LIMIT ? OFFSET ?`. Offline Item detail = `json.decode(data)`. Thumbnails offline come
from the synced thumbnail directory: `<catalog_root>/thumbnails/<SKU>.jpg` (make the dir path a
configurable setting; on Android this is a Syncthing-synced folder the operator picks).

## Mobile-first UI requirements (from the operator)

- Operable **without a keyboard**: dropdowns, chips, big tap targets, pull-to-refresh.
- Status colors are meaningful: ONLINE = green, OFFLINE = amber, error/critical = red.
- Item status badges color-coded by pipeline stage (In Stock / Draft / Staged / Active / Sold).
- Works on a phone-width and a tablet-width layout (responsive grid column count).

## Deliverables checklist

- [ ] `apps/tgw_app/` builds for Linux (`flutter build linux`) and analyzes clean
      (`flutter analyze`). If the Android SDK isn't present on this box, ensure the project is
      configured for Android and say so in the result file — don't fail the whole task on it.
- [ ] `pubspec.yaml` with pinned dependency versions (flutter_riverpod, dio, sqflite +
      sqflite_common_ffi, flutter_secure_storage, cached_network_image or similar).
- [ ] Settings screen persists base URL + Bearer token (secure storage) + offline DB path.
- [ ] Browse + Item + Home + Settings implemented per above; later tabs stubbed/disabled.
- [ ] A short `apps/tgw_app/README.md`: how to set the token, point at the DB, run on Linux,
      and build for Android.
- [ ] `GEMINI-003-result.md` → `inbox/` with the file tree, assumptions, and verified commands.

## Constraints

- Do **not** commit to git (Dave controls history). Just write files.
- Do **not** invent backend endpoints — if you need one that isn't listed, note it as a
  "BACKEND-NEEDED" item in the result file (Claude will add it). One you'll likely hit: there is
  **no `GET /api/health`** yet — for the Home tab, derive a coarse status from
  `/api/queue/status` reachability and flag health as BACKEND-NEEDED.
- Keep the Dart idiomatic and conventional. Prefer clarity over cleverness.
