---
title: TGW Master Plan
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-05 (session 4)
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
### Pipeline fixes and additions — 2026-06-03
- `ebay_stage`: Content-Language header added to all Inventory API PUT/POST calls (errorId 25709)
- `ebay_stage`: Active listing guard — skips items with `ebay_listing.status=Active` before creating duplicate offers
- `ebay_stage`: Condition retry with USED_EXCELLENT on errorId 25021 (category rejects granular condition)
- `ebay_publish`: Content-Language fix; condition fallback on 25021; no pre-publish offer PUT (offer PUT is full-replace, strips fields)
- `ebay_price`: Now sets launch price (110% of max→.99) as the staged/published price; stores `target_price` = p25; `p75` added to comps
- `ebay_reprice` worker live: self-scheduling every 6h, scans for due reprice_schedule entries
- `multi_intake`: strips `Item number` from existing item JSON when child SKU matches parent; writes `source_sku`
- `conditions.py`: full condition policy cache (26 sets / 15K categories); `best_condition()` — same-or-worse fallback; wired into `ebay_draft`
- `tgw staged` / `tgw publish` — operator review gate before any item goes live
- **Open issue**: errorId 25002 `Item.Country` at publish for some categories (34032, 14027, 13916) — offer body is correct, investigating category-specific requirements

### Pipeline additions — 2026-06-05
- **PP-PRICE-004 DONE** — `tgw/velocity.py` aggregation module; `workers/velocity_stats.py` nightly
  self-scheduling worker; `tgw velocity-report` CLI (`--refresh`, `--category`, `--min-sold`, `--json`,
  `--output`); `velocity-stats.json` written to catalog_root (1,540 categories, 55k items on first run);
  `suggest_price()` gains `velocity` param → returns `velocity_hint: 'hold_launch'` when category
  sell-through at launch >50%. Stage breakdown (launch/retail/move%) populates as new-pipeline items sell.
- **PP-SOLD-001 Tier 2 run 2** — re-ran `import-sold-csv` after archive index update (22,124 entries):
  909 additional fuzzy matches; ~3,083 total sold items now recorded with `ebay_sale` blocks.
  **Archive tombstone pass added** (`pull.restore_archive_tombstone`): when archive index matches a
  listing ID but ItemData JSON is absent, the full item JSON is extracted from the archive ZIP and
  restored as `_archive_tombstone: True`, then marked sold normally. Dry-run peeks without writing.
  Archive IDs (223–326xxx, ~2018–2023) predate the 2-year CSV window — 0 hits until a full all-time
  eBay sold export is used (`tgw import-sold-csv <all-time.csv> --fuzzy`).

### Pipeline additions — 2026-06-04
- **PP-SYNC-001 ALL PHASES DONE** — `ebay_sync` full write-back; `tgw ebay-pull`; `tgw import-sold-csv`; `tgw ebay-sweep` (markdown checklist, 3 groups, clickable eBay links). Shared sync logic in `tgw/ebay/pull.py`.
- **PP-SOLD-001 Tier 1 DONE** — `ebay_legacy_sync` extended: `_sync_sold()` polls GetOrders,
  365-day initial lookback in 90-day windows, state file at `runtime/state/ebay-sold-sync-state.json`
- **PP-SOLD-001 Tier 4 code DONE** — eBay push webhook: `POST /webhooks/ebay/notification` in
  `http_server.py`; 10-min cached listing index; `_mark_item_sold()` shared between polling and webhook;
  `apis/ebay/notifications.py`: `set_notification_preferences()`, `parse_sold_notification()`,
  `verify_notification_signature()`; `tgw setup-ebay-hooks` CLI command; nginx config + cloudflared
  setup script at `/opt/TGW/config/nginx/`. **Infrastructure deployment deferred — see Operator TODO.**
- **PP-LOOKUP-001 ALL TIER 1 DONE (2026-06-05)** — `apis/lookup/` package: upcitemdb (primary),
  go-upc (secondary), open_library (ISBN/books), discogs (music), open_food_facts (food/household),
  igdb (video games, Twitch OAuth), justtcg (trading cards, no auth). `LookupResult` dataclass
  with `prompt_context()`; `lookup_product()` dispatcher with 30-day cache, category-keyword routing,
  barcode-field discovery, and name-based fallback for IGDB/JustTCG. Integrated into `ai_identify`;
  `tgw lookup <SKU>` CLI. Verified: upcitemdb live. IGDB needs `secrets_root/igdb-credentials.json`.
- **SKU migrate** — `ebay_sku_migrate` worker running hourly; ~8,350 eBay live listings remain;
  shipping policy now category-aware (FC4 default, 7 category overrides in config)

### Phase 2a observation gate ✅ CLEARED 2026-06-02
- `ebay_token_refreshed` observed at 12:07 — full expiry+refresh cycle confirmed
- No separate cron existed to retire; worker is sole token manager
### Retired this session
- `queue-launcher.service` disabled; stub in code preserves the console script
- Filesystem `.queue_worker` / `.queue_worker_config` discovery removed from all code
- eBay credentials removed from `tgw-api-config.json`; now in `secrets_root`

## Implementation TODO — next build priorities

Ordered by value and readiness. PP-LOOKUP-001 Tier 1 is the key unlock — it feeds
quality scoring, SEO, and better comp search.

| Priority | Item | Status | Notes |
|----------|------|--------|-------|
| ✅ | **PP-QUALITY-001** listing quality scorer | DONE 2026-06-04 | score_draft; ebay_draft+price integration; tgw staged Q+PC columns |
| ✅ | **PP-PRICE-003** comp search improvement | DONE 2026-06-04 | lookup_query stage 0; condition-filtered comps; price_confidence H/M/L |
| ✅ | **PP-HINT-001** bulk requeue command | DONE 2026-06-04 | `tgw requeue` all filters implemented; `--catalog-only` suppresses eBay cascade |
| ✅ | **PP-SEO-001** title enhancement pass | ALL PHASES DONE 2026-06-04 | Phases 1–6 complete; EPID needs `commerce.catalog.readonly` scope (silent skip until granted) |
| 3 | **PP-SOLD-001 Tier 2** CSV import test | DONE — re-run w/ full history | 2-year CSV: 208 listing-ID + 909 fuzzy matches (run 2). Archive tombstone pass built (pull.py `restore_archive_tombstone`). Archive IDs 223–326xxx predate the 2-year window; **need full all-time eBay sold export** to get hits. |
| 4 | **PP-SOLD-001 Tier 3** sweep checklist | after Tier 2 | `tgw ebay-sweep` output → physical review workflow |
| 5 | **PP-REF-001** item JSON schema doc | DONE 2026-06-04 | `docs/.../reference/TGW-Item-JSON-Schema.md` — all fields, sub-dicts, writers, pipeline flow diagram |
| 6 | **PP-CI-001** linting + GitHub Actions | DONE 2026-06-04 | ruff clean; `--no-fix` CI; `.pre-commit-config.yaml` (files: src/tests only); pre-commit installed |
| 7 | **PP-REPRICER-001** market-aware repricer | blocked | Blocked on `buy.marketplace_insights` scope approval |
| ✅ | **PP-LOOKUP-001 Tier 1 remaining** | DONE 2026-06-05 | IGDB, JustTCG, Open Food Facts — all implemented; credentials needed for IGDB |
| ✅ | **PP-PRICE-004** velocity analytics | DONE 2026-06-05 | `tgw velocity-report`; `velocity_stats` nightly worker; `velocity` param in `suggest_price()`; velocity-stats.json in catalog |
| 10 | **PP-MULTIMODEL-001** multi-AI routing | — | Task routing guide: Haiku/Sonnet/Opus/Gemini Code/Perplexity/Ollama; e-sneaker-net pattern; informs all future work |

**Running in background:**
- `ebay_sku_migrate` — ~8,350 live listings remaining; ~5/hr; ~70 days to complete
- PP-SOLD-001 Tier 4 webhook — code done; awaiting operator infra (nginx/cloudflared)

## Operator TODO — deferred installs and configs

Short-cycle tasks waiting on operator action (not code changes). Check off when done.

### eBay webhook endpoint — PP-SOLD-001 Tier 4 infrastructure (code done 2026-06-04)
- [ ] **Choose path** — run both commands and compare IPs:
  `curl -s https://ifconfig.me` vs `ip route get 1.1.1.1 | awk '{print $7; exit}'`
  — Match → Path A (nginx+certbot). Differ → Path B (Cloudflare Tunnel, works behind NAT).
- [ ] **Path A — nginx + Let's Encrypt** (static public IP):
  ```
  apt install nginx certbot python3-certbot-nginx
  cp /opt/TGW/config/nginx/ebay-webhook.conf /etc/nginx/sites-available/tgw-webhook
  # edit server_name to your actual subdomain (e.g. hooks.yourdomain.com)
  ln -s /etc/nginx/sites-available/tgw-webhook /etc/nginx/sites-enabled/
  nginx -t && systemctl reload nginx
  certbot --nginx -d hooks.yourdomain.com
  ```
