# TGW — Claude Code Session Guide

Trader Grim's Warehouse (TGW) is a resale business (eBay seller: DaveBuko-Webkulap) running a
custom inventory management and eBay automation platform built in Python. Dave runs the business
and directs all development. Read this file first, then read the master plan before doing anything.

## PRIME DIRECTIVES — override everything below except direct instructions from Dave

These are Dave's standing orders. They have been violated repeatedly by sessions that
treated them as background prose. They are not background. Every design decision and
every line of code is checked against these first:

1. **The local dataset IS the business; eBay is a rented window.** Preserve the data
   set — all of it, always. Never discard, overwrite, or decline to record data;
   anything received from outside (eBay, AI models, lookups) is an asset the moment it
   arrives, and persisting it is part of receiving it. Raw is permanent; derived is
   recomputable. A feature that touches external data and grows the dataset by nothing
   is a red flag — say so. Read `reference/TGW-Data-Charter.md` before any pipeline
   work. (Invariants E5/E7; raw capture at `apis/ebay/client.py` — never bypass it.)
2. **Act on alarms immediately.** A thermal alarm, health RED, crash loop, or quota 429
   is YOUR incident the moment you see it: investigate to root cause in the same turn,
   never acknowledge-and-continue. Check your own processes first.
3. **Implement exactly what Dave specified.** If you substitute anything — a cadence, a
   TTL, a default — you flag the deviation in your reply and get it approved. Silent
   substitutions have caused real production outages twice.
4. **"Tests pass" is not done. Done = verified live on real data**, with the observable
   result (URL, log line, item JSON, eBay state) shown to Dave.
5. **When Dave states a new standing requirement, encode it before proceeding**: add it
   here, add an invariant + detector, and note which check enforces it. A requirement
   that lives only in conversation will be lost — that is a proven failure mode of this
   project, not a hypothetical.

## Start every session here

**Step 0 — check thermal status before anything else:**

```
cat /opt/TGW/var/run/thermal.status 2>/dev/null || echo "NORMAL|0|0"
```

If the status is HOT, THROTTLE, or SHUTDOWN: **stop all disk-intensive operations** (no recursive grep/find on ItemData/ItemCatalog). At THROTTLE the watchdog has already stopped workers — do not restart them. At HOT, slow down and avoid large scans.

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

**Step 4 — register planned work before touching any code or config:**

Before making any change this session, do both of these:

1. Check existing todos: `sudo -u tgw tgw todo` — mark any relevant items `in_progress`.
2. For new work: `sudo -u tgw tgw todo add "what you are about to do"` — then mark it `in_progress`.
3. Write a recovery breadcrumb to `docs/TGW-Plan-Vault/inbox/INPROGRESS-<slug>.md` — one short
   paragraph describing what you are working on and where you are. If the session is interrupted,
   the next session startup sequence will read this and reconstruct your state.

**This is mandatory, not optional.** A session that makes changes without a todo + inbox note
loses recoverability. Run `/tgw-exit` when done or switching to a1131 — it finalises the note.

## Key paths

