---
title: TGW Master Plan
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-02
maintained_by: Opus (planner)
---

# TGW Master Plan

## How to read this file
- This is the living spec. Open in Obsidian with the **Markmap** plugin to see the mind-map.
- It is also plain Markdown — paste it into any model to give full project context.
- Headings are the structure. The PM-intake worker updates this file from dropped notes.
- Each leaf task is sized for one Sonnet/Haiku execution session.
- Vault sync protocol (Syncthing, conflict resolution): see `OPERATIONS-vault-sync.md`

## Settled architecture
### Do not relitigate
- tgw-api is the fence — all ItemData reads/writes go through it
- One folder per SKU — `ItemData/<SKU>/<SKU>.json` + media
- Python owns data state — tgw.source becomes thin one-line wrappers
- resolve() is the canonical selector engine
- Bulk-first — claim a set, operate on the set, return a summary
- Workers are thin — they ask tgw-api, never construct paths
- Output contract — every call returns one JSON object with an `ok` key
- SKU format `tgwYYYYMMDDHHMMSSs` — **18 chars**: date + time + 1-digit tenths; string-comparison sortable
### Queue decision (settled)
- Pure state-machine model — PostgreSQL is the single work ledger
- No filesystem `.job.json` path — the old launcher/filesystem queue retires
- systemd keeps worker processes alive; PostgreSQL decides what work is done
- Workers are interchangeable hands; intelligence lives in the ledger
- A shared `QueueWorker` base holds claim/lease/complete/fail — no worker hand-rolls SQL
- PostgreSQL is now load-bearing — health, backups, startup ordering matter
### Process liveness (settled)
- systemd templated units `tgw-worker@<queue>.service` — not a custom launcher
- `After=postgresql.service`, `Requires=postgresql.service` on all worker units
### Secrets (settled)
- One canonical `secrets_root` directory, resolved from `tgw-api-config.json`
- Directory lives outside repo tree (`/opt/TGW/secrets/`), `chmod 700`, files `chmod 600`, owned by `tgw`
- Every secret resolves from `secrets_root` — no hardcoded paths anywhere
- Token state, refresh tokens, eBay app/cert credentials, all future marketplace keys live here
### Satellite catalog (compact format now established)
- SQLite is the compact catalog format — master builds `tgwcatalog.db`; satellite carries a filtered subset
- Schema: indexed scalar columns (sku, title, location, status, price, qty, image) + full JSON `data` column
- Thumbnail cache at `catalog_root/thumbnails/<SKU>.jpg` — same path on master and satellite
- PP-ADD-001 (Phase 6) sync return path still needs design: dirty-flag / change-log per row, merge strategy
- Item schema and API fence design should not accidentally preclude a deferred/offline mode
### Catalog rebuild (settled pattern)
- Any worker that writes to ItemData enqueues a `catalog-rebuild` job — never calls `build_all_catalogs()` inline
- `catalog-rebuild` worker claims the job → calls `build_all_catalogs()` (JSON + SQLite + location tree) → succeeds
- Thumbnail rebuild is a separate `thumbnail-gen` job: takes a SKU, generates only that item's thumbnail (fast path)
- Full thumbnail sweep (`tgw build-thumbnails`) runs on demand or scheduled; per-SKU job runs after each intake
- Batching: `catalog-rebuild` jobs use `not_before = now + 30s` so rapid successive writes coalesce

## Current state
### Done
- Installable Python package `tgw` with src/ layout, pyproject, console scripts
- Platform layer: config, resolver, items, catalog, logging, notify, health
- tgw-api split into config/resolver/items/catalog/api modules
- Output-contract bug fixed (list now wrapped in ok/count/items)
- State machine schema applied and wired to live PostgreSQL (`state_machine` db)
- Backup service running (inotify + rsync hardlink snapshots)
- 19+ unit tests passing; GitHub private repo live
- SQLite catalog (`tgwcatalog.db`) — 55,347 items; `tgw build-sqlite` + `tgw build-all`
- Thumbnail cache — 54,310 thumbnails at `catalog_root/thumbnails/`; `tgw build-thumbnails`
- **Phase 1 COMPLETE** — secrets_root, QueueWorker base + HardFailure pattern, echo worker, systemd `tgw-worker@.service` template, health extended (Postgres + SQLite + thumbnails), old launcher retired
- **Phase 2a COMPLETE — observation phase** — token_refresh worker live under systemd; OAuth token active (expires ~2h, auto-refreshes); expiry-based self-reschedule; running alongside eBay cron
- **Phase 2b COMPLETE** — PM-intake worker live under systemd; watches `inbox/`, calls `Qwen2.5:latest` via Ollama, patches Master Plan, archives notes; `tgw/apis/ollama.py` client added
- **Phase 2c COMPLETE** — `tgw suggest "..."` appends timestamped entries to `suggestions/SUGGESTIONS.md`
- **State-machine bug fixed** — `recover_expired_jobs()` now promotes `retry_wait` jobs back to `queued` when `not_before` passes; previously transient failures left jobs stuck indefinitely
### Phase 2a observation gate ✅ CLEARED 2026-06-02
- `ebay_token_refreshed` observed at 12:07 — full expiry+refresh cycle confirmed
- No separate cron existed to retire; worker is sole token manager
### Retired this session
- `queue-launcher.service` disabled; stub in code preserves the console script
- Filesystem `.queue_worker` / `.queue_worker_config` discovery removed from all code
- eBay credentials removed from `tgw-api-config.json`; now in `secrets_root`

