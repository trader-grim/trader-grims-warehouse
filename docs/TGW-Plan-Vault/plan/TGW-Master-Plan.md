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
- SKU format `tgwYYYYMMDDHHMMSSmmm` — date is a string-comparison selector
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
### Phase 2a observation gate (do not retire cron until cleared)
- `tgw-worker@token_refresh.service` active; queue self-perpetuating
- Gate: confirm one full expiry+refresh cycle in journal, then retire `ebay_api_token_refresh` cron
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
### 2a. Token refresh worker ✅ (observation phase — cron still running)
- Prerequisite: Phase 1.0 secrets_root complete; run initial OAuth once on production machine
- On claim: check token expiry; if within buffer → refresh; on success → `succeeded` + reschedule
- Transient failure (network, eBay 5xx) → `retry_wait` with backoff
- Hard failure (refresh token dead) → `dead_letter` + `notify()` — human re-consent required
- Self-reschedule on success: enqueue next run at `not_before = now + interval`
- Run alongside existing eBay cron; observe across one full expiry cycle before retiring cron
- Gate: cron retired only after one observed expiry+refresh cycle confirms the worker is reliable
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
### Onto a doubly-proven foundation
- 3a. inotify/Syncthing detect stable camera bundle arrival
- 3b. Move bundle to `ItemData/<SKU>/`, lock item
- 3c. Local AI identify (qwen2.5vl:7b) — offline path
- 3d. Online path: eBay Taxonomy → category, Media API → image refs
- 3e. AI fills eBay specifics; create/update draft; write back to item JSON
- 3f. Offline path: write draft CSV for later upload
- 3g. After item JSON written: enqueue `catalog-rebuild` job (coalesced, `not_before +30s`) + `thumbnail-gen` job for this SKU

## Phase 4 — eBay pipeline buildout
- 4a. eBay photo uploader (`src/tgw/ebay/upload.py`) — after upload, enqueue `thumbnail-gen` for the SKU (local photos are source of truth; eBay URLs do not replace them)
- 4b. Listing sync-back (`src/tgw/ebay/sync.py`) — after writing updated fields to item JSON, enqueue `catalog-rebuild` job
- 4c. Category/aspect client (`src/tgw/ebay/categories.py`)
- 4d. Category template system (specifics defaults per category)
- 4e. Retire eBay token cron (gate: Phase 2a fully observed)
- 4f. Duplicate item/listing check worker (PP-ADD-006): pre-upload gate queries SQLite catalog for dedup + eBay active listing check; configurable block-vs-warn policy
- 4g. Inventory API migration sweep (PP-ADD-008): periodic scan for unmigrated items; auto-create Inventory API records (dry-run mode); dashboard widget % migrated

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
- One-time + ongoing enforcement across three groups: Epoch 0 legacy IDs, 2005–2007 era (202005–202007 prefix format), length standardization
- SKU audit report: distribution across all three groups + length histogram
- Canonical SKU format spec document required before any migration code
- Migration script: dry-run mode → live run with rollback support
- `sku_history` table: (sku_current, sku_prior, changed_at, change_reason, changed_by)
- Enforcement: validation at ingestion points — reject or auto-correct non-conforming SKUs
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