- [ ] **Path B — Cloudflare Tunnel** (behind NAT / dynamic IP, recommended if no static IP):
  ```
  sudo bash /opt/TGW/config/nginx/cloudflared-setup.sh
  # edit /etc/cloudflared/config.yml — replace REPLACE_WITH_YOUR_SUBDOMAIN
  # add CNAME in ZoneEdit: hooks.yourdomain.com -> <tunnel-id>.cfargotunnel.com
  systemctl start cloudflared && systemctl enable cloudflared
  ```
- [ ] **Add `dev_id` to `/opt/TGW/secrets/ebay-credentials.json`** — from developer.ebay.com →
  My Account → Application Keys → DevID field. Enables full SOAP signature verification.
  Add: `"dev_id": "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"`
- [ ] **Register URL with eBay** (after endpoint is live and TLS works):
  `tgw setup-ebay-hooks --url https://hooks.yourdomain.com/webhooks/ebay/notification`
- [ ] **Verify**: `tgw setup-ebay-hooks --check`
- [ ] **Restart tgw-http** so the new webhook route is live:
  `systemctl restart tgw-worker@ebay_legacy_sync.service` (and restart tgw-http service)

### Manual Seller Hub fixes — wrong shipping profile on 10 items
10 items migrated with FRE (eBay Standard Envelope) profile instead of FC4.
Categories: 7317 (Game Pieces) + 261068 (Action Figures). Correct profile: FC4 (199931446015).
Seller Hub: Listings → search by Item ID → Edit listing → Shipping → select FC4.
- [ ] 327195083346  - [ ] 327195083374  - [ ] 327195083408  - [ ] 327195083423
- [ ] 327195083451  - [ ] 227372145582  - [ ] 327195085940  - [ ] 227372145665
- [ ] 227372145712

### Tool installs — PP-MULTIMODEL-001 external AI tools
- [ ] **nvm + npm** (Node.js version manager): `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash` then `nvm install --lts`
  — needed for: `markmap-cli` (render reference docs), future JS tooling
- [ ] **markmap-cli**: `npm install -g markmap-cli` — renders reference `.md` files to HTML: `markmap <file> --no-open -o out.html`
- [ ] **Gemini CLI / Google Cloud SDK**: install `google-cloud-sdk` or the standalone `gemini` CLI
  — for PP-MULTIMODEL-001 Gemini Code large-context sessions; use Google account credentials
  — alternative: use Gemini via https://aistudio.google.com (no install required, e-sneaker-net pattern)
- [ ] **Perplexity workflow**: no install needed — use https://perplexity.ai directly (e-sneaker-net);
  save research results as `.md` to `docs/TGW-Plan-Vault/inbox/` for PM-intake to file

### PP-REMOTE-001 — Tailscale
- [ ] `curl -fsSL https://tailscale.com/install.sh | sh` then `tailscale up`
- [ ] Join account network; verify `tgw-http` reachable over Tailscale from remote devices
- [ ] If using Tailscale Funnel for webhook: `tailscale funnel 7373` + CNAME in ZoneEdit
  (alternative to Cloudflare Tunnel; requires Tailscale to be running)

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
### 4c. Category condition policy module ✅ COMPLETE (2026-06-03)
- `apis/ebay/conditions.py` — caches full eBay Metadata API condition policy table (15K categories, 26 unique sets)
- `best_condition(cfg, category_id, item_condition)` — resolves to best allowed conditionId; NEVER upgrades condition (same-or-worse fallback only); returns None when no valid condition exists
- `CONDITION_RANK` dict maps all conditionIds to a buyer-quality rank (0=New … 9=For Parts)
- `CONDITION_ID_TO_ENUM` maps conditionId → Inventory API enum string
- `draft_listing` now stores `condition_id`, `condition_label`, `condition_enum` — used by stage/publish directly
- Cache at `catalog_root/ebay-condition-policies.json`; refreshed every 7 days
- Key insight: conditionId 3000 has 4 different buyer labels across categories ("Used", "Pre-owned", "Pre-owned - Good", "Open Box/Used") — label stored in draft, not just the ID
- Eliminates the entire class of errorId 25021 (invalid condition for category) errors
### 4d. Category template system — deferred; see PP-HINT-001 (eBay enrichment) below
### 4e. Retire eBay token cron ✅ COMPLETE — no separate cron existed; token_refresh worker is sole manager
### 4f. Duplicate item/listing check worker (PP-ADD-006) — pending
### 4g. Inventory API migration sweep (PP-ADD-008) — pending
### 4h. Pricing module — see PP-PRICE-001 below
### 4i. Live listing revision / update draft — pending; see PP-REVISION-001 below

### PP-MULTIMODEL-001 — Multi-AI Strategy and Task Routing

#### Problem
TGW work spans a wide range of AI tasks with different characteristics: routine transforms,
complex implementation, large-data analysis, and research. A single model (Claude Sonnet)
handles all of these today — but we have access to other AI tools better suited for specific
task types. Without a routing strategy, we default to the most expensive/capable tool for
every task, leaving efficiency and cost on the table.

#### Available AI tools and their strengths

**Claude Haiku** (fast, cheap — Claude Code)
- Routine field classification, simple validation logic, boilerplate generation
- Batch tasks where throughput matters more than depth
- Quick summarization or lookup with minimal context

**Claude Sonnet** (balanced — current default for Claude Code sessions)
- Worker implementation, debugging, moderate planning
- Code generation requiring TGW architecture awareness
- Default model; upgrade to Opus only when Sonnet falls short

**Claude Opus** (high-capability, expensive — `/fast` mode in Claude Code)
- Architecture design, PP design, settled architecture decisions
- Complex cross-system reasoning; reserve for when Sonnet produces inadequate results

**Local Ollama** (free, CPU-bound — already in production)
- PM-intake: `Qwen2.5:latest` classifies inbox notes + patches master plan
- `ai_identify`: `qwen2.5vl:7b` identifies items from photos (~18s/call)
- `ebay_draft`: `Qwen2.5` fills eBay item specifics (structured extraction)
- Constraint: 32GB CPU-only; model loads ~10 min; ~18s per inference call after warm

**Gemini Code** (large context window, different training corpus)
- High-token tasks: analyze full 55K item catalog, large eBay sold CSV exports
- Cross-reference large corpora: all worker source files simultaneously, full item JSON corpus
- Cases where Google's training data may have better coverage than Anthropic's
- Token-intensive comparisons that exceed Sonnet's practical context budget

**Perplexity** (live web research with cited sources)
- eBay API updates, new scope announcements, developer forum research
- Pricing strategy research with cited market data
- Competitive analysis: what do other resale automation platforms do?
- Deep-research tasks where footnoted citations are needed for decisions

#### E-sneaker-net pattern
No direct API integration planned between these tools — the workflow is manual:
1. Identify a task suited for a different tool (routing guide below)
2. Export relevant context (question, data excerpt, file content) from TGW session
3. Run in the appropriate external AI tool
4. Import result back: paste answer, or save as `.md` to `docs/TGW-Plan-Vault/inbox/` for PM-intake to file

This is analogous to sneakernet (physically carrying data) but between AI subscriptions.

#### Task routing guide

| Task type | Best tool | Reason |
|-----------|-----------|--------|
| PP design, settled architecture | Opus | High-stakes, complex reasoning |
| Worker implementation | Sonnet | Code quality + architecture awareness |
| Data analysis < ~80K tokens | Sonnet | Already in context |
| Data analysis > ~80K tokens | Gemini Code | Context limit avoidance |
| PM-intake / plan patching | Ollama Qwen2.5 | Free; good enough for classification |
| Item photo identification | Ollama Qwen2.5VL | Vision model; free per call |
| eBay aspects fill | Ollama Qwen2.5 | Structured extraction; free |
| eBay API change research | Perplexity | Live web + citations |
| Pricing / market research | Perplexity | Cited market data |
| Simple field transform / boilerplate | Haiku | Fast + cheap |
| Batch classification jobs | Haiku | Throughput-optimized |
| Large corpus cross-reference | Gemini Code | Context window advantage |

#### Opportunities identified
- **ebay_draft aspects improvement**: run a Gemini Code session with all 55K item JSONs + current
  aspect fill results to find systematic gaps — too much data for a Sonnet session
- **Perplexity research queue**: maintain a list of open research questions (eBay API scope
  status, pricing competitors, Cassini algorithm updates) and batch-run them in Perplexity
  periodically; file results via inbox
- **Haiku for tgw health summary formatting**: simple string transforms, no architecture needed
- **Gemini for data scrub passes**: Pass 1–3 (PP-ADD-005 data cleanup) involves processing
  large item JSON corpora — Gemini's context window makes full-corpus analysis feasible in one shot

#### Implementation
Primarily a working practice guide — no code changes required initially.
- When designing a new PP or worker: explicitly choose the model tier as part of the design note
- Phase 5 usage monitoring will tag each AI job with model name + cost tier when built
- Perplexity research results → save as `.md` in `docs/TGW-Plan-Vault/inbox/` for PM-intake to file

---

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
#### Migration script ✅ READY TO RUN (2026-06-03) — see `src/tgw/sku_migration.py`
- `tgw sku-migrate` CLI with --check-collisions, --class, --dry-run/--run,
  --include-live-ebay, --limit, --manifest