## Phase 1 — Queue foundation ✅ COMPLETE (2026-06-02)
### 1.0 secrets_root migration ✅
- Add `secrets_root` key to `tgw-api-config.json`; join existing `get_tgw_paths()` auto-creates
- Create `/opt/TGW/secrets/` outside repo tree; `chmod 700`, files `chmod 600`, owner `tgw`
- Move existing secret files in; update token manager and health to resolve from `secrets_root`
- Fix health path bug: `tgw health` and token manager must read/write the same file
- Add `secrets/` to `.gitignore` (belt-and-suspenders)
- Verify: `tgw health` shows token status green; no hardcoded secret paths remain in `src/`
### 1a. Echo worker ✅
- Build `QueueWorker` base class: claim → do → complete/fail loop
- Build no-op echo worker subclassing it (proves plumbing, zero business risk)
- Wire to PostgreSQL claim_queue_jobs / mark_succeeded / mark_failed
- Verify: insert job → worker leases → completes → state correct
- Verify: kill mid-job → lease expires → recover_expired_jobs requeues
- systemd templated unit wiring: `tgw-worker@echo.service`
### 1b. Startup ordering + health ✅
- systemd: workers depend on postgresql.service being up
- Extend `tgw health` to check Postgres reachability + queue depth
- Wire tgw.logging into the worker base (every claim/complete logged)
### 1c. Retire the old path ✅
- Remove filesystem `.job.json` discovery from launcher
- Retire dead queue symlinks and the old launcher once echo proven

## Phase 2 — First real workers
### 2a. Token refresh worker ✅ COMPLETE (gate cleared 2026-06-02)
- Self-schedules based on token expiry; refreshes when ≤30 min remain
- Transient failure → `retry_wait`; hard failure (dead refresh token) → `dead_letter` + notify
- Template for everything after: claim → lease → run → succeed/retry/dead_letter → reschedule
### 2b. PM-intake worker ✅ COMPLETE (2026-06-02)
- Watches `inbox/` — a dropped note enqueues a job
- Worker reads the note, calls local Ollama (`Qwen2.5:latest`) to classify what changed
- Updates this Master Plan file; idempotent, safe to re-run, logs every change
- Notes truncated to 4000 chars; plan sent as headings-only (CPU-only machine — use sparingly)
- `tgw/apis/ollama.py` — reusable client for all future Ollama workers
### 2c. tgw suggest + plan intake ✅ COMPLETE (2026-06-02)
- `tgw suggest "..."` appends `- [ ] TIMESTAMP :: text` to `suggestions/SUGGESTIONS.md`
- Folder-drop intake: drop a `.md` file in `inbox/` → PM-intake worker files it (Phase 2b)

