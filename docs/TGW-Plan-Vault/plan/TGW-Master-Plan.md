---
title: TGW Master Plan
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-05 (session 5)
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
  setup script at `/opt/TGW/config/nginx/`. **Infrastructure deployment deferred — see Track 4 § Priority 5.**
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

## Implementation TODO — next priorities

### Recently completed (sessions 4–5)
- ✅ **PP-QUALITY-001** listing quality scorer (2026-06-04)
- ✅ **PP-PRICE-003** comp search improvement (2026-06-04)
- ✅ **PP-HINT-001** bulk requeue command (2026-06-04)
- ✅ **PP-SEO-001** title enhancement, all phases (2026-06-04)
- ✅ **PP-REF-001** item JSON schema doc (2026-06-04)
- ✅ **PP-CI-001** linting + GitHub Actions (2026-06-04)
- ✅ **PP-LOOKUP-001** all Tier 1 sources (2026-06-05)
- ✅ **PP-PRICE-004** velocity analytics (2026-06-05)
- ✅ **PP-LISTING-001** description footer + picklist line (2026-06-04) — confirmed in `ebay_draft.py`; plan not updated until now
- ✅ **PP-SOLD-001 Tier 2** CSV import (run 2) — 909 fuzzy + archive tombstone pass; need full all-time CSV for archive hits

### Active / next build priorities

| Priority | PP | Status | Notes |
|----------|----|--------|-------|
| 1 | **PP-STORE-001** eBay store categories | ready | `GetStore` cache + `store_category_id` in draft/stage/publish |
| 2 | **PP-REF-002** eBay error code reference | planned | Grep workers → markmap doc; surfaces unhandled dead-letters |
| 3 | **PP-CAPTURE-001** `tgw note` alias | ready | Trivial; `tgw note "..."` = `tgw suggest "..."`; quiet-queue stub |
| 4 | **PP-SHELL-001** tgw.source cleanup | ready | Audit → pare deprecated → migrate to pyproject.toml console scripts |
| 5 | **PP-IFDIR-001** interface file org | ready | Move mc/keyd configs into `etc/interfaces/`; update installers |
| 6 | **Data scrub Pass 1** field rename | ready | `#VERIFIED` → `verified`; history key merge; dry-run first |
| 7 | SKU search first-18 | ready | Match catalog search on first 18 chars; cover residual format drift |
| 8 | **PP-TODO-001** multi-agent TODO | design ready | PostgreSQL `todo_items` table + `tgw todo [agent]` CLI |
| 9 | **PP-MC-001 Phase 2** tgwitem edit | design ready | copyin, ebay/ + pipeline/ subdirs |
| 10 | **PP-GLOBALS-001** analysis | analysis only | Identify offer-invariant fields; design before coding |
| 11 | **PP-HINT-001** remaining gaps | ongoing | eBay Browse enrichment in ebay_draft; per-SKU hint trail |
| 12 | **PP-SOLD-001 Tier 3** sweep | operator gated | Run `tgw ebay-sweep` after full-history CSV import |
| — | **PP-REPRICER-001** | blocked | Blocked on `buy.marketplace_insights` scope approval |

### Running in background
- `ebay_sku_migrate` — ~8,350 live listings remaining; ~5/hr; ~70 days to complete
- PP-SOLD-001 Tier 4 webhook — code done; awaiting operator infra (nginx/cloudflared)
- `velocity_stats` worker — ✅ **ENABLED 2026-06-05**; running nightly

### Archive tombstone — ceiling confirmed
eBay Seller Hub sold history export maxes at **2 years** (confirmed 2026-06-05). Archive IDs
(223–326xxx, ~2018–2023) are permanently outside this window. The archive tombstone pass is
built and correct but will not fire from CSV import alone. Options for future archive hits:
- Terapeak in Seller Hub (UI only, no API) — manual spot-checks on high-value archive items
- Wait: if `ebay_sku_migrate` eventually maps an archived eBay ID to a current SKU, that path
  may surface additional matches
- Accept the gap: ~22K archive entries; the business impact of unmarked-sold legacy items is low

## Work Tracks — active delegation (session 5)