- `sku_history` table created in `state_machine` DB
- 7 Class A collision pairs auto-resolved (hundredths digit fallback)
- 1 Class B collision auto-resolved (alternate suffix window)
- Single live-item test verified end-to-end (Class B epoch-0)
- Rollback manifest written to `/opt/TGW/var/log/sku-migrate-<ts>.json` on every run
- **Bug fixed (2026-06-04)**: `rename_sku` now wraps all OS operations in try/except; per-item errors are counted and logged without killing the batch. Also guards against stale `new_link` symlink from a partial prior run.

#### ✅ MIGRATION COMPLETE for non-eBay items (2026-06-04)
- Steps 1–3, 5, 6 done; ~8,370 eBay live listings remain (step 4 — gradual batch)
- 228 non-eBay items (classes F,D,E,B) migrated; 26,423 Class A non-live migrated
- All catalogs rebuilt (`tgw build-all`) — 55,351 items
- `bundle_intake.SKU_RE` updated to `r'^tgw\d{15}$'` (enforces exact 18-char format)

#### Execution sequence (when ready)
1. ✅ `tgw sku-migrate --check-collisions`  — confirm still clean
2. ✅ `tgw sku-migrate --class F,D,E,B --run`  — 228 items, no eBay listings, done
3. ✅ `tgw sku-migrate --class A --run`  — 26,423 Class A without live listings, done
4. **eBay live listings (~8,370 Class A) — spread delist/relist over time, not bulk**
   - Bulk delist+relist in one shot resets listing age, loses watchers, tanks placement
   - Spreading ~50 relists/day through the day is fine and may actually help the algorithm
     (fresh listings boosted by eBay's new-listing window, spread across the day)
   - Physical inventory sweep (PP-SOLD-001) first — sold items don't need renaming at all,
     reducing the batch by however many have sold
   - Worker approach: `tgw-worker@ebay_sku_migrate.service` — daily batch of N items,
     scheduled across the day (e.g. 5 items/hour), tracks progress in `sku_history` table
   - Rate: configurable; start conservative (10/day), increase if no issues
5. ✅ `tgw build-all`  — all catalogs rebuilt (55,351 items)
6. ✅ Add SKU validation at intake — `bundle_intake.SKU_RE` now enforces exactly 18 chars

#### Remaining work
- Intake enforcement: validate 18-char format at bundle_intake / multi_intake ingestion points
- Post-migration verification report (`tgw sku-migrate --verify` or manual audit)
- Catalog/search SKU matching: tolerate any variant by matching on first 18 characters — covers residual format drift without requiring full normalization first
### Data scrub passes (priority elevated 2026-06-03)
- Pass 1: itemdata_scrub dry-run → review → --write (merge history keys, drop junk)
- Pass 2: photo_history_recovery dry-run → review → --write
- Pass 3: import eBay listings to fill gaps; then freeze the field schema
- Epoch-zero SKU purge (tgw1970*) subsumed by PP-ADD-005 normalization
- Recovery source: historical-tgw-catalog.json
- Field rename: `#VERIFIED` → `verified` (legacy field from eBay CSV export; hash prefix was never intentional — fold into Pass 1 or run as a standalone scrub step)

### ItemArchive — legacy sold/ended listing history
- Path: `/opt/TGW/data/history/ItemArchive/` — zipped legacy item packages from the old system
- Archive eBay index: `/opt/TGW/var/archive-ebay-index.json` — 6,824 entries built by `tgw import-sold-csv`
- Content: older eBay listings predating the current ItemData structure; useful for sold reconciliation
- Integrate: re-run `tgw import-sold-csv` after `ebay_sku_migrate` progresses to build up more archive matches
- Archive index grows as migrated SKUs accumulate in `sku_history` table

### PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)

#### Problem
Sold reconciliation fails when routed through the catalog as intermediary — catalog is
batched and may not have `ebay_listing.listing_id` at sale time. Status fields across the
55K+ item catalog are stale for many legacy items. Physical inventory has gaps.

#### Three reconciliation tiers
1. **eBay API** — `GetMyeBaySelling` (active + sold) and `GetOrders` (date ranges); match
   `listing_id` directly against `ItemData/*/\*.json`. `ebay_legacy_sync` already does the
   active side — extend it to pull sold orders and mark items status=Sold.
2. **Sold report CSV** — match eBay item number against `ebay_listing.listing_id` in item
   JSON directly, never through catalog. CSV download from Seller Hub.
3. **Physical inventory sweep** — generate checklist of ambiguous-status SKUs (no
   `ebay_listing`, or active/sold unresolved) for human review. Item gone → sold/missing;
   item present → available.

#### Local mirror principle (settled 2026-06-03)
Every durable eBay-side ID and URL written back into item JSON immediately after API call
succeeds. Makes sold/active guards reliable without hitting eBay API at pipeline time.

#### Known data quality issues to resolve
- Many items have `Item number` from legacy eBay CSV export — this is the parent bundle's
  listing ID, not the individual item's. `multi_intake` now strips it on encounter.
- Items with `legacy_listing_resolved: True` may still have active listings — Active listing
  guard in `ebay_stage` catches new cases; sync pass needed to make data authoritative.
- Physical inventory gaps from old system — sold items not marked, available items stale.

#### Status (2026-06-04)
- **Tier 1 DONE**: `ebay_legacy_sync` extended with `_sync_sold()` — GetOrders polling,
  365-day initial lookback in 90-day windows, state file at `runtime/state/ebay-sold-sync-state.json`.
  Items matched by `listing_id`, written `status=sold` + `ebay_sale` block, catalog_rebuild enqueued.
- **Tier 2 CSV import done (run 2)** — 2-year CSV (`2024-06-05 → 2026-06-04`): 208 listing-ID
  matches + 909 fuzzy-title matches (total ~3,083 sold items now recorded).
  **Archive tombstone pass added** (`pull.restore_archive_tombstone`): when archive index matches
  a listing ID but ItemData JSON is absent, the item is extracted from the archive ZIP and restored
  as a tombstone (`_archive_tombstone: True`, `ebay_listing.listing_id` set from `Item number`),
  then marked sold normally. Idempotent; dry-run peeks without writing.
  Archive index is 22,124 entries (223–326xxx range, ~2018–2023 era). The 2-year CSV does not
  overlap this range — **0 archive matches until a full all-time eBay sold export is used**.
  To get archive hits: download complete sold history from Seller Hub (all years) and re-run
  `tgw import-sold-csv <full-history.csv> --fuzzy`.
  Flags: `--fuzzy` / `--fuzzy-threshold`; archive pass auto-activates when cache exists.
- Tier 3 (physical sweep checklist) pending.

#### Tier 4 (future) — eBay push notification webhook
eBay Trading API supports `SetNotificationPreferences` + `FixedPriceTransaction` event for real-time
sold notification (reduces latency from daily poll → seconds). Requires:
- A stable **public HTTPS URL** (eBay does TLS validation; bare IP:port not accepted)
- New endpoint in tgw-api: `POST /webhooks/ebay/notification` — parse XML, verify delivery token,
  call the same sold-mark logic as `_sync_sold()`
- Exposure options: nginx reverse proxy + static public IP + DNS, or **Cloudflare Tunnel** (free,
  zero port-opening, works behind NAT — preferred if no static IP)
- Deferred until public endpoint question is resolved (see PP-REMOTE-001)
- Daily GetOrders polling already provides coverage in the meantime

#### Sequencing
- Depends on PP-ADD-005 (SKU normalization) for reliable listing_id → SKU matching
- `ebay_legacy_sync` extension (add sold order pull) is first deliverable ✓ DONE
- Physical sweep tool after sync is authoritative
- `status` field freeze after Pass 3 data scrub

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

## Reference library (docs/TGW-Plan-Vault/reference/)

Markmap documents — open in Obsidian (Markmap plugin) or render with `markmap <file> --no-open -o out.html`.
Rendered HTML snapshots at `/opt/TGW/var/www/`.

### ✅ Complete
- `eBay-API-Landscape.md` — full eBay API surface: REST families, Trading API, scopes, TGW usage map, constraints
- `TGW-HTTP-API.md` — tgw-http FastAPI endpoint reference (derived from http_server.py)
- `TGW-Pipeline-Flow.md` — worker sequence: triggers, reads, writes, next-queue for every worker
- `TGW-Config-Reference.md` — every config key, derived keys, legacy/stale keys, secrets inventory
- `PP-LOOKUP-001-APIs.md` — product enrichment API stack: Tier 1 (free) + Tier 2 (paid/decision)
- `TGW-Ollama-Prompts.md` — actual prompt templates for ai_identify + ebay_draft; tuning notes
- `CATEGORY-QUIRKS.md` — per-category eBay quirks: fulfillment overrides, condition limits, error patterns
- `TGW-Item-JSON-Schema.md` — item JSON field reference: all fields, sub-dicts, types, writer workers, pipeline stage flow diagram
- `ISSUES.md` — active bugs and known gaps (ISS-001 through ISS-008); closed issues log
- `HARDWARE-AI-INFERENCE.md` — Ollama model sizing, GPU upgrade planning (pre-existing)
- `echo.py` / `worker_base.py` — new worker templates (pre-existing)

### PP-REF-001 — TGW Item JSON Schema ✅ DONE 2026-06-04
- Reference doc: `docs/TGW-Plan-Vault/reference/TGW-Item-JSON-Schema.md`
- Covers: all top-level fields, `draft_listing`, `ebay_offer`, `ebay_listing`, `ebay_photos`, `reprice_schedule`, `product_lookup` sub-fields; each with type, pipeline stage set, writer worker
- ASCII pipeline flow diagram showing field accumulation order
- Legacy-only fields section for pre-pipeline imported items
- Notes for PP-GLOBALS-001 design

### PP-REF-002 — eBay Error Code Reference (planned)
- **Problem**: error handling scattered across ebay_stage, ebay_publish, ebay_price, ebay_draft; no consolidated view of what errors we handle vs. what we let dead-letter
- **Approach**
  - Grep all worker + API files for errorId, error_code, HTTPError patterns
  - Cross-reference against eBay developer docs for known error meanings
  - Classify each: handled (with how) / unhandled (dead-letters) / transient (retried)
  - Output: `eBay-Error-Codes.md` markmap grouped by API + severity
- **Value**: surfaces unhandled errors that should be caught; reduces dead-letter surprises; informs PP-HINT-001 fail-forward work
- **Effort**: medium — grep is fast but eBay docs cross-reference takes time

### PP-CI-001 — Linting, CI, and code quality automation ✅ DONE 2026-06-04
- ruff `src/` + `tests/` passes clean
- CI (`.github/workflows/ci.yml`): `ruff check --no-fix` + `pytest -v --tb=short`; `pip install -e ".[dev]"` installs pytest+ruff+pre-commit
- `.pre-commit-config.yaml`: local ruff hook scoped to `^(src|tests)/`, `--no-fix` so it catches unfixed issues
- Pre-commit hook installed in `.git/hooks/pre-commit` (run `pre-commit install` on new clones)
- mypy skipped — not installed; codebase uses `Dict[str, Any]` heavily, low ROI for now
- `tools/`, `docs/`, `systemd/` excluded from hook (have pre-existing issues, not production code)

### PP-SEO-001 — eBay listing SEO and search placement optimisation

#### Problem
AI-generated titles and specifics are functionally correct but not optimised for eBay's
Cassini search algorithm. Buyers search brand + model + variant; AI often produces generic
nouns. Better title and specifics completeness → higher placement → faster sell-through
without touching price.

#### eBay Cassini ranking signals (what we can control)
- **Title** — 80 chars; brand + model number + key attribute; no keyword stuffing or ALL CAPS
- **Item specifics** — REQUIRED filled = baseline; RECOMMENDED filled = ranking boost; more = better
- **Category accuracy** — wrong category makes a listing invisible regardless of other quality
- **EPID association** — associating listing with eBay Catalog EPID auto-fills verified specifics
  and enables structured product display; biggest single SEO leverage for branded items with UPCs
- **Condition granularity** — more specific condition = better buyer trust (where category allows)
- **Photo count** — eBay recommends 12; ≥ 8 is strong; < 3 is a penalty
- **Description richness** — 200+ words; keyword-rich; HTML structure (headers, bullets) helps mobile

#### How the new toolset enables this

**PP-LOOKUP-001 → title and specifics quality**
- `product_lookup.brand` + `product_lookup.mpn` → inject into title if not present
- `product_lookup` fields pre-fill specifics before AI runs: Brand, MPN, Model, EAN
- `product_lookup.category` cross-checks AI-assigned eBay category — disagreement = flag
- Pre-filled specifics give AI richer context → better FREE_TEXT values for remaining fields

**eBay Catalog API EPID lookup (Commerce Catalog, scope: commerce.catalog.readonly)**
- Search eBay product catalog by UPC/EAN → get EPID (eBay Product ID)
- EPID association at staging time → eBay auto-fills standard specifics from its own product record
- No AI needed for specifics on known branded items — eBay provides them authoritatively
- Highest-leverage action for any item with a scannable barcode

**PP-QUALITY-001 quality score → SEO gap visibility**
- Quality score already measures title length, specifics completeness, photo count
- Add SEO-specific sub-scores: brand-in-title, MPN-in-title, keyword diversity, category confidence
- Surface SEO gaps distinctly from general quality issues in `tgw staged`

**PP-PRICE-004 velocity + Analytics API (future) → SEO feedback loop**
- Low impressions despite competitive price = likely SEO problem (title/category/specifics)
- Per-listing traffic data from Analytics API (sell.analytics.readonly) enables: identify
  listings live 14+ days with < 10 views → flag for SEO review before repricing
- Closes the loop: fix SEO first, reprice only if SEO is solid and item still doesn't move

#### Implementation phases

**Phase 1 — Title enhancement pass ✅ DONE 2026-06-04**
- `tgw/seo/title.py` — `enhance_title(title, product_lookup, item_specifics) -> dict`
- Rules applied in order:
  1. If brand known (product_lookup.brand or item_specifics.Brand) and not in title → prepend
  2. If MPN/model known and not in title → append if ≤80 chars
  3. Flag `title_too_short` (< 40), `title_too_long` (> 80), `all_caps:<words>` (alpha-only caps),
     `no_brand`, `no_model`
  4. ALL CAPS check excludes model numbers containing digits/hyphens (avoids false positives)
- `draft_listing.title` = enhanced; `draft_listing.title_ai` = original if changed;
  `draft_listing.title_flags` = flag list

**Phase 2 — Specifics pre-fill from product lookup ✅ DONE 2026-06-04**
- `ebay_draft` builds `prefilled` dict from `product_lookup`: Brand, MPN, Model, EAN, UPC, ISBN
- Only injects for aspects that exist in this category's aspect list
- Validates SELECTION_ONLY aspects against allowed values before injecting
- `_build_prompt()` now shows prefilled values as "Known values" section; AI fills remaining
- `prefilled` overrides AI output in merge step (product database is authoritative)

**Phase 3 — EPID association ✅ DONE 2026-06-04 (code complete; scope pending)**
- `apis/ebay/catalog.py` — `lookup_epid(cfg, barcode)` via Commerce Catalog API; silent 401/403 skip
- `ebay_stage.py` — runs EPID lookup before staging if no `epid` in item JSON; caches result
- `ebay/sync.py` — includes `product.epid` in inventory item PUT body when present
- **Scope needed**: `commerce.catalog.readonly` — not yet granted; lookups silently return None until approved

**Phase 4 — Category confidence check ✅ DONE 2026-06-04**
- `_category_confidence(pl_category, ebay_category)` in `ebay_draft.py` — Jaccard keyword overlap
- `high` ≥ 0.30, `medium` ≥ 0.10, `low` < 0.10 (stopwords excluded)
- Written to `draft_listing.category_confidence`; surfaced in `tgw staged` CC column (! = low)

**Phase 5 — Description enrichment ✅ DONE 2026-06-04**
- `ebay_draft.py` — if `product_lookup.description` ≥ 20 words: second Ollama call generates
  200+ word prose description using product info + condition + title; plain text, no markdown
- Written to `draft_listing` as `description` (enriched) + `description_source: enriched`; original
  AI description preserved for items without product lookup data

**Phase 6 — SEO audit CLI ✅ DONE 2026-06-04**
- `tgw seo-audit` — scans staged/live items; table sorted worst-first by quality score
- Columns: SKU, Q (quality), PC (price confidence), CC (category confidence), St (L/S), Days, Issues, Title
- Analytics API (`sell.analytics.readonly`) needed for impression data — not yet applied

#### New config keys needed
```json
"seo": {
  "title_min_chars": 40,
  "title_max_chars": 80,
  "title_brand_inject": true,
  "title_mpn_inject": true,
  "epid_lookup": true,
  "description_min_words": 200
}
```

#### SKU as search term (SEO note — 2026-06-05)
TGW SKUs (`tgwYYYYMMDDHHMMSSs`) are unique on the internet — Google indexes them.  This is
valuable: include the SKU in the listing description and/or item specifics so buyers who find
the item via Google can verify it is the same one.  Already in PP-SEO-001 Phase 5 (description
enrichment); ensure SKU is baked into the description body, not just eBay metadata.

#### Dependencies
- PP-LOOKUP-001 ✅ Tier 1 done — brand/MPN/EAN data flowing
- PP-QUALITY-001 — quality score framework (Phase 1-2 can run without it; Phase 3+ needs it)
- Commerce Catalog API scope (`commerce.catalog.readonly`) — apply alongside current scopes
- Analytics API scope (`sell.analytics.readonly`) — needed for Phase 6 only

#### Effort
- Phases 1-2: low — rule-based title pass + specifics pre-fill, fits in ebay_draft
- Phase 3 (EPID): medium — new API scope + catalog query + inventory item field
- Phases 4-5: low — category check + description enrichment, fits in ebay_draft
- Phase 6: medium — Analytics API integration, new CLI command

## Open questions
- Per-queue worker counts (start: 1 each; serialize AI work in Phase 5)
- Where does the Ollama lock live — in the job manager worker or a Postgres advisory lock? (Phase 5 decision)
- PP-ADD-001 conflict resolution policy: last-write-wins vs. manual review (decide before Phase 6 dev)
- Thumbnail cache: install Pillow (`pip install Pillow` or `pip install trader-grims-warehouse[thumbnails]`) then run `tgw build-thumbnails`
- Item JSON globals block: should offer-invariant properties (condition class, preferred category, weight, shipping intent) have a dedicated `globals` block, or stay as top-level fields? Analyze before implementing — see PP-GLOBALS-001

### PP-REMOTE-001 — Remote Full Capability (SSH / Tailscale / tmux)
- Install and configure Tailscale on master for secure remote access; add to account network
- tmux: persistent session layout for TGW ops (catalog pane, worker monitor, Claude Code pane)
- Verify `tgw-http` reachable over Tailscale for Flutter app on remote devices
- Verify macro dispatcher (`tgw-macro`) works over SSH — clipboard via OSC52 or tmux buffer fallback
- SSH hardening: key-only auth, `tgw` user access, sudoers scoped to needed ops only
- **Open question**: should Claude Code have its own dedicated system user (e.g. `claude`) with scoped
  permissions, separate from the `tgw` worker user? Relevant to sudoers design and audit trail clarity.
  Decision: make part of the PP-REMOTE-001 hardening pass.

### PP-DEPLOY-001 — MX Linux OS Image Integration

#### Context
The system runs MX Linux. `mx-slapshot` creates bootable, installable OS images; Dave has a
library of images going back many years. Goal: make TGW a first-class citizen of the OS image
so that `image + /opt/TGW` = complete, running system with zero manual setup.

#### Design goals
- TGW fully operational from a fresh image restore — no manual service enables, no path fixes
- `tgw` user moved to UID < 1000 (system user range); ensures UID survives across image restores
  and avoids conflicts with future desktop user accounts
- Long-term: discontinue direct interactive `tgw` user sessions; all operator interaction through
  `tgw-http`, CLI tools (`tgw ...`), and Claude Code running as a scoped user

#### Work items
- [ ] Identify all places UID/GID assumptions exist (file ownership in `/opt/TGW/`, secrets
  permissions, systemd `User=tgw`, crontabs if any)
- [ ] Plan UID migration: choose target UID (e.g. 999), usermod, chown sweep, test all services
- [ ] Document image snapshot procedure: what must be in `/opt/TGW/` vs what's in the image
- [ ] Add TGW service enables to image baseline (systemd preset or post-install hook)
- [ ] Test: fresh image restore + mount `/opt/TGW` → `tgw health` green with no intervention

#### Dependencies
- PP-REMOTE-001 (Tailscale) — remote access must survive UID change
- PP-SHELL-001 — tgw.source cleanup before baking into image

### PP-CAPTURE-001 — Idea and Task Capture Pipeline

#### Problem
Good ideas and small tasks surface mid-session, mid-work, or on a second device. The current
path — drop a `.md` file in `inbox/` or run `tgw suggest "..."` — works but isn't ergonomically
the first thing you reach for. The risk is ideas escaping into conversation chat where they
don't persist to the next session.

#### Proposal
Make `tgw suggest` the canonical back-channel for every idea, small task, and BTW thought —
instead of saying it as a parenthetical in conversation. Advantages:
- Auto-processed by PM-intake at the start of every session
- Creates an audit trail (timestamped, in git via plan updates)
- Survives context resets and context compression
- Works from the macroboard (`x` key → `tgw suggest`)

#### "Quiet queue" trigger
When no workers have active jobs (queue depth = 0 across all queues), surface pending
suggestions or operator TODOs via a `tgw status` or notification. This bridges the gap
between "workers finished" and "operator knows what to do next."

#### Implementation ideas
- `tgw suggest` already works — it's about adoption as a habit
- Consider alias `tgw note "..."` or `tgw btw "..."` for mid-session use (shorter to type)
- Quiet-queue hook: `ebay_price_reducer`/`ebay_sync` could emit a notification when
  queue is empty — or a lightweight cron `tgw quiet-check` that fires daily
- CLAUDE.md session protocol already picks up `tgw suggest` entries via SUGGESTIONS.md scan

#### Status
Design open — no code changes yet. Adoption is the first step; tooling follows.

### PP-SHELL-001 — Shell Environment Cleanup (tgw.source / tgw-dev.source)
- Audit `tgw.source`: replace functions that duplicate `tgw` CLI subcommands with one-line wrappers or remove; keep only short-name convenience aliases worth keeping
- Audit `tgw-dev.source`: migrate anything useful to `tgw.source`; retire the dev file
- Rule of thumb: if it's not interactive/session-specific, it belongs as a `pyproject.toml` console script in the package, not a bash alias
- Outcome: `tgw.source` is a thin convenience layer on the `tgw` CLI; no parallel API surviving alongside it

### PP-IFDIR-001 — Interface File Organization
- Currently: MC configs live at `/opt/TGW/mc/` (outside repo); keyd at `etc/keyd/`; no unified structure
- Goal: move all operator interface configs into repo under `etc/interfaces/mc/`, `etc/interfaces/keyd/`, etc.; update install scripts to deploy from there
- Makes repo the single source of truth for all interface configuration; simplifies new-node bootstrap

### PP-STORE-001 — eBay Store Category Support
- Add `store_category_id` to `draft_listing`; allow items to be filed into eBay store sections
- Store category list queried once via Trading API `GetStore` and cached (store categories rarely change)
- Default store category configurable per eBay category in `tgw-api-config.json`
- Wired into `ebay_stage` and `ebay_publish` offer bodies

### PP-GLOBALS-001 — Item JSON Globals Metadata (design required)
- Problem: some item properties don't change between offers — condition class, preferred category, weight, default shipping intent — but are either scattered top-level or duplicated in every offer block
- Design question: dedicated `globals` block vs. top-level fields vs. derive from existing fields
- Analyze actual offer-invariant properties before implementing; define the schema before writing any code
- Depends on: PP-ADD-005 (SKU normalization) + Pass 3 data scrub (field schema freeze)

### PP-LOOKUP-001 — Product Data Enrichment (multi-source lookup stack)

#### Purpose
Augment AI identification and eBay draft quality by pulling structured product data before the
vision model runs. When a barcode, ISBN, or item type is known, structured data (title, brand,
MPN, description, category, retail price) is far more reliable than AI inference alone. Results
cache in item JSON to avoid repeat API calls.

#### Integration point
`ai_identify` worker: before calling the vision model, run `lookup_product(item)` — checks for
`upc`, `isbn`, or `asin` fields; fetches from the appropriate source; merges result into
`product_lookup` key in item JSON; passes title/brand/description as hint context to the model.
Also exposed as `tgw lookup <SKU>` CLI for manual enrichment.

#### Shared infrastructure
- `apis/lookup/` package — one module per source, common `LookupResult` dataclass
- `lookup_product(item_json)` dispatcher: routes by field presence (`upc` → barcode stack, `isbn` → books, `asin` → Amazon path, item category hints → specialty sources)
- Results stored under `product_lookup: {source, fetched_at, title, brand, description, mpn, ean, msrp, category, raw}` in item JSON
- All API keys in `secrets_root/` (one JSON file per service); missing key = silent skip for that source
- Cache: re-fetch only if `product_lookup` absent or `fetched_at` > 30 days ago (configurable)

---

#### Tier 1 — Free, implement now

**General barcode (UPC / EAN / ISBN-13)**
- **upcitemdb** (primary): 698M+ barcodes. Free tier: 100 requests/day; batch endpoint accepts multiple UPCs per request — effective throughput well above our intake scale. Use batch where available; confirm per-source batch limits at implementation time.
- **Go-UPC** (secondary / coverage gap): 1B+ items, different database coverage. Worth querying both and merging when upcitemdb returns no result.
- Routing: upcitemdb first; if result is empty or low-confidence, try Go-UPC; cache merged result. Apply same batch-first approach to all sources that support it.

**Books (ISBN)**
- **Open Library**: free, no auth, no rate limit advertised. `GET https://openlibrary.org/api/books?bibkeys=ISBN:<isbn>&jscmd=data&format=json`. Returns title, authors, publishers, subjects, cover URL. `apis/lookup/open_library.py`
- Trigger: `isbn` field present in item JSON, or AI-identified category matches Books

**Music / vinyl / CDs**
- **Discogs**: free with registered API key (OAuth or token). Returns release title, artists, tracklist, label, year, genre, marketplace price stats. `secrets_root/discogs-credentials.json`. `apis/lookup/discogs.py`
- Trigger: `discogs_id` or `barcode` on a known music release; or AI category matches Music/Vinyl

**Video games**
- **IGDB** (via Twitch/IGDB API): free for non-commercial or with Twitch developer account. Returns game title, platforms, genres, cover art, release year. `secrets_root/igdb-credentials.json`. `apis/lookup/igdb.py`
- Trigger: AI category matches Video Games / Gaming

**Trading cards**
- **JustTCG**: free tier, no key required. Returns card name, set, rarity, market price. `apis/lookup/justtcg.py`
- TCGPlayer API: closed to new signups — do not use
- Trigger: AI category matches Trading Cards / CCG

**Food / beverage / household consumables**
- **Open Food Facts**: free, no auth, no rate limit. Returns product name, brand, ingredients, allergens, categories, image. `GET https://world.openfoodfacts.org/api/v2/product/<barcode>.json`. `apis/lookup/open_food_facts.py`
- Trigger: category matches Food/Beverage, or barcode lookup returns food category hint

---

#### Tier 2 — Paid / decision required before implementing

**Amazon price history (Keepa)**
- What: Amazon product data + full price history (ASIN, title, brand, specs, all-time price chart, sales rank)
- Cost: €19/month minimum (token-based; tokens replenish at rate tied to plan)
- Decision needed: worth it if you move significant Amazon-originated SKUs (electronics, toys, media); overkill for one-offs
- `apis/lookup/keepa.py` — implement when/if subscribed; `secrets_root/keepa-credentials.json`

**High-volume barcode (Barcode Lookup)**
- What: 30+ fields including pricing, product images, full descriptions — richer than upcitemdb
- Cost: subscription (month-to-month, no long-term commitment); free trial available
- Decision needed: evaluate if upcitemdb + Go-UPC free tiers prove insufficient at scale
- `apis/lookup/barcode_lookup.py` — stub, not implemented until subscribed

**eBay sold price data (Marketplace Insights API)**
- Already tracked under PP-PRICE-001 — `buy.marketplace_insights` scope application pending
- When approved: integrate as a lookup source for `suggest_price()` in `ebay_price` worker
- Not duplicated here; cross-reference PP-PRICE-001

---

#### Status (2026-06-04)

**Tier 1 — DONE (core stack):**
- ✅ `apis/lookup/base.py` — `LookupResult` dataclass; `barcode_from_item()` (scans 9 field names + item_specifics); `prompt_context()` compact string for AI injection
- ✅ `apis/lookup/upcitemdb.py` — primary barcode source (698M+ barcodes); optional key at `secrets_root/upcitemdb-credentials.json`; handles 429
- ✅ `apis/lookup/go_upc.py` — fallback barcode source; silent skip if no key
- ✅ `apis/lookup/open_library.py` — books by ISBN; no auth required
- ✅ `apis/lookup/discogs.py` — music by barcode; requires `secrets_root/discogs-credentials.json`
- ✅ `apis/lookup/dispatcher.py` — routes by barcode type; music keyword detection; 30-day cache; does NOT write back (caller's responsibility)
- ✅ `apis/lookup/__init__.py` — `lookup_product`, `LookupResult` exports
- ✅ `ai_identify` worker integration — runs lookup before Ollama; saves `product_lookup`; `_USER_PROMPT_ENRICHED` template; priority: enriched → hinted → plain
- ✅ `tgw lookup <SKU> [--force] [--save]` CLI command
- ✅ Verified live: upcitemdb hit on `tgw202102110216337` (TV Guide Star Trek, UPC 086441182826)

**Tier 1 — DONE (2026-06-05, all sources implemented):**
- ✅ IGDB — video games (`apis/lookup/igdb.py`); requires `secrets_root/igdb-credentials.json`; title-based search via Twitch OAuth; in-memory token cache
- ✅ JustTCG — trading cards (`apis/lookup/justtcg.py`); no key required; name-based search
- ✅ Open Food Facts — household/food (`apis/lookup/open_food_facts.py`); no key required; barcode lookup
- Dispatcher updated: food-hint → OFF before upcitemdb; game-hint + title → IGDB fallback; TCG-hint + title → JustTCG fallback

**Credentials to add when available:**
- `secrets_root/discogs-credentials.json` — `{"personal_access_token": "..."}`
- `secrets_root/go-upc-credentials.json` — `{"api_key": "Bearer <token>"}`
- `secrets_root/upcitemdb-credentials.json` — `{"api_key": "..."}` (optional; increases rate limit)

#### Avoid / do not implement
- Amazon PAAPI — sunset April 30, 2026
- GoodReads API — discontinued Dec 2020; use Open Library
- TCGPlayer API — closed to new signups
- CamelCamelCamel — no public API
- eBay Finding API — deprecated, blocked at app tier (see PP-PRICE-001 notes)

---

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

### PP-WHISPER-001 — Audio capture and voice-to-suggest interfaces

#### Problem
Capturing ideas, item hints, and descriptions mid-workflow is friction-heavy when hands are
full during physical processing. Text entry via keyboard or Tasker tap is usable but slow.
Voice capture (Whisper) offers zero-friction idea capture during item photography and sorting.

#### Scope
- `whispertosuggest`: short audio clip → Whisper transcription → `tgw suggest "..."` append
  (the audio-native equivalent of typing a suggestion)
- `whispertoidentify`: whisper a hint or item description → writes `ai_hint` + triggers `ai_identify`
- Tasker integration: Tasker button/shortcut on Android → POST to `tgw-http` with voice text
- Deferred capture: record audio during photo session, process later (batch transcription)
  — avoids Ollama load during active photo runs; audio files dropped to a queue dir

#### Integration ideas (from tgw.source review)
- Whisper.cpp already installed or planned (see PP-REMOTE-001 AI runtime manager)
- `tgw suggest` already the canonical capture back-channel — whisper is a voice front-end to it
- Tasker on Android: press record → transcribe → POST `/api/items/<sku>/action` or `tgw-http`
  hint endpoint; SKU from barcode scan or CurrentItem symlink

#### Dependencies
- PP-REMOTE-001 (Tailscale + `tgw-http` reachable from Android)
- PP-IFDIR-001 (interface configs organized)
- Whisper.cpp binary installed (PP-ADD-010 AI runtime manager)

---

### PP-TODO-001 — Multi-agent TODO tracker (`tgw todo`)

#### Problem
Tasks and reminders are captured in `tgw suggest` / SUGGESTIONS.md but there is no structured
command to list open TODOs by agent or priority — items mix with ideas and require full plan
review to surface actionable tasks.

#### Concept
`tgw todo [agent]` — lists open tasks, similar to `tgw picklist` but for action items:
- `tgw todo` — all open items across all agents
- `tgw todo admin` — operator physical tasks (shipping, labeling, inventory)
- `tgw todo claude` — Claude Code implementation queue
- `tgw todo gemini` — Gemini Code / large-context analysis tasks
- `tgw todo db` — database / data scrub tasks

Versatile enough to add human and AI agents over time.  Each entry has: agent, priority,
description, added_at, source (suggestion / inbox / session note).

#### Storage design
- Back-end: PostgreSQL table `todo_items (id, agent, priority, body, source, added_at, done_at)` in `state_machine` DB
- Or: flat TOML/Markdown file under `docs/TGW-Plan-Vault/` with front-matter per entry
- `tgw todo add [agent] "text"` — create entry; `tgw todo done <id>` — mark complete
- Could feed the "quiet queue" hook in PP-CAPTURE-001 — surface `tgw todo claude` when workers go idle

#### Dependencies
- PP-CAPTURE-001 (idea pipeline design) — aligns on storage back-end choice

---

## Pending projects (revisit)

### PP-HINT-001 — AI hint + eBay enrichment (revisit required)
- First iteration shipped 2026-06-03: `ai_hint` field, `tgw hint` command, hinted vision prompt
- **Known gaps to address:**
  - `tgw requeue` bulk command: filter-based batch re-queue (e.g. "all items with photos but no title") for catalog maintenance — without triggering eBay listing pipeline
  - eBay Browse API enrichment in `ebay_draft`: search similar active listings by title, extract common aspects and category signal to supplement AI-generated specifics
  - Full item history / hint trail: per-SKU log of identification rounds, hints used, AI vs human changes — feeds audit and tuning visibility
  - eBay Marketplace Insights scope (`buy.marketplace_insights`): contact eBay Developer Support directly (limited-release, no self-service); Finding API discontinued 2025 — not an option
  - Revision of already-identified items: `tgw hint --force` works but downstream ebay_draft/ebay_draft re-runs need to be aware of published state (don't auto-push changes to live listings)
  - Tuning: run difficult items through, observe results, adjust prompt and hint format
  - **Shipping profile at intake**: operator sets shipping profile during physical processing based on item size; simple `tgw` command or camera app field sets `shipping_profile` on the item JSON at intake time, overriding the per-category default (FC4). Low-touch: one field, one tool adjustment. See PP-DEPLOY-001 for camera app context.

### PP-QUALITY-001 — Listing quality scoring ✅ COMPLETE (2026-06-04)

#### Status
- `tgw/listing_quality.py` — `score_draft(item, photo_count) -> QualityResult`, 7 signals, 100-point scale
- `ebay_draft` worker: counts raw image files; stores `aspects_required_total/filled` + `aspects_recommended_total/filled` in draft; calls scorer → `draft_listing.quality`
- `ebay_price` worker: re-scores quality after writing `price_comps` (comp_pts were 0 at draft time)
- `tgw staged` table: Q column (worst-first), PC column (price confidence H/M/L), flag display
- `tgw quality <SKU...> [--save]` — manual inspection / rescore

#### Score signals (100 pts total)
| Signal | Pts | Notes |
|--------|-----|-------|
| Title 40–80 chars | 10 | <25=0, 25–39=5, 40–80=10, >80=5 |
| Brand in title | 25 | Checks product_lookup.brand + item_specifics.Brand |
| Model/MPN in title | 10 | Checks MPN/Model from specifics or product_lookup |
| Required specifics fill % | 15 | Stored counts from ebay_draft |
| Recommended specifics fill % | 5 | Stored counts from ebay_draft |
| Photo count ≥ 3 | 20 | 1=6, 2=12, ≥3=20 |
| Description word count | 5 | ≥150=5, ≥75=3, ≥25=1 |
| Price comp count | 10 | ≥5=10, ≥3=6, ≥1=3 |

#### Dependency chain
PP-LOOKUP-001 → PP-QUALITY-001 → better `tgw staged` triage → fewer weak listings published

### PP-PRICE-001 — Pricing module ✅ COMPLETE (2026-06-03)
- `tgw/ebay/pricing.py` + `workers/ebay_price.py`; `ebay_price` enqueued automatically by `ebay_draft`
- Three-stage Browse API fallback: full title → category+short title → category only → category_price_defaults
- `price_comps` block: `{count, min, p25, median, p75, max}`; `suggest_price()` accepts `category_id` for defaults fallback
- Sets `draft_listing.price` = **launch price (110% of max → .99)** — the initial listed price creating a visible eBay discount when lowered
- Stores `ebay_offer.target_price` = p25 (eventual move price for repricer)
- Idempotent: skips items already priced
- `category_price_defaults` in config — fallback when Browse API comps are thin (edit `tgw-api-config.json`)
- **Sold-price access**: Browse API gives asking prices only. Apply for `buy.marketplace_insights` scope; Finding API blocked at app tier. See API investigation notes below.

#### eBay Pricing API Access — Investigation Required
Current data source is Browse API active listing prices (asking prices, not sold prices).
Sold prices are significantly more accurate for pricing decisions.  The following APIs
provide sold/trend data and should be investigated for access expansion:

**1. eBay Finding API — `findCompletedItems`** ❌ DEAD
- **Discontinued early 2025** — blocked at app tier (error 10001); eBay shut down broad access
- Do not pursue; no migration path available for new apps
- Replaced by Marketplace Insights API (see below)

**2. eBay Marketplace Insights API — `item_sales/search`** ⚠ APPLY NOW
- Returns actual sold item data with sale price, date, quantity
- REST endpoint: `GET /buy/marketplace_insights/v1/item_sales/search`
- **Scope required:** `buy.marketplace_insights` — **limited-release, select partners only as of 2026-06**
- No self-service approval; requires direct contact with eBay Developer Support via the developer portal
- Application requires a compelling business justification — frame as resale automation platform
- **Action:** Contact eBay Developer Support directly; do not wait for self-service portal
- In the meantime: Browse API p25 is the floor; our own sold history (PP-PRICE-004) supplements

**3. eBay Terapeak (via Seller Hub) — UI only, no API**
- eBay's own sold-price research tool: Seller Hub → Research → Terapeak
- 3 years of sold data including Best Offer accepted prices — the most complete sold dataset eBay offers
- **Confirmed no API** — eBay acquired Terapeak and removed its original API; data is UI-only
- Scraping would be a ToS violation; do not pursue
- Useful for manual price validation on high-value or unusual items
- Third-party services with legal eBay sold data access: **130Point.com**, **ZIK Analytics** (approved API partners) — evaluate if Marketplace Insights approval takes too long

**4. eBay Browse API — current implementation**
- `GET /buy/browse/v1/item_summary/search` — active listings only
- Works with existing token; no additional scope needed
- Limitation: active asking prices, not sold prices; p25 is conservative but not market-clearing
- Docs: https://developer.ebay.com/api-docs/buy/browse/resources/item_summary/methods/search

**Interim strategy:** Browse API p25 (implemented) + our own sold history aggregation (PP-PRICE-004)
is a reasonable substitute until Marketplace Insights access is obtained. Operator should use
Terapeak in Seller Hub manually for high-value or thin-comp items.

### PP-REPRICE-001 — Automatic markdown price reducer ✅ INITIAL COMPLETE (2026-06-03)
- Worker renamed `ebay_price_reducer` — distinct from the future market-aware repricer
- Three-stage markdown: **launch** (110% of max → .99, day 0) → **retail** (p75, day 3) → **move** (p25, day 17)
- All periods and percentiles configurable in `tgw-api-config.json` `reprice_stages` array
- `to_99(price)` — rounds up to next .99 (e.g. $15.23→$15.99, $16.00→$16.99)
- `reprice_schedule` written to item JSON at publish time: `[{stage, label, price, due_at, done_at}, ...]`
- `workers/ebay_price_reducer.py` — self-scheduling every 6h; applies due stages via Inventory API offer PUT
- `tgw staged` — table of UNPUBLISHED offers awaiting review; `tgw publish <sku...>` — approval gate
- `reprice_skip: true` on item JSON excludes from reducer
- **Open:** category-level price defaults UI/command; reprice-check command for thin-comp items
- **Item.Country fix**: `shipToLocations.regionIncluded` must be in offer body — added to `_build_offer_bodies` permanently

### PP-REPRICER-001 — Market-aware dynamic repricer (design pending)
- Distinct from `ebay_price_reducer` (scheduled markdown): this watches market prices and adjusts dynamically
- Inputs: sold-price data (needs `buy.marketplace_insights` or Finding API), sell-through rate, days listed, competition count
- Design deferred until sold-price API access obtained — Browse API asking prices are the wrong signal for dynamic repricing
- Will consume `reprice_schedule` as floor (never price below the move price)

### PP-PRICE-003 — Comp search quality improvement ✅ COMPLETE (2026-06-04)

#### Status
All three fixes implemented in `tgw/ebay/pricing.py`:

**Stage 0 — product_lookup query** (`_lookup_query`):
- If `product_lookup.brand + mpn` → `"{brand} {mpn}"` (tightest)
- If `product_lookup.brand + title` → `"{brand} {short_product_title}"` (strips brand dedup)
- Falls through to existing Stage 1–3 (full title, category+short, category only)
- Source string: `browse:lookup_query` when fired; `+cond` suffix when condition-filtered

**Condition-filtered comps** (`_prices_condition_filtered`):
- Parses Browse API `condition.conditionDisplayName`; maps to internal rank (0=New … 5=ForParts)
- Keeps comps with `browse_rank >= item_rank` (same-or-worse condition)
- Falls back to unfiltered if filter leaves < MIN_COMPS results
- 15-entry `_BROWSE_CONDITION_RANK` covers all Browse API condition variants

**Price confidence** (`_price_confidence`):
- `high`: ≥5 comps AND max/min ratio < 3 (tight cluster)
- `medium`: ≥3 comps OR wide range
- `low`: <3 comps, category default, or insufficient data
- Stored in `draft_listing.price_confidence`; displayed as H/M/L in `tgw staged` PC column
- `ebay_price` passes `item_condition` and `product_lookup` to `suggest_price()`

### PP-PRICE-004 — Sold velocity analytics and feedback loop ✅ COMPLETE (2026-06-05)

#### Implemented
- `tgw/velocity.py` — core aggregation module; scans ItemData; groups sold items by eBay
  category; computes sold_count, active_count, stale_count, median_days_to_sale,
  sell_at_launch/retail/move/unknown_pct, never_sold_pct, median_sale_price, p25_sale_price
- `tgw velocity-report [--refresh] [--category ID] [--min-sold N] [--json] [--output FILE]`
  — CLI: loads `velocity-stats.json` (or recomputes); renders table or JSON
- `catalog_root/velocity-stats.json` — aggregated stats; 1540 categories, 55k items on first run
- `workers/velocity_stats.py` — self-scheduling nightly worker (24h interval)
- `suggest_price(..., velocity=...)` — `velocity` param added; if category sell_at_launch_pct
  > 50%, returns `velocity_hint: 'hold_launch'` for future repricer use

#### Stage determination
Requires `reprice_schedule` with `done_at` timestamps (new-pipeline items only). Legacy sold
items (2174 total) record stage as 'unknown' and still contribute to price stats. Stage-based
percentages will populate as new-pipeline items accumulate sold history.

#### To enable velocity_stats worker
`systemctl enable --now tgw-worker@velocity_stats.service`
(already registered in pyproject.toml + uses `tgw-worker@.service` template)

#### Next step (PP-REPRICER-001)
Wire `velocity_hint: 'hold_launch'` into reprice schedule construction to extend stage 0
hold period for fast-moving categories. Blocked on `buy.marketplace_insights` scope.

### PP-LISTING-001 — Description footer and picklist line (pending)
- Add configurable boilerplate footer to all eBay listing descriptions
- Current: plain AI-generated description with no seller branding or item identifier
- Footer components:
  1. Seller boilerplate (shipping speed, location, return policy) — configurable text in config
  2. Picklist line: SKU + location in human-readable format for warehouse picking
  3. Future: QR code image (generate locally, upload to eBay EPS, embed in HTML description)
- Applied in `ebay_draft` when building `draft_listing.description`
- Config key: `description_footer` (boilerplate text) + `picklist_line_format` (template string)

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
- Auto-sync: when offer fields are edited locally (price, condition, aspects), changes should push to eBay without requiring manual Seller Hub edits — design must prevent overwriting live state not yet pulled (depends on PP-SYNC-001 sync pass being authoritative first)

### PP-SYNC-001 — eBay data sync, sold reconciliation + local mirror

#### Core principle
Every durable eBay-side ID and URL must be written back into item JSON immediately
after the API call succeeds. Guards for sold/active state must be reliable at pipeline
time without hitting eBay — which requires the local copy to be authoritative.

#### Three reconciliation tiers
1. **eBay API pull** — `GetMyeBaySelling` (active + sold); `GetOrders` with date ranges
   for history beyond 90 days. Match `listing_id` directly against `ItemData/*/\*.json`
   `ebay_listing.listing_id` — never route through the catalog.
2. **Sold report CSV import** — match eBay item number directly against item JSON
   `ebay_listing.listing_id`. Set `status: Sold`, record sale price and date.
3. **Physical inventory sweep** — generate checklist of ambiguous-status SKUs (no
   `ebay_listing`, or unresolved active/sold) for human review. Item gone from shelf →
   sold/missing. Item present → available.

#### What "download current eBay data" means
- Pull all active offers/listings → write back into `ebay_listing` / `ebay_offer` per item JSON
- Pull sold order history → match by SKU/listing_id → set `status: Sold`, record sale price + date
- `ebay_legacy_sync` already writes `ebay_listing` from Trading API — extend this, don't rebuild
- `ebay_sync` exists but writes too little back to item JSONs — extend its write-back
- Record EPS photo URLs durably: `draft_listing.imageUrls` can be overwritten on draft rebuild; EPS links are permanent after upload and should survive in a stable `ebay_photos_eps` field (or ensure `ebay_photos` list is never wiped on re-draft)

#### Known data quality issues (from audit)
- Many items have "Item number" from legacy eBay CSV export fields that are the **parent
  bundle's** item number, not the individual item's — strip on encounter
- Items with `legacy_listing_resolved: True` may still have active listings — the active
  listing guard in `ebay_stage` now catches this, but the underlying data needs the sync
  pass to be authoritative
- Physical inventory has gaps from the old system: sold items not marked, available items
  with stale status

#### Implementation plan — ✅ ALL PHASES COMPLETE (2026-06-04)
- **Phase 1 ✅** — `ebay_sync` extended: now writes `offer_id`, `listing_status`, `price`,
  `category_id`, `quantity` back to `ebay_listing` + `ebay_offer` on every 6h cycle.
  Removed `if not ebay_listing: return 0` guard — builds record from scratch for any SKU match.
- **Phase 2 ✅** — `tgw ebay-pull` CLI: on-demand Trading API pull (active listings + sold orders).
  Shared logic extracted to `tgw/ebay/pull.py`; `ebay_legacy_sync` worker refactored to use same module.
  Flags: `--no-active`, `--no-sold`, `--dry-run`. Enqueues `catalog_rebuild` if changed.
- **Phase 3 ✅** — `tgw import-sold-csv <file>`: imports eBay Seller Hub sold-orders CSV.
  Matches "Item number" → `ebay_listing.listing_id`; flexible column-name fallbacks;
  `--show-columns` for format inspection; `--dry-run`. Idempotent (skips already-sold items).
- **Phase 4 ✅** — `tgw ebay-sweep`: ambiguous-status checklist for physical review.
  Three groups: A (active eBay / unclear local), B (out-of-stock legacy / no listing),
  C (no status / no listing). Filters: `--location`, `--limit`, `--groups`. Outputs markdown
  table with clickable eBay listing links; `--output <file>` for Obsidian review.
- Dependency: PP-ADD-005 SKU normalization (non-eBay items done; live listings migrating ~5/hr)

### PP-PRICE-002 — Repricer strategy (confirmed)
- **Initial list price:** 110% of p100 (max active listing price) — sets above market to
  capture opportunistic higher-price sales
- **Reprice schedule:** After a configurable hold period (e.g. 3–5 days), begin stepping
  down: p100 → p75 → p50 → p25, one step per period
- **Floor:** p25 or a configured minimum price — do not go below
- Repricer worker (`ebay_reprice`) reads `ebay_offer.price_comps`, computes next price,
  calls eBay Offer PATCH, writes new price back to item JSON
- Builds on PP-PRICE-001 `ebay_offer` block and `price_comps` already established
- `ebay_reprice` worker stub registered in `pyproject.toml` — needs implementation

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

---

## PP-MACRO-001 — keyd Macroboard

### Vision
A dedicated keyboard (one of the four identical Dell USB keyboards) acts as a
single-touch macro board for TGW and eBay operations. Highlight a SKU, location,
or any identifier anywhere on screen, then press the matching macro key — no
Ctrl+C, no command typing. If nothing is highlighted, macros fall back to the
current item (`/opt/TGW/CurrentItem` symlink).

The TGW layer on the macroboard is the **canonical definition** for the eventual
all-keyboard sub-layer: once wired and proven here, the same `[tgw_layer]` block
gets added to `default.conf` and bound to a chord on all four keyboards.

### Files (all committed, ready to install)
| File | Purpose |
|------|---------|
| `etc/keyd/tgw-macroboard.conf` | keyd config — device target + layer definition |
| `/opt/TGW/bin/tgw-macro` | Macro dispatcher — all action logic |
| `/opt/TGW/bin/tm` | Thin launcher — `runuser -u tgw` + env setup |

### ⚠ Install blocked — waiting for second keyboard
Cannot install until a second keyboard is connected so the macroboard keyboard
can be safely dedicated without losing console access. When ready:

```bash
# 1. Connect the second (normal use) keyboard first.

# NOTE: On Debian the keyd binary is named keyd.rvaiya (naming conflict with
# an unrelated Debian package). Package and service are still "keyd".
#   Binary:   /usr/bin/keyd.rvaiya
#   Service:  keyd.service  (systemctl start/stop/reload keyd)
#   Config:   /etc/keyd/

# 2. Identify the macroboard's unique device ID:
keyd.rvaiya list-devices
# Look for "Dell Dell USB Keyboard" entries. Both show as 413c:2105.
# The one on the dedicated USB port will have a distinct path/serial hash.
# Example output line: "413c:2105:a1b2c3d4e5f6  Dell Dell USB Keyboard"

# 3. Edit the config to target the correct device:
sudo nano /opt/TGW/src/trader-grims-warehouse/etc/keyd/tgw-macroboard.conf
# Replace "413c:2105" in [ids] with the full unique ID from step 2.

# 4. Install and reload:
sudo cp /opt/TGW/src/trader-grims-warehouse/etc/keyd/tgw-macroboard.conf /etc/keyd/
sudo systemctl reload keyd

# 5. Test: press Caps Lock on the macroboard → LED behaviour changes.
#    Highlight a SKU in any window → press g → notification should appear.
```

### Key map — TGW layer (Caps Lock to enter, ESC or Caps Lock to exit)

```
ITEM INFO & FIELDS          OPEN / VIEW
  g  Get summary → notify     o  Open folder (Dolphin)
  t  Title update (prompt)    i  Images (gwenview)
  l  Location update (prompt) j  JSON edit (konsole)
  v  Verified (mark In Stock)
  h  Hint → requeue identify  EBAY BROWSER
  u  set cUrrent item          e  eBay search by SKU
                               b  Browse listing (ebay.com/itm)
PIPELINE (in order)          S-e  Edit/revise listing
  1  ai_identify               f  Find sold comparables
  2  ebay_draft              S-s  Seller Hub overview
  3  ebay_price
  4  ebay_stage              ADMIN / SYSTEM
  5  publish                  k  health checK → notify
  p  Publish (same as 5)      q  Queue depths → notify
                              c  Catalog rebuild
PICKLIST                      d  stageD items list
  a  Add picklist line        w  Weight (USB scale → clipboard)
S-a  Add question line        y  whisper dictation (15s)
                              z  short dictation (7s)
LOCATION BULK                 x  suggest (plan inbox)
  m  Move all in location      r  Requeue --no-draft count
S-l  Open location folder
```
`S-` = Shift held. Navigation keys (Enter, arrows, F-keys, Backspace) pass through normally.

### Clipboard / fallback behaviour
- **Highlighted text** → used directly as the item argument (Wayland primary
  selection via `wl-paste --primary` — no Ctrl+C needed)
- **X11 fallback** → `xsel -o --primary`
- **Nothing selected** → `basename $(readlink /opt/TGW/CurrentItem)`
- Actions that need a value (title, location, hint) open a `kdialog` prompt
- Actions that produce output use `notify-send` (5s timeout)
- Actions that open things (Dolphin, browser, konsole) just open them

### Future: all-keyboard sub-layer
Once the macroboard layer is proven:
1. Copy the `[tgw_layer]` block from `tgw-macroboard.conf` into `default.conf`
2. Bind a chord on `[main]` to `swap(tgw_layer)` — e.g. `rightalt+space`
3. All four keyboards get single-chord TGW access; macroboard stays always-on

---

- ## Phase 7 — Vault Synchronization
- ### Syncthing Configuration
- #### Why This Matters
- #### Decision: Syncthing for Vault Sync
- #### tgw-Specific Conflict Resolution Protocol
- #### Optional: Git Backing for Version History
- #### Constraints Carried Forward (New)