## Phase 3 — Camera-intake pipeline
### 3a. Bundle detection + intake ✅ COMPLETE (2026-06-02)
- `incoming/newitems/<SKU>/` — per-item dir with stub JSON + photos (any camera names)
- `incoming/newitems/<SKU>.zip` — single-item zip inside SKU dir
- `incoming/newitems/multi/<SKU>/` — multi-item zip; split by timestamp subdirs
- Stability gate: all files unmodified for 30s (Syncthing safe)
- Workers: `bundle_intake`, `multi_intake`
### 3b. Move to ItemData ✅ COMPLETE (2026-06-02)
- Photos moved to `ItemData/<SKU>/`; canonical `<SKU>.json` written with stub fields
- Multi-item split: child SKUs = parent SKU + sequential increment (tgw...010 → ...011, ...012)
- Downstream: enqueues `catalog_rebuild` (coalesced 30s), `thumbnail_gen`, `ai_identify`
- Workers: `catalog_rebuild`, `thumbnail_gen`
### 3c. Local AI identify (qwen2.5vl:7b) ✅ COMPLETE (2026-06-02)
- Sends resized primary photo (512px, ~56KB) to `qwen2.5vl:7b` via Ollama
- Returns title, category, description, condition as JSON; writes to item JSON
- Cold-start: model loads in ~10 min; subsequent calls ~18s — worker pre-warms on startup
- Skip logic: skips only when `ai_identified: true` AND no `ai_reidentify` flag
- Worker: `ai_identify`; `ai_identified: true` flag written to JSON; `ai_reidentify` cleared after use
### 3c-ext. AI hint system ✅ COMPLETE (2026-06-03)
- `ai_hint` field in item JSON: operator-supplied keyword or phrase to guide vision model
- Hint source priority: explicit `ai_hint` → human-set title (if not SKU and not yet identified)
- Hinted prompt includes "I know this item is: {hint}" — AI produces full eBay-quality title + description using both the hint and the photo
- `tgw hint <SKU> "text"` — writes hint, sets `ai_reidentify: true`, enqueues `ai_identify` job
- `tgw hint <SKU> "text" --force` — same but also forces re-run on already-identified items
- Previously-identified items are not re-run unless `--force` is given
- **Revisit (PP-HINT-001):** hint system is first iteration only — see open items below
### 3d. Online path: eBay Taxonomy → category ✅ COMPLETE (2026-06-02)
- `apis/ebay/client.py` — shared auth'd GET/POST for all eBay REST calls
- `apis/ebay/taxonomy.py` — category suggestions; tries title first, falls back to AI category string
- `ebay_category_id` + `ebay_category_name` written to item JSON
### 3e. AI fills eBay specifics; create/update draft ✅ COMPLETE (2026-06-02)
- `apis/ebay/specifics.py` — fetches aspects for a categoryId, filters boilerplate
- `workers/ebay_draft.py` — Qwen2.5 fills SELECTION_ONLY + FREE_TEXT aspects, validates choices
- `draft_listing` block written to item JSON: title, categoryId, condition, format, quantity, price=null, item_specifics, description
### 3f. Offline path: write draft CSV for later upload
- If eBay unreachable: write CSV row with known fields for manual upload
### 3g. Downstream catalog jobs ✅ COMPLETE (2026-06-02)
- `catalog_rebuild` job (coalesced, `not_before +30s`) enqueued after every write
- `thumbnail_gen` job (per-SKU) enqueued after intake

## Phase 4 — eBay pipeline buildout
### 4a. eBay photo uploader ✅ COMPLETE (2026-06-02)
- `tgw/ebay/upload.py` — `upload_photo()` via Trading API `UploadSiteHostedPictures`; returns eBay EPS FullURL
- `workers/ebay_upload.py` — idempotent; skips already-uploaded photos; writes `ebay_photos` list + `draft_listing.imageUrls`
- Enqueued automatically by `ebay_draft` after draft is written
### 4b. Listing publish + sync-back ✅ COMPLETE (2026-06-02)
- `tgw/ebay/sync.py` — `publish_draft()`: upserts inventory item, finds/creates offer, publishes; `fetch_all_offers()` paginated
- Condition mapping: AI strings → eBay Inventory API enums (e.g. "Good" → "USED_GOOD")
- Account policies + merchant location fetched once per process, cached
- `workers/ebay_publish.py` — manual trigger; gates on price non-null + photos uploaded; writes `ebay_listing` block
- `workers/ebay_sync.py` — self-scheduling every 6h; syncs eBay offer status back to item JSON
- eBay returns 400 (not empty list) when no Inventory API offers exist — handled gracefully
### 4c. Category/aspect client — deferred; existing taxonomy.py + specifics.py cover current needs
### 4d. Category template system — deferred; see PP-HINT-001 (eBay enrichment) below
### 4e. Retire eBay token cron ✅ COMPLETE — no separate cron existed; token_refresh worker is sole manager
### 4f. Duplicate item/listing check worker (PP-ADD-006) — pending
### 4g. Inventory API migration sweep (PP-ADD-008) — pending
### 4h. Pricing module — pending; see PP-PRICE-001 below
### 4i. Live listing revision / update draft — pending; see PP-REVISION-001 below