**Strategy test** (2026-06-05): The 4-track structure is an experiment in AI delegation —
routing tasks to the right model/tool at design time rather than defaulting everything to
Sonnet. PP-TODO-001 (multi-agent TODO tracker) is partly motivated by making this delegation
trackable: each track's queue becomes an agent-tagged TODO list that `tgw todo [agent]` can
surface. This session is the first real run of the pattern; assess after a few sessions whether
the routing overhead pays off in throughput.

PP-MULTIMODEL-001 is now the working model. Each new task is routed to the right tool at design time.

### Track 1 — Claude Sonnet (minimal intervention)
One bounded session per item. Ordered by value.

| # | PP | Task | Size |
|---|----|------|------|
| 1 | PP-STORE-001 | eBay store category support — `GetStore` cache, `store_category_id` in draft/stage/publish | S |
| 2 | PP-REF-002 | eBay error code reference — grep all workers, cross-reference eBay docs, markmap | S |
| 3 | PP-CAPTURE-001 | `tgw note`/`tgw btw` alias + quiet-queue stub | XS |
| 4 | PP-SHELL-001 | `tgw.source` / `tgw-dev.source` audit + cleanup | M |
| 5 | PP-IFDIR-001 | Reorganize interface configs into `etc/interfaces/` | S |
| 6 | Data scrub P1 | `#VERIFIED`→`verified` rename + history key merge (dry-run first) | M |
| 7 | SKU search | Catalog/search match on first 18 chars | XS |
| 8 | PP-TODO-001 | PostgreSQL `todo_items` + `tgw todo [agent]` CLI | M |
| 9 | PP-MC-001 P2 | `tgwitem` copyin + `ebay/` + `pipeline/` subdirs | M |
| 10 | PP-GLOBALS-001 | Analysis only — identify offer-invariant fields; design doc | S |
| 11 | PP-HINT-001 | eBay Browse enrichment in `ebay_draft`; per-SKU hint trail | M |

### Track 2 — Claude Haiku (fast, cheap, no arch context needed)
Use `/model haiku` or spawn as a Haiku session. Hand it a data excerpt + schema + clear task.

| Task | Give Haiku | Expect |
|------|-----------|--------|
| Data scrub P1 script | Sample item JSON + field list | Batch Python script for `#VERIFIED` rename + history key merge |
| Category price defaults | `velocity-stats.json` excerpt (top 20 categories) | Config JSON for `category_price_defaults` |
| Error code table (PP-REF-002) | Grep output of all `errorId` patterns from workers | Formatted markdown table: code, API, meaning, TGW handling |
| PP-TODO-001 scaffold | DB schema + CLI spec | `CREATE TABLE` SQL + argparse CLI stub |
| Health summary formatter | `tgw health` JSON output structure | Pretty-print formatter (no TGW arch needed) |

### Track 3 — Perplexity (live web research, cited sources)
Research briefs in `docs/TGW-Plan-Vault/perplexity/`. Paste brief into Perplexity → save result as `.md` to `inbox/` for PM-intake.

| Brief | File | Priority | What it unblocks |
|-------|------|----------|-----------------|
| eBay API scope expansion | `PERPLEXITY-001-ebay-scopes.md` | HIGH | PP-REPRICER-001, PP-SEO-001 Phase 3+6 |
| eBay Cassini 2025–2026 | `PERPLEXITY-002-cassini-seo.md` | HIGH | PP-SEO-001 tuning, listing quality strategy |
| Sold price data alternatives | `PERPLEXITY-003-sold-price-data.md` | HIGH | PP-REPRICER-001 unblock if MI scope stays closed |
| Third-party integration status | `PERPLEXITY-004-integrations.md` | MEDIUM | IGDB, Whisper.cpp, Discogs, Go-UPC |

### Track 4 — Operator (Dave must act to unblock)

#### ✅ Done
- [x] `velocity_stats` worker enabled (2026-06-05)
- [x] 2-year eBay sold CSV confirmed as maximum available — archive tombstone ceiling accepted

---

#### Priority 1 — eBay Developer Account (new keyset + scope requests)

**Strategy:** Request a fresh keyset (new App ID / Cert ID / Dev ID) with all desired scopes
applied at once. Avoids piecemeal scope expansion later. See complete desired scope list below.

- [ ] Go to https://developer.ebay.com → My Account → Application Keys → **Create new keyset**
  - App name suggestion: `TGW-Automation-v2` or similar
  - Note new App ID, Cert ID, Dev ID — replace in `secrets_root/ebay-credentials.json`