| What | Path |
|------|------|
| Source | `/opt/TGW/src/trader-grims-warehouse/src/tgw/` |
| Config | `/opt/TGW/config/tgw-api-config.json` |
| Secrets | `/opt/TGW/secrets/` (chmod 700, files 600) |
| ItemData | `/opt/TGW/data/ItemData/<SKU>/<SKU>.json` + photos |
| Catalog | `/opt/TGW/data/ItemCatalog/` |
| Logs | `/opt/TGW/var/log/` |
| Universal search index | `/opt/TGW/.recoll/` (config + xapiandb; not in git) — `recoll -q "..."` for cross-archive recovery/audit queries (PP-SEARCH-001 Phase 0) |
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
| `LLM-Providers-Quotas.md` | **Any LLM provider/model/quota change** — Google free tier is ~20/day/model PER PROJECT (not the published 1,000); OpenRouter primary, Google = operator emergency reserve; rediscovered 3× before being written down |
| `PP-LOOKUP-001-APIs.md` | Product enrichment, barcode lookup, ai_identify augmentation |
| `PP-PROMO-001-sale-event-design.md` | Sale event automation via Promotions API — design, API shape, operator checklist |
| `CATEGORY-QUIRKS.md` | Per-category eBay quirks, fulfillment overrides, condition limits |
| `TGW-Item-JSON-Schema.md` | Item JSON field reference — all fields, types, which worker writes/reads, pipeline stage |
| `ISSUES.md` | Active bugs and known gaps — check before diagnosing a known problem |
| `eBay-Error-Codes.md` | eBay API error codes, HTTP status handling, dead-letter diagnosis |
| `SHELL-AUDIT.md` | tgw.source / tgw-dev.source function audit — what to keep, wrap, or remove |
| `HARDWARE-AI-INFERENCE.md` | Ollama model sizing, GPU upgrade planning, inference perf |
| `TGW-Data-Charter.md` | **Any pipeline/worker/eBay work** — the data axiom, asset inventory, rules for new work (Prime Directive 1) |
| `invariants.md` | System invariants (A1–E7) + enforcement status — check before any structural change |
| `TGW-Architecture-Services.md` | Service-by-service responsibility, deps, failure modes, critical invariants |
| `TGW-Architecture-Overview.md` | System topology — how subsystems connect |
| `TGW-NixOS-Reference.md` | NixOS bootstrap sequence, Syncthing topology, host inventory, troubleshooting |
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
| `nix/CLAUDE-NIX.md` | **Any Nix work** — file map, locked decisions, user accounts, eval-and-fix workflow |
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
- **A worker's skip/guard is a finding, not a log line (invariant C11)** — when
  a worker refuses to act on a real recurring condition, persist the reason
  durably on the item (queryable by `catalog-verify`), never just log it and
  move on. Before trusting a static local flag to gate an action, re-verify
  it live against the authoritative external source — local state can go
  stale (Dave, s43: manual Seller Hub use during the Inventory-API migration
  gap silently changed what was true on eBay's side without our records
  updating; the same class "could happen again"). See invariants.md C11.

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
- **Before making any code or config changes** — log the work first:
  1. Create a todo: `tgw todo add "what you're about to do"` (or `tgw todo` to check existing)
  2. Drop an inbox note: write a brief `.md` file to `docs/TGW-Plan-Vault/inbox/` describing
     what you're working on and where you are. Filename: `INPROGRESS-<slug>.md`. This lets
     the startup sequence reconstruct context if the session is interrupted.
  3. Mark the todo `in_progress` when you start, `done` when complete.
- **Run `tgw health` after significant changes** to config, secrets, or workers
- **Commit only when Dave asks** — he controls git history
- **All commands as `tgw` user** — use `sudo -u tgw` or note this when suggesting commands
- **Suggest, don't implement** for exploratory questions until Dave approves direction
- **Workers need restart after source changes** — `systemctl restart tgw-worker@<queue>.service`
- **Re-enqueue manually after dead_letter** — dead_letter jobs don't auto-retry; use `state_machine.enqueue_job()` with a fresh dedupe key
- **Test environment + thermal-relief compute** — use `ssh a1131` for UI/integration testing
  instead of a VM; it's a NixOS host on the LAN with a partial TGW install and 18 GB free RAM.
  Run `/tgw-exit` before switching to it so the inbox note captures your current state.
  **a1131 is shared Dave+Claude precisely for thermal relief** (tgw-prod runs hot): on hot
  days run your own heavy checks — test suites, big greps, review sweeps — there via ssh.
  Never pause pipeline workers for heat (worker load is only a thermal problem when our own
  bugs loop). NFS shares of the data Claude's checks need: todo #1146. Caveat: a1131's repo
  checkout can be stale (#1082) — sync repo state before trusting its test results.
- **Run a code check at least once per work day, more if the session touches a lot of
  files** (Dave, 2026-07-04): a full week of commits (2026-06-24 through 2026-07-02)
  never went through `/code-review`/ultrareview because the diff grew too large to
  review before anyone tried — and the first review that *did* run, on just one day's
  diff, found 7 real confirmed bugs. Don't let unreviewed work accumulate: `/code-review`
  (free, inline) for a quick same-day pass; `/code-review ultra` for a periodic cloud
  pass while the diff is still small enough to clear its size guard. If a day's own diff
  already feels large, review it immediately rather than waiting — it only grows harder
  to review, not easier. See todo #1143 for the one-time backlog catch-up plan
  (full-codebase cohesion audit, staged per-subsystem, run opportunistically against
  spare usage).

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

As of 2026-06-28 (session 31): PP-FENCE-001 Sessions A+B COMPLETE. Workers still stopped — restart now unblocked.
PP-FENCE-001 Session B done: all 30 atomic_write_json sites in workers/ and ebay/ migrated to fence calls; 27 tests pass; CI grep audit added. Gaps documented in source: multi_intake (2 sites), ebay_sku_migrate (3 sites), pull.py restore_archive_tombstone (1 site).
eBay backfill complete: 2,089 published listings have offer_id/listing_id/price; remaining items are draft/unpublished (expected).
PP-PHOTO-001 sync infrastructure live (`tgw-itemdata-sync` service, `gdrive_sync.py`).
PP-REPRICER-001 blocked on `buy.marketplace_insights` scope (eBay DS 8 questions pending).