## Phase 5 — AI operations layer
### Ollama job manager
- Serializes model jobs (one model loaded at a time, 32GB CPU-only)
- A queue worker that owns the Ollama lock
- Uninstall redundant models (llava, minicpm-v, moondream, etc.)
### AI work-distribution + usage monitoring
- Priority #2 deliverable
- Track which model did which job, time + token/compute cost
- Interface to see usage across Claude / Perplexity / Gemini / Ollama
- Feeds the "cost per item" and electricity-cost goals
### History merge worker (PP-ADD-003)
- Background queue worker: aggregate, deduplicate, and organize item history by SKU
- Per-SKU event log (event type, timestamp, source, actor, payload)
- Incremental merge on new events; full rebuild on demand
- Prerequisite: PP-ADD-005 SKU normalization complete or running in parallel
### Picklist generator (PP-ADD-009)
- Replace phone-app-based picklist generation
- Input: order IDs → output: pick list sorted by location/bin
- Print-ready PDF + QR code option encoding picklist_line data
- Trigger from GUI app (Phase 6) or standalone web page
- Keep plain-text picklist_line as fallback during transition

## Phase 6 — Satellite and later horizons
### Satellite / client operation — disconnected catalog (PP-ADD-001)
- SQLite format already established at master level (`tgwcatalog.db`); satellite carries a filtered subset (e.g. `WHERE location IN (...)` or full copy if storage allows)
- Thumbnail cache at `catalog_root/thumbnails/<SKU>.jpg` — same relative path on satellite; partial copy synced alongside SQLite
- Dirty-flag / change-log: add `dirty` flag and `local_updated_at` column to satellite schema before dev
- Sync/promotion worker: conflict detection, merge strategy, audit trail
- API surface: pull catalog updates (delta from master SQLite), push local changes to master
- Admin UI: per-node sync status, pending migrations
- Decision required before dev: conflict resolution policy (last-write-wins vs. manual review)
### Linux / Android GUI application (PP-ADD-002)
- Technology spike: Flutter, Tauri, or Qt (target: Linux x86_64 + ARM, Android 10+)
- Catalog browser queries `tgwcatalog.db` directly (SQLite); thumbnails served from `thumbnails/<SKU>.jpg`
- Catalog editor: field-level edit writes to item JSON via tgw-api, then enqueues `catalog-rebuild` + `thumbnail-gen`
- Inventory interface, settings/connection panel
- Picklist generator (PP-ADD-009) as embedded screen; QR code as first-class element
- Packaging: .deb / .AppImage for Linux; signed .apk / Play Store for Android
### Backup / archive / sync integration (PP-ADD-004)
- Scheduled full + incremental backup (configurable retention policy)
- Archive tier: compress and move aged records to cold storage
- Sync engine: push/pull master ↔ satellite (evaluate reuse of PP-ADD-001 worker)
- Restore procedure and runbook (tested)
- Health dashboard: last backup time, backup size, sync lag per node
### AI runtime manager (PP-ADD-010)
- Periodic health check + update for non-pip-installed services: Claude Code, Ollama, Whisper.cpp
- All three share similar install/update pattern (binary download, checksum verify, in-place replace)
- Unified CLI: `mgr status`, `mgr update [all]`, `mgr restart <component>`
- Scheduled health check with alert on unhealthy state (log + optional notification)
### LTSP fat-client worker expansion
- Remote nodes as more hands at the same foreman
### Multi-marketplace abstraction
- Amazon, FB Marketplace
### Sales website frontend
- Affiliate self-competition

## Data cleanup (parallel track)
### SKU normalization (PP-ADD-005) — Critical; unblocks PP-ADD-003, 006, 008
#### Audit ✅ COMPLETE + decisions confirmed (2026-06-03) — see `SKU-Audit-Report.md`
- Canonical format is **18 chars**: `tgwYYYYMMDDHHMMSSs` (tenths, not ms) — chosen for barcode labels
- 55,351 items; 20,328 already canonical (Class C); 35,023 to migrate
- **7 format classes, all migration rules confirmed:**
  - C: len-18 `tgwYYYYMMDDHHMMSSs` — **20,328 ✅ already canonical**
  - A: len-20 `tgwYYYYMMDDHHMMSSmmm` — 34,737; truncate last 2 digits; collision check first; live listings via slow eBay batch (delist→relist ~50/batch)
  - B: Epoch-0 `tgw1970...` — 26; new format `tgw201501021970xxx` (last 3 of original); no eBay listings
  - D: Underscore len-18 — 33; strip `_`, append `0`
  - E: YYMMDD 2020-era — 16; prepend `20` to expand year, truncate ms to 1 digit
  - F: No-tenths len-17 — 210; append `0`
  - G: Anomaly len-19 — 1 item (disposed); manual