- [ ] On the new keyset, request **all scopes in the desired list** (see below) via the "Get a Token" / OAuth consent flow and the scope editor
- [ ] For `buy.marketplace_insights` — **this requires a separate contact** (limited release):
  - Go to https://developer.ebay.com/support → contact Developer Support
  - Frame: "We are a private resale automation platform (eBay seller: DaveBuko-Webkulap) automating inventory pricing and listing management. We need `buy.marketplace_insights` to power our automated pricing engine using actual sold-item data rather than active-listing prices."
  - Reference: Marketplace Insights API docs at developer.ebay.com/api-docs/buy/marketplace-insights
- [ ] Update `secrets_root/ebay-credentials.json` with new keyset values after approval:
  ```json
  {
    "app_id": "...",
    "cert_id": "...",
    "dev_id": "...",
    "ru_name": "..."
  }
  ```
- [ ] Re-run OAuth flow to get a new user token against the new keyset:
  `sudo -u tgw tgw health` — confirm token active
- [ ] Restart all eBay workers after new token is live:
  ```
  sudo systemctl restart tgw-worker@ebay_legacy_sync.service
  sudo systemctl restart tgw-worker@ebay_sync.service
  sudo systemctl restart tgw-worker@ebay_price_reducer.service
  sudo systemctl restart tgw-worker@ebay_sku_migrate.service
  ```

##### Complete desired scope list for new keyset

| Scope | Have | Priority | What it enables |
|-------|------|----------|----------------|
| `sell.inventory` | ✅ | core | Create/update/delete inventory items and offers |
| `sell.account` | ✅ | core | Fulfillment policies, merchant location, payment policies |
| `sell.marketing` | ✅ | core | Promotions, campaigns |
| `buy.marketplace_insights` | ❌ | **HIGH** | Sold price data → PP-REPRICER-001 |
| `commerce.catalog.readonly` | ❌ | **HIGH** | EPID lookup by UPC/EAN → PP-SEO-001 Phase 3 |
| `sell.analytics.readonly` | ❌ | **HIGH** | Per-listing impressions/clicks → PP-SEO-001 Phase 6 |
| `sell.fulfillment.readonly` | ❌ | medium | Read orders via REST (supplements Trading API GetOrders) |
| `sell.finances.readonly` | ❌ | medium | Payout/financial data for accounting and reconciliation |
| `sell.stores.readonly` | ❌ | medium | Read eBay store category tree → PP-STORE-001 |
| `sell.reputation.readonly` | ❌ | low | Feedback score tracking and monitoring |
| `commerce.notification.subscription` | ❌ | low | REST-based webhook event subscriptions (future alt to Trading API) |

---

#### Priority 2 — API credentials (15–20 min each, each unlocks a lookup source)

- [ ] **IGDB** (video game lookups):
  1. Go to https://dev.twitch.tv → Log in with Twitch account (create if needed)
  2. Register new application: Name=`TGW`, OAuth Redirect=`http://localhost`, Category=`Other`
  3. Copy Client ID + generate Client Secret
  4. Write: `sudo -u tgw nano /opt/TGW/secrets/igdb-credentials.json`
     ```json
     {"client_id": "...", "client_secret": "..."}
     ```
  5. `sudo chmod 600 /opt/TGW/secrets/igdb-credentials.json`

- [ ] **Discogs** (music/vinyl lookups):
  1. Go to https://www.discogs.com/settings/developers
  2. Click "Generate new token"
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/discogs-credentials.json`
     ```json
     {"personal_access_token": "..."}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/discogs-credentials.json`

- [ ] **Go-UPC** (barcode fallback — better coverage than upcitemdb alone):
  1. Go to https://go-upc.com/api → sign up for free tier
  2. Copy API key
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/go-upc-credentials.json`
     ```json
     {"api_key": "Bearer <your-token>"}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/go-upc-credentials.json`

- [ ] **upcitemdb** (optional — increases free rate limit from 100/day):
  1. Go to https://www.upcitemdb.com/api → sign up
  2. Write: `sudo -u tgw nano /opt/TGW/secrets/upcitemdb-credentials.json`
     ```json
     {"api_key": "..."}
     ```
  3. `sudo chmod 600 /opt/TGW/secrets/upcitemdb-credentials.json`

