---

mindmap-plugin: markdown

---


# TGW Master Plan

## How to read this file
- This is the living spec. Open in Obsidian with the **Markmap** plugin to see the mind-map.
- It is also plain Markdown — paste it into any model to give full project context.
- Headings are the structure. The PM-intake worker updates this file from dropped notes.
- Each leaf task is sized for one Sonnet/Haiku execution session.

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
### Queue decision (settled this session)
- Pure state-machine model (Option B) — PostgreSQL is the single work ledger
- No filesystem `.job.json` path — the old launcher/filesystem queue retires
- systemd keeps worker processes alive; PostgreSQL decides what work is done
- Workers are interchangeable hands; intelligence lives in the ledger
- A shared `QueueWorker` base holds claim/lease/complete/fail — no worker hand-rolls SQL
- Consequence accepted: PostgreSQL is now load-bearing — health, backups, startup ordering matter

## Current state
### Done
- Installable Python package `tgw` with src/ layout, pyproject, console scripts
- Platform layer: config, resolver, items, catalog, logging, notify, health
- tgw-api split into config/resolver/items/catalog/api modules
- Output-contract bug fixed (list now wrapped in ok/count/items)
- State machine schema applied + smoke-tested on the HP box
- Backup service running (inotify + rsync hardlink snapshots)
- eBay OAuth get/refresh working; token kept alive by a cron job (do not remove yet)
- 19+ unit tests passing; GitHub private repo live
### Running but to be retired
- queue-launcher spawning filesystem workers that do no useful work
- old filesystem queue system (worked, but superseded by state-machine design)
### Built but unwired
- PostgreSQL state machine (schema.sql + state_machine.py) — connected to nothing yet

## Phase 1 — Queue foundation
### 1a. Echo worker (reference implementation)
- Build `QueueWorker` base class: claim → do → complete/fail loop
- Build no-op echo worker subclassing it (proves plumbing, zero business risk)
- Wire to PostgreSQL claim_queue_jobs / mark_succeeded / mark_failed
- Verify: insert job → worker leases → completes → state correct
- Verify: kill mid-job → lease expires → recover_expired_jobs requeues
- Decide: custom launcher vs systemd templated units `tgw-worker@.service`
### 1b. Startup ordering + health
- systemd: workers depend on postgresql.service being up
- Extend `tgw health` to check Postgres reachability + queue depth
- Wire tgw.logging into the worker base (every claim/complete logged)
### 1c. Retire the old path
- Remove filesystem `.job.json` discovery from launcher
- Retire dead queue symlinks and the old launcher once echo proven

## Phase 2 — First real worker (PM-intake)
### Why this one first
- No external dependency, no business blast radius
- Delivers the #1 priority (markmap plan hub) as a live pipeline
### 2a. PM-intake worker
- Watches `inbox/` — a dropped note enqueues a job
- Worker reads the note, calls local Ollama (qwen2.5) to classify what changed
- Updates this Master Plan file, master map, and short-term todo
- Idempotent; safe to re-run; logs every change
### 2b. tgw suggest + plan intake
- `tgw suggest "..."` appends to `suggestions/` for next planning session
- Folder-drop intake: drop a plan doc → filed into the right plan section

## Phase 3 — Camera-intake pipeline
### Onto a doubly-proven foundation
- 3a. inotify/Syncthing detect stable camera bundle arrival
- 3b. Move bundle to `ItemData/<SKU>/`, lock item
- 3c. Local AI identify (qwen2.5vl:7b) — offline path
- 3d. Online path: eBay Taxonomy → category, Media API → image refs
- 3e. AI fills eBay specifics; create/update draft; write back to item JSON
- 3f. Offline path: write draft CSV for later upload

## Phase 4 — eBay pipeline buildout
- 4a. eBay photo uploader (`src/tgw/ebay/upload.py`)
- 4b. Listing sync-back (`src/tgw/ebay/sync.py`)
- 4c. Category/aspect client (`src/tgw/ebay/categories.py`)
- 4d. Category template system (specifics defaults per category)
- 4e. Retire eBay token cron once queue-based refresh proven

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

## Phase 6 — Later horizons
- Chat-history preservation across platforms (priority #4)
- HUD / barcode reader warehouse interface
- LTSP fat-client worker expansion (just more hands at the foreman)
- Multi-marketplace abstraction (Amazon, FB Marketplace)
- Sales website frontend with affiliate self-competition

## Data cleanup (parallel track)
- Pass 1: itemdata_scrub dry-run → review → --write (merge history keys, drop junk)
- Pass 2: photo_history_recovery dry-run → review → --write
- Pass 3: import eBay listings to fill gaps; then freeze the field schema
- Purge tgw1970* epoch-zero bad SKUs
- Recovery source: historical-tgw-catalog.json

## Open questions for next session
- Custom launcher or systemd templated units for worker liveness?
- Per-queue worker counts (start: 1 each, serialize AI work)
- Where does the Ollama lock live — in the job manager or a Postgres advisory lock?