#### Remaining work
- Canonical SKU spec (one-paragraph normative definition for enforcement code)
- `sku_history` table in `state_machine` DB: `(sku_current, sku_prior, changed_at, change_reason, changed_by)`
- Migration script: dry-run → review → live run with rollback manifest
- Enforcement at intake points (bundle_intake, multi_intake): reject non-canonical on input
- Post-migration verification report
### Data scrub passes
- Pass 1: itemdata_scrub dry-run → review → --write (merge history keys, drop junk)
- Pass 2: photo_history_recovery dry-run → review → --write
- Pass 3: import eBay listings to fill gaps; then freeze the field schema
- Epoch-zero SKU purge (tgw1970*) subsumed by PP-ADD-005 normalization
- Recovery source: historical-tgw-catalog.json

## Shelved
### eBay relisting obfuscation (PP-ADD-007) — shelved; ToS review required
- Concept: delist → mutate photo checksum → regenerate title/description → assign mock SKU → relist as new
- Shelved because: the photo mutation step is designed to defeat eBay's duplicate image detection — this is the mechanism that makes it work, and it is almost certainly a policy violation
- Simple relist (end listing → relist unchanged) is permitted and does not need this tool
- eBay policies to read before reconsidering:
  - **Duplicate listings**: same single-quantity item cannot appear as multiple active listings; technical manipulation to defeat detection is not permitted
  - **Search and browse manipulation**: relisting to artificially reset listing age or boost placement is prohibited
  - **Image manipulation**: pixel-level edits to change image hashes specifically to evade duplicate detection
  - **Item identity via custom label**: using a new mock SKU to cause eBay to treat a relisted item as unrelated
  - **Account risk**: violations can trigger listing removal, seller limits, or account suspension
- Do not implement until an explicit eBay ToS review confirms the specific techniques are compliant

## Open questions
- Per-queue worker counts (start: 1 each; serialize AI work in Phase 5)
- Where does the Ollama lock live — in the job manager worker or a Postgres advisory lock? (Phase 5 decision)
- PP-ADD-001 conflict resolution policy: last-write-wins vs. manual review (decide before Phase 6 dev)
- Thumbnail cache: install Pillow (`pip install Pillow` or `pip install trader-grims-warehouse[thumbnails]`) then run `tgw build-thumbnails`

## PP-MC-001 — Midnight Commander Admin Interface

### Vision
MC is the primary console administration tool for TGW — on the master machine, over SSH,
and on LTSP/satellite nodes. The half-height layout (catalog/item panes top, Claude Code
bottom) is the target working environment. MC was chosen for its Norton Commander lineage,
universal availability, zero-friction install, and suitability as both a primary interface
and a fallback when graphical tools aren't present. It is the first app installed on any new
system in this operation.

All writes go through `tgw-http` (the FastAPI service, PP-EDITOR-001) when available.
Reads use the local SQLite catalog and ItemData directly — MC works offline on any node.

### What exists (as of 2026-06-03)
**Built and installed (`/opt/TGW/mc/` + `~/.config/mc/`):**
- `tgwitem` extfs — browse SKU JSON as VFS: `meta.json`, `fields/` (one .txt per field), `photos/` (images/video). Implements list + copyout + run.
- `tgwcatalog` extfs — 55K+ items organised by location as a navigable VFS. Reads search-catalog.json.
- `tgwqueue` extfs — live PostgreSQL queue snapshot; subdirs per state, one file per job.
- `tgwhealth` extfs — platform health checks as named OK_/FAIL_ files.
- `tgwservices` extfs — systemd TGW service status.
- `tgw-mc-status.py` — F2 menu viewer: health, queue, services, catalog stats, item summary.
- `tgw-view-image.sh` — chafa renderer; forces `--format=symbols` for MC's ascii viewer.
- `mc.ext.ini` — file associations: SKU JSON → tgwitem VFS; sentinels → VFS; images/video → chafa.
- `mc.menu` — F2 menu: `v`=VFS guide, `h`=health, `q`=queue, `s`=services, `l`=catalog, `i`=item summary, `p`=image preview.
- `install-system-mc.sh` — system-wide installer (ext, menu, extfs scripts).