- [ ] After any credential added: `sudo -u tgw tgw health` — confirm no errors

---

#### Priority 3 — Perplexity research (copy-paste, save result to inbox)

Briefs are in `docs/TGW-Plan-Vault/perplexity/`. Open each in Obsidian, paste the prompt into
https://perplexity.ai, save the result as `PERPLEXITY-001-result.md` etc. into
`docs/TGW-Plan-Vault/inbox/` — PM-intake will file it automatically.

- [ ] **PERPLEXITY-001** — eBay scope expansion (do this first; informs Priority 1 above)
- [ ] **PERPLEXITY-002** — Cassini SEO 2025–2026
- [ ] **PERPLEXITY-003** — Sold price data alternatives
- [ ] **PERPLEXITY-004** — Third-party integration status (Whisper.cpp, Discogs, IGDB, Go-UPC)

---

#### Priority 4 — Physical inventory and Seller Hub

- [ ] **eBay sweep** — generate ambiguous-status checklist for physical review:
  ```
  sudo -u tgw tgw ebay-sweep --output /opt/TGW/var/ebay-sweep.md
  ```
  Then open `/opt/TGW/var/ebay-sweep.md` in Obsidian; work through Group A (active eBay / unclear local) first
  
- [ ] **Wrong shipping profiles** — 9 listings with FRE instead of FC4.
  Seller Hub: Listings → search by Item ID → Edit → Shipping → select FC4 (199931446015)
  - [ ] 327195083346  - [ ] 327195083374  - [ ] 327195083408  - [ ] 327195083423
  - [ ] 327195083451  - [ ] 227372145582  - [ ] 327195085940  - [ ] 227372145665
  - [ ] 227372145712

---

#### Priority 5 — Infrastructure

- [ ] **Second keyboard** → connect → install macroboard (PP-MACRO-001):
  ```
  keyd.rvaiya list-devices   # find the unique ID for the macroboard keyboard
  sudo nano /opt/TGW/src/trader-grims-warehouse/etc/keyd/tgw-macroboard.conf
  # replace "413c:2105" in [ids] with the full unique ID
  sudo cp .../etc/keyd/tgw-macroboard.conf /etc/keyd/
  sudo systemctl reload keyd
  # Test: Caps Lock on macroboard → LED changes
  ```

- [ ] **Tailscale** (remote access, PP-REMOTE-001):
  ```
  curl -fsSL https://tailscale.com/install.sh | sh
  sudo tailscale up
  # Join your Tailscale account; verify tgw-http reachable from remote device
  ```

- [ ] **eBay webhook endpoint** (PP-SOLD-001 Tier 4 — reduces sold-detection latency from daily → seconds):
  First check if you have a static public IP:
  ```
  curl -s https://ifconfig.me && ip route get 1.1.1.1 | awk '{print $7; exit}'
  ```
  Same → Path A (nginx + certbot). Different → Path B (Cloudflare Tunnel, works behind NAT).
  - **Path A** (static public IP):
    ```
    apt install nginx certbot python3-certbot-nginx
    cp /opt/TGW/config/nginx/ebay-webhook.conf /etc/nginx/sites-available/tgw-webhook
    # edit server_name to your actual subdomain (e.g. hooks.yourdomain.com)
    ln -s /etc/nginx/sites-available/tgw-webhook /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    certbot --nginx -d hooks.yourdomain.com
    ```
  - **Path B** (behind NAT / dynamic IP):
    ```
    sudo bash /opt/TGW/config/nginx/cloudflared-setup.sh
    # edit /etc/cloudflared/config.yml — replace REPLACE_WITH_YOUR_SUBDOMAIN
    # add CNAME in ZoneEdit: hooks.yourdomain.com -> <tunnel-id>.cfargotunnel.com
    systemctl start cloudflared && systemctl enable cloudflared
    ```
  - Add `dev_id` to `/opt/TGW/secrets/ebay-credentials.json` (from developer.ebay.com → My Account → Application Keys → DevID field):
    `"dev_id": "XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX"`
  - Register URL: `tgw setup-ebay-hooks --url https://hooks.yourdomain.com/webhooks/ebay/notification`
  - Verify: `tgw setup-ebay-hooks --check`
  - Restart: `systemctl restart tgw-worker@ebay_legacy_sync.service` (and tgw-http)

