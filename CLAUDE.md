# TGW — Claude Code Session Guide

Trader Grim's Warehouse (TGW) is a resale business (eBay seller: DaveBuko-Webkulap) running a
custom inventory management and eBay automation platform built in Python. Dave runs the business
and directs all development. Read this file first, then read the master plan before doing anything.

## Start every session here

**Step 1 — process any pending plan updates before reading the plan:**

1. Check `docs/TGW-Plan-Vault/inbox/` for any `.md` files. If any exist, read them and
   incorporate their content into the master plan, then delete (or move) each processed file.
2. Check `docs/TGW-Plan-Vault/suggestions/SUGGESTIONS.md` for any unprocessed suggestions.
   Evaluate each unchecked item:
   - **Actionable now** → incorporate into master plan as a PP-* item; check off with "→ master plan"
   - **Deferred / not yet ready** → add to `docs/TGW-Plan-Vault/plan/FUTURE-IDEAS.md` with full
     context, research, and promotion criteria; check off with "→ FUTURE-IDEAS.md (reason)"
   - **Do NOT** leave items unchecked or skip them because they are marked "deferred" — deferred
     items still need a home in FUTURE-IDEAS.md so they are never silently lost.

**Future Ideas (`plan/FUTURE-IDEAS.md`):** Do NOT read or process this file at routine session
start. It contains long-horizon concepts to consider only at dedicated planning sessions or when
Dave explicitly asks to review future ideas. When an item in FUTURE-IDEAS.md is ready to promote,
add it to the master plan and remove it from FUTURE-IDEAS.md.

**Step 2 — read the (now-current) master plan:**

```
cat docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md
```

The master plan is the single source of truth: what's done, what's in progress, settled
architecture decisions, and open pending projects (PP-* items). The PM-intake worker keeps it
current from notes dropped into `docs/TGW-Plan-Vault/inbox/`.

**Step 3 — run plan reconciliation + status check (PP-PLANDB-001 Phase 3+4):**

```
tgw plan check
tgw plan status
```

`tgw plan check` reports orphaned pp_refs (todos referencing PP items not in the plan), mismatched
plan_anchors, done-in-plan/open-in-tracker mismatches, and stale round tags. Warnings go to the
admin loop (PP-DOCFLOW-001) for correction — use `tgw todo set-meta <id> --pp <ref>` to fix pp_refs.

`tgw plan status` shows one-line open/done/blocked counts + latest activity date per PP-* item.
Use `tgw plan status --pp PP-XXX-001` to drill into a single item.

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

All docs now live in `docs/TGW-Plan-Vault/` (Syncthing-synced Obsidian vault).
Plain Markdown; open in Obsidian for interactive mind map view where noted.

### `reference/` — technical reference (read before working in that area)

| File | Read when working on... |
|------|------------------------|
| `eBay-API-Landscape.md` | Any eBay API integration, scopes, new API research |
| `TGW-HTTP-API.md` | tgw-http endpoints, Flutter app, MC copyin |
| `TGW-Pipeline-Flow.md` | Worker logic, queue flow, enqueue decisions, debugging |
| `TGW-Config-Reference.md` | Config keys, secrets, policy IDs, adding new config |
| `TGW-Ollama-Prompts.md` | ai_identify + ebay_draft prompts, tuning levers |
| `PP-LOOKUP-001-APIs.md` | Product enrichment, barcode lookup, ai_identify augmentation |
| `PP-PROMO-001-sale-event-design.md` | Sale event automation via Promotions API — design, API shape, operator checklist |
| `CATEGORY-QUIRKS.md` | Per-category eBay quirks, fulfillment overrides, condition limits |
| `TGW-Item-JSON-Schema.md` | Item JSON field reference — all fields, types, which worker writes/reads, pipeline stage |
| `ISSUES.md` | Active bugs and known gaps — check before diagnosing a known problem |
| `eBay-Error-Codes.md` | eBay API error codes, HTTP status handling, dead-letter diagnosis |
| `SHELL-AUDIT.md` | tgw.source / tgw-dev.source function audit — what to keep, wrap, or remove |
| `HARDWARE-AI-INFERENCE.md` | Ollama model sizing, GPU upgrade planning, inference perf |
| `invariants.md` | 29 system invariants (A1–E4) + enforcement status — check before any structural change |
| `TGW-Architecture-Services.md` | Service-by-service responsibility, deps, failure modes, critical invariants |
| `TGW-Architecture-Overview.md` | System topology — how subsystems connect |
| `runbooks/INDEX.md` | Incident response index — dead-letter triage, pipeline stall, token failure, etc. |
| `claude-cli.md` | Claude CLI / Antigravity config reference |
| `echo.py` / `worker_base.py` | Starting point when writing a new worker |

### `plan/` — planning and process docs

| File | Read when... |
|------|-------------|
| `TGW-Master-Plan.md` | Every session — architecture decisions, PP-* design, completion status |
| `handoff.md` | Starting a new session — current risks, recommended next sequence |
| `next-process.md` | Tool routing decisions (Claude vs Aider vs Antigravity), session handoff SOP |
| `PLAN-backup-dr.md` | Working on PP-BACKUP-001 or DR planning |
| `PLAN-nixos-migration.md` | Working on PP-NIXOS-001 or infra migration |
| `FUTURE-IDEAS.md` | **Planning sessions only / when Dave asks** — deferred concepts with full context + promotion criteria; not read at routine session start |

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
`catalog_rebuild`, `plan_render`, `thumbnail_gen`, `ebay_draft`, `ebay_upload`, `ebay_price`,
`ebay_stage`, `ebay_publish`, `ebay_dole`, `ebay_sync`, `ebay_legacy_sync`, `echo`

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
- Missing (apply separately): `buy.marketplace_insights` (sold price data), `commerce.catalog.readonly` (EPID), `sell.analytics.readonly` (impressions)
- Default fulfillment policy for most categories: **FC4** (override in `tgw-api-config.json` per category if needed)

## Current phase

See `docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` for the authoritative current state.
See `docs/TGW-Plan-Vault/plan/handoff.md` for current risks and recommended next sequence.

As of 2026-06-11 (session 26): 563 tests passing. Pipeline fully live.
PP-DOCFLOW-001 P1+P2, PP-PYIPC-001, PP-BACKUP-001 Phase A, history-index complete.
`ebay_sku_migrate` running (~8,350 live listings remain). `velocity_stats` nightly.
PP-REPRICER-001 blocked on `buy.marketplace_insights` scope (eBay DS 8 questions pending).
PP-PORTABLE-CATALOG-001 P2 unblocked (PP-PYIPC-001 done).