### Phase 1 — Fix what's broken ✅ COMPLETE (2026-06-03)
- ✅ `tgwitem cmd_run` for fields fixed: temp file → less shows field value (not raw archive JSON)
- ✅ `tgwcatalog` migrated to SQLite (`tgwcatalog.db`): list call now ~0.8s vs multi-second JSON load; falls back to search-catalog.json if DB absent
- ✅ `tgwservices` now enumerates all `tgw-worker@*` units dynamically via `systemctl list-units --output=json`; fixed infra list includes `tgw-http`
- ✅ `tgw-view-image.sh`: TERM/COLORTERM forced for MC viewer context; COLUMNS/LINES detection improved; chafa `--format=symbols` already correct
- ✅ `tgwitem cmd_run` for photos: added `--format=symbols --colors=full` to force Unicode half-block art (prevents sixel/kitty auto-detect)
- ✅ `tgwitem` copyout for photos: serves full ItemData JSON (richer than catalog row)
- Remaining known gap: **No copyin on tgwitem** — fields still read-only; `copyin` not implemented (Phase 2)
- Note: image viewing in MC's `%view{ascii}` may still need interactive tuning — chafa+MC ANSI rendering is terminal-dependent

### Phase 2 — Item editing
- Implement `copyin` in `tgwitem` — save edited field file back to item JSON; enqueue `catalog_rebuild`
- Add `ebay/` subdir to `tgwitem` VFS — `draft_listing/` and `ebay_offer/` fields; read-only first
- Add `pipeline/` subdir to `tgwitem` — current job state per queue for this SKU (live PG query)
- F2 menu actions inside `tgwitem` VFS: re-identify, re-draft, re-price, re-stage, set-hint — enqueues jobs via `tgw-http` API or direct state_machine call

### Phase 3 — eBay form + gallery
- `ebay/` subdir fields become editable via copyin (price, condition, aspects, title)
- Image gallery mode: inside `photos/`, F3 renders image with chafa; arrow keys navigate
- `tgwcatalog` → Enter on item → jump to `tgwitem` VFS for that SKU (via real path)
- Thumbnail preview in catalog listing (chafa in narrow column — feasibility TBD)

### Phase 4 — Universal admin extensions
- Queue action menu: from `tgwqueue` VFS, F2 on a dead_letter job → re-queue or cancel
- Health drill-down: from `tgwhealth` VFS, Enter on FAIL_ → show detail + suggested fix
- Log viewer: `tgwlogs` VFS — recent journalctl output per worker, filterable
- SSH-clean: all operations work with no X11 forwarding, no GUI dependencies

### PP-MC-002 — LTSP / satellite console nodes (later)
- Package MC config + sentinels + extfs scripts for deployment to LTSP fat clients
- Read-only satellite mode: reads local synced `tgwcatalog.db` + thumbnails; writes queue to master via `tgw-http` when reachable
- Installation playbook (Ansible or shell) for new node bootstrap

---

## PP-EDITOR-001 — Item Editor / Inventory Management App

### Vision
Cross-platform graphical app (Linux desktop + Android tablet) for full inventory management.
The Android tablet is the primary mobile interface for warehouse operations — browsing by
location, identifying items, setting prices, staging to eBay, and eventually scanning and
picklist generation. Flutter is the settled technology choice: true cross-platform with
Android as a first-class target; reads `tgwcatalog.db` directly via sqflite when offline;
writes go through `tgw-http` when connected to master. Syncthing handles catalog + thumbnail
sync to the tablet automatically.

### Architecture
```
tgw-http (FastAPI)         ← shared API for all write operations
     ↑                ↑
MC console         Flutter app (Linux + Android)
(PP-MC-001)        sqflite reads tgwcatalog.db directly (offline)
                   Dio http client for writes (online)
```

### Phase A — tgw-http FastAPI service ✅ COMPLETE (2026-06-03)
- `tgw serve` subcommand starts FastAPI HTTP server on port 7373
- Bearer token auth — API key at `secrets_root/tgw-api-key.json`
- All 8 endpoints implemented and smoke-tested:
  - `GET /api/items` — SQLite search (text, location, status, date range, limit/offset)
  - `GET /api/items/:sku` — full item JSON + _images/_videos + _queue_jobs (last 50)
  - `PATCH /api/items/:sku` — multi-field atomic update; location tree kept in sync; enqueues catalog_rebuild
  - `GET /api/items/:sku/thumbnail` — serves thumbnail from cache
  - `POST /api/items/:sku/action` — enqueues any pipeline stage (ai_identify sets ai_reidentify); handles dedupe gracefully
  - `GET /api/queue/status` — job counts per queue+state from PostgreSQL
  - `GET /api/ebay/aspects/:category_id` — delegates to existing specifics.py
  - `GET /api/locations` — distinct locations from SQLite