---

#### Priority 6 — External AI tooling (PP-MULTIMODEL-001)

- [ ] **nvm + npm** (needed for markmap-cli and future JS tooling):
  ```
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  # restart shell or: source ~/.bashrc
  nvm install --lts
  npm install -g markmap-cli
  # Test: markmap docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md --no-open -o /tmp/plan.html
  ```

- [ ] **Gemini CLI** (large-context data sessions — 55K item corpus analysis):
  Option A (no install): use https://aistudio.google.com — paste data, get results, save to inbox/
  Option B (install): `pip install google-generativeai` or use `google-cloud-sdk`

- [ ] Perplexity: https://perplexity.ai — no install; briefs ready in `perplexity/` folder

---

## Phases 1–4 ✅ COMPLETE (2026-06-02 → 2026-06-03)

- **Phase 1** — Queue foundation: `secrets_root`, `QueueWorker` base, echo worker, systemd template, health extended, old launcher retired
- **Phase 2** — First workers: `token_refresh` (OAuth, self-scheduling), `pm_intake` (Ollama inbox processor), `tgw suggest` capture
- **Phase 3** — Camera-intake pipeline: `bundle_intake`, `multi_intake`, `ai_identify` (qwen2.5vl:7b, ~18s/call), `ai_hint` system, eBay taxonomy + `ebay_draft` (Qwen2.5 specifics fill)
- **Phase 4** — eBay pipeline: `ebay_upload` (EPS photos), `ebay_publish`, `ebay_sync` (6h), category condition policy (`apis/ebay/conditions.py`, 15K cats, 26 sets, errorId 25021 eliminated)
- **Pending stubs from Phase 4**: PP-ADD-006 (duplicate check), PP-ADD-008 (Inventory API sweep), PP-REVISION-001 (live listing revision)

### PP-MULTIMODEL-001 — Multi-AI Task Routing ✅ ADOPTED (session 5)

Live as Work Tracks (see `## Work Tracks`). Routing table for reference:

| Task type | Tool | Reason |
|-----------|------|--------|
| PP design, arch decisions | Opus | High-stakes reasoning |
| Worker implementation | Sonnet | Arch awareness |
| Data analysis > ~80K tokens | Gemini Code | Context window |
| PM-intake / plan patching | Ollama Qwen2.5 | Free, good enough |
| Photo identification | Ollama Qwen2.5VL | Vision, free/call |
| eBay aspects fill | Ollama Qwen2.5 | Structured extraction |
| eBay API / market research | Perplexity | Live web + citations |
| Simple transforms / boilerplate | Haiku | Fast + cheap |
| Large corpus cross-reference | Gemini Code | Context advantage |

E-sneaker-net: export context → run in external AI → save result to `inbox/` for PM-intake.

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

### PP-CI-001 ✅ DONE 2026-06-04
ruff clean; GitHub Actions CI (`ruff check --no-fix` + `pytest`); `.pre-commit-config.yaml` scoped to `src/tests/`; pre-commit installed in `.git/hooks/`.

### PP-SEO-001 ✅ ALL PHASES DONE 2026-06-04

All 6 phases implemented in `ebay_draft` + `tgw/seo/title.py` + `apis/ebay/catalog.py`:
- **P1** title enhancement — brand/MPN inject, flags (`no_brand`, `title_too_short`, etc.); `draft_listing.title_flags`
- **P2** specifics pre-fill from `product_lookup` (Brand, MPN, Model, EAN); authoritative over AI output
- **P3** EPID association — `lookup_epid()` in `ebay_stage`; **silent skip until `commerce.catalog.readonly` granted**
- **P4** category confidence — Jaccard overlap; `draft_listing.category_confidence`; `tgw staged` CC column
- **P5** description enrichment — 200+ word Ollama-generated prose when `product_lookup.description` ≥ 20 words; SKU baked into body
- **P6** `tgw seo-audit` CLI; **impression data blocked until `sell.analytics.readonly` granted**

Config keys in use: `seo.title_min_chars=40`, `title_max_chars=80`, `title_brand_inject`, `title_mpn_inject`, `epid_lookup`, `description_min_words=200`.

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

