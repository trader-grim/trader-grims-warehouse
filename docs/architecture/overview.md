# TGW Architecture Overview

**Status:** living document. Created 2026-06-10 from the master plan, reference library,
source tree, live config, and systemd layout. Anything not verifiable from code or config is
marked **ASSUMPTION**.

Companion document: [services.md](services.md) — per-service detail (responsibility, I/O,
dependencies, data stores, external APIs, failure modes, invariants).

---

## 1. What TGW is

Trader Grim's Warehouse (TGW) is a single-operator resale business platform (eBay seller
DaveBuko-Webkulap, running since 2011). It manages ~55,000 inventory items and automates the
full listing lifecycle: photo intake → AI identification → eBay draft → photo upload → pricing
→ staging → operator review → publish → scheduled markdown → sold sync.

It is a **filesystem-first** system: the canonical record for every item is a JSON file on
disk. Everything else — SQLite catalog, PostgreSQL queue, thumbnails, eBay listings — is
derived from or coordinated around those files.

## 2. Design principles (settled — do not relitigate)

These are settled architecture decisions recorded in the master plan
(`docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md § Settled architecture`):

| Principle | Meaning |
|---|---|
| **tgw-api is the fence** | All ItemData reads/writes go through `tgw` Python functions (`items.py`, `resolver.py`). Nothing else constructs ItemData paths. The JSON backend is an implementation detail. |
| **One folder per SKU** | `ItemData/<SKU>/<SKU>.json` + photos/videos in the same folder. |
| **PostgreSQL is the work ledger** | Database `state_machine`, table `queue_jobs`. Pure state-machine model; the retired filesystem `.job.json` queue must not return. |
| **Workers are thin** | Workers ask tgw-api; intelligence lives in the ledger and the platform layer. A shared `QueueWorker` base owns claim/lease/complete/fail — no worker hand-rolls SQL. |
| **Output contract** | Every CLI/API call returns exactly one JSON object with an `ok` key: `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. |
| **Secrets from `secrets_root`** | One canonical secrets directory (`/opt/TGW/secrets/`, chmod 700, files 600, owned by `tgw`), resolved via config. No hardcoded secret paths in `src/`. |
| **Catalog rebuild is always a job** | Writers enqueue `catalog_rebuild` (dedupe key `catalog_rebuild:pending`, 30 s coalescing delay); never call `build_all_catalogs()` inline. |
| **resolve() is the selector engine** | Any identifier (SKU, location, UPC, eBay item ID, date range, free text) → set of SKUs (`resolver.py`). |
| **Bulk-first** | Claim a set, operate on the set, return a summary. |
| **SKU format** | `tgwYYYYMMDDHHMMSSmmm` — 18 significant chars, string-comparison sortable. Search matches on first 18 chars. |

## 3. System context

```
                        ┌────────────────────────────────────────────────┐
                        │              eBay (external)                   │
                        │  OAuth2 · Inventory · Trading · Browse ·       │
                        │  Taxonomy · Notifications (SOAP webhook)       │
                        └───────▲────────────────────────────┬───────────┘
                                │ REST/SOAP                  │ push (pending infra)
┌───────────────┐       ┌───────┴────────────────────────────▼───────────┐
│ Product lookup │ REST  │              TGW host (single machine)        │
│ APIs: upcitemdb│◄──────┤                                               │
│ go-upc, discogs│       │  systemd                                      │
│ open_library,  │       │   ├─ postgresql.service   (state_machine DB)  │
│ open_food_facts│       │   ├─ ollama.service       (qwen2.5 / qwen2.5vl)│
│ igdb, justtcg, │       │   ├─ tgw-http.service     (FastAPI :7373)     │
│ pricecharting  │       │   ├─ tgw-worker@<queue>   (×18 template units)│
└───────────────┘       │   ├─ trader-grims-backup  (inotify+rsync)     │
                        │   └─ queue-workers-startup.timer (+10 s boot)  │
┌───────────────┐       │                                               │
│ Operator       │ CLI   │  Data: /opt/TGW/{data,config,secrets,        │
│ (Dave)         ├──────►│        incoming,runtime,var}                  │
│ phone/tablet   │ HTTP  │                                               │
│ Claude/Gemini  │ MCP   │  Repo: /opt/TGW/src/trader-grims-warehouse    │
└───────────────┘       └───────────────┬───────────────────────────────┘
                                        │ Syncthing
                                ┌───────▼────────┐
                                │ Obsidian plan  │
                                │ vault (other   │
                                │ devices)       │
                                └────────────────┘