- `src/tgw/http_server.py`; `etc/systemd/tgw-http.service` (installed, enabled, running)
- fastapi + uvicorn[standard] added to pyproject.toml dependencies

### Phase B — Flutter skeleton
- Flutter project at `apps/tgw_app/`; Linux + Android build targets confirmed
- sqflite reading from `tgwcatalog.db` (same path layout as master, synced by Syncthing)
- Dio HTTP client wired to `tgw-http`
- Navigation shell (bottom nav bar)
- Connection state: online (API available) vs. offline (catalog read-only)

### Phase C — Browse + item view
- Gallery screen: thumbnail grid, title, location chip, pipeline status badge
- Filters: location selector, status filter, text search
- Item detail screen: tabbed — Item fields / eBay draft / Offer status

### Phase D — Edit + pipeline actions
- Edit screen: title, condition, price, item_specifics (aspect form), hint field
- Historical title suggestions (pulldown from catalog)
- AI buttons: "Re-identify", "Set hint + re-identify"
- Pipeline action dispatch: pick start/end stage, confirm, enqueue via API
- Save → PATCH /api/items/:sku

### Phase E — eBay offer form
- Aspect fields from `/api/ebay/aspects/:cat` — SELECTION_ONLY as dropdown, FREE_TEXT as field
- Price with comp range display (from ebay_offer.price_comps)
- Stage / Publish actions
- Mirrors Seller Hub form layout

### Later phases (separate PPs)
- Scanner input (barcode/SKU lookup → item detail)
- Picklist generator (PP-ADD-009) as embedded screen
- Offer management list view
- Fulfillment workflow
- Tasker hooks for push notifications from master → tablet

---

## Pending projects (revisit)