### PP-LOOKUP-001 — Product Data Enrichment ✅ ALL TIER 1 DONE (2026-06-05)

`apis/lookup/` package; `lookup_product()` dispatcher; results in `product_lookup` key (30-day cache).
Integrated into `ai_identify` (runs before Ollama) and `tgw lookup <SKU>` CLI.

**Tier 1 sources (all implemented):**
- `upcitemdb` (primary, 698M barcodes, 100/day free) → `go_upc` fallback (1B items)
- `open_library` (books/ISBN, no auth) · `discogs` (music, needs credential) · `igdb` (games, Twitch OAuth)
- `justtcg` (trading cards, no auth) · `open_food_facts` (food/household, no auth)

**Credentials still needed** (silent-skip until added):
- `secrets_root/igdb-credentials.json` — `{"client_id":"...","client_secret":"..."}`
- `secrets_root/discogs-credentials.json` — `{"personal_access_token":"..."}`
- `secrets_root/go-upc-credentials.json` — `{"api_key":"Bearer <token>"}`
- `secrets_root/upcitemdb-credentials.json` — optional; increases rate limit

**Tier 2 (decide when Tier 1 proves insufficient):** Keepa (€19/mo, Amazon price history); Barcode Lookup (richer fields, subscription). Stubs not implemented yet.

**Do not implement:** Amazon PAAPI (sunset 2026), GoodReads (discontinued), TCGPlayer (closed), CamelCamelCamel (no API), eBay Finding API (dead 2025).

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

#### Connection to Work Tracks strategy test
The 4-track delegation model (session 5) is the motivating use case. Work Tracks gives each
agent a queue; PP-TODO-001 makes that queue queryable and persistent across sessions. The
`tgw todo claude` / `tgw todo gemini` / `tgw todo admin` structure maps directly to Tracks 1,
2, and 4. Build PP-TODO-001 so Work Tracks items can be seeded into it on first run.

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

### PP-QUALITY-001 ✅ COMPLETE (2026-06-04)
`tgw/listing_quality.py` — `score_draft()`, 7 signals, 100-pt scale. Signals: title length (10), brand in title (25), MPN in title (10), required specifics % (15), recommended specifics % (5), photo count ≥3 (20), description words (5), comp count (10). Scored in `ebay_draft` + rescored in `ebay_price`; `tgw staged` Q/PC columns; `tgw quality <SKU>` CLI.

### PP-PRICE-001 ✅ COMPLETE (2026-06-03)
`tgw/ebay/pricing.py` + `ebay_price` worker (auto-enqueued by `ebay_draft`). Browse API 3-stage fallback → `price_comps {count,min,p25,median,p75,max}`. Launch price = 110% of max→.99; `target_price` = p25. `category_price_defaults` config fallback for thin comps.

#### eBay Sold-Price API Access — status
- **Finding API `findCompletedItems`** ❌ DEAD (discontinued early 2025; error 10001)
- **Marketplace Insights API** ⚠ LIMITED RELEASE — `buy.marketplace_insights` scope required; no self-service; contact eBay Developer Support. Endpoint: `GET /buy/marketplace_insights/v1/item_sales/search`. Dave is applying via new keyset request.
- **Terapeak** — UI-only (Seller Hub → Research → Terapeak); 3 years data; no API; use manually for high-value items
- **Third-party**: 130Point.com, ZIK Analytics — legal approved partners; evaluate via PERPLEXITY-003
- **Interim**: Browse API p25 + PP-PRICE-004 velocity data is the current substitute

### PP-REPRICE-001 ✅ INITIAL COMPLETE (2026-06-03)
`ebay_price_reducer` worker: launch (day 0, 110%→.99) → retail (p75, day 3) → move (p25, day 17). `reprice_stages` array configurable; `to_99()` rounding; `reprice_skip: true` to exclude. Self-scheduling every 6h. `reprice_schedule` in item JSON tracks stage history.

### PP-REPRICER-001 — Market-aware dynamic repricer (design pending)
- Distinct from `ebay_price_reducer` (scheduled markdown): this watches market prices and adjusts dynamically
- Inputs: sold-price data (needs `buy.marketplace_insights` or Finding API), sell-through rate, days listed, competition count
- Design deferred until sold-price API access obtained — Browse API asking prices are the wrong signal for dynamic repricing
- Will consume `reprice_schedule` as floor (never price below the move price)