```

Clients of the platform:

- **`tgw` CLI** (`src/tgw/api.py`, ~3,500 lines) — the operator's primary surface; also thin
  shell wrappers in `etc/interfaces/shell/tgw.source`.
- **tgw-http** (`src/tgw/http_server.py`, FastAPI, port 7373) — Flutter app, tablet web forms
  (`/form/intake`, `/form/bulk`), MC (Midnight Commander) extfs integrations, eBay webhook.
- **MCP server** (`src/tgw/mcp_server.py`, `tgw-mcp-server`) — 10 tools exposing item/queue/
  health/todo operations to Claude Code and other AI agents.
- **Flutter app** (`apps/` — `tgw_app`; the `flutter/` tree is a vendored Flutter SDK) —
  mobile item browse/edit/SKU-lookup client against tgw-http.

## 4. Data stores — what is canonical, what is derived

| Store | Location | Role | Canonical? |
|---|---|---|---|
| **ItemData** | `/opt/TGW/data/ItemData/<SKU>/<SKU>.json` + media | Per-item record; fields accumulate per pipeline stage (see `reference/TGW-Item-JSON-Schema.md`) | **YES — single source of truth for item state** |
| PostgreSQL `state_machine` | local socket, db `state_machine` | `queue_jobs` work ledger + `queue_job_history` + `queue_workers` + `todo_items` + `sku_history` | **YES — single source of truth for work state** (not item state) |
| SQLite catalog | `ItemCatalog/tgwcatalog.db` | Indexed scalar columns + full JSON `data` column; serves all list/search reads | Derived (rebuilt by `catalog_rebuild`) |
| JSON/CSV catalogs | `ItemCatalog/search-catalog.json`, `*.csv` | Legacy/interop catalog outputs | Derived |
| Location tree | `ItemCatalog/by-location/` symlink tree | Browse-by-bin filesystem view | Derived |
| Thumbnails | `ItemCatalog/thumbnails/<SKU>.jpg` | 256×256 cache for UIs | Derived |
| `velocity-stats.json` | `ItemCatalog/` | Per-category sold velocity aggregates (1,540 categories) | Derived |
| Secrets | `/opt/TGW/secrets/*.json` | eBay credentials/token, tgw-http API key, lookup API keys | Canonical (operator-provisioned) |
| Runtime state | `/opt/TGW/runtime/state/` (e.g. `ebay-sold-sync-state.json`) | Worker cursors/checkpoints | Canonical for the owning worker |
| Plan vault | `docs/TGW-Plan-Vault/` (Syncthing-synced) | Master plan, suggestions, inbox, reference library | Canonical for planning (not runtime) |
| Clipboard store | SQLite via `src/tgw/clip.py` (PP-CLIP-001) | Clipboard history for intake workflows | Peripheral |

**Key consequence:** eBay itself holds state (offers, listings, sold orders) that TGW mirrors
back into item JSON (`ebay_offer`, `ebay_listing`, `ebay_sale` blocks). The design goal
(memory: local eBay mirror) is that every eBay-side ID/URL is recorded in the item JSON, so
the local store can answer questions without calling eBay.

## 5. The queue system

- Schema: `src/tgw/queue/schema.sql`. Job states: `queued → leased → running →
  succeeded | retry_wait | failed | dead_letter | cancelled`.
- Claiming uses `claim_queue_jobs()` (PL/pgSQL, `FOR UPDATE SKIP LOCKED`), lease-based with
  expiry; `recover_expired_jobs()` requeues expired leases and promotes mature `retry_wait`
  jobs (called by every worker every 60 s).
- Dedupe: partial unique index on `dedupe_key` over active states — the mechanism behind
  coalesced catalog rebuilds and single-flight self-scheduling workers.
- `worker_base.py` adds a second-chance layer: when retries are exhausted,
  `classify_dead_letter()` string-matches known transient errors (expired token, photos not
  yet uploaded, network timeouts) and requeues with a backoff instead of dead-lettering.
  Dead-letter is reserved for failures needing a human; `HardFailure` short-circuits straight
  to dead_letter + `notify()`.
- Dead-letter jobs **never auto-retry** — operator re-enqueues via `tgw dead-letter --requeue`
  / `--requeue-transient`.
- 18 queues (canonical list: `WORKER_QUEUES` in `src/tgw/queue/__init__.py`): token_refresh,
  pm_intake, bundle_intake, multi_intake, ai_identify, catalog_rebuild, thumbnail_gen,
  ebay_draft, ebay_upload, ebay_price, ebay_price_reducer, ebay_stage, ebay_publish,
  ebay_sync, ebay_legacy_sync, ebay_sku_migrate, velocity_stats, echo.
- Process liveness is systemd's job: templated `tgw-worker@<queue>.service` units with
  `Requires=postgresql.service`, started via `queue-workers.target` 10 s after boot.

## 6. The item pipeline (happy path)

```
photo drop in incoming/newitems/<SKU>/        (operator, camera relay, KDE Connect)
  → bundle_intake   moves media → ItemData/<SKU>/, writes stub JSON
      ├→ thumbnail_gen, catalog_rebuild (30 s coalesced)
      └→ ai_identify  barcode lookup (apis/lookup/) + Ollama qwen2.5vl:7b vision
            └→ ebay_draft   Taxonomy API category + aspects, Ollama fills specifics,
               │            best_condition() fallback, SEO title, quality score
               ├→ ebay_upload  Trading API photo upload → EPS URLs
               └→ ebay_price   Browse API comps → launch price (110 % of max → .99),
                  │            target_price = p25; category-group floor fallback
                  └→ ebay_stage  Inventory API PUT item + POST offer (UNPUBLISHED)
                       → operator reviews `tgw staged`, runs `tgw publish <sku>`
                          → ebay_publish  POST offer/publish; writes ebay_listing
                                          + reprice_schedule (launch→p75@d3→p25@d17)
post-publish, continuous:
  ebay_price_reducer (6 h)  applies due markdown stages
  ebay_sync (6 h)           offer/listing status write-back
  ebay_legacy_sync          GetMyeBaySelling + GetOrders → marks items sold
  velocity_stats (nightly)  per-category sold velocity → pricing feedback loop
  ebay_sku_migrate (hourly) legacy listing delist→rename→relist (~8,350 remaining)
```

Two operator gates are deliberate: **publish is manual only** (`tgw publish`), and intake
templates (`tgw set-template`, category-groups) front-load human knowledge so AI output is
constrained.

## 7. AI / external intelligence

- **Ollama, local, CPU-only** (32 GB host): `qwen2.5vl:7b` vision identification,
  `qwen2.5:latest` for drafting aspects and pm_intake plan patching. All inference is
  serialized through a Postgres advisory lock (`queue/ollama_lock.py`, lock id 8472) because
  two concurrently loaded models thrash the machine. This makes Ollama a global throughput
  bottleneck by design; prompts are kept lean (see `reference/TGW-Ollama-Prompts.md` and
  `HARDWARE-AI-INFERENCE.md`).
- **Product lookup** (`apis/lookup/`): dispatcher with 30-day cache, category-keyword routing
  and barcode-field discovery; sources upcitemdb (primary), go-upc, open_library, discogs,
  open_food_facts, igdb, justtcg, pricecharting. Results enrich the ai_identify prompt and
  draft descriptions. Every source degrades gracefully if its key is absent.
- **Delegated AI work tracks** (planning-level, not runtime): Claude (build), Gemini (large-
  context data analysis), Perplexity (research briefs). The rule that external AI data I/O
  goes through the tgw-api fence extends the settled fence principle.

## 8. eBay integration surface

Client code in `src/tgw/apis/ebay/` (`client.py`, `taxonomy.py`, `trading.py`,
`specifics.py`, `conditions.py`, `notifications.py`, `catalog.py`) and orchestration in
`src/tgw/ebay/` (`sync.py`, `pricing.py`, `pull.py`, `upload.py`, `market_data.py`,
`description.py`).

- **Auth:** OAuth user token, auto-refreshed by `token_refresh` worker (30-min buffer,
  expiry-based self-reschedule), stored at `secrets_root/ebay-token.json`. Granted scopes:
  `sell.inventory`, `sell.account`, `sell.marketing`. **Scopes are locked — never add
  speculatively** (a speculative scope change broke OAuth on 2026-06-05).
  Missing scopes block features: `buy.marketplace_insights` (sold-price data →
  PP-REPRICER-001 live mode), `commerce.catalog.readonly` (EPID), `sell.analytics.readonly`.
- **Hard-won API rules** (encoded in code, documented in `reference/eBay-Error-Codes.md`):
  `Content-Language: en-US` on all Inventory PUT/POST; offer PUT is full-replace (never PUT
  before publish); many categories only accept conditionId 3000 → `best_condition()`
  same-or-worse fallback + 25021 retry; `availabilityDistributions` with
  `merchantLocationKey` needed to avoid 25002 `Item.Country`.
- **Policies are config, not item data:** fulfillment FC4 default + per-category and
  per-size-class overrides, payment, return policy IDs in `tgw-api-config.json`. Per-item
  `shipping_profile` override exists; precedence item > category > size_class > global.

## 9. Runtime topology and boot

Single host (currently MX Linux; **NixOS migration committed** — PP-NIXOS-001; `flake.nix` +
`nix/tgw.nix` module exist, validated in VM only). All services run as user `tgw`; source
files `rw-------`; permissions policy enforced by `scripts/tgw-permissions-reset.sh --check`
(wired into `tgw health` ownership check).

Boot order: postgresql → tgw-http → ollama → trader-grims-backup →
tgw-worker@token_refresh (individually enabled, belt-and-suspenders) → +10 s timer →
queue-workers.target → all worker instances.

Health: `tgw health` checks config paths, Postgres (incl. per-queue dead_letter breakdown),
SQLite catalog, thumbnails, ownership/permissions, optionally eBay token. Run after any
change to config, secrets, workers, or paths.

## 10. Security model

- **Trust boundary is the LAN/host, not the app.** tgw-http Bearer-token auth
  (`secrets_root/tgw-api-key.json`) protects `/api/*`; the `/form/*` HTML endpoints are
  deliberately **unauthenticated** for phone/tablet use on the internal network
  (**ASSUMPTION:** the host is never exposed beyond LAN/Tailscale; nginx/cloudflared webhook
  ingress is designed but not deployed).
- eBay webhook authenticates by SOAP signature instead of Bearer; verification is
  **incomplete without `dev_id`** in `ebay-credentials.json` (ISS-005) and the code's
  accept-when-unsigned behavior is an encoded, deliberate interim decision.
- Secrets never in the repo or config JSON; eBay credentials were explicitly removed from
  `tgw-api-config.json` in Phase 1.

## 11. Known divergences and open issues (verified, from ISSUES.md + config inspection)

- **ISS-003:** live config sets `full_catalog_path: master-catalog.json` but
  `load_config()` defaults to `tgwcatalog.json` and the code default silently wins.
- **ISS-004:** `ebay_sku_migrate` config block is read from `cfg['raw']`, bypassing
  `load_config()` normalization (a `batch_size` bug related to this was fixed 2026-06-10).
- Live config still carries ~9 legacy keys ignored by `load_config()` (`api_root`,
  `archive_root`, `log_root`, …) — documented as safe-to-remove.
- **ISS-002:** 10 legacy items live with the wrong shipping policy (manual Seller Hub fix).
- **ISS-008:** `legacy_listing_resolved` items may still have active legacy listings; the
  `ebay_stage` active-listing guard catches new collisions but the historical data is not
  authoritative.
- Master-plan tables go stale; **the todo tracker (`tgw todo claude`) is the canonical task
  queue** — the plan is the reference spec.

## 12. Where to read more

| Topic | Document |
|---|---|
| Per-service detail | `docs/architecture/services.md` (companion to this file) |
| Worker pipeline flow | `docs/TGW-Plan-Vault/reference/TGW-Pipeline-Flow.md` |
| Item JSON fields | `docs/TGW-Plan-Vault/reference/TGW-Item-JSON-Schema.md` |
| HTTP endpoints | `docs/TGW-Plan-Vault/reference/TGW-HTTP-API.md` |
| Config keys & secrets | `docs/TGW-Plan-Vault/reference/TGW-Config-Reference.md` |
| eBay error handling | `docs/TGW-Plan-Vault/reference/eBay-Error-Codes.md` |
| Active bugs | `docs/TGW-Plan-Vault/reference/ISSUES.md` |
| Plans / roadmap | `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` |