### PP-HINT-001 — AI hint + eBay enrichment (revisit required)
- First iteration shipped 2026-06-03: `ai_hint` field, `tgw hint` command, hinted vision prompt
- **Known gaps to address:**
  - `tgw requeue` bulk command: filter-based batch re-queue (e.g. "all items with photos but no title") for catalog maintenance — without triggering eBay listing pipeline
  - eBay Browse API enrichment in `ebay_draft`: search similar active listings by title, extract common aspects and category signal to supplement AI-generated specifics
  - Full item history / hint trail: per-SKU log of identification rounds, hints used, AI vs human changes — feeds audit and tuning visibility
  - eBay Marketplace Insights scope (`buy.marketplace_insights`): apply in eBay developer portal for sold+trend data; interim: use Finding API `findCompletedItems` (App ID only, no user token)
  - Revision of already-identified items: `tgw hint --force` works but downstream ebay_draft/ebay_draft re-runs need to be aware of published state (don't auto-push changes to live listings)
  - Tuning: run difficult items through, observe results, adjust prompt and hint format

### PP-PRICE-001 — Pricing module ✅ INITIAL COMPLETE (2026-06-03)
- Initial pricing shipped: `tgw/ebay/pricing.py` + `workers/ebay_price.py`
- `ebay_price` worker enqueued automatically by `ebay_draft` after draft is written
- Three-stage Browse API fallback: full title → category+short title → category only
- Writes `ebay_offer` block: {price, price_source, price_comps {count,min,p25,median,max}, priced_at}
- Also sets `draft_listing.price` so `ebay_publish` can read it directly
- Idempotent: skips items already priced in `ebay_offer.price`
- **Repricer:** not yet built — periodic re-query + dirty flag + push; see PP-REVISION-001
- **`ebay_offer` block is now established** — PP-REVISION-001 can proceed with this as the pricing foundation

#### eBay Pricing API Access — Investigation Required
Current data source is Browse API active listing prices (asking prices, not sold prices).
Sold prices are significantly more accurate for pricing decisions.  The following APIs
provide sold/trend data and should be investigated for access expansion:

**1. eBay Finding API — `findCompletedItems`**
- Would return sold AND unsold completed listings; filter `SoldItemsOnly=true` for sold prices
- Auth: App ID only (`SECURITY-APPNAME` header) — no user OAuth token required
- Endpoint: `https://svcs.ebay.com/services/search/FindingService/v1`
- **Status: BLOCKED** — error 10001 "Service call has exceeded the number of times the operation is allowed to be called"
- This is an app-tier restriction, not a rate limit.  The Finding API has been deprecated by eBay and access is now restricted to apps that registered before the deprecation or have approved migration status
- eBay migration guide: https://developer.ebay.com/develop/ebay-api-capabilities/finding-api-migration
- Developer forum thread on errorId 10001: https://community.ebay.com/t5/Developer-Support/Finding-API-errorId-10001/td-p/
- **Action:** Contact eBay developer support to request Finding API access or confirm retirement timeline for this app ID

**2. eBay Marketplace Insights API — `item_sales/search`**
- Returns actual sold item data with sale price, date, quantity
- REST endpoint: `GET /buy/marketplace_insights/v1/item_sales/search`
- **Scope required:** `buy.marketplace_insights` — this is a **limited-availability scope**
- To apply: https://developer.ebay.com/develop/apis/restful-apis/buy-apis#marketplace-insights
- Application process: submit through eBay developer portal; approval is not guaranteed and may require business justification
- **Action:** Apply for `buy.marketplace_insights` scope at the link above

**3. eBay Terapeak (via Seller Hub) — not an API**
- eBay's own sold-price research tool, available to sellers in Seller Hub → Research → Terapeak
- URL: https://www.ebay.com/sh/research
- Not accessible via API; would require screen-scraping (ToS violation) or manual lookup
- Useful for manual pricing research while waiting for API access

**4. eBay Browse API — current implementation**
- `GET /buy/browse/v1/item_summary/search` — active listings only
- Works with existing token; no additional scope needed
- Limitation: active asking prices, not sold prices; p25 is conservative but not market-clearing
- Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search

**Interim strategy:** Browse API p25 (implemented) is a reasonable floor until sold-price access is obtained.
Operator should review and adjust prices in Seller Hub before publishing, especially for niche items.

### PP-STAGE-001 — eBay draft staging ✅ COMPLETE (2026-06-03)
- `workers/ebay_stage.py` — creates UNPUBLISHED offer on eBay; visible/editable in Seller Hub immediately
- `tgw/ebay/sync.py` split: `stage_draft()` (inventory item + offer, no publish) + `publish_offer()` (one API call)
- `ebay_price` enqueues `ebay_stage` automatically when price is successfully set
- `ebay_publish` simplified: reads `ebay_offer.offer_id`, calls `publish_offer()`, writes `ebay_listing`
- Stopgap until PP-REVISION-001 full revision system is built; leverages native Seller Hub editing

### PP-REVISION-001 — Live listing revision / update draft (design open)
- Three distinct workflows identified: new listing draft | live listing revision | ended→relist
- Revision needs: known baseline (live state synced from eBay), proposed delta, drift visibility
- Draft for new listing (`draft_listing`) is a historical record after publish — not the revision staging area
- Open design question: sparse delta vs full replacement for revision payload; history of applied revisions
- Relist: inventory item already exists on eBay; need fresh pricing + new offer; structurally re-create not update
- `ebay_offer` block now established (PP-PRICE-001) — proceed when ready

### MILESTONE-001 — tgw.source replacement ✅ (2026-06-03)
- The new TGW system (Phases 1–4 + PP-STAGE-001) constitutes a ~95% functional replacement of the legacy `tgw.source` system, significantly improved
- Full automated pipeline: photo intake → AI identification → eBay taxonomy → AI specifics → pricing → eBay draft staging → operator review → one-click publish
- 13 systemd workers running; PostgreSQL state machine; SQLite catalog; 55K+ item catalog
- Legacy tgw.source is now thin wrappers; new system is the authoritative data path
- Remaining gap (~5%): live listing revision / repricer / relist workflow (PP-REVISION-001)

- **PP-ADD-001 Satellite / Client Operation --- Disconnected Catalog Support**
  - **Project Details**
    - **Project ID**: PP-ADD-001
    - **Priority**: High
    - **Estimated Effort**: Large (4--6 weeks)
    - **Phase / Track**: Infrastructure
    - **Dependencies**: Master catalog schema, SKU normalization (PP-ADD-005), History module (PP-ADD-003)
  - **Overview**
    - Enable satellite/client nodes to operate independently when disconnected or loosely connected from the master system. Includes thumbnail generation for catalog browsing, temporary catalog update handling, and a defined data migration path to promote local changes back to master.

- ## Phase 7 — Vault Synchronization
- ### Syncthing Configuration
- #### Why This Matters
- #### Decision: Syncthing for Vault Sync
- #### tgw-Specific Conflict Resolution Protocol
- #### Optional: Git Backing for Version History
- #### Constraints Carried Forward (New)