### PP-PRICE-003 ✅ COMPLETE (2026-06-04)
`pricing.py`: stage-0 product_lookup query (`brand+mpn` tightest); condition-filtered comps (same-or-worse rank only, 15-entry `_BROWSE_CONDITION_RANK`); price confidence H/M/L (`draft_listing.price_confidence`, `tgw staged` PC column).

### PP-PRICE-004 ✅ COMPLETE (2026-06-05)
`tgw/velocity.py` + `velocity_stats` nightly worker (✅ enabled 2026-06-05). `tgw velocity-report` CLI. `velocity-stats.json` in catalog_root (1,540 categories). `suggest_price()` gains `velocity_hint: 'hold_launch'` for fast-moving categories. Stage breakdown (launch/retail/move%) populates as new-pipeline items sell.

### PP-LISTING-001 — Description footer and picklist line ✅ DONE (2026-06-04)
- Implemented in `workers/ebay_draft.py` — footer + picklist line built into `draft_listing.description`
- Seller boilerplate text + SKU/location picklist line; config keys: `description_footer`, `picklist_line_format`
- Future: QR code image (generate locally, upload to eBay EPS, embed in HTML) — deferred

### PP-STAGE-001 ✅ COMPLETE (2026-06-03)
`ebay_stage` creates UNPUBLISHED Seller Hub offer; `ebay_price` auto-enqueues it. `stage_draft()` + `publish_offer()` split in `sync.py`. `tgw staged` → operator review → `tgw publish <sku>`.

### PP-REVISION-001 — Live listing revision / update draft (design open)
- Three distinct workflows identified: new listing draft | live listing revision | ended→relist
- Revision needs: known baseline (live state synced from eBay), proposed delta, drift visibility
- Draft for new listing (`draft_listing`) is a historical record after publish — not the revision staging area
- Open design question: sparse delta vs full replacement for revision payload; history of applied revisions
- Relist: inventory item already exists on eBay; need fresh pricing + new offer; structurally re-create not update
- `ebay_offer` block now established (PP-PRICE-001) — proceed when ready
- Auto-sync: when offer fields are edited locally (price, condition, aspects), changes should push to eBay without requiring manual Seller Hub edits — design must prevent overwriting live state not yet pulled (depends on PP-SYNC-001 sync pass being authoritative first)

### PP-SYNC-001 ✅ ALL PHASES COMPLETE (2026-06-04)
Core principle: every eBay-side ID/URL written back to item JSON immediately after API call. All matches by `listing_id` directly — never through catalog. Four phases done: `ebay_sync` write-back (6h) · `tgw ebay-pull` on-demand CLI · `tgw import-sold-csv` (2-year max, archive tombstone pass built) · `tgw ebay-sweep` physical review checklist (3 groups, clickable links, `--output`). Tier 3 (physical sweep) operator-gated; Tier 4 webhook code done, infra pending.

### PP-PRICE-002 (confirmed strategy — implemented in PP-REPRICE-001)
Launch 110% max→.99 · retail p75 day 3 · move p25 day 17. `ebay_reprice` stub in pyproject.toml; full market-aware version is PP-REPRICER-001 (blocked on scope).

### MILESTONE-001 ✅ (2026-06-03)
tgw.source replacement ~95% complete. Full pipeline: intake → AI identify → eBay draft → upload → price → stage → operator review → publish → sync. 13+ systemd workers; PostgreSQL state machine; SQLite catalog; 55K+ items.
- Full automated pipeline: photo intake → AI identification → eBay taxonomy → AI specifics → pricing → eBay draft staging → operator review → one-click publish
- 13 systemd workers running; PostgreSQL state machine; SQLite catalog; 55K+ item catalog
- Legacy tgw.source is now thin wrappers; new system is the authoritative data path
- Remaining gap (~5%): live listing revision / repricer / relist workflow (PP-REVISION-001)

- **PP-ADD-001** — Satellite / disconnected catalog support. Full design in Phase 6 § Satellite above. Depends on PP-ADD-005 + PP-ADD-003.

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

## Phase 7 — Vault Synchronization
Syncthing operational. Conflict resolution protocol and git backing details: `OPERATIONS-vault-sync.md`.
