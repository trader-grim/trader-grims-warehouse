# TGW — Claude Code Session Guide

Trader Grim's Warehouse (TGW) is a resale business (eBay seller: DaveBuko-Webkulap) running a
custom inventory management and eBay automation platform built in Python. Dave runs the business
and directs all development. Read this file first, then read the master plan before doing anything.

## Start every session here

**Step 1 — process any pending plan updates before reading the plan:**

1. Check `docs/TGW-Plan-Vault/inbox/` for any `.md` files. If any exist, read them and
   incorporate their content into the master plan, then delete (or move) each processed file.
2. Check `docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md` for any unprocessed suggestions.
   If new items are present, evaluate and incorporate actionable ones into the master plan.

**Step 2 — read the (now-current) master plan:**

```
cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md
```

The master plan is the single source of truth: what's done, what's in progress, settled
architecture decisions, and open pending projects (PP-* items). The PM-intake worker keeps it
current from notes dropped into `docs/TGW-Plan-Vault/inbox/`.

Memory index (cross-session context): `/home/tgw/.claude/projects/-opt-TGW-src-trader-grims-warehouse/memory/MEMORY.md`

## Key paths

| What | Path |
|------|------|
| Source | `/opt/TGW/src/trader-grims-warehouse/src/tgw/` |
| Config | `/opt/TGW/config/tgw-api-config.json` |
| Secrets | `/opt/TGW/secrets/` (chmod 700, files 600) |
| ItemData | `/opt/TGW/data/ItemData/<SKU>/<SKU>.json` + photos |
| Catalog | `/opt/TGW/data/ItemCatalog/` |
| Logs | `/opt/TGW/var/log/` |
| Plan vault | `docs/TGW-Plan-Vault/` (Syncthing-synced Obsidian) |
| Plan inbox | `docs/TGW-Plan-Vault/inbox/` (drop .md files here) |
| **Reference docs** | `docs/TGW-Plan-Vault/reference/` — read before working on relevant areas |

## Reference library

Markmap documents in `docs/TGW-Plan-Vault/reference/` — read the relevant one before working
in that area. All are plain Markdown; open in Obsidian for interactive mind map view.

| File | Read when working on... |
|------|------------------------|
| `eBay-API-Landscape.md` | Any eBay API integration, scopes, new API research |
| `TGW-HTTP-API.md` | tgw-http endpoints, Flutter app, MC copyin |
| `TGW-Pipeline-Flow.md` | Worker logic, queue flow, enqueue decisions, debugging |
| `TGW-Config-Reference.md` | Config keys, secrets, policy IDs, adding new config |
| `TGW-Ollama-Prompts.md` | ai_identify + ebay_draft prompts, tuning levers |
| `PP-LOOKUP-001-APIs.md` | Product enrichment, barcode lookup, ai_identify augmentation |
| `CATEGORY-QUIRKS.md` | Per-category eBay quirks, fulfillment overrides, condition limits |
| `TGW-Item-JSON-Schema.md` | Item JSON field reference — all fields, types, which worker writes/reads, pipeline stage |
| `ISSUES.md` | Active bugs and known gaps — check before diagnosing a known problem |
| `HARDWARE-AI-INFERENCE.md` | Ollama model sizing, GPU upgrade planning, inference perf |
| `echo.py` / `worker_base.py` | Starting point when writing a new worker |

## Settled architecture (do not relitigate)

- **tgw-api is the fence** — all ItemData reads/writes go through it
- **One folder per SKU** — `ItemData/<SKU>/<SKU>.json` + media
- **PostgreSQL is the work ledger** — database `state_machine`; workers use `QueueWorker` base
- **Workers are thin** — ask tgw-api, never construct paths directly
- **Output contract** — every API call returns `{ok, ...}`
- **Secrets from `secrets_root`** — no hardcoded paths anywhere in `src/`
- **Catalog rebuild is always a job** — never call `build_all_catalogs()` inline
- **SKU format** — `tgwYYYYMMDDHHMMSSmmm`

## Running workers (systemd)

```bash
systemctl list-units 'tgw-worker@*'
journalctl -u 'tgw-worker@<queue>.service' -f
```

Workers: `token_refresh`, `pm_intake`, `bundle_intake`, `multi_intake`, `ai_identify`,
`catalog_rebuild`, `thumbnail_gen`, `ebay_draft`, `ebay_upload`, `ebay_price`, `ebay_stage`,
`ebay_publish`, `ebay_sync`, `ebay_legacy_sync`, `echo`

## Checking queue state

```bash
psql -U tgw state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  GROUP BY queue_name, state ORDER BY queue_name, state;"
```

## Health check

Always run after touching config, secrets, workers, or paths:

```bash
tgw health
```

Run as `tgw` user — source files are `rw-------`, secrets are `chmod 600`.

## Working rules for Claude

- **Read the master plan first** — it has the full architecture context
- **Run `tgw health` after significant changes** to config, secrets, or workers
- **Commit only when Dave asks** — he controls git history
- **All commands as `tgw` user** — use `sudo -u tgw` or note this when suggesting commands
- **Suggest, don't implement** for exploratory questions until Dave approves direction
- **Workers need restart after source changes** — `systemctl restart tgw-worker@<queue>.service`
- **Re-enqueue manually after dead_letter** — dead_letter jobs don't auto-retry; use `state_machine.enqueue_job()` with a fresh dedupe key

## eBay API notes

- Auth: OAuth user token, refreshed by `token_refresh` worker, stored in `secrets_root`
- All Inventory API PUT/POST calls require `Content-Language: en-US` header
- Condition granularity: many categories only accept conditionId 3000 ("Used") — `USED_EXCELLENT` maps to this; `USED_GOOD`/`USED_ACCEPTABLE` may be rejected
- Have scopes: `sell.inventory`, `sell.account`, `sell.marketing`
- Missing (apply separately): `buy.marketplace_insights` (sold price data)
- Default fulfillment policy for most categories: **FC4** (override in `tgw-api-config.json` per category if needed)

## Current phase

See master plan `## Current state`, `## Implementation TODO`, and `## Phase N` sections.
As of 2026-06-05: Phases 1–4 + PP-STAGE-001 + PP-REPRICE-001 + PP-LISTING-001 + PP-SYNC-001
(all phases) + PP-SOLD-001 Tier 1 + PP-LOOKUP-001 ALL Tier 1 complete (IGDB, JustTCG, OFF added).
Pipeline: photo intake → AI identify (with barcode product lookup) → eBay draft → upload →
price (launch=110% max→.99) → stage → `tgw staged` operator review → `tgw publish` → live.
`ebay_price_reducer` handles scheduled markdown (p75 day 3 → p25 day 17).
`ebay_sku_migrate` running (~8,350 eBay live listings remain; ~70 days at 5/hr).
Next priorities (see Implementation TODO table in plan):
  PP-MULTIMODEL-001 (task routing guide) → PP-REPRICER-001 (blocked on scope) → PP-PRICE-004 (velocity).
