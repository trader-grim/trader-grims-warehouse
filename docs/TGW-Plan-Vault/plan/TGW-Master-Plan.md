---
title: TGW Master Plan
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-13 (session 29 cont. — PP-SHELL-001 T3 done (#96); discogs httpx migration done (#98); PP-FREESHIP-001 done (#123); 4 suggestions processed; 11 new todos seeded #125–#135; suite 964)
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

### Pipeline additions — 2026-06-09 (session 14, batch 3)
- **`tgw dead-letter` command DONE** — `dead_letter_jobs(queue, limit)` + `requeue_dead_letter_job(job_id)` added to `state_machine.py`. `cmd_dead_letter()` in `api.py`: lists dead_letter jobs grouped by queue with classify_dead_letter verdict ([transient]/[permanent]), error snippet, finished timestamp. `--requeue JOB_ID` re-enqueues from payload (cancels dead_letter entry); `--cancel QUEUE` bulk-cancels. `tgw_dead_letter` MCP tool added (10 tools total). `dead-letter` in bash completion. 3 new tests.
- **`notify()` on HardFailure DONE** — `worker_base.py` HardFailure path now fires `notify(..., level='error')` symmetrically with the transient requeue path.
- **`offline_draft_stall` catalog-verify rule DONE** — New warning rule in `_verify_item()`: `offline_draft: true` + file mtime > 2h triggers `offline_draft_stall`. 2 new tests.
- **77 tests pass** (up from 72).

### Planning — 2026-06-08 (session 16, Round 3 + key decisions)
- **Inbox cleared**: PP-NIXOS-001 Perplexity analysis (MX vs NixOS for PostgreSQL + Python DR)
  processed. Findings: NixOS architecturally superior; `poetry2nix` for Python; WAL recovery
  ExecStartPost gotcha documented. Alternatives noted: Guix System, Silverblue.
- **NixOS COMMITTED** — Dave confirmed NixOS is the target. PP-NIXOS-001 promoted from
  evaluation to active migration prep. PP-DEPLOY-001 MX image = final safety-net snapshot
  before cutover. No timeline pressure — migrate when ready.
- **ISS-009 downgraded** — production keyset active; token not fully blocking live eBay.
- **SUGGESTIONS.md corruption artifact removed** — session 15 API interruption had left raw
  response text (lines 112–296) mixed into the file; cleaned up session 16.
- **Round 3 locked** — 8 ranked items. PP-BULKEDIT-001 (tablet web UI + CLI) is #1 by Dave's
  direction. Guiding principle: time-saving interfaces usable now, better later.
  See `## Work Tracks → Track 1 — Round 3`.

### Execution — 2026-06-07 (session 17, Track 1 Round 3 — ALL 8 items DONE)
- **All 8 Round 3 items shipped** (todo IDs 21–28 now closed). Suite **263 → 315 passing**
  (+52 tests), ruff clean, `tgw health` fully green. Per-item:
  - **#21 `tgw restart-workers`** — restarts `tgw-worker@<queue>.service` (all or named);
    uses `sudo -n` when non-root, prints the command if passwordless sudo is unavailable.
    New canonical `WORKER_QUEUES` tuple in `queue/__init__.py` (single source of truth; 18 queues).
  - **#22 PP-BULKEDIT-001 Phase 1** — shared `items.bulk_edit` core (filter→preview→apply,
    fields: title/location/status/ai_hint/shipping_profile; location routes through
    `locationupdate` for tree sync, ai_hint set without re-queue). CLI `tgw bulk` (dry-run
    default, `--apply`). Web: `GET /form/bulk` (tablet-first HTML, network-trust like
    `/form/intake`) + `POST /api/bulk/{preview,apply}` (Bearer). Enqueues catalog_rebuild on apply.
  - **#23 catalog-verify `--fix`** — dry-run default, `--write` to apply; auto-strips stale
    `TEMPLATE:` title prefix (conservative; never writes an empty title); per-SKU fix log.
  - **#24 PP-REPRICER-001 read-only** — `ebay/market_data.py`: `MarketDataProvider` +
    OwnSalesProvider (velocity), BrowseCompsProvider (Browse GET, reuses `suggest_price`),
    StubProvider (the `buy.marketplace_insights`-blocked sold-data slot). `tgw reprice-suggest`
    blends → reduce/hold/raise. **Strictly read-only — no eBay write calls (verified).**
  - **#25 PP-MC-001 Phase 4** — `tgwlogs` MC extfs VFS: read-only journalctl per worker;
    WORKER_QUEUES allowlist + argv-list subprocess (no shell injection) + output cap
    (`TGWLOGS_LINES`, default 500/max 5000). Registered (mc.ext.ini, installer, sentinel, README).
  - **#26 PP-SHELL-001 T3** — `tgw.source`/`tgw-dev.source` version-controlled at
    `etc/interfaces/shell/` (sha256-verified verbatim) + README + operator-gated `install.sh`
    block (backup+idempotent). Audit-correction: all 6 named ARCH-VIOLATES already wrapped
    (lines 201–1066); remaining direct-jq writes are in DEPRECATED funcs (Tier-2 *removal*, not wrap).
  - **#27 PP-NIXOS-001** — `flake.nix` (buildPythonApplication, no poetry.lock) + `nix/tgw.nix`
    (NixOS module: tgw user w/ configurable uid, postgres state_machine, per-queue worker
    services, tgw-http, opt-in backup) + `nix/README.md` VM-validation steps. Watch-item flagged:
    `python3Packages.mcp` availability in nixos-24.11. **Dave validates in VM (not built on MX).**
  - **#28 PP-DEPLOY-001** — `reference/PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md`: pre-snapshot
    checklist (drain workers, pg_dump, perms `--check`), MX Snapshot include/exclude (exclude
    bulk ItemData → rclone restore), ISO verify, full restore, post-NixOS retention.
- **Adversarial review workflow could not run inline** — sub-agents hit the session limit. The
  operator then ran cloud **`/ultrareview`**, which found 3 real bugs (all now FIXED + regression-
  tested; suite **315 → 321**):
  - *bulk_edit partial-success* — `ok=len(failed)==0` conflated partial success with total failure:
    catalog_rebuild was skipped after partial writes and the CLI's failure summary was dead code.
    Fixed: gate rebuild on `count` (not `ok`) in `cmd_bulk` + `http_server.bulk_apply`; CLI handler
    now branches on the `error` key so the partial-success summary is reachable.
  - *catalog-verify `--fix` stale `item_viols`* — fixed violations were double-reported (open TODO +
    FIXES) and `--fix --write --mark-verified` skipped the hall pass for fixed-clean items. Fixed:
    apply fixes then re-run `_verify_item` on the mutated doc BEFORE accumulating violations / the
    mark gate.
  - *negative `--limit`* sliced from the end (`skus[:-5]`). Fixed: `if limit > 0` in `bulk_edit` +
    `cmd_reprice_suggest`; `BulkBody.limit` now `Field(ge=0)`.
  - One finding was a **false positive**: ultrareview ran on the committed tree and reported
    `market_data.py` / `etc/interfaces/shell/` / `extfs.d/tgwlogs` as "missing" — they exist locally
    but are **untracked**. ⚠ **COMMIT REMINDER:** `git add` the new files (`src/tgw/ebay/market_data.py`,
    `etc/interfaces/shell/`, `etc/interfaces/mc/system/extfs.d/tgwlogs`, `etc/interfaces/mc/logs.tgwlogs`,
    `flake.nix`, `nix/`, and the 4 new `tests/test_*.py`) or the features break exactly as the review
    described. install-system-mc.sh would also benefit from the same `[[ -f ]]` guard the shell block has.
- **4 new operator suggestions arrived mid-session** (SUGGESTIONS.md, 2026-06-08T01:25–01:54):
  install quickstart guide; NixOS from-scratch/adopt + site-config-in-github DR; client-machine
  migration path (tgwOS on the spare intake box); "tgw plan builder" (DB-driven plan). Left
  unprocessed for the next planning pass — new scope, not Round 3.

### Planning — 2026-06-07 (session 15, Track 1 round-2 backlog)
- **Track 1 round 1 confirmed COMPLETE** (sessions 6–14); stale numbered rows collapsed.
- **Full code-verified audit** of all 31 open PP-* items + 9 issues (multi-agent, each finding
  checked against actual source — not plan labels). Produced the ranked, sized round-2 backlog
  under `## Work Tracks → Track 1 — Round 2`: **25 Claude-ready slices** (Tiers A–E) +
  blocked-by-blocker groups + recommended sequence. No code executed — planning only.
- **Stale-done drift corrected**: PP-MCP-001 (9→10 tools), PP-SOLD-001 (Tier 3/4 DONE),
  PP-VERIFY-001 (Phase 2 DONE, 27 tests), PP-MC-001 (Phase 2 DONE), PP-INTAKE-001 (P1/P2 DONE;
  `fulfillment_policy_id` template claim struck), PP-CAPTURE-001 / PP-HINT-001 / PP-STRIKE-001
  (shipped, were "planned"), PP-GLOBALS-001 dependency (satisfied). **ISS-006 closed** (stale).
- **Landmine fixed inline**: plan line ≈2208 falsely claimed `tgw picklist` exists — corrected.
- Top of next batch (best-first): PP-GLOBALS-001 (XS, `weight_oz`) → PP-SOLD-001 / PP-MCP-001 /
  PP-INTAKE-001 / PP-EDITOR-001 / PP-STRIKE-001 (test+reconcile) → PP-FULFILLMENT-001 (`picklist`).

### Execution — 2026-06-07 (session 15, Track 1 round-2 ranks 1–18)
- **Tiers A+B+C+D DONE** — suite 77 → **263 passing**, ruff clean, `tgw health` fully green.
  See `## Work Tracks → Track 1 — Round 2` for the per-rank execution status. Commit `9fa38ee` =
  ranks 1–14; Tier D + permissions = the following commit.
- **Security fix (surfaced by the new PP-DEPLOY-001 `check_ownership`)**: `discogs-credentials.json`
  was `0664` (group/world-readable secret) → fixed to `0600`. Reworked `tgw-permissions-reset.sh`
  (now version-controlled at `scripts/`): added a secrets section, a fast `--check` audit, and a
  non-root chmod fallback. **Hardened the policy so it never world-exposes** TGW files (app trees
  `2750`/`0640`, was `2755`/`0644`) — the old script would have loosened the private codebase.
  Remaining (needs sudo): `config/trader-grims-backup.yaml` is root-owned `0644` — run
  `sudo tgw-permissions-reset.sh` to fix. Config backup at `tgw-api-config.json.bak-session15`.
- **Worker restart needed on deploy**: `worker_base.py` (notify), `sync.py` (ebay_stage/publish),
  `dispatcher.py` (ai_identify).

### Pipeline additions — 2026-06-09 (session 14, batch 2)
- **PP-DEADLETTER-001 health integration DONE** — `dead_letter_breakdown()` added to `state_machine.py`: returns per-queue dead_letter counts. `check_postgres()` in `health.py` now shows per-queue breakdown in detail string (e.g. `dead_letter=3 [ebay_draft:2, ebay_price:1]`) and returns `dead_letter_by_queue` dict. `tgw_queue_status` MCP tool also returns `dead_letter_by_queue`. Worker transient requeue now calls `notify()` with level='warning' so it surfaces on desktop/webhook.
- **tgw todo CRUD DONE** — Three new operations in `todo.py` + CLI: `--update ID text` (rewrite body), `--delegate ID agent` (reassign), `--set-priority ID N` (reprioritize). All idempotent on not-found. 9 new tests in `tests/test_todo.py`.
- **Bash completion improvements** — `tgw todo` flags updated with `--update --delegate --set-priority`; `--severity` completes with `critical warning info`; `tgw requeue --status` completes with known status values.
- **72 tests pass** (up from 63).

### Pipeline additions — 2026-06-09 (session 14)
- **GEMINI-001 processed** — Category group quality review. 22/24 groups calibration OK. Two coherence issues resolved:
  - `electrical_fixtures` split: category 185134 (Circuit Breakers) moved to new `electrical_industrial` group (size_class=small_box, floor $10.19, typical $25.47); `electrical_fixtures` now covers wall plates/switches only (size_class=packet). ai_hints updated.
  - `tools_hand` coherence issue noted (flashlights vs wrenches 2.57x spread) — logged as Track 2 follow-up; not split yet pending volume data.
  - `refrigerator_magnets` merge into `collectibles_pins_buttons` declined (different price tiers, distinct ai_hints needed).
  - ai_hints improved: `books` (+antiquarian/collectible/vintage), `photos_ephemera` (+souvenir/travel memorabilia), `media_records` (+vinyl album).
  - Category-groups.json updated (now 25 groups). Changes are live; no worker restart needed (loaded at runtime).
- **GEMINI-002 processed** — Data scrub analysis. Completeness matrix by pipeline stage documented. 4 stall patterns identified (offline_draft, legacy API divergence, in-flight, unmigrated legacy). New catalog-verify rules derived; 3 implemented immediately (see PP-VERIFY-001 Phase 2 below). Key finding: hybrid items (legacy + new pipeline fields) are highest risk — they have two "heads" that can disagree. Offline draft stall is a high-signal indicator for pipeline health.
- **PP-VERIFY-001 Phase 2 DONE** — `catalog_verified` hall pass field:
  - `_write_field()` in `items.py`: clears `catalog_verified` on any field write (except when writing `catalog_verified` itself).
  - HTTP PATCH handler in `http_server.py`: also clears `catalog_verified` on multi-field updates via API.
  - `cmd_catalog_verify()`: new flags `--mark-verified` (write hall pass to passing items), `--force` (mark even with violations, for operator acknowledgement), `--skip-verified` (skip already-passed items for fast re-scan). Result dict includes `skipped_verified` and `marked_verified` counts.
  - `tgw_catalog_verify` MCP tool updated with same flags.
  - 3 new `_verify_item` rules (from GEMINI-002): `negative_price` (warning), `inventory_api_no_offer` (warning), `barcode_lookup_fail` (info).
  - 9 new tests. 63 total tests pass.
- **63 tests pass** (up from 54 at session 13).

### Pipeline additions — 2026-06-08 (session 13)
- **PP-DEADLETTER-001 DONE** — `classify_dead_letter(error_text)` in `worker_base.py`: returns `('requeue', delay_seconds)` or `('dead_letter', 0)`. `requeue_with_backoff(job_id, owner, delay, error)` in `state_machine.py`: transitions running→retry_wait with reset attempt_count and custom delay. `QueueWorker._process()` now intercepts exhausted-retry exceptions and auto-reschedules transient errors (token expired 900s, no eBay photos 600s, directory not empty 30s, ReadTimeout 120s, LEASE_EXPIRED 120s, ConnectionError 120s). Dead_letter reserved for true hard failures only. 6 new tests.
- **PP-VERIFY-001 Phase 1 DONE** — `tgw catalog-verify [--location LOC] [--limit N] [--severity critical|warning|info] [--output PATH] [--json]` command in `api.py`. 9 rule checks: `no_title` (critical), `stale_template_prefix` (critical), `json_parse_error` (critical), `title_is_sku` (warning), `title_too_short` (warning), `no_location` (warning), `no_photo` (warning), `invalid_ebay_category` (warning), `bad_verified_date` (info), `unknown_status` (info). Outputs markdown checklist grouped by severity. Phase 2 (hall pass `catalog_verified` field) remains. 10 new tests.
- **PP-MCP-001 DONE** (code) — `src/tgw/mcp_server.py`: 9 MCP tools exposed via FastMCP: `tgw_get_item`, `tgw_search_items`, `tgw_queue_status`, `tgw_health`, `tgw_enqueue`, `tgw_get_todo`, `tgw_add_suggest`, `tgw_hint_trail`, `tgw_catalog_verify`. `tgw-mcp-server` console script added to pyproject.toml. `mcp>=1.0` added to dependencies. **Registration requires operator action**: add MCP server block to `~/.claude/settings.json` (see Operator Priority 2 below). Cannot self-register; Claude Code settings are operator-controlled.
- **54 tests pass** (up from 38 at session 11 start).

### Pipeline additions — 2026-06-08 (session 12)
- **1 suggestion processed** — Track 2 (Gemini) "History consolidation" task split into two distinct tasks: "AI conversation history consolidation" (Dave's AI session history) and "Data/archive history consolidation" (ItemArchive zips, legacy records, archive-ebay-index.json — analogous to the archive index work already done).

### Pipeline additions — 2026-06-08 (session 11)
- **PP-HINT-001 trail DONE** — `identification_history` list added to item JSON. `append_history_event()` helper in `items.py` (UTC ISO ts injected automatically). Two event types: `ai_identify` (round, model, prompt_type, hint, lookup_source, title, category, condition, ebay_category_id) and `hint_set` (hint, prev_hint, by='operator'). `ai_identify.py` appends on every run; `cmd_hint()` in `api.py` appends on every hint write. `tgw hint-trail <sku>` CLI prints formatted history. `hint-trail` added to bash completion. 10 new tests; 38 total pass.
- **Track 1 COMPLETE** — all items in the Track 1 table are now done.

### Pipeline additions — 2026-06-07 (session 10)
- **8 suggestions processed** — PP-PERP-AUTO-001 expanded with Qtile scraping layout, tmux/ltsp/qtile/ssh stack, simplified paste→download workflow, and iterative markdown loop. Gemini history consolidation task noted in Track 2 (plan before executing). `catlocmvall` → `mvitems` rename with expanded selectors. PP-TASKER-001: barcode scanner confirmed available via Tasker on camera phone; intent audit needed to capture scan output.
- **PP-MC-001 Phase 2 DONE** — `tgwitem` extfs: `copyin` for `fields/` (all except `sku.txt`) and `meta.json`; `ebay/` read-only subdir (`draft`, `offer`, `listing`, `reprice`, `lookup`); `pipeline/` live PostgreSQL job states per SKU; `actions/` dir with `re-identify`, `re-draft`, `re-price`, `re-stage`, `re-publish` — press Enter to enqueue. Repo updated; deploy: `sudo bash etc/interfaces/mc/install-system-mc.sh`.
- **tgw status** — alias for `tgw health`; lower friction under duress.
- **tgw mvitems** — new command replacing/expanding `catlocmvall`: `tgw mvitems <to_loc> [skus...] [--from loc] [--search q] [--status s] [--check-only]`. Uses `resolve()` for flexible multi-selector targeting. `catlocmvall` kept as deprecated alias.
- **tgw bash completion** — `etc/completion/tgw-completion.bash`; all subcommands, SKU completion from catalog, location completion from location-tree, per-subcommand flags. Sourced automatically via `tgw.source`. Install system-wide: `sudo cp etc/completion/tgw-completion.bash /etc/bash_completion.d/tgw`.
- **tgw suggest-edit** — opens `SUGGESTIONS.md` in `$EDITOR`; `--pending-only` extracts unprocessed entries to a temp file for focused review.
- **28 tests pass** — all existing tests green after all changes.
- **PP-GLOBALS-001 ANALYSIS DONE** — no `globals` block needed; top-level fields already serve this role. All offer-invariant properties (condition, ebay_category_id, category_group, size_class, upc) are correctly placed at top level. Policy IDs and marketplace constants are account-wide (config), not per-item. Single missing field: `weight_oz` (float, nullable) — add when PP-INTAKE-001 Phase 2 is implemented (the natural write path). No restructuring needed. See pending projects for full audit table.
- **PP-HINT-001 Browse enrichment DONE** — `_fetch_browse_aspect_hints()` in `ebay_draft.py`: calls Browse API with `fieldgroups=ASPECT_REFINEMENTS` + `category_ids` filter before AI prompt; extracts most common aspect value per field from similar active listings; injects as "Common values from similar active eBay listings" section into Ollama prompt. Falls back to `{}` on failure — never blocks drafting. `draft_listing.browse_hint_count` records how many applicable hints were provided. 28 tests pass.

### Pipeline additions — 2026-06-06 (session 9)
- **21 suggestions processed** — full SUGGESTIONS.md backlog cleared. New pending projects:
  PP-MCP-001 (tgw MCP server), PP-FULFILLMENT-001 (barcode/label/scale hardware),
  PP-TASKER-001 (Tasker + Join integration), PP-EMAIL-001 (email auto-processing + SMTP),
  PP-PERP-AUTO-001 (Perplexity semi-automation via ydotool), PP-CLAUDE-HELP-001 (troubleshoot-
  mode claude launch). PP-EDITOR-001 expanded with comprehensive admin GUI spec (mobile-first,
  Ready state, rate-limited listing dole-out). PP-CLIP-001 expanded with full clipboard action
  surface. PP-TODO-001 updated with unique-ID per task requirement. PP-INTAKE-001 updated with
  computer-side intake workflow + camera root path. PP-NIXOS-001 expanded with Debian vs NixOS
  tradeoff analysis. Track 1 additions: bash completion, tgw CLI synonyms, suggestion editor.
  PP-MC-002 updated with LTSP remoteapps note. Later projects: custom camera app, VNC/RDP combo.

### Pipeline additions — 2026-06-06 (session 8)
- **PP-SHELL-001 T2 MAJOR PROGRESS** — All ARCH-VIOLATES functions replaced with thin `tgw` CLI wrappers: `hintupdate`, `locationupdate`, `statusupdate`, `titleupdate`, `verifiedupdate`, `catlocmvall`. `ic_test()` syntax error fixed (stray UI content after closing brace). `statusupdate` added as `tgw statusupdate <value> <sku...>` CLI command (writes `#STATUS` field; rename in Data Scrub Pass 2). `verifiedupdate` in `items.py` upgraded to write both `verified` + `#STATUS=In Stock` atomically. Deprecated blocks removed: `mktgwcatalog-location`, `mktgwcatalog-location-ebcat`, `mk-ebay-category-csvs`, `mktgwtodo`, `mktgwcatalog`, `mktgwcsv-jj`, `mktgwcsv-jq`, `mktgwjson-jj-old`, `mktgwjson-jj`, `mktgwjson-jq`, `mktgwcatalog_plus_fbimport`, `backupitemdata`, `archiveitemdatatmp`, `archiveitemzips`, `set_queue`, `unfoldio`, `searchcatalog_versionupdate`, `jsonaddsku`, `gpt_title`, `gpt_desc`, `tgwcd`. File reduced 3405 → 2879 lines. Remaining deprecated blocks: csvmerge*, addphotos*, resumedraft, data2json*, archivenewitems, newitem*, eb_template_*, mkjob*, mkebimport, mkebupdate, mkfbimport (Tier 2 pass not yet complete).
- **PP-INTAKE-001 Phase 1 DONE** — `tgw set-template <group_key> [sku] [--list] [--camera GROUP_KEY] [--dry-run]`. Writes `category_group`, `ai_hint` (prepended), `size_class`, `ebay_category_id` (if not set) to item JSON. Pushes `SETTEMPLATE:<group_name>` to clipboard via `wl-copy`/`xclip` for KDE Connect camera relay. No-arg → `--list` shows all 24 templates. `--camera` pushes template string only (no JSON write). `--dry-run` preview. Resolves SKU from CurrentItem symlink when no SKU arg. `_current_item_sku()` + `_push_clipboard()` + `_build_template_fields()` helper functions in `api.py`. Closes the template→pipeline loop: xmouse button → `tgw set-template` → item JSON pre-populated → ai_identify uses better hint → suggest_price hits group floor immediately.
- **PP-PYIPC-001 added** — New PP: Python library integration for Syncthing REST API + KDE Connect. Research brief at `perplexity/PERPLEXITY-005-library-audit.md` (also covers broader library audit); Track 3 (Perplexity).
- **PERPLEXITY-005 added** — Library audit brief: Syncthing Python client, KDE Connect DBus/REST, psycopg3 migration, Ollama client library, USB scale HID, barcode lookup alternatives. In `perplexity/PERPLEXITY-005-library-audit.md`.
- **ISS-009 Token double-buffer bug FIXED** — `refresh_access_token()` had a 5-min internal guard conflicting with the worker's 30-min buffer, delaying the real eBay call until the last 5 minutes of token life. Fixed: added `force=True` parameter; worker now passes `force=True` to bypass the internal guard. `tgw restart-ebay-token` added: clears dead_letter token jobs and enqueues a fresh token_refresh immediately. `clear_dead_letter(queue_name)` added to `state_machine.py`.
- **PP-PRICE-005 DONE** — Category groups taxonomy (`/opt/TGW/config/category-groups.json`): originally 24 groups, expanded to 25 (session 14 — `electrical_industrial` split); 65+ eBay categories mapped; fields: `name`, `store_category`, `ebay_categories`, `size_class` (flat/packet/small_box…), `ai_hint`, `pricing` (floor, typical_used, typical_new seeded from velocity p25). Integrated into `suggest_price()`: Stage 4 fallback (group typical × condition_factor before returning null); hard floor applied to ALL prices including Browse API results. `tgw category-groups [cat_id] [--list] [--reseed]` CLI; `config['category_groups_path']` config key. Immediately unblocks items with thin Browse API comps that previously stalled with null price. **Semi-chaotic storage design**: `size_class` per group encodes physical storage class; items stored by size not category — group membership gives a default size assumption at intake.
- **ISS-009 Operator action required** — eBay refresh token dead (HTTP 400 2026-06-05 17:00). Dave must run browser OAuth re-consent then `sudo -u tgw tgw restart-ebay-token` to re-start the cycle. See ISSUES.md ISS-009.
- **OAuth flow improved (session 8)** — `get_access_token.py` now shows a clear bordered prompt
  so it's obvious where to paste the redirect URL. `tgw get-ebay-token --code 'v%5E...'` flag
  added: supply URL-encoded auth code directly to bypass browser step (useful when browser flow
  ran but paste failed). Code exchange runs immediately without waiting for another browser session.
- **PP-PYIPC-001 added (session 8)** — Python library integration for Syncthing REST API + KDE
  Connect DBus. PERPLEXITY-005-library-audit.md brief created (covers full stack review: psycopg3,
  Ollama client, USB scale HID, barcode lookup, eBay SDK status). See pending project section.
- **PP-VERIFY-001 added (session 8)** — `tgw catalog-verify` command: scans ItemData for assumption
  violations (title empty, bad location format, stale TEMPLATE: prefix, invalid verified date, etc.);
  outputs markdown violation checklist; `catalog_verified` hall pass flag clears on any field write.
  Phase 1 scaffold delegated to Gemini (Track 2). See pending project section.
- **All suggestions processed (session 8)** — SUGGESTIONS.md fully cleared including Perplexity
  programming capability note (now in Priority 6 and Track 3 documentation).
- **Gemini CLI confirmed installed (session 8)** — `gemini` in PATH; elevated to Track 2 primary
  for large-context data tasks. Free with Google Drive subscription. Perplexity expiry checklist
  added to Priority 6: PERPLEXITY-005 is the only unrun brief.

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

### Recently completed (sessions 4–10)
- ✅ **PP-QUALITY-001** listing quality scorer (2026-06-04)
- ✅ **PP-PRICE-003** comp search improvement (2026-06-04)
- ✅ **PP-HINT-001** bulk requeue command (2026-06-04)
- ✅ **PP-SEO-001** title enhancement, all phases (2026-06-04)
- ✅ **PP-REF-001** item JSON schema doc (2026-06-04)
- ✅ **PP-CI-001** linting + GitHub Actions (2026-06-04)
- ✅ **PP-LOOKUP-001** all Tier 1 sources (2026-06-05)
- ✅ **PP-PRICE-004** velocity analytics (2026-06-05)
- ✅ **PP-LISTING-001** description footer + picklist line (2026-06-04)
- ✅ **PP-SOLD-001 Tier 2** CSV import (run 2) — 909 fuzzy + archive tombstone pass; need full all-time CSV for archive hits
- ✅ **PP-STORE-001** eBay store categories (2026-06-05)
- ✅ **PP-STRIKE-001** strikethrough pricing (2026-06-05) — disabled by default; enable once Dave verifies Seller Hub access
- ✅ **PP-CAPTURE-001** `tgw note` + `tgw btw` aliases (2026-06-05)
- ✅ **PP-REF-002** eBay error code reference (2026-06-05)
- ✅ **Data Scrub Pass 1** `#VERIFIED`→`verified` rename (2026-06-05) — 55,226 items
- ✅ **PP-IFDIR-001** interface file org (2026-06-05)
- ✅ **PP-SHELL-001 Tier 1** shell source audit (2026-06-05)
- ✅ **SKU search first-18** (2026-06-05)
- ✅ **PP-TODO-001** multi-agent TODO tracker (2026-06-05)
- ✅ **PP-WM-001 Phase 1** Qtile WM base config (2026-06-05); operator install pending
- ✅ **PP-PRICE-005** Category groups taxonomy (2026-06-06)
- ✅ **PP-TOKEN-001** token double-buffer bug fix + `tgw restart-ebay-token` (2026-06-06)
- ✅ **PP-SHELL-001 Tier 2** ARCH-VIOLATES replacements + deprecated block removal (2026-06-06) — all 6 ARCH-VIOLATES functions replaced with thin `tgw` CLI wrappers; `statusupdate` CLI added; `verifiedupdate` now writes `verified`+`#STATUS` atomically; 20+ deprecated blocks removed; file 3405→2879 lines. Remaining deprecated: csvmerge*, addphotos*, data2json*, archivenewitems, mkjob*, newitem* (minor, no arch risk — wrap if needed).
- ✅ **PP-INTAKE-001 Phase 1** `tgw set-template` command (2026-06-06) — writes category_group, ai_hint (prepended), size_class, ebay_category_id to item JSON; pushes SETTEMPLATE: to clipboard for KDE Connect relay; `--list`, `--camera`, `--dry-run`; resolves SKU from CurrentItem symlink; closes the template→pipeline loop.
- ✅ **PP-MC-001 Phase 2** `tgwitem` copyin + ebay/ + pipeline/ + actions/ (2026-06-07) — copyin for fields/ and meta.json; ebay/ read-only subdir; pipeline/ live PG jobs; actions/ pipeline triggers. Deploy: `sudo bash etc/interfaces/mc/install-system-mc.sh`.
- ✅ **tgw status** alias for `tgw health` (2026-06-07)
- ✅ **tgw mvitems** — expands `catlocmvall` with SKU list / --from / --search / --status selectors (2026-06-07); catlocmvall kept as deprecated alias
- ✅ **tgw bash completion** — `etc/completion/tgw-completion.bash`; auto-sourced via `tgw.source` (2026-06-07)
- ✅ **tgw suggest-edit** — opens SUGGESTIONS.md in $EDITOR; `--pending-only` for focused review (2026-06-07)
- ✅ **PP-GLOBALS-001 analysis** (2026-06-07) — no `globals` block needed; top-level fields already serve this role; add `weight_oz: float | null` in PP-INTAKE-001 Phase 2 (natural write path)
- ✅ **PP-HINT-001 Browse enrichment** (2026-06-07) — `_fetch_browse_aspect_hints()` in `ebay_draft.py`; ASPECT_REFINEMENTS fieldgroup + category filter; injects common values into Ollama prompt; `browse_hint_count` in draft_listing
- ✅ **PP-HINT-001 trail** (2026-06-08) — `identification_history` in item JSON; `append_history_event()` in `items.py`; events: `ai_identify` + `hint_set`; `tgw hint-trail <sku>` CLI

### Active / next build priorities

**See `tgw todo claude` for the live task queue.** The plan is the canonical *reference spec*; `tgw todo` is for *assignment of duties* derived from the plan. Use `tgw todo` to find what to work on; use the plan for design context and status history.

Operator-gated items still tracked here:
| PP | Status | Notes |
|----|--------|-------|
| **PP-SOLD-001 Tier 3** sweep | operator gated | Run `tgw ebay-sweep` after full-history CSV import |
| **PP-PYIPC-001** | ✅ **DONE 2026-06-11** | `tgw.apis.syncthing` + `tgw.apis.kdeconnect`; 25 tests; see row 57 above |
| **PP-REPRICER-001** live | blocked | Blocked on `buy.marketplace_insights` scope |

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
| ✅ | PP-STORE-001 | eBay store category support — done (session 6) | S |
| ✅ | PP-STRIKE-001 | Strikethrough pricing — done (session 6); enable via config once verified | S |
| ✅ | PP-CAPTURE-001 | `tgw note`/`tgw btw` aliases — done (session 6) | XS |
| ✅ | PP-REF-002 | eBay error code reference — done (session 6); `reference/eBay-Error-Codes.md` | S |
| ✅ | PP-SHELL-001 T1 | Shell audit + targeted fixes — done (session 6); `reference/SHELL-AUDIT.md` | M |
| ✅ | PP-IFDIR-001 | Interface file org — done (session 6); MC + keyd in `etc/interfaces/`; symlink live | S |
| ✅ | Data scrub P1 | `#VERIFIED`→`verified` rename — done (session 6); 55,226 items; `tgw data-scrub`; bash + Python verifiedupdate updated | M |
| ✅ | PP-SHELL-001 T2 | (round-1 #1) ARCH-VIOLATES + deprecated removal — done session 8 (also listed below) | M |
| ✅ | PP-IFDIR-001 | (round-1 #3) Interface configs in `etc/interfaces/` — done session 6 (also listed above) | S |
| ✅ | SKU search | (round-1 #5) Catalog/search match on first 18 chars — done session 6 | XS |
| ✅ | PP-TODO-001 | PostgreSQL `todo_items` + `tgw todo [agent]` CLI — done (session 6) | M |
| ✅ | PP-WM-001 P1 | Qtile base config + TGW widgets — done (session 7); operator install pending | M |
| ✅ | PP-SHELL-001 T2 | ARCH-VIOLATES + deprecated block removal — done (session 8); SHELL-AUDIT.md updated | M |
| ✅ | PP-INTAKE-001 P1 | `tgw set-template` command — done (session 8); closes template→pipeline loop | M |
| ✅ | PP-MC-001 P2 | `tgwitem` copyin + `ebay/` + `pipeline/` + `actions/` subdirs — done (session 10) | M |
| ✅ | tgw synonyms | `tgw status` alias for health; `tgw mvitems` expands catlocmvall — done (session 10) | XS |
| ✅ | bash completion | `tgw` bash/zsh tab completion — `etc/completion/tgw-completion.bash`; sourced via tgw.source — done (session 10) | S |
| ✅ | suggestion editor | `tgw suggest-edit [--pending-only]` — done (session 10) | XS |
| ✅ | PP-GLOBALS-001 | Analysis done (session 10) — no globals block; add `weight_oz` in PP-INTAKE-001 P2 | S |
| ✅ | PP-HINT-001 Browse | Browse ASPECT_REFINEMENTS enrichment in `ebay_draft` — done (session 10) | S |
| ✅ | PP-HINT-001 trail | `identification_history` in item JSON; `tgw hint-trail <sku>` — done (session 11) | M |
| ✅ | PP-INTAKE-001 P2 | Intake web form `/form/intake/<sku>`; template chips, weight_oz, barcode, condition, ai_hint — done (session 11) | M |
| ✅ | PP-DEADLETTER-001 | `classify_dead_letter()` + `requeue_with_backoff()`; auto-reschedule transient failures — done (session 13) | S |
| ✅ | PP-VERIFY-001 P1 | `tgw catalog-verify`; 9 rules; markdown checklist — done (session 13) | M |
| ✅ | PP-MCP-001 | `tgw/mcp_server.py`; 9 tools; `tgw-mcp-server` console script; MCP registration = operator task — done (session 13) | M |
| ✅ | GEMINI-001/002 | Category group review processed; `electrical_industrial` split; 3 new verify rules; ai_hints improved — done (session 14) | S |
| ✅ | PP-VERIFY-001 P2 | `catalog_verified` hall pass; `--mark-verified`/`--force`/`--skip-verified`; clear-on-write — done (session 14) | S |
| ✅ | PP-DEADLETTER-001 health | `dead_letter_breakdown()` per-queue; health detail + MCP tool; `notify()` on requeue — done (session 14) | XS |
| ✅ | tgw todo CRUD | `--update`/`--delegate`/`--set-priority`; 9 tests — done (session 14) | XS |
| ✅ | bash completion values | `--severity` → critical/warning/info; `todo --update/--delegate/--set-priority` — done (session 14) | XS |

**Track 1 round 1 is COMPLETE** (sessions 6–14). Every numbered item above is done. The
round-2 backlog below was produced by a full code-verified audit of all open PP-* items and
issues (session 15, 2026-06-07) — see `### Track 1 — Round 2` immediately below.

### Track 1 — Round 2 — code-verified backlog (session 15, 2026-06-07)

Produced by auditing every open PP-* item + every open issue against the **actual code**, not
plan labels. Each item below was classified ready / blocked and sized; the audit also caught
substantial stale-done drift (the plan was crediting several shipped features as "to build" and
vice-versa — see the reconciliation subsection). **Nothing here is executed yet — this is the
plan.** Ordering is value-per-risk, best-first. All "ready" slices are buildable + testable
offline (pure functions, mocked tests, or local-only data); none require the dead eBay token.

**Cross-cutting rules for every round-2 slice:**
- Do **not** commit until Dave asks (he controls git history).
- Run `tgw health` after any change touching config or `health.py` (PP-GLOBALS-001,
  PP-DEPLOY-001, PP-WM-001 notify block).
- Restart affected `tgw-worker@<queue>` units after editing a worker (e.g. `ai_identify` for
  the PP-LOOKUP-001 routing change).
- **ISS-009 (eBay token)** — ⬇ DOWNGRADED (session 16): production keyset is active; token
  refresh likely resolves it. Does **not** block live eBay work — run `tgw restart-ebay-token`
  if token jobs are dead-lettered. No longer a hard blocker for Round 3 work.

**Execution status (session 15, 2026-06-07): Tier A + Tier B COMPLETE (ranks 1–6).**
Suite 77 → **184 passing**, ruff clean. Uncommitted, pending Dave's review:
- ✅ R1 PP-GLOBALS-001 — `weight_oz` → `packageWeightAndSize` in `sync.py` + 7 tests
  (`test_ebay_sync.py`). ⚠️ restart `ebay_stage`/`ebay_publish` to pick up on deploy.
- ✅ R2 PP-SOLD-001 — 20 tests (`test_sold_recon.py`); accept-when-unsigned encoded as deliberate.
- ✅ R3 PP-MCP-001 — 19 tests (`test_mcp_server.py`) incl. 10-tool drift guard.
- ✅ R4 PP-INTAKE-001 — 16 tests (`test_set_template.py`) + `fulfillment_policy_id` claim struck above.
- ✅ R5 PP-EDITOR-001 — 30 tests (`test_http_server.py`, FastAPI TestClient; PATCH/merge/auth/rebuild).
- ✅ R6 PP-STRIKE-001 — 18 tests (`test_strikethrough.py`); MSRP gate + offer-body gate. Config flag stays off.

**Tier C COMPLETE (ranks 7–14)** — suite **184 → 234 passing**, ruff clean, all CLI parses + bash completion added. Uncommitted, pending review:
- ✅ R7 PP-FULFILLMENT-001 — real `tgw picklist` (location-sorted) + `_item_ebay_id` + 4 tests (`test_picklist.py`). Plan landmine retired.
- ✅ R8 PP-HINT-001 — per-item `shipping_profile`: `tgw setshipping` + `_resolve_fulfillment_id` precedence (item > category > size_class > global).
- ✅ R9 PP-STORAGE-001 — `fulfillment_policy_by_size_class` wired into the same resolver; 11 tests (`test_listing_policies.py`). ⚠️ `ebay_sku_migrate` has its own policy copy — left untouched (actively migrating ~8,350 listings); parity is a follow-up.
- ✅ R10 PP-LOOKUP-001 — `apis/lookup/pricecharting.py` + dispatcher routing (strictly additive, fires only when Tier-1 missed) + first lookup tests, 9 (`test_lookup.py`). ⚠️ routing is on the `ai_identify` hot path → restart that worker on deploy.
- ✅ R11 PP-CAPTURE-001 — `tgw quiet-check` + `state_machine.active_depths()`; 5 tests (`test_quiet_check.py`).
- ✅ R12 PP-PERP-AUTO-001 — `tgw perp-run <BRIEF-ID> [--list]` + `## Prompt` parser; 9 tests (`test_perp_run.py`).
- ✅ R13 PP-WHISPER-001 — `tgw whispertosuggest <wav>` (ffmpeg→whisper-cli→cmd_suggest), subprocess-mocked; 6 tests. Model file still operator-supplied.
- ✅ R14 PP-CLAUDE-HELP-001 — `CLAUDE-TROUBLESHOOT.md` + `tgw claude-help [issue] [--worker] [--launch]`; 6 tests.
**Tier D COMPLETE (ranks 15–18)** — suite **234 → 263 passing**, ruff clean. Committed 9fa38ee covers 1–14; Tier D uncommitted pending review:
- ✅ R15 PP-WM-001 — (a) qtile chord bug fixed: chords now call the new `tgw enqueue-sku <sku> <queue>` (CLI sibling of MCP tgw_enqueue) + 4 tests (`test_enqueue_sku.py`); (b) notify activated — `notifications` block added to live config (backends `log,file` — desktop opt-in, behavior-neutral) + `worker_base` calls `notify.configure()` at startup (wrapped so it can't block a worker). ⚠️ restart all workers to pick up the worker_base change.
- ✅ R16 PP-DEPLOY-001 — read-only `check_ownership()` in `health.py`, wired into `check_all`; 8 tests. **Live finding:** flags `discogs-credentials.json` at mode 0o664 (group/other-readable secret — should be 0o600). UID-below-1000 is informational (doesn't fail). This makes `tgw health` report red on `ownership` until the file is fixed — operator decision (chmod 600).
- ✅ R17 PP-EMAIL-001 — `smtp`/`email` backend in `notify.py` (stdlib, fail-soft, out of default backends); 7 tests (`test_notify_smtp.py`).
- ✅ R18 PP-CLIP-001 — `src/tgw/clip.py` SQLite store + `tgw clip {list,last-sku,search,wipe}`; 10 tests (`test_clip.py`). Xlib daemon deferred (desktop-session-blocked).
Config backup: `/opt/TGW/config/tgw-api-config.json.bak-session15`. eBay scopes untouched.
Next available: Tier E (ranks 19–25) — bulk-mutation / larger / lower value-per-risk.

#### Tier A — XS, highest value-per-risk (do first)
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 1 | PP-GLOBALS-001 | Wire `weight_oz` into eBay inventory body (`packageWeightAndSize`) in `_build_offer_bodies()` with a 0-guard mirroring `ebay_sku_migrate.py:299`; unit-test. Operator already captures `weight_oz` at intake but it's dropped — staged offers ship with no calculated-shipping weight. Plan's "wait for PP-INTAKE-001 P2" dependency (≈line 1106) is **satisfied/stale**. | XS |

#### Tier B — test-only / doc-reconcile for already-shipped hot-path code (near-zero regression risk; front-load)
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 2 | PP-SOLD-001 | `tests/test_sold_recon.py` for the token-free path: `pull.find_title_match` (Jaccard/threshold/tie-reject), `mark_item_sold` idempotency, `build_listing_index`, `notifications.parse_sold_notification`/`verify_notification_signature` (encode current accept-when-unsigned as deliberate), `cmd_ebay_sweep` A/B/C. **Also reconcile: Tier 3 ebay-sweep + Tier 4 webhook are DONE, not "pending/future".** | S |
| 3 | PP-MCP-001 | `tests/test_mcp_server.py` for all **10** tools (mock `_get_cfg`/state_machine; `tgw_health` runs `include_ebay=False`). Fix drift: plan table lists only 9 (omits `tgw_dead_letter`); docstring says `~/.claude/mcp_servers.json` vs plan's `settings.json` block. | S |
| 4 | PP-INTAKE-001 | Tests for set-template: `_build_template_fields`, `cmd_set_template` (--list/--dry-run/--camera/unknown-key/CurrentItem), `POST /api/items/{sku}/set-template`. **Reconcile: P1 & P2 DONE; STRIKE the §1612 claim that the template writes `fulfillment_policy_id` — the code never does** (PP-HINT-001 shipping_profile is the cleaner per-item mechanism). | S |
| 5 | PP-EDITOR-001 | `tests/test_http_server.py` via FastAPI `TestClient` against the **untested 28 KB backend** every Flutter phase + MC console depends on: GET/PATCH `/api/items/{sku}` (sku-immutable, empty-field, merge, `catalog_verified` auto-pop, location-tree sync, coalesced rebuild), `/api/items`, `/api/locations`, `/api/category-groups`, bearer-auth 401. Mock PG + `enqueue_job`. Flutter app stays GUI-blocked. | M |
| 6 | PP-STRIKE-001 | Tests for the existing (untested) strikethrough gating: `ebay_price.py:104` MSRP>launch, `ebay/sync.py:285` `originalRetailPrice` gated on `strikethrough_enabled`. Optional: add MSRP line to `ebay_draft.py` description footer. **Reconcile: core code is DONE (plan says "Planned"); do NOT flip `strikethrough_enabled` — needs account approval + live token.** | S |

#### Tier C — new operator-facing capability, pure/additive code
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 7 | PP-FULFILLMENT-001 | Real `tgw picklist` CLI: location-sorted plain-text list (location/SKU/title/eBay id) over the token-free `list_items()`. **LANDMINE: plan line ≈2208 falsely says this already exists — it does NOT** (only `picklist_line()` in `ebay/description.py`). Hardware sub-features (scale/printer/PDF/QR) stay blocked. | S |
| 8 | PP-HINT-001 | Per-item `shipping_profile` override: `tgw setshipping <sku> <profile>` writes item JSON; `_get_listing_policies()` honors `item['shipping_profile']` with precedence item > category > global > API. **Reconcile: requeue / Browse enrichment / hint-trail / `hint --force` already DONE; only this remains.** Must not auto-repush published listings. | S |
| 9 | PP-STORAGE-001 | Wire the (currently write-only) `size_class` field into fulfillment-policy resolution: add `fulfillment_policy_by_size_class` map + extend `_get_listing_policies(..., size_class=None)` with precedence below per-category. Pairs with #8 — same resolver; sequence them together. Defer intake-UI prompt + weight-derivation. | S |
| 10 | PP-LOOKUP-001 | `apis/lookup/pricecharting.py` (games/cards/collectibles market value; graceful-skip if no key, like `igdb.py`) + dispatcher routing for is_game/is_tcg + **first-ever `tests/test_lookup.py`**. Routing edit is on every `ai_identify` run — keep strictly additive (fires only when result is None and key present); restart `ai_identify`. | S |
| 11 | PP-CAPTURE-001 | `tgw quiet-check`: read-only over `queue_depths()` + SUGGESTIONS.md/TODOs; surface pending count when queues idle (also confirm no running jobs; stdout default, notify opt-in). **Reconcile: `suggest`/`note`/`btw` + `suggest-edit` already DONE.** Add first tests for the capture commands. | S |
| 12 | PP-PERP-AUTO-001 | `tgw perp-run <BRIEF-ID> [--list]`: resolve a brief under `perplexity/`, parse the `## Prompt` body, push to clipboard via `_push_clipboard()` + stdout fallback. One parser unit test. GUI automation (ydotool/watcher/Qtile layout) deferred. | S |
| 13 | PP-WHISPER-001 | `tgw whispertosuggest <wav>`: ffmpeg → `whisper-cli` → parse → existing `cmd_suggest()`. Mock-test the parse + dispatch. whisper-cli + ffmpeg installed; **`ggml-base.en.bin` model absent** → live transcription needs operator download (plumbing is mock-testable now). | S |
| 14 | PP-CLAUDE-HELP-001 | Author `CLAUDE-TROUBLESHOOT.md` (worker→queue→DB flow, condensed ISSUES.md, diagnostic decision tree) + a `tgw claude-help [issue] [--worker]` launcher calling `claude --append-system-prompt-file`. `claude` CLI + flags confirmed present. | S |

#### Tier D — infra activation / diagnostics
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 15 | PP-WM-001 | Two headless fixes: (a) `qtile/config.py:178,184` chord keys call non-existent `tgw requeue-sku $SKU <queue>` — replace with a real per-SKU enqueue path; (b) the `tgw.notify` desktop backend is fully coded but inert — `configure_from_api_config()` has zero call sites and the `notifications` block is absent from config; add the block + call it at worker startup (keep desktop opt-in, default `['log','file']`). Phase-1 GUI verify stays desktop-blocked. | S |
| 16 | PP-DEPLOY-001 | Read-only `check_ownership(cfg)` in `health.py` wired into `check_all`: resolve tgw UID via `pwd.getpwnam`, flag UID ≥ 1000 (migration boundary), spot-check key roots + secrets (600/700) for owner/mode drift. Diagnoses only — the actual UID migration/image bake stays operator-gated. Don't walk all of ItemData. | S |
| 17 | PP-EMAIL-001 | Add an `smtp` backend to `notify.py` `_BACKENDS` (stdlib `smtplib`/`EmailMessage`, fail-soft, keep out of default backends), read from the `notifications` config block; mock-tested. Credential-free foundation; operator drops an app-password later. Inbound eBay-message half stays token/scope-blocked. | S |
| 18 | PP-CLIP-001 | Non-GUI core only: `src/tgw/clip.py` — SQLite store at `~/.local/share/tgw-clip/history.db`, `record_clip()` SKU classifier, query fns, `tgw clip {list,last-sku,search,wipe}` + tests. **Defer the Xlib daemon / socket / Qtile widget / systemd unit (desktop-session-blocked).** `python3-xlib` is already installed. | S |

#### Tier E — bulk-mutation / larger / lower value-per-risk
| Rank | PP | Slice | Size |
|------|----|-------|------|
| 19 | PP-VERIFY-001 | Phase 3 `--fix`: auto-correct **only** safe mechanical rules (start with stale `TEMPLATE:` title-prefix strip), route through the single-item write path so the hall pass clears, coalesce a rebuild, per-SKU fix log, tests. **Only ranked item that mutates real ItemData in bulk** — explicit flag, default dry-run, conservative. **Reconcile: Phase 2 is DONE (27 tests, ~13 rules), not "Next".** | S |
| 20 | PP-MC-001 | Phase 4 `tgwlogs` extfs VFS (read-only `journalctl`-per-worker; guard unit-name injection, cap output) + **first headless extfs CLI-contract test**. **Reconcile: Phase 2 is DONE** (448-line `tgwitem` committed) — the §1144 subsection still shows it open. | S |
| 21 | PP-REPRICER-001 | Read-only foundation only: `market_data` provider interface (`OwnSalesProvider` from velocity-stats.json, `BrowseCompsProvider` from item `ebay_offer.price_comps`, `MarketplaceInsightsProvider` stub) + `recommend_price()` floored by the reprice move price + `tgw reprice-suggest [SKU\|--all]` dry-run + tests. **No eBay write** (live push + insights scope stay blocked). | M |
| 22 | PP-SHELL-001 | Bring `tgw.source`/`tgw-dev.source` under version control (copy into `etc/interfaces/shell/`) + apply the WRAP tier: replace the 6 ARCH-VIOLATES fns with `tgw <subcmd>` one-liners (all CLI equivalents now exist) + fix the `ic_test()` artifact. **Deliver as reviewable in-repo copy; do NOT mutate the live `/opt/TGW/bin` file** — cutover is operator-controlled. | M |
| 23 | PP-VISION-001 | Offline visual-fingerprint index over the 54K existing thumbnails (Pillow+numpy phash/histogram, dependency-free) + `tgw locate <image> [--size-class]` ranked-SKU output; index build is a **batch job** (catalog-rebuild-is-a-job rule). Baseline precision — frame as a workflow proof, not a final CLIP matcher. | M |
| 24 | PP-MC-002 | Satellite-capable extfs refactor: env-driven DSN/paths (`TGW_NODE_ROLE`/`TGW_HTTP_BASE`) + a `role=satellite` branch routing writes to tgw-http instead of psycopg2. Default `role=master` (preserve current behavior), gate behind env vars, test the data-source helper. Real LTSP/hardware rollout stays operator-gated. | M |
| 25 | PP-NIXOS-001 | Author `flake.nix` + `nix/tgw.nix` from existing `pyproject.toml`/`install.sh`/systemd units. ⬆ **NixOS now COMMITTED TARGET** (session 16) — active migration prep, not just evaluation. PP-DEPLOY-001 MX image = final safety-net image before cutover. Cannot build/test here (no nix toolchain) — produce files, Dave validates in VM. | M |

### Track 1 — Round 3 (session 16)

**Guiding principle:** Build time-saving interfaces usable now, especially on tablet. Maintain stability. Build better later. NixOS is the committed destination — prepare the path, don't block on it.

**Decisions confirmed (session 16):**
- NixOS = committed target. PP-NIXOS-001 promoted from evaluation to active prep.
- PP-DEPLOY-001 MX image = one final restore image as safety net before migrating.
- PP-BULKEDIT-001 = #1 priority. Tablet-usable: web UI via browser + Termux SSH fallback.
- ISS-009 downgraded — production keyset active; `tgw restart-ebay-token` if token dead-lettered.

**Active task list:** ✅ **ALL 8 DONE** (todo IDs 21–28 closed, session 17 — see `### Execution — 2026-06-07 (session 17 …)` above for the per-item summary). The todo tracker is the canonical queue. Next: a `/code-review` pass once the session limit resets, then pick the next batch (blocked items below remain operator/research-gated).

### Track 1 — Round 4 (session 18)

**Guiding principle:** Mix of pipeline hygiene, new operator-facing capability, and NixOS prep. Keep building usable things now; prep the spare machine path.

**Input to this round:**
- Round 3 all 8 DONE; 321 tests passing; git clean.
- 27 dead_letter jobs (all from 2026-06-02/03 pre-fix era) — need triage.
- `tgw todo claude` empty — seeded below.
- PP-PLASMA-001 + PP-PORTABLE-CATALOG-001 never got formal plan sections (added below).

| # | PP | Task | Size |
|---|----|------|------|
| 29 | — | Dead_letter triage: cancel 27 stale pre-fix jobs; add `tgw dead-letter --requeue-transient` flag to batch-requeue all transient-classified entries in one shot; re-enqueue 6 `no ebay_category_id` items through ai_identify | XS |
| 30 | PP-REF-003 | Author `reference/TGW-Quickstart.md`: all `tgw` CLI subcommands + workers + web forms + MC VFS + Qtile chords organised by workflow (health→intake→pipeline→eBay→admin); stubs for physical processes; replaces hunting through plan for command syntax | M |
| 31 | PP-VISION-001 P1 | Offline phash/histogram fingerprint index over 54K thumbnails (Pillow+numpy, no external deps); batch build job (`catalog-rebuild` rule); `tgw locate <image> [--size-class S]` CLI returns ranked SKU matches; index stored in SQLite catalog | M |
| 32 | PP-PORTABLE-CATALOG-001 P1 | Add plan section (design below); `tgw export-catalog <dest>` command: copies `tgwcatalog.db` + thumbnails subset to destination path; no Syncthing API needed for Phase 1 — Syncthing handles transport; lays groundwork for spare machine client setup | S |
| 33 | PP-PLASMA-001 | Add formal plan section (missing since session 16 suggestion); design notes for Plasma 6 + Qtile dual-desktop; no code this round — design/tracking only | XS |
| 34 | PP-TODO-001 | `GET /form/todos` in tgw-http: tablet-friendly HTML table of open todos grouped by agent; auth-gated (Bearer or network-trust like `/form/intake`); low-friction daily queue review from tablet/phone | S |
| 35 | PP-NIXOS-001 | Update `flake.nix` + `nix/README.md`: configure `NVM_DIR=/opt/TGW/.nvm`, `NPM_CONFIG_PREFIX=/opt/TGW/.npm` so nvm/npm install under `/opt/TGW/` when operator runs nvm install; ensures `/opt/TGW` is a fully self-contained imageable entity with no tgw home-dir dependencies | XS |

#### Execution — 2026-06-08 (session 18, Track 1 Round 4 — ALL 7 items DONE)
Built largely via a parallel build workflow (5 file-isolated agents) + main-loop wiring for the
shared `api.py`/`config.py`/completion surface. Suite **321 → 346** (+25), ruff clean, `tgw health`
green. ⚠ Sub-agent **session limit** + transient socket errors killed the adversarial-review workflow
(same constraint as session 17) — review was done **in the main loop** instead (Opus 4.8), with live
end-to-end probes. Per item:
- **#29 `dead-letter --requeue-transient`** — batch re-enqueues every `[transient]`-classified
  dead_letter job (honours `--queue`), via the existing `requeue_dead_letter_job` + `classify_dead_letter`.
  5 tests (`test_dead_letter.py`). **Live triage run:** 2 transient requeued; 5 now-fixable
  "no ebay_category_id" items (categories since populated) re-driven through `ebay_draft` (NOT
  ai_identify — would overwrite good categories); 2 stale `pm_intake` lease-expired orphans cancelled.
  Board **27 → 23**. The remaining 23 are **real eBay rejections** (25709×8, 25002 Item.Country×3
  [tracked known issue], 25738×2, 25021×1) + superseded `ebay_draft` records — left for operator
  review, deliberately **not** mass-cancelled (would hide real signal). Once the re-driven jobs
  clear, `tgw dead-letter --cancel ebay_draft` clears the superseded records.
- **#30 PP-REF-003** — `reference/TGW-Quickstart.md` (9 sections, every `tgw` subcommand cross-checked
  against `api.py` add_parser names; MC/Qtile/macroboard key maps; worker table; physical-process stubs).
- **#31 PP-VISION-001 Phase 1** — `src/tgw/fingerprint.py` (Pillow-only dHash + joint-RGB histogram;
  index in `fingerprints.db`; 64-bit dhash stored as TEXT to dodge SQLite signed-int overflow).
  `tgw build-fingerprints` (batch build, `build-thumbnails` precedent) + `tgw locate <image>
  [--size-class --top --json]`. 8 tests. **Full index built: 54,314 rows in 87s**; self-match
  verified distance 0.0000. ⚠ **`--size-class` filter is a no-op until items carry `size_class`** —
  0 of 83,520 catalog rows have it (set-template hasn't populated it at scale; enrichment + SKU
  match verified working). New config key `fingerprint_index_path`.
- **#32 PP-PORTABLE-CATALOG-001 Phase 1** — `src/tgw/catalog_export.py` `export_catalog()` +
  `tgw export-catalog <dest> [--no-thumbnails --limit --check-only]`; copies `tgwcatalog.db` +
  thumbnail subset for Syncthing relay. 8 tests. Live verified (179 MB / 83,520-row db copies clean).
- **#33 PP-PLASMA-001** — formal plan section added (dual-desktop Qtile+Plasma 6; NixOS declares both).
- **#34 PP-TODO-001** — `GET /form/todos` in tgw-http: tablet-first HTML todo dashboard grouped by
  agent, no Bearer (network trust, like `/form/intake`), `html.escape` on all fields, graceful
  200-on-DB-error. 4 tests (`test_http_server.py`).
- **#35 PP-NIXOS-001** — `nix/tgw.nix` `commonService.environment` now sets `HOME`/`NVM_DIR`/
  `NPM_CONFIG_PREFIX` under `/opt/TGW` + tmpfiles for `.nvm`/`.npm`/`.venvironments` (propagates to
  every worker + tgw-http + backup via the verified `recursiveUpdate` merge); `nix/README.md`
  home-dir-independent section; `flake.nix` devShell note. No nix toolchain on host → review-only.

⚠ **COMMIT REMINDER** (untracked, will break features if not `git add`ed): `src/tgw/fingerprint.py`,
`src/tgw/catalog_export.py`, `tests/test_fingerprint.py`, `tests/test_catalog_export.py`,
`tests/test_dead_letter.py`, `docs/TGW-Plan-Vault/reference/TGW-Quickstart.md` (+ modified
`src/tgw/api.py`, `src/tgw/config.py`, `src/tgw/http_server.py`, `tests/test_http_server.py`,
`etc/completion/tgw-completion.bash`, `nix/tgw.nix`, `flake.nix`, `nix/README.md`).

**Follow-ups surfaced this session:**
- `size_class` is virtually unpopulated (0/83,520) → `tgw locate --size-class` + PP-STORAGE-001
  resolver are inert until set-template adoption grows or a backfill runs. Candidate: a
  `size_class` backfill from `category_group` defaults (category-groups.json has per-group size_class).
- 23 real eBay-rejection dead-letters (25709/25002/25738/25021) need item-data/code fixes —
  25002 Item.Country is the tracked open issue at `## Current state` line ~83.

### Inbox processing — 2026-06-10 (session 19/20)

8 queued inbox files processed + 20 SUGGESTIONS.md items marked done. Key findings:

**GEMINI-003** — Flutter app scaffold (Phases B+C+D) delivered by Gemini. Full app at
`apps/tgw_app/`: Riverpod state, Dio HTTP, sqflite DB, all screens (SKU list, scan, detail,
edit stubs). `flutter analyze` clean. Build environment needs `libsecret-1-dev` (Linux) and
Android SDK licences; expected. Phase D edit flows disabled in UI (stubs visible, not wired).
⚠ **BACKEND-NEEDED**: app polls `/api/queue/status` for connectivity; a proper `/api/health`
JSON endpoint is the right contract → added as todo #37.

**GEMINI-004** — Multimodal photo QA on 20 items. ⚠ **Critical finding**: boilerplate
contamination in `description_history` — text from "John F. Rider Perpetual
Troubleshooter's Manuals" (electronics service manual series) injected into items across
diverse categories (confirmed on SKUs `tgw201501021970398`, `tgw201501021970498`,
`tgw201501021970953`; likely more). Probable cause: batch description import or AI prompt
bleed-through. Data scrub needed → added as Round 5 item. Alt-text pilot: Ollama/Gemini
vision can generate useful captions and SEO fields from item photos → added as todo #38.
Alt-text sidecar naming convention: `<SKU>-alt.jpg` (confirm with Dave whether this is a
renamed secondary image or an annotated derivative before implementing).

**GEMINI-005** — Pricing calibration. 3 concrete `category-groups.json` edits recommended:
- `electrical_fixtures`: `typical_used` 15.43 → **12.50** (align with market p25)
- `media_records`: `typical_used` 12.03 → **13.50** (increase to capture value)
- `collectibles_pins_buttons`: `typical_used` 9.72 → **10.50** (increase to capture value)
Run `tgw category-groups --reseed` after. Added as Round 5 items #40–41.

**GEMINI-006** — Marketing/category insights. Top zero-inventory high-velocity categories
(ST=1.00, 0 active): Sewing Buttons, Network Cards, Heavy Equipment Manuals, Lapel Pins,
Locomotives, Collectible Magazines. Store category mappings: `tools_hand`→"Tools & Workshop
Equipment", `electronics_adapters_chargers`→"Power Adapters & Chargers",
`electronics_remotes`→"TV, Video & Home Audio Accessories", `kitchen_utensils`→"Kitchen
Tools & Gadgets". Priority quality improvements: Headphones, Flashlights, Wrenches.

**PERPLEXITY-005** — Full 36KB result processed (`PERPLEXITY-005-result.md`). PP-PYIPC-001
is now research-complete. Key decisions: `pyncthing` + custom `httpx` `/rest/events/disk`
consumer for Syncthing; `pydbus` + `kdeconnect-cli` for KDE Connect; psycopg3 migration
path clear; `aiosqlite` for FastAPI catalog reads; `discogs_client` deprecated → wrap in
adapter; EasyPost for shipping rates (PirateShip has no public API); `python-evdev` for
barcode scanners; `hidapi`/`hid` for USB scales; Go-UPC/Apify for enrichment upgrades.
See PP-PYIPC-001 section for full findings.

**PERPLEXITY-006** — Flutter offline-first sync pattern (full research). Key design
decisions for PP-PORTABLE-CATALOG-001 P2:
- Syncthing + SQLite: safe only with one writer; clients must treat catalog as a closed artifact
- **Pattern**: snapshot + copy to app-private storage; open the copy, never the synced file directly
- **Stack**: `sqflite` + `sqflite_common_ffi` (Linux); `sqlite3` package (sqlite3_flutter_libs deprecated)
- **HTTP**: `dio` + `dio_smart_retry` (successor to abandoned dio_retry)
- **Offline queue**: roll own outbox SQLite table (states: pending/sent/ack); avoid black-box plugins
- **Connectivity**: `connectivity_plus` + health-ping check; `workmanager` for Android background flush
- **Flutter secure storage**: requires `libsecret-1-dev` on Linux
- **Server-side snapshot**: `sqlite3.Connection.backup()` for atomic export
See PP-PORTABLE-CATALOG-001 Phase 2 design below.

**syncthing-nixos-nginx-research.md** — Complete NixOS Syncthing deployment design:
- Isolated headless `tgw` user instance on port 8385/22001; regular users use 8384/22000
- NixOS declarative `services.syncthing`; per-hostname config via config dir symlink (LTSP fat clients)
- Nginx reverse proxy with `insecureSkipHostCheck` + WebSocket headers
- Auto-TLS: systemd oneshot generating self-signed cert before nginx starts
- GUI access from dev machine: `ssh -L 9000:127.0.0.1:8385 user@server`
See PP-NIXOS-001 → Syncthing deployment section.

**system-app-config-and-nixos-flake-design.md** — NixOS multi-tier flake architecture:
`flake-parts` framework; modules: `bases/master.nix` + `bases/portable.nix`, `interfaces/cli.nix`,
`graphical/tiled.nix` + `plasma.nix` + `thin-client-rdp.nix`, `ai/compute-node.nix`.
LTSP fat clients share NFS `/nix/store`; Ollama model weights on NFS mount (not in initrd).
Separate dev flake: `nix develop ./dev-env`. See PP-NIXOS-001 → Flake architecture section.

**Dead-letter triage 2026-06-09 (between sessions 18/19)** — 744 `ai_identify` dead-letters
from Ollama HTTP 500 crash (~2026-06-08) reset via `tgw dead-letter --requeue-transient`.
Root cause: `batch_size` config key missing from `TgwConfig` in `config.py` → `ValidationError`
on load after config update. Fixed: `batch_size: int = 1` added to `TgwConfig`. Worker
restarted; recovery confirmed.

**eBay Developer Support (Track 4)** — eBay responded to the `buy.marketplace_insights`
scope request with 8 questions Dave must answer. See Track 4 Priority 1 for the response
action item.

**Blocked — not Round 4 (held for later rounds):**
Same blocker groups as Round 3 plus:
- PP-PLANDB-001 — design discussion needed before code; currently design-open
- PP-PORTABLE-CATALOG-001 P2+ — PP-PYIPC-001 ✅ done; Syncthing API + PERPLEXITY-006 result both available; **unblocked as of 2026-06-11**
- PP-VISION-001 P2+ — CLIP/embedding model requires GPU upgrade

#### Blocked — not Claude-ready (grouped by blocker)
- **Operator / host-level ops** — `PP-REMOTE-001` (Tailscale + tmux + SSH hardening + sudoers + claude-user decision); `PP-DEPLOY-001` full epic (usermod UID<1000, recursive chown, image bake, fresh-restore reboot — only the read-only audit check #16 is ready). *Unblock:* operator does the host work, then Claude can add reviewable config (tmux launcher, OSC52 helper).
- **Hardware / physical device** — `PP-MACRO-001` install+prove needs a 2nd keyboard, live desktop, and `keyd list-devices` hash (a static drift-validation test is the only code-only slice, and it doesn't advance the goal). *Unblock:* operator wires the dedicated keyboard + captures the `[ids]` hash.
- **Android device + push creds** — `PP-TASKER-001` (Tasker/Join apps, barcode-intent audit; even a server-side push backend needs an operator ntfy topic / Join key to validate). *Unblock:* operator audits phone intents + supplies a push URL/key.
- ~~**External research + creds/services** — PP-PYIPC-001~~ **✅ UNBLOCKED**: PERPLEXITY-005 research complete; Syncthing is live at `127.0.0.1:8384`; API key in `/opt/TGW/.local/syncthing/config.xml` (in-project). Libraries decided: `pyncthing` + custom `httpx` events consumer; `pydbus` for KDE Connect. No operator action needed — PP-PYIPC-001 is Claude-ready.
- **Design-open / architecture decision** — `PP-REVISION-001` live-listing revision. `ReviseFixedPriceItem` exists (`trading.py:266`) but only for SKU-label changes; `ebay_stage.py:6` calls itself "the stopgap until the full revision system is built." Open question (sparse-delta vs full-replacement) + depends on PP-SYNC-001 being authoritative + live token for any push. *Unblock:* Dave settles the delta-vs-replacement design; then a dry-run delta computer is a buildable first slice.

#### Stale-done reconciliation (doc-only corrections — several bundled into the slices above)
Audit caught the plan crediting shipped work as open and missing shipped tools. Corrections:
- **PP-MCP-001** — 10 MCP tools shipped (`tgw_dead_letter` added); plan table (≈L2139) lists 9. Registration-path docstring drift. *(bundled into rank 3)*
- **PP-SOLD-001** — Tier 3 `ebay-sweep` (`api.py:1517`) + Tier 4 webhook (`notifications.py` + `http_server.py:714` + `tgw setup-ebay-hooks`) are **DONE**; plan calls them "pending/future" (≈L831–842). *(bundled into rank 2)*
- **PP-VERIFY-001** — Phase 2 **DONE** with 27 passing tests + ~13 rules; plan marks it "Next" and claims 10 tests / 9 rules. "catalog-rebuild resets the hall pass" is moot — `catalog.py` never references `catalog_verified`. *(bundled into rank 19)*
- **PP-MC-001** — Phase 2 **DONE** (448-line `tgwitem` committed); §1144 subsection still shows it open. *(bundled into rank 20)*
- **PP-INTAKE-001** — P1 & P2 **DONE** (committed); §1601 says "to build". §1612 claims the template writes `fulfillment_policy_id` — **it never does; strike it.** *(bundled into rank 4)*
- **PP-CAPTURE-001** — `suggest`/`note`/`btw` + `suggest-edit` **DONE**; plan calls them "planned". *(bundled into rank 11)*
- **PP-HINT-001** — requeue / Browse enrichment / hint-trail / `hint --force` **DONE**; only `shipping_profile` remains. *(bundled into rank 8)*
- **PP-FULFILLMENT-001** — plan line ≈2208 falsely states `tgw picklist` exists. **Active landmine — corrected inline** + rank 7 builds the real command.
- **PP-GLOBALS-001** — "wait for PP-INTAKE-001 P2" (≈L1106) is satisfied/stale; the intake form already captures `weight_oz`. *(bundled into rank 1)*
- **PP-STRIKE-001** — core code **DONE**; Track-1 table + planning text said "Planned". *(bundled into rank 6)*
- **ISS-006** — `_USER_PROMPT_ENRICHED` is fully wired (`ai_identify.py:171–204`); issue is stale → **closed in ISSUES.md**.

### Track 1 — Round 5 (session 19/20)

**Guiding principle:** Process Gemini inbox findings into code. Address data quality issues.
Pipeline hygiene + Flutter backend gap.

**Input:** Round 4 all 7 DONE; 346 tests passing; 4 open todos (#36–39); 8 inbox files processed.

**Session 21 progress:** #36 DONE (433 tests passing; 121 items backfilled via `ebay_category_id`; catalog_rebuild enqueued). Todos remaining: #37–39 + #47–49.

**Session 22 progress:** #37 DONE (439 tests passing). Todos remaining: #38–39 + #47–49.

**Session 23 progress:** #38 DONE (455 tests passing). Todos remaining: #39 + #47–49.

| # | PP | Task | Size |
|---|----|------|------|
| ✅ 36 | PP-STORAGE-001 | `size_class` backfill — `tgw data-scrub --pass 2 [--write]`; 121 items populated via `ebay_category_id` reverse map; catalog_rebuild enqueued; 13 new tests (`test_scrub.py`); suite 433 — **DONE session 21** | S |
| ✅ 37 | PP-EDITOR-001 | `GET /api/health` — Bearer-auth; mirrors `check_all()` JSON + `dead_letter_count`; HTTP 503 on failure; 6 new tests; suite 439 — **DONE session 22** | S |
| ✅ 38 | — | `tgw alt-text <sku> [--model MODEL] [--dry-run]`: Ollama vision → `alt_text` + `seo_caption` in `draft_listing`; original photo archived to `data/history/ItemData/<sku>/` if not there; production photo renamed to `<sku>-alt.jpg`; 16 new tests (`test_alt_text.py`); suite 455 — **DONE session 23** | M |
| ✅ 39 | — | ~~Fix 25002 `Item.Country` dead-letter rejections~~ — **RESOLVED 2026-06-11 (ISS-001 closed)**: `availabilityDistributions` + `merchantLocationKey` fix (session 9) confirmed working; originally-affected items live via Inventory API. Session-23 25002-lookalikes were item-specifics validation errors on an already-live Trading-API item; all 15 stale dead-letters cleared | S |
| 40 | — | `category-groups.json` pricing calibration (GEMINI-005): update `electrical_fixtures`→12.50, `media_records`→13.50, `collectibles_pins_buttons`→10.50; run `tgw category-groups --reseed` | XS |
| 41 | — | `category-groups.json` store categories (GEMINI-006): populate `store_category` for `tools_hand`, `electronics_adapters_chargers`, `electronics_remotes`, `kitchen_utensils` | XS |
| 42 | — | Data scrub: scan `description_history` for "John F. Rider" and generic boilerplate contamination (GEMINI-004); report affected SKUs; strip contamination strings | S |
| 43 | PP-FULFILLMENT-001 | Standard Envelope constraint (≤0.25 in thick, uniform): wire into `_resolve_fulfillment_id()` as a size/category gate; add note to CATEGORY-QUIRKS.md | S |
| ✅ 44 | PP-CAPTURE-001 | `GET/POST /form/suggest` — punctuation-safe suggestion web form; plain HTML (no JS), network-trust like `/form/intake`; reuses `cmd_suggest()`; whitespace collapsed to keep one checklist line per entry; 5 tests; suite 480 — **DONE session 24 (uncommitted, pending review)** | S |
| 45 | — | `TGW-Quickstart.md` pipe examples: add `--skus-only` / stdin `-` / multi-SKU patterns; note `tgw enqueue-sku` queue-first path | XS |
| 46 | — | Ledger ops-query ergonomics (from runbook work 2026-06-10): `queue_job_history` has no `queue_name` (per-queue history needs `JOIN queue_jobs USING (job_id)`) and uses `created_at`; job columns are `payload_json`/`error_code`/`error_detail` (not `payload`/`last_error`). Fix: add SQL views to `queue/schema.sql` (e.g. `v_dead_letters`, `v_job_history` with queue_name) and/or a `tgw queue history` subcommand so operators stop hand-writing joins; `reference/runbooks/` already uses the correct join form | S |
| ✅ 47 | PP-SHELL-001 | **DONE 2026-06-11 (session 23).** Canonical hyphenated names adopted; deprecated aliases kept. `tgw search TEXT` added. Quickstart updated. Key findings: (1) `statusupdate VALUE SKUS...` — value-first is intentional for multi-SKU; kept as-is, documented. (2) `enqueue-sku QUEUE SKUS...` — queue-first is correct (you target a queue, not an item); quickstart was wrong and is now fixed. (3) `ebay-pull` has no scoping — deferred (needs design). (4) Nested-field CLI writes → HTTP PATCH / MC extfs path (PP-CONTEXT-001, not CLI). (5) `requeue` is ai_identify-only but generically named — leave for PP-SHELL-001 Tier 3. Canonical rename table: `titleupdate`→`update-title`, `locationupdate`→`update-location`, `verifiedupdate`→`update-verified`, `statusupdate`→`update-status`, `setshipping`→`set-shipping`, `whispertosuggest`→`whisper-suggest`. | M |
| ✅ 48 | PP-CONTEXT-001 | **DONE 2026-06-11 (session 23).** `tgw set-context <sku>` / `tgw get-context [--sku-only]` / `tgw clear-context`. Primary store: `runtime/state/current-item.json` `{sku, set_at, set_by}`. Compat symlinks (`/opt/TGW/CurrentItem`, `CurrentItem.json`) maintained atomically via temp+os.replace. Legacy symlink fallback preserved in `get-context`. `tgw_sku` → `tgw get-context --sku-only`. `tgwset` → `tgw set-context`. `set-template` updated to use `context.current_sku(cfg)`. 20 tests in `test_context.py`. `CurrentLocation` dropped (derive location from SKU via `tgw resolve`). | M |

### Track 1 — Round 6 (session 24)

**Input:** Round 5 fully drained except rows 40–43/45 (seeded as todos session 24). Suite 480.
Lint-policy incident (session 24): bare `ruff check` mutated 8 files because pyproject set
`fix = true` — root cause removed; see #49.

| # | PP | Task | Size |
|---|----|------|------|
| ✅ 49 | — | Lint policy hardening — **DONE session 24**: `fix = true` removed from pyproject (a bare `ruff check` must never mutate the tree; fixes are explicit via `ruff check --fix`); `systemd/history/` excluded (archived dead scripts, not lint-gated); the 8 pending isort autofixes kept and committed separately from feature work | XS |
| ✅ 50 | — | `tools/migrate_batch.py` **DONE session 26** — archived to `tools/archive/migrate_batch.py`; superseded by `ebay_sku_migrate` worker; added `tools/archive` to ruff exclude | S |
| ✅ 51 | — | `tools/repair_itemdata_json.py` **DONE session 26** — fixed Python 3.11 f-string backslash (lambda rewrite); removed unused `nxt`; ruff clean | XS |
| ✅ 52 | PP-DOCFLOW-001 | **Design session HELD 2026-06-11 (session 24)** — all four open questions settled by Dave; design recorded below; Phase 1 build seeded as todo | M |
| ✅ 53 | PP-DOCFLOW-001 | **Phase 1 build DONE 2026-06-11 (session 25)**: pm_intake ported to `call_model()` + `tgw-models.json` → `openrouter/google/gemini-2.5-flash`; actions: `no_change \| append_to_section \| file_document \| flag_for_review`; `new_section` demoted to review-flag; 4h submission-delay gate + `tgw admin-file [--now]`; `reference/FILING-LOG.md` audit trail; `inbox/review/` + `dev-workflow/research/` dirs; `pm_intake_delay_hours` config key; 19 offline tests | M |
| ✅ 56 | PP-DOCFLOW-001 | **Phase 2 DONE 2026-06-11 (session 26)**: `tgw.suggestions` module — `parse_pending()`, `classify_batch()` (1 LLM call via `suggestions_classify` → openrouter/gemini-2.5-flash), `apply_classifications()`, `format_report()`; `tgw classify-suggestions [--apply] [--limit N]`; dry-run default; `already_done` entries marked `[x]` on `--apply`; `todo` entries create DB todos; `plan_append`/`review_flag` report-only. 16 offline tests. | M |
| ✅ 57 | PP-PYIPC-001 | **DONE 2026-06-11 (session 26)**: `tgw.apis.syncthing` — `_parse_api_key()` from config.xml, `folder_status()`, `folder_is_idle()`, `list_folders()`, `scan_folder()`, `disk_events()` long-polling generator; `tgw.apis.kdeconnect` — `list_devices()`, `get_device_id()`, `ping()`, `send_text()`, `send_file()`, `push_clipboard()` via kdeconnect-cli; `syncthing_config_path`/`syncthing_url` config keys; `pyncthing>=0.1` in pyproject.toml; 25 tests | M |
| ✅ 58 | — | **`tgw history-index` DONE 2026-06-11 (session 26)**: `tgw.history_index` module; `index_archive_unindexed()` scans ~32K legacy Magento zips not in `archive-ebay-index.json` → `var/history-itemdata-index.jsonl` (sku/title/location/status/price/condition); `index_loose_csvs()` parses eBay-OrdersReport-*.csv → `var/history-loose-csv-index.jsonl`; `tgw history-index [--target ItemArchive\|loose-csv\|all] [--dry-run] [--limit N]`; smoke-tested production (54,683 zips); 13 tests. Run `tgw history-index --target all` in a screen session to populate | M |
| ✅ 54 | PP-BACKUP-001 | **Phase A build DONE session 25**: `tgw-db-backup` + `tgw-cloud-sync` + `tgw-secrets-backup` scripts + systemd units/timers in `etc/systemd/`; `check_backups()` in health.py + tests — **scripts exist, operator must install** | M |
| 55 | PP-BACKUP-001 | **Phase A operator items** (todo #61): approve plan ✅ done; remaining: gpg passphrase custody decision (off-machine!); install+enable the three timers; first manual cloud sync in an off-hours window; `rclone about dbukove:` quota check; A5 restore drill + record RTO times | M |

### Track 1 — Round 7 (session 28, 2026-06-12)

**Produced by a full docs-tree gap analysis — see `plan/PLAN-round7-platform-gaps.md`**
(the reference spec for this round: what was designed-but-unbuilt, noted-but-never-planned,
and newly proposed). 14 Claude tasks + 2 Gemini/Antigravity tasks + 6 operator items seeded
into the tracker 2026-06-12 with `--source round7`. Highlights: sync-conflict resolution
worker (zero-data-loss), Ready state + rate-limited dole-out, AI usage ledger (cost per
item), alt-text batch via OpenRouter, computer-side intake, picklist/label PDFs, Taxonomy
category validation, `tgw report sales`, PP-PROMO-001 (new — markdown sale events on the
held `sell.marketing` scope, design-first). Three tasks are Aider-eligible.

**All four reserved discussions held + decided with Dave 2026-06-12 (session 28):**
PP-REVISION-001 = **sparse delta + pinned baseline** (drift gate at apply; dry-run delta
computer first); PP-PLANDB-001 = **Option C generated taskboard** (companion file
`plan/TGW-Taskboard.md`; DOCFLOW admin is the single write-gateway — Dave submits via
inbox/suggest only); PP-CLIP-001 = **dual-backend watcher** (X11 stable now, Wayland
first-class via `wl-paste --watch`; build after Qtile install); Aider = **committed** (amended
2026-06-12: used even with Antigravity as primary agent/agent manager; Antigravity-first trial
week stands as routing calibration). Decisions recorded in the respective PP sections +
next-process.md; follow-on todos #109–#118 seeded.

### PP-DOCFLOW-001 — The TGW Project Admin (LLM document + suggestion intake)

**Status: PHASE 1 + PHASE 2 COMPLETE 2026-06-11 (sessions 25–26). Phase 3+ (admin skills expansion) is future scope.**

**Mental model (Dave, session 24):** model this tool as a **real-life project admin** — the
best ones always have the plan ready to be worked on: all docs filed and readily available,
**cross-indexed to the appropriate tasks**. Ours will just be better. When we move to
planning, everything — thoughts, notes, files, binaries — is collected and easily
accessible. It is an admin function, but a *knowledgeable* admin: it knows where or what a
doc is.

**Decisions (Dave, 2026-06-11):**
1. **Evolve pm_intake in place** — same worker/queue/unit, ported to the session-23
   dispatcher (`tgw.apis.llm.call_model`), action vocabulary extended. Compute is no
   longer a constraint: route to fast capable Gemini, "go overboard" — well under $1/mo
   at classification-prompt sizes. (Note: pm_intake is currently **enabled + active** on
   local Ollama — verified session 24; the remembered disable-to-reserve-compute is not
   in effect.)
2. **Review surface = the admin pattern**: filed docs land in the right vault location;
   anything uncertain goes to `inbox/review/` + a todo pointing at it. Any fall-through
   is cleaned up in the normal session-start ritual (the existing safety net).
3. **Trigger model — batched, not continuous:**
   - **Auto-run as planning prep** (before a planning/Claude session) and
   - **manually triggerable** (`tgw admin-file`) — e.g. after dumping a stack of research.
   - **Submission-delay window**: items must age N hours before absorption — gives the
     human submitter a chance to correct a hasty submission *before group resources are
     spent on it* (manual trigger can override with `--now`).
4. **Suggestions: batched at session start** (Phase 2) — the admin pre-classifies;
   Claude reviews dispositions instead of raw entries.
5. **Plan writes: append-only.** The cloud model may `append_to_section`; `new_section`
   and anything structural becomes a review flag. (This *tightens* current pm_intake,
   which can create sections today.)

**Scope notes from the session:** intake accepts anything — "a one word comment or a
folder full of docs and binaries." Binaries (photos, PDFs, zips) are in scope: filed by
type/context (the dispatcher already supports vision for image classification when
needed — later phase). Cross-indexing means filed docs get index entries linking them to
the relevant PP-* items / tasks, so planning sessions start with material attached.

**Phase 1 (MVP — build next; seeded as todo):**
- Port `pm_intake` from direct `ollama.chat()` to `call_model('pm_intake', ...)`;
  set `tgw-models.json`: `pm_intake → openrouter / google/gemini-2.5-flash`
  (Ollama stays the automatic fallback — frees CPU for vision/pipeline).
- Extend actions: `no_change | append_to_section | flag_for_review | file_document`
  (`new_section` demoted to a review flag per decision 5).
- `file_document`: move the file **verbatim** (never reflow) to
  `reference/` / `perplexity/` / `dev-workflow/research/`; append an entry to a filing
  log/index (`reference/FILING-LOG.md`: date, source, destination, related PP-*, model,
  confidence); optional one-line plan pointer (append-only).
- `flag_for_review`: move to `inbox/review/` + create a todo (agent claude or dave).
- Submission-delay gate (mtime-based, configurable, e.g. 4 h) + `tgw admin-file [--now]`
  manual trigger.
- Audit trail on every action (the `identification_history` pattern).

**Phase 2:** suggestions join the path — session-start batch pass pre-classifies
unprocessed SUGGESTIONS.md entries into todo / plan-append / review-flag dispositions for
Claude's review. Cross-index todo ↔ filed-doc links.

**Phase 3 (later):** binaries with vision classification; whole-folder submissions as one
unit; Antigravity batch jobs for large backlogs; **URL/URI submissions** (Dave 2026-06-11
18:25 — pm_intake accepts a link, fetches the content, files/classifies it like a doc).

**Admin skills expansion (Dave, 2026-06-11 18:23 — Phase 3+/4 scope):** like a real-life
admin, the project admin should also handle **presentation and aggregation**: spreadsheets,
charts, SKU groupings too complicated for the generic tgw filters, topic summaries,
research consolidations, basic project documentation work — on request ("even you could
request topic summaries"). Builds on the same dispatcher; routes to large-context
providers (Gemini/Antigravity) per PP-MULTIMODEL-001. Constraint: outputs are *artifacts
filed in the vault* (reports, sheets), never direct writes to curated data
(PP-REVISION-001 governing principle).

**Invariants:** writes only inside the plan vault (pm_intake's existing rule); originals
never destroyed (move, never rewrite-in-place; `processed/` archive retained);
flag-don't-guess on low confidence; plan writes append-only.

### Track 2 — Gemini CLI (large-context data + self-contained tasks)
**Status 2026-06-10 update**: Google One → **Google AI Plus** with compute-based limits (5-hour
refresh window). Keep individual Gemini tasks small and self-contained to avoid hitting the
compute cap. Also available: **Antigravity/Flow** ✅ CLI configured + v2.0 installed (2026-06-11).

**Antigravity CLI + OpenRouter config (inbox research 2026-06-11):** `agy` reads `~/.gemini/antigravity-cli/settings.json`. Add OpenRouter as a custom provider:
```json
{ "llm_providers": { "openrouter": { "base_url": "https://openrouter.ai/api/v1", "api_key": "YOUR_KEY", "default_model": "openrouter/free" } } }
```
Google Drive access via: (a) Google Workspace MCP (add to `~/.gemini/config/mcp_config.json`), or (b) rclone mount at a local directory (`cd ~/mnt/gdrive && agy`). The rclone approach is simpler since ItemData is already synced to GDrive. Both methods confirmed working in `agy` CLI.

**How to delegate to Gemini**: Write a self-contained task file with all needed context
baked in (no live system access). Drop data excerpts, schemas, and the task description.
Save result to `inbox/` for PM-intake.

| Task | Give Gemini | Expect |
|------|-----------|--------|
| ✅ PP-VERIFY-001 scaffold | done (session 13) — scaffold superseded by full Phase 1 implementation |
| ✅ Data scrub analysis | done (GEMINI-002, session 14) — completeness matrix, stall patterns, legacy scrub rules; 3 new verify rules implemented |
| ✅ Category-group quality review | done (GEMINI-001, session 14) — `electrical_industrial` split; ai_hints improved; `tools_hand` coherence noted |
| ✅ Flutter app scaffold | done (GEMINI-003, session 19) — Phases B+C+D at `apps/tgw_app/`; analyze clean; libsecret-1 build dep; BACKEND-NEEDED /api/health endpoint (todo #37) |
| ✅ Photo QA + alt-text pilot | done (GEMINI-004, session 19) — boilerplate contamination finding; alt-text viable via Ollama vision; sidecar naming confirmed |
| ✅ Pricing data analysis | done (GEMINI-005, session 19) — 3 calibration edits (see Round 5 #40); reseed reminder; tier pattern notes |
| ✅ Marketing/category insights | done (GEMINI-006, session 19) — store category mappings; zero-inventory high-velocity list; SEO keyword opportunities |
| ✅ TGW camera app design | done (gemini todo #115, session 29) — full Flutter scaffold proposal at `reference/PP-INTAKE-002-camera-app-design.md`; mobile_scanner + flutter_tts + Riverpod + Foldio360 root bypass + BLE direct control; Dave reviews before build |
| ✅ xmouse replacement design | done (gemini todo #116, session 29) — Flutter architecture survey at `reference/PP-INTAKE-003-xmouse-replacement-design.md`; flutter_rfb (Apache-2.0 VNC) + dartssh2 + flutter_inappwebview; 3-phase roadmap; Dave reviews before build |
| ebay_draft aspect fill audit | Grep of aspect fill rates per category | Which categories have worst specifics coverage; tuning recommendations |
| **AI conversation history consolidation** | Dave's conversation history with AI assistants (Claude, Perplexity sessions) | Organize + consolidate into structured reference; **plan scope with Dave before executing** (session 10 note) |
| ✅ **Data/archive history consolidation** | GEMINI-007 (2026-06-10) — **CRITICAL: MasterArchive I/O errors detected** (`/dev/sdc5`). `ls`/`du` work (cached dir entries), but `cat`/`touch` fail with EIO. **Operator must run `dmesg`, `umount /media/tgw/MasterArchive`, `fsck /dev/sdc5` before any indexing**. Folder inventory: `ItemData/` 584G (1.1M files, KEEP-INDEX), `job_archive/` 371G (KEEP-COLD), `ItemArchive/` 163G 54K zips (KEEP-INDEX, only 40% in archive-ebay-index.json), `magento/` 129G (KEEP-COLD), `eBay/` 60G (KEEP-COLD), `GarageSale/` 33G (KEEP-COLD), `ItemCreation/` 8.8G drafts (MIGRATE). Cleanup order: (1) fix mount, (2) consolidate zips, (3) index loose CSVs, (4) complete ItemArchive index to 100%, (5) offload cold data. `tgw history-index --target <folder>` design sketch included. See GEMINI-007-result.md. | |

### PP-DATALEARN-001 — Gemini Data Mining + AI-Calculated Fields

**Architecture principle (session 18):** All Gemini data reads/writes must go through the tgw-api fence. Gemini gets a task file with context baked in; it calls tgw-http endpoints or produces structured output for PM-intake. No direct filesystem or DB access from external AI tools. This extends the "tgw-api is the fence" settled architecture to the external AI layer.

**Task queue (Track 2):** See table above — pricing analysis, marketing insights, history consolidation, category quality review.

**Alt-text pipeline research (session 18):** Alt-text for item photos is a future enrichment opportunity (accessibility + SEO in external surfaces). Research links:
- https://medium.com/@petter.eckerbom/building-an-ai-multilingual-alt-text-pipeline-thats-fast-and-open-source-032982a5170c
- https://github.com/lukeslp/alt-text-local-llm (local LLM variant — compatible with our Ollama stack)
- https://surfai.app/blog/best-ai-image-description-generator-tools (survey)

Alt-text can feed `draft_listing.description` enrichment and future accessibility features. Track for Track 2 / PP-SEO-001 Phase 5+ when compute allows.

**Alt-text file-naming convention (session 19 — Dave 17:47):** When alt-text generation lands,
adopt a `<SKU>-alt.jpg` naming convention for the associated/derivative photo (sidecar to the
primary `<SKU>....jpg`). Wire the convention into the alt-text writer (Claude todo #38 `tgw
alt-text`) and reflect it in GEMINI-TASK-004's output spec. ⚠ Intent slightly ambiguous — confirm
with Dave whether `-alt.jpg` is (a) a renamed/secondary image file, or (b) the naming for an
alt-text-annotated derivative — before implementing the writer.

**Alt-text provider strategy (session 21–22):** Use Antigravity/OpenRouter LLMs for
alt-text in batches to offload Ollama (CPU-only, slow). Google Drive rclone sync in place
for ItemData — available for cloud provider access. Best free vision models on OpenRouter
(inbox research 2026-06-11): `google/gemma-4-31b-it:free` (top-rated, spatial awareness),
`google/gemma-4-26b-a4b-it:free` (MoE, fast), `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`
(scene description). Ultra-cheap paid: `google/gemini-1.5-flash` (~$0.075/M tokens),
`meta-llama/llama-3.2-11b-vision-instruct` (~$0.05–0.10/M). Use `openrouter/free` to
auto-route to the shortest-queue free vision model. Rate limit: ~20 req/min on free tier.
Prompt template: "Act as an expert in web accessibility and SEO. Analyze this image and
provide a concise, descriptive alt-text (max 150 characters). Describe the main subject,
setting, and context accurately without using fluff words like 'image of'..."

**Zero-bandwidth GDrive→EPS upload strategy (inbox research 2026-06-11):** ItemData is rclone-synced
to Google Drive. Photos can flow directly from Drive to eBay Picture Services without local download:
eBay's `UploadSiteHostedPictures` (Trading API) accepts image data via API upload; Antigravity CLI
(`agy`) can be scripted to fetch image bytes from Drive API and POST to EPS in one pass. Requirements:
eBay Trading API access (currently have `sell.inventory` + Trading credentials), Google Drive API
scope in OAuth client. Relevant for PP-PHOTO-001 (bulk photo re-upload / migration) — evaluate when
Planning that phase. Source: `inbox/queued/20260611T093715-antigravity-remote-execution-direct-gdrive-to-eps.md`.

**LLM routing principle (session 21–22):** OpenRouter provides built-in meta-model endpoints:
`openrouter/auto` (NotDiamond-powered, routes by task complexity; session-sticky for multi-turn),
`openrouter/free` (rotating free models; vision-aware), `openrouter/fusion` (multi-model
consensus panel). Open-source self-hosted options: **LiteLLM** (Python proxy, 100+ models,
fallback/load-balance; drop-in OpenAI-compatible), **RouteLLM** (LMSYS classifier, escalates
to expensive models only when needed), **Bifrost** (Go, near-zero latency). Recommended path:
start with `openrouter/free` for alt-text (zero cost, auto-routed); add LiteLLM when mixing
local Ollama + cloud providers in one pipeline.

### Track 3 — Perplexity (live web research, cited sources)
Research briefs in `docs/TGW-Plan-Vault/perplexity/`. Paste brief into Perplexity → save result as `.md` to `inbox/` for PM-intake.
**⚠ Perplexity subscription expires ~2026-12 — run all remaining briefs before then.**

| Brief                          | File                                | Priority | What it unblocks                                 |
| ------------------------------ | ----------------------------------- | -------- | ------------------------------------------------ |
| eBay API scope expansion       | `PERPLEXITY-001-ebay-scopes.md`     | HIGH     | PP-REPRICER-001, PP-SEO-001 Phase 3+6            |
| eBay Cassini 2025–2026         | `PERPLEXITY-002-cassini-seo.md`     | HIGH     | PP-SEO-001 tuning, listing quality strategy      |
| Sold price data alternatives   | `PERPLEXITY-003-sold-price-data.md` | HIGH     | PP-REPRICER-001 unblock if MI scope stays closed |
| Third-party integration status | `PERPLEXITY-004-integrations.md`    | MEDIUM   | IGDB, Whisper.cpp, Discogs, Go-UPC               |
| ✅ Library & API audit         | `PERPLEXITY-005-result.md`          | DONE         | Full result processed (session 19/20); see PP-PYIPC-001 + PP-LOOKUP-001 + PP-FULFILLMENT-001 updates |
| ✅ Flutter offline sync        | `PERPLEXITY-006-flutter-offline-sync Result.md` | DONE | PP-PORTABLE-CATALOG-001 P2 — snapshot+copy pattern; sqflite stack; Dio+dio_smart_retry; outbox table design |

### Track 4 — Operator (Dave must act to unblock)

#### ✅ Hardware alert resolved — MasterArchive drive (2026-06-11)
`/dev/sdc5` (`/media/tgw/MasterArchive`) had I/O errors (EIO on reads, GEMINI-007).
**Repaired by Dave 2026-06-11.** `tgw history-index` built and smoke-tested (session 26). Run `sudo -u tgw tgw history-index --target all` in a screen session to populate the index (~hours for 32K zips).

---

#### ✅ Done
- [x] `velocity_stats` worker enabled (2026-06-05)
- [x] 2-year eBay sold CSV confirmed as maximum available — archive tombstone ceiling accepted
- [x] **ISS-009 downgraded (session 16)** — production keyset active; `tgw restart-ebay-token` if dead-lettered; no longer a hard blocker

---

#### Priority 0 — NixOS migration prep (session 16 decision; updated session 18)

NixOS is the **committed target OS**. Migration is not immediate — do when ready. Recommended path:

**Step 1 — Spare machine first (session 18):**
- [ ] Identify the spare intake support machine
- [ ] Install NixOS on it using the `flake.nix` already produced; configure as client (portable catalog, services not started)
- [ ] Use it to build familiarity, discover any tool gaps, and validate the flake without risk to the main production machine
- [ ] When client setup is solid → promote to tgwOS 2.0 server or full replacement for main machine

**Step 2 — Final MX safety net:**
- [ ] Use MX Snapshot to bake a bootable ISO of the current working system before any migration touches the main machine. This is the permanent safety net.

**Step 3 — VM validation:**
- [ ] Validate `flake.nix` + `nix/tgw.nix` in a NixOS VM (watch item: `python3Packages.mcp` in nixos-24.11)

**Step 4 — Main machine cutover (when ready):**
- [ ] Run `nixos-install` on new partition; keep MX as fallback until `tgw health` fully green on NixOS

---

#### Priority 0b — Qtile WM install

- [x] **Install Qtile window manager** (PP-WM-001):
  ```bash
  bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/qtile/install.sh
  ```
  Installs: `qtile`, `xclip`, `dmenu` (via apt); symlinks `~/.config/qtile/{config.py,tgw_widgets.py}`
  from repo; copies tgw-http API key to `~/.config/tgw/api-key` for bar widgets.
- [x] Log out → select **Qtile** at SDDM/LightDM session list → log back in
- [x] Verify bar shows: workspaces, W:N/N health, Q:✓ queue indicator, clock
- [ ] Test Super+T → TGW mode (bar shows `[ TGW ]`); press `h` for health, Escape to exit
- [ ] Test F12 scratchpad terminal toggle
- [ ] Edit `~/.config/qtile/autostart.sh` if compositor (picom) or notifier (dunst) is desired

---

#### Priority 1 — eBay Developer Account (new keyset + scope requests)

**Strategy:** Request a fresh keyset (new App ID / Cert ID / Dev ID) with all desired scopes
applied at once. Avoids piecemeal scope expansion later. See complete desired scope list below.

**Status 2026-06-05 ✅:** New keyset requested. All desired scopes requested including
`buy.marketplace_insights`. Awaiting eBay approval. Portal request flow has changed from
what's documented below — steps below are reference only; follow current portal UI when
updating credentials after approval.

**Status 2026-06-10 update:** eBay Developer Support responded to the `buy.marketplace_insights`
scope request with **8 questions** Dave must answer before the scope can be approved.
- [ ] **Review and respond to eBay Developer Support message** — answer the 8 questions
  about the use case for `buy.marketplace_insights` (automated pricing engine, resale
  platform, no redistribution of sold-price data to third parties). Be specific: automated
  repricing, TGW internal use only, ~55K items, eBay seller account DaveBuko-Webkulap.

⚠ When new keyset arrives: update `secrets_root/ebay-credentials.json`, update
`tgw-api-config.json` scopes to match approved scopes only, then re-run OAuth.

- [x] New keyset requested via developer.ebay.com (2026-06-05)
- [x] All desired scopes requested including `buy.marketplace_insights` (2026-06-05)
- [ ] Receive approval + credentials from eBay
- [ ] Update `secrets_root/ebay-credentials.json` with new App ID / Cert ID / Dev ID / RU name
- [ ] Re-run OAuth: `sudo -u tgw BROWSER=/usr/bin/firefox python3 .../get_access_token.py`
- [ ] Restart all eBay workers after new token is live

**Old instructions (portal UI has changed — reference only):**
- Go to https://developer.ebay.com → My Account → Application Keys → **Create new keyset**
  - App name suggestion: `TGW-Automation-v2` or similar
  - Note new App ID, Cert ID, Dev ID — replace in `secrets_root/ebay-credentials.json`
- On the new keyset, request **all scopes in the desired list** (see below) via the "Get a Token" / OAuth consent flow and the scope editor
- For `buy.marketplace_insights` — **this requires a separate contact** (limited release):
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

| Scope                                | Have | Priority | What it enables                                                    |
| ------------------------------------ | ---- | -------- | ------------------------------------------------------------------ |
| `sell.inventory`                     | ✅    | core     | Create/update/delete inventory items and offers                    |
| `sell.account`                       | ✅    | core     | Fulfillment policies, merchant location, payment policies          |
| `sell.marketing`                     | ✅    | core     | Promotions, campaigns                                              |
| `buy.marketplace_insights`           | ❌    | **HIGH** | Sold price data → PP-REPRICER-001                                  |
| `commerce.catalog.readonly`          | ❌    | **HIGH** | EPID lookup by UPC/EAN → PP-SEO-001 Phase 3                        |
| `sell.analytics.readonly`            | ❌    | **HIGH** | Per-listing impressions/clicks → PP-SEO-001 Phase 6                |
| `sell.fulfillment.readonly`          | ❌    | medium   | Read orders via REST (supplements Trading API GetOrders)           |
| `sell.finances.readonly`             | ❌    | medium   | Payout/financial data for accounting and reconciliation            |
| `sell.stores.readonly`               | ❌    | medium   | Read eBay store category tree → PP-STORE-001                       |
| `sell.reputation.readonly`           | ❌    | low      | Feedback score tracking and monitoring                             |
| `commerce.notification.subscription` | ❌    | low      | REST-based webhook event subscriptions (future alt to Trading API) |

---

#### Priority 1b — TGW MCP Server registration (2 min)

PP-MCP-001 code is **done** (`src/tgw/mcp_server.py`, 9 tools). Needs one manual step to activate
in Claude Code because Claude cannot self-modify its own settings:

1. Open `~/.claude/settings.json` in your editor
2. Add the `"mcpServers"` block (merge with existing content):
   ```json
   {
     "model": "opusplan",
     "theme": "dark",
     "mcpServers": {
       "tgw": {
         "command": "sudo",
         "args": ["-u", "tgw", "/opt/TGW/.venvironments/tgw/bin/python", "-m", "tgw.mcp_server"],
         "env": {}
       }
     }
   }
   ```
3. Restart Claude Code — the `tgw_*` tools will appear in future sessions.

**What this unlocks:** Claude can query live queue state, item data, health, and TODO items
mid-session without shell escapes. Makes future debugging sessions significantly faster.

---

#### Priority 2 — API credentials (15–20 min each, each unlocks a lookup source)

- [ ] **IGDB** (video game lookups) — ⏳ App registered 2026-06-05; key not appearing in portal yet:
  1. Go to https://dev.twitch.tv → Log in with Twitch account (create if needed)
  2. Register new application: Name=`TGW`, OAuth Redirect=`http://localhost`, Category=`Other`
  3. Copy Client ID + generate Client Secret
  4. Write: `sudo -u tgw nano /opt/TGW/secrets/igdb-credentials.json`
     ```json
     {"client_id": "...", "client_secret": "..."}
     ```
  5. `sudo chmod 600 /opt/TGW/secrets/igdb-credentials.json`

- [x] **Discogs** (music/vinyl lookups) — ✅ Done 2026-06-05:
  1. Go to https://www.discogs.com/settings/developers
  2. Click "Generate new token"
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/discogs-credentials.json`
     ```json
     {"personal_access_token": "..."}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/discogs-credentials.json`

- [x] **Go-UPC** — ❌ No free tier available (2026-06-05); skip for now; upcitemdb + go-upc paid plan if needed later:
  1. Go to https://go-upc.com/api → sign up for free tier
  2. Copy API key
  3. Write: `sudo -u tgw nano /opt/TGW/secrets/go-upc-credentials.json`
     ```json
     {"api_key": "Bearer <your-token>"}
     ```
  4. `sudo chmod 600 /opt/TGW/secrets/go-upc-credentials.json`

- [x] **upcitemdb** — ✅ Free tier (100/day) works keyless; no credential needed; code already handles this:
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
- [ ] **PERPLEXITY-005** — Library audit (Syncthing Python client, KDE Connect DBus, USB scale HID)

**PP-PERP-AUTO-001**: when briefs pile up, use ydotool semi-automation to reduce copy-paste overhead.
See PP-PERP-AUTO-001 section for design.

---

#### Priority 3b — Tasker / Join evaluation (15–30 min)
- [ ] Compare Join vs KDE Connect for clipboard relay and push notifications; document findings in inbox
- [ ] Identify 3–5 highest-value Tasker automation opportunities from PP-TASKER-001 — start with barcode scan → tgw-http intake

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

- [x] **Tailscale** ✅ installed 2026-06-11 (PP-REMOTE-001):
  Tailscale installed and configured. Verify `tgw-http` reachable over Tailscale from remote
  devices; verify `tgw-macro` works over SSH. SSH hardening (key-only, sudoers) still open.

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

- [x] **markmap-cli** ✅ INSTALLED (2026-06-11) — `markmap-cli` now available. Test: `markmap docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md --no-open -o /tmp/plan.html`
- [ ] **nvm + npm** (needed for other JS tooling if required later):
  ```
  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
  # restart shell or: source ~/.bashrc
  nvm install --lts
  ```

- [x] **Gemini CLI** ✅ INSTALLED (2026-06-06) — `gemini` available in PATH; excellent for
  large-context data tasks; free with Google Drive subscription. Elevated to Track 2 primary
  for data analysis and self-contained scaffold tasks. See `## Work Tracks § Track 2`.

- [ ] **⚠ Perplexity expiry ~2026-12** — subscription expires in ~6 months. Run all remaining
  research briefs before expiry:
  - [x] PERPLEXITY-001 — eBay sold price API
  - [x] PERPLEXITY-002 — pricing strategy
  - [x] PERPLEXITY-003 — barcode lookup
  - [x] PERPLEXITY-004 — IGDB/Discogs APIs
  - [ ] **PERPLEXITY-005** — TGW library audit (brief ready at `perplexity/PERPLEXITY-005-library-audit.md`)
  Note from Dave (2026-06-06): Perplexity is also a capable Python programmer — it designed the
  state machine architecture. Use it for architecture research too, not just web lookups.

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

**Antigravity (agy) token-limit observations (2026-06-12):**
- Tasks 82–85 ran to completion; task 86 (Gemini CLI export) ran out of tokens mid-run
  on the selected model and completed via fallback model. Task 116 (xmouse design) similarly
  hit token ceiling on `agy --high-reasoning`; Sonnet fallback finished it.
- **Routing refinement:** keep individual Antigravity tasks small and self-contained (same
  rule as Gemini CLI per 2026-06-10 note). Avoid `--high-reasoning` for tasks that can be
  completed by standard Sonnet — reserve it for tasks requiring deep cross-file synthesis.
- **Quality note:** compare code quality between agy and Claude Sonnet on a test response
  before committing to agy for code-generation tasks.
- **Fallback pattern:** design multi-step tasks to be resumable — if a model runs out of
  tokens mid-task, a second agent with different limits should be able to pick up the output.

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
- `eBay-Error-Codes.md` — all eBay errorIds + HTTP status handlers; dead-letter diagnosis guide; scope gaps table
- `HARDWARE-AI-INFERENCE.md` — Ollama model sizing, GPU upgrade planning (pre-existing)
- `SHELL-AUDIT.md` — tgw.source / tgw-dev.source function disposition (KEEP/WRAP/ARCH-VIOLATES/DEPRECATED)
- `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` — operator runbook: bake the final MX Snapshot restore image before NixOS cutover (session 17)
- `echo.py` / `worker_base.py` — new worker templates (pre-existing)
- `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` — operator runbook: bake final MX Snapshot restore image (session 17)

### 🗒 Planned
- `TGW-Quickstart.md` — PP-REF-003: all tgw CLI subcommands, workers, MC VFS, Qtile/macroboard keys, per-workflow; stubs for physical processes

---

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

### PP-REF-003 — TGW Installation Quickstart Reference Guide (planned)

#### Problem
No single document lists all available TGW tools, commands, and their usage in a format suitable for a new operator or for quick lookup during setup. The CLAUDE.md covers session protocol; the reference docs cover specific subsystems; but there is no "what can I do?" entry-point document.

#### Design
- **Scope**: all `tgw` CLI subcommands + workers + MC extfs VFS tools + Qtile chords + macroboard keys + tgw.source convenience functions
- **Format**: Markdown quickstart — organized by workflow (intake → pipeline → eBay → admin), not alphabetically
- **Physical process hooks**: leave stubs for "associated physical processes" (intake station setup, scale use, camera trigger) — Dave will fill in over time
- **Target location**: `docs/TGW-Plan-Vault/reference/TGW-Quickstart.md` (plain Markdown; markmap-compatible)

#### Output structure (proposed)
1. System health and status commands
2. Item intake workflow (set-template → intake → identify → draft → price → stage → publish)
3. Bulk operations (bulk-edit, mvitems, catalog-verify)
4. eBay management (sync, sweep, reprice-suggest, dead-letter)
5. Admin and diagnostics (todo, health, restart-workers, dead-letter)
6. MC interface (extfs VFS list, key actions)
7. Qtile / macroboard quick-reference
8. Worker reference (queue name → purpose → how to restart)

#### Status
✅ **DONE (session 18)** — `reference/TGW-Quickstart.md` authored (9 sections; all `tgw`
subcommands cross-checked against `api.py`; MC/Qtile/macroboard key maps; worker table; physical-
process stubs left for Dave). Keep it updated as new commands ship.

---

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

#### Cassini research findings (PERPLEXITY-002, 2026-06-05) — tuning notes
Cited research from Perplexity (export.ebay.com, Listtune, 3Dsellers, Webinterpret, 2025–2026):

**Ranking priority order (working model 2025–2026):**
1. Relevance: title keywords + matching item specifics + correct category
2. Conversion/velocity: sales history, CTR, return rate
3. Seller metrics: defect rate, late shipment, cancellations, feedback
4. Listing quality + completeness: photo count/quality, description clarity, specifics coverage
5. Price + terms: competitive price, fast handling, 30-day+ returns

**Key validated decisions:**
- Item specifics completeness estimated at ~30% of Cassini score (Listtune/3Dsellers testing)
- ALL-CAPS words in titles explicitly documented by eBay to hurt rank — TGW title pipeline already strips/warns
- Brand + MPN should appear in **both** title AND item specifics for double relevance signal
- EPID association is beneficial for used items when exact model match exists (auto-fills structured data)
- No official "200-word rule" — focus on completeness/clarity; first 800 chars matter most for mobile
- Photos expanding from 24 to 40 slots (eBay April 2026 test); 8–12 photos recommended baseline for used
- Condition granularity matters for filter visibility, not a direct ranking bonus
- Keyword stuffing (repeated terms, comma-separated lists) documented as penalized

**PP-QUALITY-001 tuning opportunities (future pass):**
- Photo score threshold: flag listings with < 5 photos (hard fail); soft-warn < 8
- Title: add ALL-CAPS word detection flag to `title_flags`
- Description: first-800-chars keyword check (mobile snippet quality)
- Item specifics: Required/Recommended fill % as primary score signal (already partially done)

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

### PP-NIXOS-001 — NixOS Migration Evaluation

#### Motivation (session 9 analysis)
Debian's advantage is stability and ubiquity — the system is rock solid and dependencies are
well-understood. The trade-off is dependency lock-in and an outdated feature set (older kernel,
older Python, older packages). NixOS offers:
- **Parallel version deployment**: run the stable system unchanged while testing a newer version
  of any component (Python, Postgres, Qtile) in a separate Nix derivation — no risk to the
  running system
- **Atomic rollback**: if a change breaks something, `nixos-rebuild switch --rollback` restores
  the last good state in seconds
- **Disaster recovery**: the entire system configuration is a single file (`/etc/nixos/configuration.nix`);
  combined with `/opt/TGW` and a repo restore, a full system rebuild is automated
- **Reproducibility**: any node can be cloned to the exact same state from the config file

#### Perplexity research findings — PostgreSQL + Python + DR (session 16)
Comprehensive analysis commissioned from Perplexity (MX Linux vs NixOS for PostgreSQL-backed
state machine). Key conclusions:

**DR verdict: NixOS is architecturally superior for DR.**
- MX Linux: OS-level DR is "Debian + scripting you build yourself." LuckyBackup (rsync-based)
  and MX Snapshot (bootable ISO) are GUI-centric and not inherently infra-as-code.
- NixOS: entire OS config is version-controlled Nix files. DR = "restore Postgres base backup
  + WAL" + "nixos-rebuild from flake." Config is the single source of truth.

**PostgreSQL on NixOS:**
- First-class module (`services.postgresql`) — version, data path, config, initial DB/users all declared
- pgBackRest + WAL archiving modules available (some permission/UMask rough edges in defaults — overridable)
- ⚠ **Known gotcha**: WAL-recovery conflict — `ExecStartPost` hook tries `ALTER USER` while DB is
  in read-only recovery mode → systemd kills the service. Mitigation: disable the hook or add a
  recovery-mode guard. Solvable but non-obvious.

**Python on NixOS — updated strategy:**
- Flake-based devShells with `direnv`/`devenv` for auto-activation on `cd`
- Packaging: `poetry2nix` or `buildPythonPackage` for deps not in nixpkgs
- Full pattern: one flake defines devShells (dev) + app package + nixosConfigurations (prod systemd services)
- TGW's `pyproject.toml` is already flake-compatible — straightforward to wrap

**Given Dave's background (Gentoo 8 years, LFS, custom OSes):**
Perplexity's explicit recommendation: NixOS is learnable — a small change given the background.
The Nix language is just a new dialect. The functional/declarative constraints become features,
not friction.

**Alternatives assessed:**
- **Guix System** — same design space as NixOS but Guile Scheme syntax; can also overlay on MX
- **Fedora Silverblue/Kinoite** — immutable rpm-ostree base + containers for app stack; less fully declarative
- **"MX + Nix overlay"** — keep MX host, add Nix for reproducible devShells without full migration

#### Decision framework (updated)
| Factor | MX Linux (Debian) | NixOS |
|--------|--------|-------|
| OS-level rollback | MX Snapshot ISO (coarse) | Fine-grained generational rollbacks |
| PostgreSQL integration | Standard Debian; you write all scripts | First-class module; some edge cases |
| Backup tooling | LuckyBackup/Snapshot; GUI-centric | Define pgBackRest/WAL in Nix; fully automatable |
| DR automation ceiling | High — you build declarative layer | Very high — OS is infra-as-code |
| Python env mgmt | Standard pip/venv | ⚠ Needs flake + poetry2nix; solvable |
| Dependency freshness | ⚠ Older packages | ✅ Latest available |
| Learning curve | ✅ Familiar | Moderate for Dave (low given background) |
| MX Linux image compat | ✅ Natural | ❌ Incompatible with mx-slapshot (mutually exclusive) |

**PP-DEPLOY-001 (MX Linux image) and PP-NIXOS-001 are mutually exclusive end-states.**
Decision recommendation: NixOS, pending the Python flake prototype.

#### Work items

**Completed (session 17–18):**
- [x] `flake.nix` + `nix/tgw.nix` authored (Round 3 #27) — `buildPythonApplication`, per-queue worker services, PostgreSQL, tgw-http, tgw user
- [x] `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` authored (Round 3 #28) — pre-snapshot checklist, ISO verify, full restore steps
- [x] NixOS committed as target OS (session 16 decision)

**Pending — operator / VM actions:**
- [ ] **Validate flake.nix in NixOS VM** — Dave builds + tests on a VM; note: `python3Packages.mcp` availability in nixos-24.11 is a watch item
- [ ] ⚠ Mitigate WAL recovery gotcha: `ExecStartPost` ALTER USER runs while DB is in read-only recovery → service killed. Add recovery-mode guard before production use.
- [ ] **Spare intake machine as first NixOS target** (session 18 decision): install NixOS on the spare intake support machine; configure as client/portable-catalog (services not started); gain tool familiarity without risk to the main machine. When proven: promote to tgwOS server or full replacement.

**Flake architecture requirements (session 18):**
- **Platform flake** (`flake.nix`) — `tgw` user + workers + PostgreSQL + `tgw-http`; already authored
- **venv / nvm / npm on `/opt/TGW/`** — move tgw user virtualenv (`/opt/TGW/.venvironments/tgw/`) and nvm/npm (`/opt/TGW/.nvm/`) out of `~tgw/` so `/opt/TGW` is a self-contained imageable entity with no home-dir dependencies; update flake `HOME` or env vars accordingly
- **Personal operator flake** (separate) — Firefox, KDE Plasma, personal apps; composable via NixOS `imports`; not part of the platform flake
- **Dependency source-of-truth unification (open, session 19)** — `flake.nix` declares `pillow` as a **base** runtime dep (lines 63 + devShell 119), but `pyproject.toml` only carries `Pillow>=10.0` in the **optional** extras (`thumbnails` + `dev`), not base deps. The CI commit (`3be0d85`) added Pillow to the `dev` extra so fingerprint tests run, but did **not** reconcile the base-vs-optional divergence. Before NixOS cutover, make one source authoritative — either promote Pillow to a base `pyproject` dependency (PP-VISION-001 fingerprint is now core, not optional) or drive the flake from `pyproject` extras via `poetry2nix`/lockstep so the two can't drift. Pick the former if fingerprinting is here to stay (recommended); the latter if extras-as-optional is the intended packaging contract.

**DR / bootstrap design (session 18):**
NixOS install must support two bootstrap modes:
1. **Fresh warehouse start** — empty `/opt/TGW`; workers spin up; first item intake begins immediately
2. **Adopt existing data** — `/opt/TGW` restored from backup; config applied; full pipeline resumes from last state

Recovery equation: `NixOS flake + site config GitHub repo + ItemData restore = full system rebuild`

**Site config in GitHub** — `tgw-api-config.json` and non-secret config in a private GitHub repo; NixOS flake fetches at build time; enables any node to self-configure without local copy.

**Google Drive DR** — rebuild kit (NixOS ISO pointer, site config repo URL, rclone restore script for ItemData) lives on Google Drive. Major disaster: boot NixOS ISO → pull config from GitHub → restore ItemData from Drive → `tgw health` green.

#### Syncthing NixOS deployment (session 19/20 — syncthing-nixos-nginx-research.md)

TGW runs a dedicated headless Syncthing instance (separate from personal user instances):

```nix
# In modules/bases/master.nix
services.syncthing = {
  enable = true;
  user = "tgw";
  dataDir = "/opt/TGW/sync";
  configDir = "/opt/TGW/.config/syncthing";
  guiAddress = "127.0.0.1:8385";
  settings.options.listenAddresses = [ "tcp://0.0.0.0:22001" ];
  settings.options.insecureSkipHostCheck = true;
};
```

Port allocation:
- TGW headless: 8385 (GUI/REST), 22001 (sync protocol)
- Regular user instances: 8384 (default), 22000 (default)

Nginx reverse proxy block (for remote GUI access):
```nginx
server {
  listen 8386;
  location / {
    proxy_pass http://127.0.0.1:8385;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
}
```

Auto-TLS: systemd oneshot `Before=nginx.service` generates self-signed cert. Replace with
ACME when the machine has a DNS name.

GUI access from dev workstation: `ssh -L 9000:127.0.0.1:8385 tgw@server`

LTSP fat clients: per-hostname Syncthing config via symlink
`/opt/TGW/.config/syncthing/config.xml → /opt/TGW/.config/syncthing/config.d/<hostname>.xml`
(different folder mappings per machine role).

**Operator unblock steps for PP-PYIPC-001:**
- [ ] Install Syncthing on NixOS target; confirm REST accessible at 8385
- [ ] Generate API key in Syncthing GUI → save to `secrets_root/syncthing-api-key` (chmod 600)
- [ ] Add `"syncthing_api_key_path": "/opt/TGW/secrets/syncthing-api-key"` to `tgw-api-config.json`

#### Multi-tier flake architecture (session 19/20 — system-app-config-and-nixos-flake-design.md)

Recommended structure for the full TGW NixOS platform using `flake-parts`:

```
nix/
  modules/
    bases/
      master.nix        # PostgreSQL, tgw workers, tgw-http, backup, Syncthing
      portable.nix      # portable catalog client (no workers, read-only tgw-http)
    interfaces/
      cli.nix           # terminal tools: tmux, mc, tgw.source, bash completion
    graphical/
      tiled.nix         # Qtile (primary intake workstation)
      plasma.nix        # KDE Plasma 6 (general-purpose desktop)
      thin-client-rdp.nix  # lightweight RDP thin client session
    ai/
      compute-node.nix  # Ollama + GPU drivers; models on /var/lib/ollama/models
  hosts/
    production-server.nix   # master + cli + tiled (+ plasma optional)
    portable-laptop.nix     # portable + cli + plasma
  dev-env/
    flake.nix           # separate stacking flake; nix develop ./dev-env
```

LTSP fat clients: NFS `/nix/store` shared; model weights on NFS mount (not in initrd).
Thin clients: `thin-client-rdp.nix` module; lightweight sessions connecting to master.
Dev flake: separate from platform flake; `nix develop ./dev-env` does not require system rebuild.

**Round 5+ tasks:** Refactor `flake.nix` to match this structure; add `portable.nix` base;
add `ai/compute-node.nix`; wire Syncthing service into `master.nix`.

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
- **Suggestion editor** (session 9 addition): lightweight tool to review, annotate, edit, or delete
  entries from SUGGESTIONS.md before PM-intake processes them. Use case: catching duplicates or
  clarifying ambiguous entries before they get embedded in the plan. Implementation: `tgw suggest-edit`
  opens a filterable list (fzf or TUI); edit → save → marks entry with a status tag.

#### Status
`tgw suggest` / `tgw note` / `tgw btw` — ✅ working. Suggestion editor — planned (Track 1 XS).

### PP-SHELL-001 — Shell Environment Cleanup (tgw.source / tgw-dev.source)
- Audit `tgw.source`: replace functions that duplicate `tgw` CLI subcommands with one-line wrappers or remove; keep only short-name convenience aliases worth keeping
- Audit `tgw-dev.source`: migrate anything useful to `tgw.source`; retire the dev file
- Rule of thumb: if it's not interactive/session-specific, it belongs as a `pyproject.toml` console script in the package, not a bash alias
- Outcome: `tgw.source` is a thin convenience layer on the `tgw` CLI; no parallel API surviving alongside it

**Tier 3 open items (2026-06-11):**
- **Help grouping** — `tgw --help` now lists ~65 subcommands; group them by function category
  using argparse `parents` or a custom formatter. Suggested groups: Read/Search, Write/Update,
  Pipeline, eBay, Context, Catalog/Build, Ops/Admin. Reference: argparse `add_argument_group`
  on the top-level parser or a manually-formatted epilog. Add to Track 1 when PP-SHELL-001
  Tier 3 work resumes.
- **`requeue` rename** — currently only re-queues `ai_identify` despite generic name; rename
  to `requeue-identify` or make it queue-agnostic before Tier 3 closes.

### PP-CONTEXT-001 ✅ DONE 2026-06-11 — Current-item context: `tgwset` replacement
Dave: the legacy `tgw set` (shell `tgwset` in `tgw.source`) sets an item persistently
systemwide so multiple operations can target it. It works but is fragile — needs a new
strategy, likely replaced, and the replacement must be **idempotent**.

**Dave note (2026-06-11):** Keep `tgw set` — most use cases are covered by new development
(photo display, editing live eBay listings via JSON data, uploading photos to eBay quickly)
but the feature is simple and useful in certain circumstances. **Do not remove** until the
replacement is feature-complete and field-tested.

**How the legacy mechanism works (audited session 20):**
- `tgwset()` does `rm` + `ln -sf` of three symlinks: `/opt/TGW/CurrentItem` →
  `ItemData/<SKU>/`, `/opt/TGW/CurrentItem.json` → the item JSON,
  `/opt/TGW/CurrentLocation` → catalog location dir
- `getsku()` resolves the context by `realpath` on the symlink; falls back to legacy
  `searchcatalog.json` via jq for eBay-ID→SKU and 18-char-prefix matching

**Why it's fragile:** non-atomic remove-then-link (a reader between the `rm` and `ln`
sees no context); constructs ItemData paths outside the fence; depends on the legacy
search catalog file; silent fallback to the Queue dir when the SKU doesn't validate;
no `{ok,...}` output contract; only one global context with no record of who set it.

**Design direction (discuss before building):**
- Promote to a first-class fence concept: `tgw context set <selector>` / `tgw context get`
  / `tgw context clear`, full `{ok,...}` contract, selector resolution via `resolve()`
- Idempotent by construction: setting the already-current SKU is a success no-op;
  `clear` on empty context is a success no-op; set = single atomic replace
  (`ln -sfn` via temp+rename, or sidestep symlinks entirely with a small state file
  `runtime/state/current-item.json` {sku, set_at, set_by})
- Keep `/opt/TGW/CurrentItem` symlinks as a **derived compatibility view** maintained
  by the same command (existing MC/shell consumers keep working during transition)
- Scope question for Dave: one systemwide context (current behavior) vs named/per-surface
  contexts (e.g. camera station vs desk) — systemwide is the stated requirement
- Related: PP-SHELL-001 (the shell layer keeps thin wrappers calling `tgw context`),
  PP-CLIP-001 (clipboard intake reads the context)

### PP-IFDIR-001 — Interface File Organization
- Currently: MC configs live at `/opt/TGW/mc/` (outside repo); keyd at `etc/keyd/`; no unified structure
- Goal: move all operator interface configs into repo under `etc/interfaces/mc/`, `etc/interfaces/keyd/`, etc.; update install scripts to deploy from there
- Makes repo the single source of truth for all interface configuration; simplifies new-node bootstrap

### PP-STORE-001 — eBay Store Category Support
- Add `store_category_id` to `draft_listing`; allow items to be filed into eBay store sections
- Store category list queried once via Trading API `GetStore` and cached (store categories rarely change)
- Default store category configurable per eBay category in `tgw-api-config.json`
- Wired into `ebay_stage` and `ebay_publish` offer bodies

### PP-GLOBALS-001 — Item JSON Globals Metadata ✅ ANALYSIS DONE (2026-06-07)

**Finding: no `globals` block needed.** Top-level fields already are the globals layer.

**Offer-invariant field audit:**

| Property | Current home | Assessment |
|---|---|---|
| `condition` (human string) | top-level | ✓ correct; source of truth |
| `condition_id/enum/label` | `draft_listing.*` | ✓ correct; eBay-derived copies |
| `ebay_category_id` / `ebay_category_name` | top-level | ✓ correct |
| `category_group` / `size_class` | top-level (via set-template) | ✓ correct |
| `upc` | top-level | ✓ correct |
| `format` (FIXED_PRICE) | TGW-wide constant | not worth storing per-item |
| `quantity` (always 1) | TGW-wide constant | not worth storing per-item |
| `marketplaceId` / `shipToLocations` | account-wide constant | not worth storing per-item |
| Policy IDs (fulfillment/payment/return) | config + category override | correct; never per-item |
| `merchantLocationKey` | account-wide, from config | correct; never per-item |
| **`weight_oz`** | **missing entirely** | **⬅ add this** |

**Action — add `weight_oz` (top-level, float, nullable):**
- Written by: PP-INTAKE-001 Phase 2 web form; PP-FULFILLMENT-001 USB scale; operator
- Used by: `ebay_draft` (item specifics for shipping weight); `size_class` derivation when not set by template; shipping label generation (PP-FULFILLMENT-001)
- Additive — safe to add now without waiting for Pass 3 schema freeze
- Do NOT add until PP-INTAKE-001 Phase 2 (the write path) is designed; schema freeze applies to renames/deletes only

**No schema restructuring needed.** The condition duplication between top-level and `draft_listing` is legitimate (top-level = source of truth; draft_listing = eBay API-formatted copies). Adding `globals` indirection would require every worker to change `doc.get('condition')` → `doc.get('globals', {}).get('condition')` with no benefit.

- Depends on: PP-ADD-005 (SKU normalization) + Pass 3 data scrub (field schema freeze) — for any renames; `weight_oz` addition is exempt (additive)

### PP-LOOKUP-001 — Product Data Enrichment ✅ ALL TIER 1 DONE (2026-06-05)

`apis/lookup/` package; `lookup_product()` dispatcher; results in `product_lookup` key (30-day cache).
Integrated into `ai_identify` (runs before Ollama) and `tgw lookup <SKU>` CLI.

**Tier 1 sources (all implemented):**
- `upcitemdb` (primary, 698M barcodes, 100/day free) → `go_upc` fallback (1B items)
- `open_library` (books/ISBN, no auth) · `discogs` (music, needs credential) · `igdb` (games, Twitch OAuth)
- `justtcg` (trading cards, no auth) · `open_food_facts` (food/household, no auth)

**Credential status (2026-06-05):**
- `secrets_root/igdb-credentials.json` — ⏳ Twitch app registered but key not yet visible in portal; check back
- `secrets_root/discogs-credentials.json` — ✅ Done
- `secrets_root/go-upc-credentials.json` — ❌ No free tier available; skip; Go-UPC is paid-only
- `secrets_root/upcitemdb-credentials.json` — ✅ Not needed; free tier (100/day) works keyless; code already handles this

**Integration details (PERPLEXITY-004, 2026-06-05):**
- **Discogs**: 60 req/min authenticated; personal token for automation; barcode lookup:
  `GET /database/search?barcode=<UPC>&type=release` (JSON array of releases); must send `User-Agent` header;
  30-day cache TTL recommended; search endpoint requires auth even for reads
- **IGDB**: Twitch app registration is instant; 4 req/sec / 8 concurrent max; queries use Apicalypse POST:
  `POST /v4/games` body: `search "Title"; fields id,name,slug,first_release_date; limit 10;`
  OAuth token via `POST id.twitch.tv/oauth2/token?grant_type=client_credentials`; 14–30 day cache TTL
- **Go-UPC**: Dev tier = 5,000 lookups/month, 2 req/sec; bearer token auth;
  `GET /api/v1/code/<barcode>?key=<key>`; 90–180 day cache; dedupe aggressively; monthly quota is hard stop

**Tier 2 (decide when Tier 1 proves insufficient):** Keepa (€19/mo, Amazon price history); Barcode Lookup (richer fields, subscription); **PriceCharting** (free API, current market values from eBay sold data, good for games/cards/collectibles — add as Tier 2 for those verticals). Stubs not implemented yet.

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
- **LTSP RemoteApps** (session 9 addition): expose TGW admin tools as LTSP RemoteApp sessions —
  single-application remote sessions that appear as local apps on thin clients and tablets.
  Use case: content admin on remote display stations without full Linux install. Evaluate:
  xrdp's RemoteApp mode, FreeRDP, or X2Go published applications as the transport layer.

---

## PP-WM-001 — Qtile Tiling Window Manager

### Vision
Qtile as the primary operator workstation shell — a tiling WM where TGW API hooks are
first-class citizens, not afterthoughts. The WM config is Python, the TGW stack is Python;
no IPC marshaling, no subprocess overhead for status data. The bar is a live TGW dashboard.
X11 session (not Wayland) for clipboard tool maturity and overall stability.

Chosen over: AwesomeWM (X11-only, Lua), XMonad (Haskell, steep overhead), Hyprland (great IPC
but config is declarative — all logic lives outside), Sway (i3-compatible but thin extensibility).

### Files
| File | Purpose |
|------|---------|
| `etc/interfaces/qtile/config.py` | Main Qtile config — layouts, keybindings, bar, hooks |
| `etc/interfaces/qtile/tgw_widgets.py` | Custom widgets: TGWQueueWidget, TGWHealthWidget, TGWSKUWidget |
| `etc/interfaces/qtile/install.sh` | User-level installer (run as desktop user, not root) |

### Phase 1 — Base config ✅ DONE (2026-06-05)
- **TGWQueueWidget** — polls `GET /api/queue/status` via tgw-http REST; shows pending/dead with
  color coding; click opens health terminal; API key from `~/.config/tgw/api-key`
- **TGWHealthWidget** — `systemctl list-units` for all `tgw-worker@*` + `tgw-http`; shows
  active/total ratio; color: green=all up, amber=some down; click opens unit list
- **TGWSKUWidget** — polls X11 clipboard every 2s; pattern matches `tgw[0-9]{15}`; shows SKU
  in accent color when detected; click or Super+T→c triggers lookup action
- **Super+T chord mode** — TGW command layer (bar shows `[ TGW ]`); keys: h=health, q=queue
  depths, s=staged, t=todo, v=velocity-report, c=clipboard SKU action, o=open ItemData in
  Dolphin, 1-2=pipeline triggers, F2/F4=workspace jump, Escape=exit mode
- **F12 scratchpad** — floating konsole (55% height, 85% width); always-available TGW shell
- **5 named workspaces**: shell / tgw / ebay / agents / media
- **Layouts**: MonadTall (default, 55% main), MonadWide, Columns(3), Max
- **autostart hook** — runs `~/.config/qtile/autostart.sh` on first launch (compositor stub)
- **Install**: `bash etc/interfaces/qtile/install.sh` (as desktop user); symlinks configs from
  repo; apt installs qtile + xclip + dmenu; copies API key

### Phase 2 — TGW integration depth (future)
- TGW-mode key `c` + SKU action menu: kdialog for choice (lookup / re-enqueue / open photos)
- Clipboard SKU watcher: emit `notify-send` on first detection of new SKU
- `tgw-notify` hook: workers emit `notify-send` on completion → Qtile `net_wm_state` hook
  catches notification window → updates a notification counter widget in bar
- Workspace 2 (tgw): auto-launch MC on startup, or a tgw dashboard tmux session
- Workspace 4 (agents): auto-launch Claude Code on startup

### Phase 3 — Workflow automation (future)
- Macroboard `[tgw_layer]` integration: once macroboard is live, key chord in config should
  mirror macroboard layout so both inputs do the same thing
- Quiet-queue hook: when all workers idle, surface `tgw todo claude` in a notification or
  dedicated scratchpad (connects PP-CAPTURE-001 quiet-queue concept)
- Photo intake workspace auto-route: when Gwenview or camera tool opens, auto-assign to ws5

---

## PP-CLIP-001 — TGW-Aware Clipboard Manager

### Status: DESIGN SETTLED 2026-06-12 (session 28) — build gated on Qtile install (admin #20)

**Decisions (session 28):**
- **Dual-backend watcher, both first-class.** Dave: the environment is already mixed and the
  world is moving toward Wayland — accommodate both as best we can; **X11 is the stable platform
  for now.** The daemon core (on change → classify → write SQLite → socket push) is
  backend-agnostic; watcher backends: (a) **X11/XFixes** via python-xlib (default, stable),
  (b) **Wayland** via `wl-paste --watch` subprocess (wl-clipboard — event-driven, zero protocol
  code, sidesteps python-xlib staleness entirely). Session-type detection at startup
  (`$WAYLAND_DISPLAY` / `XDG_SESSION_TYPE`) selects the backend.
- Phase-1 open questions answered: watch **both PRIMARY and CLIPBOARD** (highlight-capture of
  SKUs is the stated use case); DB at `~/.local/share/tgw-clip/` (per-user). The SQLite store +
  `tgw clip` CLI already shipped (session 15 R18) — the daemon feeds the existing store.
- Build timing: after the Qtile install (admin #20) so the daemon has its consumer. Round 7 todo #113.

### Background
Identified during PP-WM-001 (Qtile) session (2026-06-05). Immediate need is met by
**Clipster** (flat-file history, long buffer) installed today. PP-CLIP-001 is the
next-generation replacement: a TGW-specific clipboard daemon that understands SKUs,
is event-driven, and exposes its history to the rest of the system.

### Problem with existing tools
- **Polling-based** (xclip, Clipster): 1–2s lag; CPU burn; TGWSKUWidget misses rapid copies
- **Not TGW-aware**: no concept of SKU vs. random text; history is undifferentiated
- **No queryable API**: macroboard and chord actions can't reliably ask "what was the last SKU?"
  — if you've since copied something else, the SKU is gone from live clipboard
- **No persistence across sessions**: most tools lose history on logout

### Core concept
An X11 event-driven daemon (`tgw-clipd`) written in Python that:
1. Receives push notifications from X11 when clipboard ownership changes
   (XFixes `select_selection_input` — zero polling, instant response)
2. Fetches the new clipboard content and classifies it
3. Writes to a local SQLite database with TGW-aware tagging
4. Exposes a Unix socket so Qtile widgets and CLI tools can subscribe/query

### X11 event mechanism
```python
from Xlib import X, display
from Xlib.ext import fixes

dpy = display.Display()
screen = dpy.screen()
fixes.query_version(dpy)

# XFixes sends XFixesSelectionNotifyEvent when clipboard owner changes
fixes.select_selection_input(
    dpy, screen.root, dpy.get_atom('CLIPBOARD'),
    fixes.SetSelectionOwnerNotify
)
# Main loop: dpy.next_event() blocks until clipboard changes — no polling
```
`python-xlib` package. Similar for PRIMARY selection (highlight-to-copy).

### SQLite schema
```sql
CREATE TABLE clip_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,          -- Unix timestamp
    content     TEXT NOT NULL,
    content_len INTEGER NOT NULL,
    selection   TEXT NOT NULL,          -- 'clipboard' or 'primary'
    is_sku      BOOLEAN DEFAULT 0,      -- matched tgw\d{15}
    sku         TEXT,                   -- extracted SKU if is_sku
    app_name    TEXT,                   -- _NET_WM_NAME of clipboard owner (X11)
    dismissed   BOOLEAN DEFAULT 0       -- user-dismissed from history
);
CREATE INDEX idx_sku      ON clip_history (sku) WHERE is_sku = 1;
CREATE INDEX idx_captured ON clip_history (captured_at DESC);
```
DB path: `~/.local/share/tgw-clip/history.db`
Retention: configurable max rows (default 10,000); SKU rows never auto-expire.

### CLI surface
```
tgw-clip list [--limit N] [--sku-only]   # show history
tgw-clip last-sku                         # most recent SKU, regardless of current clipboard
tgw-clip search <pattern>                 # grep history
tgw-clip wipe                            # clear non-SKU history
tgw-clip daemon [--foreground]           # start/stop daemon
```

### Clipboard action surface (session 9 additions)
Requested actions to expose from clipboard context (via macroboard, Qtile chord, or tgw-clip CLI):

| Action | Description |
|--------|-------------|
| edit | Open current clip content in $EDITOR |
| send-to-suggest | Append current clip as `tgw suggest "..."` entry |
| sku-actions | If clip matches SKU: lookup, locate, open photos, add to picklist, set-template |
| location-actions | If clip matches location format: open folder, move all, view items |
| save-to-research | Tag and save clip to a "research" bucket (PERPLEXITY brief material) |
| save-to-personal | Save clip to personal notes (outside TGW pipeline) |
| save-to-sku | Associate clip with current SKU's item JSON (e.g. a URL, note, or reference) |
| combine-clips | Merge recent N clips into one buffer (for building multi-field entries) |
| split-clips | Split current clip by delimiter (line, comma, tab) into individual history entries |
| snippets | Named snippet storage + recall ("shipping boilerplate", "common titles", etc.) |
| long-history | Full history browser with search; backup to file; restore on login |

Design note: these actions are best exposed as a tgw-clip action menu (dmenu/rofi) triggered
from the macroboard `C` key or Qtile chord. The daemon provides the history; the action menu
provides the surface. SKU and location detection gates which actions are shown.

### Qtile integration (replaces polling in TGWSKUWidget)
- Daemon exposes a Unix socket at `~/.local/run/tgw-clipd.sock`
- `TGWSKUWidget` connects to socket on startup; receives push events (JSON lines)
- No more 2-second poll loop; widget updates instantly on clipboard change
- Fallback: if daemon not running, widget falls back to xclip polling (current behavior)

### tgw-macro / chord integration
- Super+T → c: calls `tgw-clip last-sku` instead of reading live clipboard
  → SKU persists across subsequent copies; chord action is reliable even after clipboard changes
- macroboard `g` / `h` / `c` keys: same `tgw-clip last-sku` fallback
- `tgw suggest "$(tgw-clip last-sku)"` pattern: capture SKU to plan inbox

### App-name tagging (Phase 2 idea)
X11 allows reading `_NET_WM_NAME` of the focused window at copy time. This means:
- "copied from Gwenview" → likely a file path → tag as `source=media`
- "copied from terminal" → likely a command or SKU → higher SKU detection priority
- "copied from Firefox" → likely a URL → tag for future eBay browse integration

### systemd unit
```ini
[Unit]
Description=TGW clipboard daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/tgw-clipd
Restart=on-failure

[Install]
WantedBy=default.target
```
User service: `systemctl --user enable --now tgw-clipd`

### Dependencies
- `python-xlib` (apt: `python3-xlib`)
- `python3-sqlite3` (stdlib)
- PP-WM-001 (Qtile) — the widget integration is Qtile-specific

### Phases
| Phase | Scope | Prerequisite |
|-------|-------|-------------|
| 1 | Daemon: X11 events + SQLite write + SKU tagging + CLI | PP-WM-001 installed |
| 2 | Qtile widget socket subscription (replace xclip polling) | Phase 1 daemon stable |
| 3 | App-name tagging; macroboard `last-sku` fallback | Phase 2 |
| 4 | eBay URL detection → auto-link to item JSON when SKU+eBay URL copied together | Phase 3 |

### Open design questions (decide before Phase 1)
- PRIMARY vs CLIPBOARD selection: watch both or just CLIPBOARD? Primary = highlight-select,
  clipboard = explicit Ctrl+C. For SKU capture, PRIMARY is more useful (highlight in terminal).
  Cost: twice the events to process.
- DB location: `~/.local/share/tgw-clip/` or alongside the TGW data tree? Lean toward
  `~/.local` since this is per-user, not per-installation.
- Daemon restart on config change: reload via SIGHUP or restart unit?
- Max content length to store: truncate at 10KB? Avoids storing accidental large pastes.
- Notify on new SKU detection: `notify-send` from daemon, or let Qtile widget handle it?

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

### Admin GUI spec (session 9 additions — mobile-first requirements)
The tablet/phone is the PRIMARY operator interface for warehouse operations. Design must be
**mostly operable without a keyboard** — checkbox and button interfaces wherever possible.

**Welcome screen (first/home tab)**
- Welcome message appropriate to system status — if major issue: prominently red/alerting
  ("eBay token expired — tap to fix", "X jobs dead-lettered" etc.)
- `tgw health` summary display — clickable chips for each service (tap = more detail)
- Key metrics: items live, items staged, queue depths, last sync time
- Operations buttons: most-common actions (publish staged, run sweep, refresh token)
- Notifications panel: worker completion, dead-letter alerts, new sold items
- **Audible alert** for critical issues that require immediate operator attention

**Listings management tab**
- **Ready state**: items fully prepared but not yet listed anywhere — separate from staged
  - "Set Ready" is the default done-state after staging review
  - Queue: items in Ready state are doled out at 1/60 of total (rate-limited automatic listing)
  - "List Now" button bypasses the dole-out rate for urgent items
  - Rate config: configurable; default = 1/60 of ready items per listing cycle
  - ✅ **Backend DONE 2026-06-12 (session 29, todo #88)** — carries the PP-REVISION-001
    draft→review→apply principle into code. `ebay_offer.ready_at` is the ready marker
    (offer `status` stays eBay's UNPUBLISHED/PUBLISHED — ebay_sync rewrites it, so the
    local review verdict has its own field; publish flips status → item leaves the pool
    automatically). `tgw.ready` module: `ready_pool()` (oldest-first), `set_ready`/
    `unset_ready` (validated, through the items fence), `tgw ready [list|set|unset <sku…>]`.
    Self-scheduling `ebay_dole` worker (velocity_stats pattern, queue `ebay_dole`):
    each cycle publishes `max(1, pool // dole_divisor)` oldest ready items via
    `cmd_publish`; config `dole_interval_s` (3600) + `dole_divisor` (60). `tgw staged`
    now excludes ready items (counts them as `ready_count`); `tgw publish` is the
    List-Now bypass. Unit needs operator enable (admin todo #120). GUI surface still
    future scope.
- Staged review queue (approve/reject checkboxes + publish button)
- Live listings browser with filter/search
- Pricing anomaly review tab: listings at extremes, stale reprice, comp mismatches

**Item editor tab**
- Browse by location (semi-chaotic location tree)
- Item detail: all fields editable; no keyboard required for most fields (dropdowns, sliders)
- Pipeline action buttons: re-identify, re-draft, re-price, stage, publish
- Photo gallery inline

**Logs tab**
- Recent worker output; filterable by worker name

**Admin tab**
- Queue management: dead-letter browser, re-queue/cancel buttons
- Health drill-down

#### Design enhancement — Model users and role-based layouts (session 16)
Different operational roles need different UI surfaces:
- **Admin** — full system control, debug access, config
- **Item creation** — intake form, barcode scan, hint entry, category group selection
- **Content admin** — batch title/description edit, photo review, quality scoring
- **Warehouse** — inventory location lookup, item physical processing, picklist
- **Operator** — staged listing approval, eBay publishing, repricing, sweeps

Planned: RBAC gates + role-specific default tabs and field visibility. Model users:
- Photographer (warehouse role)
- Pricing analyst (content admin)
- Operator (mixed admin/staging)
- Supervisor (audit/report focus)

### Phase 2 — Web-based inventory browse + listing detail ✅ COMPLETE (2026-06-14, session 29, todo #845)

Goal: a browser-accessible UI that works on any device on Tailscale — no TGW install required.
Solves the gap between the CLI/MC console and the Flutter app (which requires the Flutter toolchain
and build step). Implemented as additional routes on `tgw-http` using the same inline-HTML pattern
established by `/form/intake`, `/form/bulk`, `/form/todos`, and `/form/suggest`.

**New routes added to `src/tgw/http_server.py`:**

| Route | Auth | Description |
|-------|------|-------------|
| `GET /thumb/{sku}` | none | Thumbnail JPEG — thumbnail_root first, falls back to first ItemData image |
| `GET /media/{sku}/{filename}` | none | Serve any photo/video from ItemData; path-traversal validated |
| `GET /form/items` | none | Inventory browse page (see below) |
| `GET /form/items/{sku}` | none | Item detail page (see below) |

Both media routes use network trust (no Bearer) so `<img src>` tags in the browser work directly.
Path traversal is blocked: filename is checked with `Path(filename).name == filename`; sku is
checked for `..`; only known image/video extensions are served.

**`/form/items` — inventory browse:**
- Card grid: thumbnail, SKU (link to detail), title, status badge (colour-coded), location, price
- Live JS filtering: free-text search + location input (debounced 300 ms) + status chip bar
  (All / In Stock / Listed / Staged / Sold)
- Pagination: 60 per page, Prev/Next buttons, page X of Y display
- Hits `GET /api/items` (Bearer embedded in page JS, same pattern as `/form/intake`)
- Dark theme consistent with all other `/form/` pages

**`/form/items/{sku}` — item detail (server-rendered):**
- Two-column layout: left = photo gallery, right = field sections + diff + jobs
- **Photo gallery**: main large photo + clickable thumbnail strip; clicking a strip thumb updates
  the main photo via inline JS; photos served from `/media/{sku}/{filename}` (no auth)
- **Field sections**: Identity (title, category_group, condition, ai_hint, barcode, description),
  eBay (listing_id, status, live_price, url, qty), Physical (location, weight_oz, size_class)
- **Revision draft diff table**: if `revision_draft` is present in item JSON, renders a three-
  column table: Field | Current (from baseline snapshot) | Proposed (delta) — current in red,
  proposed in green. Shows metadata: by, at, baseline hash prefix. This is the primary use case
  for evaluating Claude's proposed revisions before applying them.
- **Pipeline jobs**: last 10 queue jobs for this SKU — queue name, state (colour-coded), updated
  timestamp, error detail
- Fully server-rendered (Python f-strings, `html.escape()` on all values); no client-side
  auth needed; photos via no-auth `/media/` routes

**Access:** `http://<tgw-host>:7373/form/items` on any Tailscale device, no login required.

### Design notes (session 19/20)

- **Recently-processed SKU sort** — `GET /api/items?sort=recently_processed`: a sort option
  ordering by the timestamp of the last pipeline action (enqueue, PATCH, or catalog_verify).
  Useful for reviewing batches just processed through ai_identify or ebay_draft. Add to catalog
  query options when PP-EDITOR-001 Phase E is in scope.

- **One-at-a-time review mode** — take any search result list and feed items to the editor
  one at a time with prev/next navigation. Operator can approve/flag/edit each SKU in sequence
  without returning to the list. Useful for post-pipeline QA, staged review, and photo review.
  Model: `GET /api/items/review-queue?from=<list_params>` returns a session token; `GET
  /api/items/review-queue/<token>/next` advances. Can be a simple URL-based state machine.

- **Backend contract for Flutter** (GEMINI-003 finding): app uses `/api/queue/status` for
  connectivity check. Correct endpoint is `GET /api/health` → JSON (todo #37); swap once done.

### Later phases (separate PPs)
- Scanner input (barcode/SKU lookup → item detail)
- **PP-INTAKE-001 intake screen** — pre-photo flow: weight, size_class, barcode scan, category group picker, ai_hint; location suggestion (semi-chaotic); Tasker camera trigger → intake form
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

#### Whisper.cpp implementation details (PERPLEXITY-004, 2026-06-05)
- **Model**: `base.en` for 5–15s English memos on 32GB CPU-only — 388MB RAM, sub-second to ~3s latency
- **Build**: CMake (most reliable), Docker `ghcr.io/ggml-org/whisper.cpp:main`, or Conan packages
- **Gotcha**: expects 16-bit WAV unless built with FFmpeg support; use `ffmpeg -ar 16000 -ac 1 -c:a pcm_s16le`
- **CLI**: `./build/bin/whisper-cli -m models/ggml-base.en.bin -f memo.wav`
- **Enable BLAS** on CPU for better throughput: `cmake -DGGML_BLAS=ON`
- **v1.8.4** released March 2026 — actively maintained
- Alternatives if needed: `faster-whisper` (Python, heavier), Vosk (lighter, lower accuracy)

#### Dependencies
- PP-REMOTE-001 (Tailscale + `tgw-http` reachable from Android)
- PP-IFDIR-001 (interface configs organized)
- Whisper.cpp binary installed (PP-ADD-010 AI runtime manager)

---

### PP-INTAKE-001 — Photographer Intake: Template-Driven Multi-Surface System

#### Core architectural insight — the Template
The template is the key. One button press selects a template (a category group) and instantly
applies the best available assumptions for that item class: `size_class`, `ai_hint`, typical
price range, store category, fulfillment policy. Everything else is optional fine-tuning.

The system is already partially wired: `category-groups.json` IS the template table (PP-PRICE-005 ✅).
The `SETTEMPLATE:name` / `COMMAND:...` clipboard protocol IS the push channel to the camera app.
The photographer already has this tooling. The work is to complete the integration loop.

**Graceful degradation by design** — the system works at every level of photographer participation:
```
No photographer input → ai_identify derives group → group defaults apply     (baseline)
Template selected     → group defaults + better ai_identify hint             (good)
Template + fine-tune  → all fields correct at intake                         (best)
```
The photographer never blocks the pipeline. More input = better result, but absence of input
is handled automatically. The system self-improves as velocity data refines template pricing.

#### Existing photographer interface — three surfaces
Already operational; PP-INTAKE-001 extends, does not replace.

| Surface | Technology | Role |
|---------|-----------|------|
| **Camera HUD** | Camera app + KDE Connect clipboard relay | Receives SETTEMPLATE:/COMMAND: from TGW; shows current item state during shoot |
| **Desktop HUD** | Qtile widget (PP-WM-001) + floating overlay | Live queue status, current SKU, pipeline progress |
| **Web form** | tgw-http (existing, to be updated) | Fine-grained field entry; opens in browser/WebView from any surface |
| **xmouse macros** | Tablet macro pad → SSH → TGW commands | One-button template selection + quick overrides |
| **USB scale** | `weight()` / `get_weight()` in tgw.source | Physical weight capture → size_class derivation |
| **Whisper dictation** | `whisper-hint()` etc. in tgw.source | Voice → ai_hint, voice → title, voice → condition |

#### The template dispatch loop
```
xmouse button press
    → SSH → tgw set-template <group_key>
        → writes group defaults to CurrentItem JSON
            (size_class, ai_hint, category_group, ebay_category_id)
        → pushes "SETTEMPLATE:<group_name>" via KDE Connect clipboard relay
            → camera app HUD updates to show active template
        → pushes "COMMAND:DATA:size_class=<val>" etc. if fine-tuning needed
    → bundle_intake picks up item with pre-populated fields
    → ai_identify gets group ai_hint as context → better result
    → suggest_price gets category_id → group floor/typical → priced even with thin comps
```

#### `tgw set-template` — ✅ BUILT (CLI session 8, web form session 11)
```bash
tgw set-template <group_key> [sku]           # apply group defaults to CurrentItem or given SKU
tgw set-template --list                      # show all available templates (from category-groups.json)
tgw set-template --camera <group_key>        # push SETTEMPLATE: to camera via KDE Connect only
```
What it writes to item JSON:
- `category_group`: group key
- `ai_hint`: group.ai_hint (prepended, preserves existing if any)
- `size_class`: group.size_class
- `ebay_category_id`: first category in group.ebay_categories (if not already set)
- ~~`fulfillment_policy_id`: derived from size_class → config lookup~~ — **NOT implemented**
  (session 15 audit): the template never writes a fulfillment policy. The cleaner per-item
  mechanism is PP-HINT-001 `shipping_profile` (round-2 rank 8) + PP-STORAGE-001
  `size_class → fulfillment_policy_by_size_class` resolver (round-2 rank 9).

xmouse maps each group to a dedicated button. 24 groups = 24 one-press intake macros.

#### Template table maintenance (self-improving)
- `tgw category-groups --reseed` recomputes typical_used/floor from current velocity data ✅
- As new items sell and velocity grows, template pricing tightens automatically
- Dave can manually curate ai_hint and size_class per group as item knowledge grows
- Future: ai_identify confidence → auto-suggest template corrections back into category-groups.json

#### Fine-grained tailoring (when template isn't quite right)
1. xmouse has additional buttons for common overrides: weight entry, condition override, barcode scan
2. Web form (tgw-http) shows template-applied defaults; photographer edits only what differs
3. Voice: `whisper-hint()` appends to the ai_hint that template already pre-filled
4. Desktop HUD shows the active template; operator can see and correct before pipeline runs

#### Background inference (future — better compute required)
When Ollama runs faster (GPU upgrade, PP-NIXOS-001 migration):
- `ai_identify` enqueued immediately when first photo lands in newitems/
- Preliminary identification returned to camera HUD while photographer is still shooting
- Result feeds back as suggested template confirmation: "Looks like Kitchen Utensils — correct?"
- Operator confirms or overrides → no post-session correction pass needed
- Weight from USB scale + ai_identify result → size_class confirmed automatically

#### Phases
- **Phase 1** — `tgw set-template` command: writes group defaults to item JSON + KDE Connect push. xmouse macro buttons per group. Closes the template→pipeline loop. `tgw set-template --list` for operator discovery.
- **Phase 2** — Web form update: add template picker (24 group chips), weight field, barcode field. Pre-fills from current template; photographer only changes what's wrong.
- **Phase 3** — Camera HUD integration: SETTEMPLATE: response shows group name + ai_hint summary + size_class on camera display; photographer sees confirmation before next shot.
- **Phase 4** — Background inference: ai_identify enqueued on first photo drop; result shown on HUD; operator confirms/overrides mid-session.
- **Phase 5** — Template self-update: ai_identify results with high confidence → suggest category_group refinements; velocity data → auto-reseed pricing monthly.

#### Computer-side intake workflow (session 9 addition)
Current path: camera app creates JSON/photo/folder set on device → Syncthing → bundle_intake.
**Alternative**: initiate the intake workflow from the computer side, reducing steps on the phone.

Concept:
1. Computer pre-creates the SKU folder and blank item JSON (with template pre-applied)
2. Syncthing pushes the folder to camera device
3. Camera app detects new folder → switches to photo mode for that SKU automatically
4. Photos taken → Syncthing returns them → bundle_intake picks up
5. Result: phone is purely a camera; all data entry on computer; faster per-item processing

This is architecturally simpler than the current push-from-camera model and may be faster
in practice. Design as a Phase 2.5 addition: `tgw create-item [--template GROUP_KEY]` that
pre-creates the folder + triggers camera app via KDE Connect COMMAND:.

#### Camera root intent (future — session 9 note)
Goal: root intake cameras to gain file system access during Foldio360 turntable sessions.
**Problem**: Foldio360 app does not expose photos until after zipping them; the zip step
doubles total processing time per spin. Root access bypasses the zip, reading photos directly.
**Path**: target Android devices known to have reliable root methods (Pixel series + Magisk).
Eventually deploy with custom ROMs to get fine-grained control and remove bloatware.
**Custom camera app (PP-INTAKE-002)** — ⬆ elevated to active design 2026-06-12 (Dave suggestion 17:51): replace
Tasker + stock camera with a TGW-native Android app that **incorporates the Tasker functions
directly into the interface** — barcode scan, template select (SETTEMPLATE HUD), camera trigger,
voice hint, upload via Syncthing folder or tgw-http — no third-party dependencies.
**Design RETURNED 2026-06-12** (gemini todo #115 done): full Flutter scaffold proposal at
`reference/PP-INTAKE-002-camera-app-design.md`. Highlights: `mobile_scanner` (ML Kit) barcode,
`flutter_tts` voice, Riverpod state, Dio HTTP, `flutter_rfb` VNC, dual upload (Syncthing
folder + tgw-http POST), Foldio360 zip-bypass via root `su` polling (short-term) + BLE direct
control via `flutter_blue_plus` (long-term). Dave must review before scaffold build begins.
Three open questions: root-privilege packaging strategy (app vs shell script), target device
for root (Pixel/Xiaomi), Syncthing path alignment (`/sdcard/Pictures/TGW_Sync/`).

**xmouse replacement app (PP-INTAKE-003)** — ⬆ elevated to active design 2026-06-12 (Dave suggestion 18:20):
open-source Android app (GitHub-based) replacing the xmouse macro pad, incorporating an
**RDP/VNC client and a form tool** in one interface — macro grid dispatching via SSH/tgw-http
(template buttons, pipeline triggers), embedded remote viewer for desktop sessions, and a form
surface for the `/form/*` tgw-http pages.
**Design RETURNED 2026-06-12** (gemini todo #116 done): full Flutter architecture survey at
`reference/PP-INTAKE-003-xmouse-replacement-design.md`. Recommendation: Flutter stack with
`flutter_rfb` (Apache-2.0 VNC, avoids GPLv3 contamination from aRDP/bVNC), `dartssh2` (MIT
SSH), `flutter_inappwebview` (form surface). 3-phase roadmap: P1 macro grid + SSH/HTTP dispatch,
P2 form tool integration, P3 embedded VNC. Dave must review before any build.
**SETTLED (Dave, 2026-06-12):** Flutter + Apache-2.0/MIT path confirmed. GPLv3 native-Android
path (bVNC/aRDP lineage) rejected. Design doc at `inbox/review/xmouse-replacement-design.md`
pending review; scaffold task to be seeded as a Claude/Aider todo after review.

#### Dependencies
- PP-PRICE-005 `category-groups.json` ✅ DONE — this is the template table
- PP-WM-001 Qtile desktop HUD ✅ Phase 1 done
- PP-WHISPER-001 voice capture (Phase 1+ whisper-hint already works)
- KDE Connect + COMMAND:/SETTEMPLATE: clipboard relay (already in tgw.source — rescued from deprecated in SHELL-AUDIT.md 2026-06-06)
- PP-REMOTE-001 (tgw-http reachable from tablet for web form)
- GPU upgrade / PP-NIXOS-001 (Phase 4 background inference)

---

### PP-TODO-001 — Multi-agent TODO tracker (`tgw todo`)

**Agent rename (Dave, 2026-06-11 18:34):** `db` agent renamed **`sokoban`** (warehouseman)
— existing item delegated, future physical/warehouse tasks use `tgw todo sokoban`.
Dave also flagged that many admin tasks live in plan tables but not the tracker — same
two-surface gap as Round-5 rows (handoff risk 9); seed operator items as todos when
rounds are created, same rule as Claude items.

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

#### Unique ID per task (session 9 requirement)
Every todo item must have a **unique numeric ID** to make interaction unambiguous:
```
tgw todo task 265 completed
tgw todo task 832 delegate gemini
tgw todo task 24 update "waiting on IGDB key"
```
IDs are auto-assigned (PostgreSQL `SERIAL`), never reused. This enables precise cross-session
references, especially in SUGGESTIONS.md entries and voice dictation (no spelling ambiguity).
`tgw todo` list output must always show the ID prominently as the first column.

#### Dependencies
- PP-CAPTURE-001 (idea pipeline design) — aligns on storage back-end choice

#### Connection to Work Tracks strategy test
The 4-track delegation model (session 5) is the motivating use case. Work Tracks gives each
agent a queue; PP-TODO-001 makes that queue queryable and persistent across sessions. The
`tgw todo claude` / `tgw todo gemini` / `tgw todo admin` structure maps directly to Tracks 1,
2, and 4. Build PP-TODO-001 so Work Tracks items can be seeded into it on first run.

#### Design enhancement — Quick-access dashboard (session 16)
Dave requested immediate access to todo queue without hunting through the master plan:
- `tgw todo` output must be **quick** (no scrolling, no plan context needed)
- Links to delegated tasks + supervisory duties **inline** in todo list
- This becomes the "source of truth" for daily work flow, especially under duress
- Mobile/tablet-friendly variant planned for future PP-EDITOR-001 admin GUI
- **Rationale**: "All of those simple little things cause distraction and consume time and lead to errors"

---

### PP-PYIPC-001 — Python IPC: Syncthing + KDE Connect Integration

#### Goal
Replace shell-subprocess calls to `kdeconnect-cli` and Syncthing with Python library
bindings so TGW workers and the FastAPI service can interact with both services
programmatically — events, status, clipboard, file transfer.

#### Syncthing (PERPLEXITY-005 findings — session 19/20)
- REST API at `localhost:8384` — Syncthing is **already running** on the production machine
- Config + API key at `/opt/TGW/.local/syncthing/config.xml` (in-project, `chmod 600`)
- API key is parsed from the config.xml `<apikey>` element at PP-PYIPC-001 implementation time
- **Recommended library**: `pyncthing` (PyPI) — requests-based, best-maintained, supports PATCH,
  modern Syncthing versions. `aiosyncthing` is stale (labeled as such even in its own README).
- **Async event streaming**: `pyncthing` is synchronous; for long-polling `/rest/events` or
  `/rest/events/disk`, implement a thin custom `httpx`-based async consumer with `since`/`timeout`
  params. This is the recommended TGW pattern — keeps the event loop non-blocking.
- **Relevant endpoints**: `/rest/events/disk` (pre-filtered file/folder events), `/rest/db/status`
  (folder state + `needBytes`) — use together to confirm sync completion before triggering rebuilds
- TGW integration: when `tgwcatalog.db` folder goes idle → enqueue `catalog_rebuild` job
- Config key to add: `syncthing_config_path` → defaults to `/opt/TGW/.local/syncthing/config.xml`

**⚠ PP-PYIPC-001 is now fully unblocked** — Syncthing is live, API key in-project. No operator action needed.

**Multi-user NixOS design (session 19/20):**
- Current port: 8384; NixOS target port: 8385 (separate from user instances; see PP-NIXOS-001)
- LTSP fat clients: per-hostname config directory symlink for location-specific folder mappings

#### KDE Connect (PERPLEXITY-005 findings)
- No mature Python PyPI package; use **`pydbus`** for D-Bus access (`org.kde.kdeconnect.daemon`)
- `kdeconnect-cli` via subprocess for one-shot operations; `pydbus` for long-running services
- Clipboard strategy: monitor X11 clipboard locally (`python-xlib`) → push via KDE Connect as
  transport. Android 10+/14 restricts clipboard access to foreground apps; desktop side is unaffected.
- TGW integration: `ic_template()`, `ic_command()` wrappers in Python; push from workers

#### Additional findings from PERPLEXITY-005

**DB migration path:**
- psycopg3 (`psycopg` on PyPI) is the clear psycopg2 successor; start synchronous, add async later
- `aiosqlite` for FastAPI catalog read paths (prevents blocking event loop); keep sync writes in workers

**`python-xlib` status:** Effectively stale upstream (no PyPI releases in 12+ months); distribution-
level patches only. Works for X11 clipboard today but not a long-term bet given Wayland migration.
Consider replacing with a Wayland-aware clipboard solution when moving to NixOS + Wayland.

**Whisper.cpp bindings:** `whispercpp.py` and `pywhispercpp` are newer/better than `whisper-cpp-python`;
both embed whisper.cpp as a submodule and track newer versions. Wrap behind a TGW audio-to-text
interface to enable swapping implementations.

**`discogs_client` deprecated:** Discogs officially marked it as "no longer maintained" and now
recommends using a generic REST client. TGW should wrap Discogs access behind an adapter in
`apis/lookup/discogs.py` and migrate to direct `httpx` calls (additive, isolates the breakage risk).

**Shipping APIs:** PirateShip has **no stable public API** (reverse-engineered only; fragile).
**EasyPost** is the recommended alternative: official Python client, rate shopping, label purchase,
address validation, tracking, insurance. Strong candidate for PP-FULFILLMENT-001 Phase 2.

**Barcode scanner:** `python-evdev` reads `/dev/input/event*` focus-independently — better than
keyboard-wedge mode for TGW's dedicated workstations. Relevant to PP-FULFILLMENT-001 hardware phase.

**USB scales:** `hidapi`/`hid` (Python `hid` package wrapping libhidapi) or `pyusb` — open by
vendor/product ID, read raw HID reports, decode weight. Better than shell-based approaches.

**Enrichment upgrades:** Go-UPC and Apify barcode/PriceCharting actors outperform upcitemdb free tier
significantly. Consider as replacement for PP-LOOKUP-001 upcitemdb primary source.

**eBay SDK:** `ebaysdk-python` last release ~April 2020; classified inactive; no support for modern
REST Sell APIs. TGW's current direct REST integration is already correct — no change needed.

#### Dependencies
- Syncthing config at `/opt/TGW/.local/syncthing/config.xml` ✅ present; API key parsed from `<apikey>` element
- Add `syncthing_config_path` to `tgw-api-config.json` (default: `/opt/TGW/.local/syncthing/config.xml`)
- Add `syncthing_url` to config (default: `http://127.0.0.1:8384`)
- PP-WM-001 (Qtile clipboard widget uses subprocess xclip; migrate to pydbus/KDE Connect)

---

### PP-VERIFY-001 — Catalog Assumption Verification + Hall Pass Flag

#### Problem
55K items accumulated over many years contain assumption violations — missing required
fields, invalid status combinations, stale eBay data, inconsistent location formats.
Currently there is no tool to enumerate violations at scale.

#### Design
**`tgw catalog-verify [--location X] [--limit N] [--write] [--fix]`**
- Scans ItemData or a subset; checks each item against a set of assumption rules
- Rules (examples): title not empty, title ≠ SKU, location format valid, has at least
  one photo, `ebay_category_id` is numeric, `verified` is YYYYMMDD format, no stale
  `TEMPLATE:` prefix in title, `#STATUS` is a recognized value, etc.
- Output: markdown checklist of violations grouped by type; SKU + field + violation
- `--write`: stamps `catalog_verified: {timestamp, by: "catalog-verify"}` on passing items

**Hall pass flag**: `catalog_verified` field in item JSON
- Set when item passes verification (or after manual operator review)
- Cleared automatically whenever any field is written (catalog-rebuild resets it)
- `tgw catalog-verify` skips items with `catalog_verified` set unless `--force`
- Prevents re-flagging manually confirmed edge cases (legacy items with intentional quirks)

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | `tgw catalog-verify` command; 9 assumption rules; markdown report by severity | ✅ **DONE (session 13)** |
| 2 | `catalog_verified` hall pass; clear-on-write in `_write_field`; `--force` to re-check | Next |
| 3 | Fix-in-place for auto-fixable issues (stale TEMPLATE: prefix auto-strip, etc.) | Future |

#### Phase 1 implementation (done)
9 rules implemented in `_verify_item()` + `cmd_catalog_verify()` in `api.py`:
- **critical**: `no_title`, `stale_template_prefix`, `json_parse_error`
- **warning**: `title_is_sku`, `title_too_short`, `no_location`, `no_photo`, `invalid_ebay_category`
- **info**: `bad_verified_date`, `unknown_status`

CLI flags: `--location`, `--limit`, `--severity`, `--output`, `--json`. 10 tests.

---

## Pending projects (revisit)

### PP-PORTABLE-CATALOG-001 — Portable / Satellite Catalog

#### Problem
The tablet and spare intake machine need read-access to item catalog + thumbnails to work as intake/browsing stations. Currently `tgwcatalog.db` lives only on the master machine. Syncthing can sync it, but there is no operator-friendly command to prepare a sync-ready bundle, and the catalog needs a stable export shape that works on a machine with no live PostgreSQL.

#### Design (Phase 1 — Syncthing-sync, no conflict resolution)
- `tgw export-catalog <dest>` command: copies `tgwcatalog.db` (55K rows) + `thumbnails/<SKU>.jpg` subset to `<dest>/`
- Syncthing watches `<dest>/` on master and syncs to client machines automatically
- Client machines: read-only browser (tgw-http or MC); writes go back to master via tgw-http when online
- **Phase 1 scope**: export only (no conflict resolution, no return path); Syncthing handles transport
- Snapshots the current catalog state; `tgw export-catalog --incremental` could be added later

#### Architecture
```
master
  tgwcatalog.db + thumbnails/  ← tgw export-catalog → export/
                                                              ↓ Syncthing
                                                         tablet, spare machine
                                                          tgw-http (read-only mode)
                                                          MC extfs tgwcatalog
```

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | `tgw export-catalog <dest>` + Syncthing transport (operator configures Syncthing) | ✅ **DONE (session 18)** — `src/tgw/catalog_export.py`; 8 tests; live verified |
| 2 | Flutter offline-first client: snapshot+copy-to-sandbox; sqflite outbox; connectivity_plus + workmanager Android flush | Future (PERPLEXITY-006 design complete; needs Syncthing API key for API-driven export trigger) |
| 3 | Conflict resolution, per-row change-log, merge audit trail | Future |

#### Phase 2 design — Flutter offline-first client (PERPLEXITY-006)

**Critical pattern — never open the synced file directly:**
```
Syncthing syncs → catalog.db (write-locked or in-use mid-sync → corruption risk)
                        ↓ app startup
                 snapshot + copy to app-private storage
                        ↓
                 open private copy (sqflite) ← safe to read/write
                 offline outbox table (pending mutations)
                        ↓ connectivity restored
                 flush outbox → POST /api/items/{sku} on master
                 server returns latest export → replace private copy
```

**Library stack:**
- `sqflite` + `sqflite_common_ffi` — SQLite on Android + Linux desktop
- `sqlite3` package (not sqlite3_flutter_libs, which is deprecated for 3.x)
- `dio` + `dio_smart_retry` — HTTP client with automatic retry
- `connectivity_plus` + health ping (`GET /api/health`) — connectivity detection
- `workmanager` — Android background flush scheduling
- `flutter_secure_storage` — token/secret storage; requires `libsecret-1-dev` on Linux

**Server-side snapshot export:**
Use `sqlite3.Connection.backup(dest)` for atomic SQLite copy (avoids mid-write corruption).
Endpoint: `GET /api/catalog/snapshot` → streams `tgwcatalog.db` snapshot.

**Sync-conflict resolution worker (DONE 2026-06-13):** `src/tgw/sync_conflict.py` + 47 tests.
Decision tree (see module docstring):

- `identical` → auto-discard (byte-for-byte match)
- `divergent_pipeline` → move to `inbox/review/`, priority-15 todo (conflict has unique/different TGW pipeline data: status `sold` vs `In Stock`, unique `ebay_listing`, etc.)
- `divergent_legacy` → move to `inbox/review/`, priority-65 todo (only obsolete M1/M2/CSV fields differ + stale-default status; low operator urgency)
- `divergent` → move to `inbox/review/`, priority-30 todo (general divergence)
- `no_canonical` → move to `inbox/review/`, priority-45 todo (canonical missing)

Zero-data-loss invariant: nothing is auto-deleted except byte-identical copies. "keep-newer" and "keep-larger" are NOT safe auto-resolution rules — mtime/size do not prove content safety. Semantic JSON analysis does.

**Design principle — zero data loss (Dave, session 19):** A `.sync-conflict-*` file is Syncthing's
*safety* mechanism, not an error. Syncthing never resolves indiscriminately — it completes the
sync and says "hey, look at this," which is precisely why it's the right choice. The worker must
honor that:
- The conflict copy is **usually redundant** (identical to, or strictly older-with-no-unique-
  content vs, the canonical file) → safe to discard.
- But **sometimes** the conflict copy is a local edit made *before* the remote synced — unique
  content that blind discard would permanently lose.
- So the worker **must compare** conflict-copy vs canonical and auto-discard *only when provably
  redundant*; anything with divergent/unique content is **flagged for operator review, never
  auto-deleted**. The invariant is: no path through this worker can cause data loss.
- (Live test case left in the vault on purpose: `.obsidian/community-plugins.sync-conflict-…json`
  — observe how a future worker classifies it before building auto-resolution.)

#### Dependencies
- `tgwcatalog.db` (already built, 55K rows)
- Thumbnail cache (already built, 54K thumbnails)
- Phase 2+: PP-PYIPC-001 (Syncthing REST API), Syncthing API key

#### Status
Plan section added session 18. Phase 1 in Round 4.

---

### PP-PLASMA-001 — KDE Plasma 6 Dual-Desktop Integration

#### Vision
TGW runs two desktop environments: Qtile (primary operator workstation — tiling, Python hooks, TGW bar widgets) and KDE Plasma 6 (general purpose — familiar, full-featured, Firefox, GLabels, LibreOffice). Both are first-class citizens. Plasma handles day-to-day use and GUI app launching; Qtile handles warehouse operations, agent sessions, and pipeline monitoring.

#### Motivation (session 16 suggestion)
The TGW operator workstation will rely heavily on the KDE framework even on Qtile. KDE apps (Dolphin, Gwenview, Konsole, KDialog, KDE Connect, GLabels) are used in the warehouse workflow. Running Plasma 6 in parallel gives a familiar environment for non-TGW tasks without compromising the Qtile operator experience.

#### Integration opportunities
| Area | Qtile | Plasma 6 |
|------|-------|----------|
| File management | F2 menu → Dolphin launch | Dolphin natively |
| Image viewing | chafa in MC / Gwenview launch | Gwenview natively |
| Clipboard relay | KDE Connect (tgw.source ic_*) | Plasma clipboard sync |
| Notifications | notify-send / dunst | Plasma notification daemon |
| GLabels (barcode) | Launch via keybinding | Plasma app launcher |
| Terminal | Konsole / scratchpad | Konsole natively |
| Quick switch | Super+T TGW mode in Qtile | Plasma Activities |

#### NixOS dual-desktop on NixOS
On NixOS, both WMs are declared in the same flake:
```nix
services.xserver.windowManager.qtile.enable = true;  # operator session
services.desktopManager.plasma6.enable = true;         # general session
```
Both available at login; user selects per-session.

#### Phases
| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Shared config: dunst notif theme, Konsole profile, Dolphin TGW bookmarks | Future |
| 2 | Qtile→Plasma clipboard bridge via KDE Connect (already works via tgw.source) | Future |
| 3 | NixOS dual-desktop declaration in flake | Depends on spare machine validation |

#### Status
Plan section added session 18. No code this round. Design/tracking only.

---

### PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)

#### Concept (session 18 — discuss before building)
Instead of a monolithic Markdown plan file, the plan at any point in time is **rebuilt on demand** from a task + relationship database. Each PP-* item, work track, and todo entry lives in the DB with explicit relationships (blocks/depends-on). The plan document becomes a rendered view, not the source of truth.

**Agent delegation extensions:** The DB can generate self-contained `CLAUDE.md` and `gemini.md` files for any delegated task — baked with exactly the context that agent needs, no more. Useful for employees, mechanical turks, or future agent roles. The todo tracker (PP-TODO-001) is the embryonic form of this; PP-PLANDB-001 is the full realization.

**Dave's note:** "tgw plan builder" — discuss the scope and design before implementing. The todo tracker is already moving in this direction; the question is whether to extend it or build a separate plan-reconstruction layer.

#### ✅ DECIDED 2026-06-12 (session 28) — Option C: DB owns tasks, generated taskboard

Design questions answered:
- **DB vs Markdown:** the DB owns *tasks* only; design prose stays hand-authored Markdown in the
  master plan (the prose was never the drift problem). Task tables leave the plan entirely and are
  rendered into a **wholly-generated companion file** `plan/TGW-Taskboard.md` (one writer — no
  Syncthing mixed-edit conflicts; `/form/todos` renders the same DB for tablet).
- **PP-* items:** stay as plan prose sections; todos link to them via `pp_ref` + `plan_anchor`.
- **Relationships:** `depends_on`/`blocks` (blocker badges on the taskboard) + `pp_ref`. That's it
  for now; tracked-by/delegates-to are covered by the existing `agent` column.
- **Generated agent context:** `tgw todo brief <id>` — self-contained per-agent task spec (the
  Aider message-file pattern from next-process.md) built from the todo + linked plan-section
  extract. Minimal context, link out for more — prevents bloat.
- **Version history:** tasks in PostgreSQL (raises the stakes on PP-BACKUP-001 Phase A #61);
  rendered taskboard lands in git via vault commits — render history for free.

**Write-gateway architecture (Dave, 2026-06-12):** Dave no longer edits the plan directly — all
input flows through `inbox/` + `tgw suggest`, i.e. the **PP-DOCFLOW-001 project admin is the
single write-gateway for both surfaces**: it classifies submissions → creates todos (setting
`pp_ref`/`depends_on` when confident, review-flag when not) → appends prose to the plan only for
design/rationale → the render job regenerates the taskboard. The drift channel is structurally
closed, not discipline-closed. `tgw plan check` (Phase 3) becomes a safety net on the *admin's*
work, and its mismatch reports feed the long-term improve-the-admin loop (misfile → review flag →
correction → prompt/rule update). Script what we know; dump the rest into the inbox for the admin.

**Phases (Round 7 todos #109/#110/#112):**
| Phase | Scope |
|-------|-------|
| ✅ 1 | **DONE 2026-06-12 (session 29)** — `todo_items` gained `pp_ref TEXT`, `depends_on INT[]`, `plan_anchor TEXT` (migration applied); `tgw todo --pp/--depends/--anchor` on `--add` + `--set-meta ID` for existing items; `tgw todo brief <id>` self-contained task spec (todo body + master-plan section extract + dependency status + constraints, Aider message-file pattern); classify-suggestions LLM sets `pp_ref` when confident (format-validated, hallucinated refs dropped); pp_ref backfilled on 23 existing todos from body text; pp_ref + blocker badges in `tgw todo` listing |
| ✅ 2 | **DONE 2026-06-12 (session 29)** — `tgw.plan_render` module + `tgw plan render` → generated `plan/TGW-Taskboard.md` (per-agent tables ID/pri/size/task, blocker badges from open `depends_on`, Obsidian links to plan headings via `pp_ref`/`plan_anchor` with auto heading resolution, done-this-week section, atomic write); coalesced `plan_render` queue job on every todo mutation (dedupe `plan_render:pending` + 30s not_before, catalog-rebuild pattern); `plan_render` worker (`tgw-worker@plan_render.service` — **operator must enable, admin todo #119**); `check_taskboard()` staleness warning in `tgw health` (yellow when tracker changed >10 min after last render). 20 new tests; 637 passing |
| 3 | `tgw plan check` plan↔tracker reconciliation in the session-start ritual (todo #112, depends_on=[110] now cleared) |
| 4 | Only if ever needed: generated PP-status lines inside plan sections |

#### Dependencies
- PP-TODO-001 (already built — provides task storage; PP-PLANDB-001 extends it)

---

### PP-BACKUP-001 — Organized Backup and Disaster Recovery Architecture

#### Problem (session 16)
`trader-grims-backup` repository is archived but still occupies repository space and mental overhead.
A custom unified backup/archiving/restoration/disaster-recovery suite is needed to replace it and
encompass all backup concerns (config, secrets, ItemData, logs, databases, system state).

#### Design goals
- **Backup repository separation**: Move `trader-grims-backup` to a separate, independently managed
  repository so TGW platform repository is uncluttered
- **Unified DR suite**: Replace fragmented backup logic with a single source of truth for:
  - Regular incremental backups (ItemData, databases, config)
  - Archival policy (old history, sold items, legacy data)
  - Restoration (point-in-time recovery, disaster scenario planning)
  - Verification (backup integrity checks, restore dry-runs)
- **Integration with PP-NIXOS-001**: The NixOS rebuild strategy enables atomic system restore
  from config + backup snapshot

#### Scope
- Phase 1: Extract `trader-grims-backup` to separate repo; audit its current usage
- Phase 2: Design unified suite (modules: backup, archive, restore, verify)
- Phase 3: Implement per-module; wire into systemd timer and health checks

#### Dependencies
- PP-NIXOS-001 (system rebuild context)

#### Status
**PLAN APPROVED 2026-06-11 (session 24)** — full plan at `plan/PLAN-backup-dr.md`
(approved by Dave same day, after amendment through all 13 session-24 suggestions;
Phase A build unblocked = todo #60). Host audit found: local snapshot tier healthy
(dedicated 699 G disk, current); cloud tier **27 days stale** (manual rclone only);
**ledger has zero dumps**; **secrets have no backup at all**. Phase A (MX-now: pg_dump
timer, scheduled rclone with --backup-dir trash, gpg-encrypted secrets bundle,
backup-freshness health check, restore drills, archive policy) → Phase B (repo split +
restic engine + `tgw backup` CLI) → Phase C (declarative in the Nix flake — resolves
NixOS plan R9 properly; recovery equation extended). Round 6 #54/#55; todos #60/#61.

---

### PP-DEADLETTER-001 — Dead-letter triage: warn+requeue instead of terminate

#### Problem (observed 2026-06-06 session 9)
Several failure types routinely end up in `dead_letter` when they should instead emit a
notification and requeue — dead-letter requires manual operator intervention to clear,
which builds up silently. The current troubleshooting workflow is: `tgw health` → see
dead_letter count → run SQL to identify → categorize → manually cancel/requeue.

#### Known dead-letter types that should be warn+requeue

| Error pattern | Current behaviour | Better behaviour |
|---------------|------------------|-----------------|
| `token is expired` (ebay_sync, ebay_legacy_sync) | dead_letter after 5 attempts | warn + back off 15min + requeue; clear on next successful sync |
| `section not found in plan` (pm_intake) | dead_letter | warn + log + skip (don't block other inbox items) |
| `no eBay photo URLs yet` (ebay_stage) | dead_letter | retry with longer backoff (ebay_upload may still be running) |
| `Directory not empty` (catalog_rebuild) | dead_letter | retry immediately (transient OS race) |
| `ReadTimeout` / `LEASE_EXPIRED` (ebay_draft) | dead_letter | retry with fresh lease |

#### Dead-letter types that ARE correct

| Error pattern | Reason to keep as dead_letter |
|---------------|-------------------------------|
| `HardFailure: no ebay_category_id` | Needs operator/AI intervention to fix item data |
| `HardFailure: eBay rejected (25002/25021/25709)` | Needs code fix or item data fix |
| `HardFailure: item specific value too long` | Needs item data fix |

#### Design — ✅ IMPLEMENTED (session 13)

**`classify_dead_letter(error_text: str) -> tuple[str, int]`** in `worker_base.py`:
- Pattern matches error text (case-insensitive substring) against `_TRANSIENT_ERRORS` list
- Returns `('requeue', delay_seconds)` or `('dead_letter', 0)`
- `requeue_with_backoff(job_id, owner, delay, error)` in `state_machine.py`:
  transitions running→retry_wait; resets `attempt_count=1`; sets `error_code='TRANSIENT'`
- `QueueWorker._process()` intercepts exhausted-retry `Exception` path:
  checks `attempt_count >= max_attempts`, classifies error, reschedules instead of dead-lettering

**Transient patterns implemented:**
| Pattern (substring, case-insensitive) | Delay |
|---|---|
| `token is expired` | 900s (15 min) |
| `no ebay photo urls yet` | 600s (10 min) |
| `directory not empty` | 30s |
| `readtimeout` | 120s |
| `lease_expired` | 120s |
| `connectionerror` | 120s |

**HardFailure** (raised explicitly) still goes directly to dead_letter — no change.
`section not found in plan` (pm_intake) handled separately in pm_intake.py (warn+skip).

**Remaining work — ✅ DONE 2026-06-12 (session 29, todo #94):**
- ✅ T/H split: `dead_letter_errors()` in `state_machine.py` + `classify_dead_letter_errors()`
  in `health.py`; `check_postgres` detail now reads
  `dead_letter=33 T0/H33 [ai_identify:12(T0/H12), …]` and returns
  `dead_letter_transient/hard/classified`; `tgw_queue_status` MCP tool returns the same
- ✅ Zero-work watchdog (the ebay_sku_migrate silent-stall pattern): `zero_work_queues(h)` —
  worker heartbeat alive + eligible queued jobs (not_before excluded, so self-scheduling
  workers don't false-positive) waiting > `zero_work_stall_hours` (config, default 4.0) with
  zero succeeded transitions in the window → yellow WARN in `check_postgres` + MCP
- `notify.warning()` emit on transient requeue path was already done (session 14)

### PP-HINT-001 — AI hint + eBay enrichment (revisit required)
- First iteration shipped 2026-06-03: `ai_hint` field, `tgw hint` command, hinted vision prompt
- **Known gaps to address:**
  - `tgw requeue` bulk command: filter-based batch re-queue (e.g. "all items with photos but no title") for catalog maintenance — without triggering eBay listing pipeline
  - eBay Browse API enrichment in `ebay_draft`: search similar active listings by title, extract common aspects and category signal to supplement AI-generated specifics
  - ✅ Full item history / hint trail (2026-06-08): `identification_history` list in item JSON; `append_history_event()` in `items.py`; `ai_identify` + `hint_set` event types; `tgw hint-trail <sku>` CLI display
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

### PP-STRIKE-001 — eBay Strikethrough Pricing

#### Background
Dave was approved for eBay's Strikethrough Pricing program many years ago. This lets sellers
display an original/retail price with a strikethrough alongside the sale price on eBay listings,
increasing perceived value and CTR. Approval is at the account level and may persist across
keyset changes.

#### Verify access
Before implementing, confirm access is still active:
- Seller Hub → Marketing → Promotions (or Sales Events) — if strikethrough/sale pricing tools
  appear, the feature is enabled
- eBay Help: search "strikethrough pricing" — if your account shows the "Sale Price" section
  in the Edit Listing form, you're approved
- Alternatively: attempt to set `originalRetailPrice` in an offer via API and observe the
  response — a clean 200 confirms access; a 25500-series error indicates the feature is
  not enabled on this keyset

#### API implementation
Strikethrough pricing is set via the `originalRetailPrice` field in the eBay Inventory API
offer body (same call as `ebay_stage`). It is **not** the Promotions API — it is a standard
offer field that requires account-level approval to use.

Offer body addition (in `ebay_stage.py` `_build_offer_body()`):
```json
{
  "pricingSummary": {
    "price": {"value": "19.99", "currency": "USD"},
    "originalRetailPrice": {"value": "34.99", "currency": "USD"}
  }
}
```

#### TGW integration
- Source field: `draft_listing.original_retail_price` — set from `product_lookup.msrp` if
  available (e.g. upcitemdb returns `msrp` for many products); operator can override via
  item JSON edit or future MC / Flutter field
- Config key: `ebay.strikethrough_enabled: true/false` — global toggle so it can be disabled
  if access lapses
- `ebay_price.py`: populate `draft_listing.original_retail_price` from `product_lookup.msrp`
  when present and > launch price; store alongside `reprice_schedule`
- `ebay_stage.py`: include `originalRetailPrice` in offer body only when field is present and
  `ebay.strikethrough_enabled` is true
- `ebay_draft.py`: may also surface the MSRP in the description footer for items where
  product_lookup returns it

#### Dependencies
- `sell.marketing` scope ✅ already held (covers Promotions API; strikethrough is an offer field)
- Account-level approval — verify before implementing
- `product_lookup.msrp` field — upcitemdb already returns this in many results

#### Status
Planned. Verify account access first; implementation is straightforward once confirmed.

### PP-PROMO-001 — Sale Event Automation (P2 complete)

**P1 DONE 2026-06-12** — Design doc + operator checklist at `reference/PP-PROMO-001-sale-event-design.md`.

**P2 DONE 2026-06-13** — `tgw promo draft` + `tgw promo list` in `src/tgw/promo.py`; 41 tests in `tests/test_promo.py`; CLI wired in `api.py`.

Automates the dead-stock → markdown sale event cycle via the eBay Promotions Management API (`ITEM_PRICE_MARKDOWN`). The `sell.marketing` scope is already held. No PP-STRIKE-001 conflict: strikethrough uses `originalRetailPrice` in the offer body; this uses the Promotions API and is independent.

**Data flow**: `reports._scan_items()` dead_stock list → filter (min_days_stale, min_price, has listing_id) → markdown draft file → operator review → `tgw promo apply` (P3) → creates DRAFT promotion on eBay → operator promotes to RUNNING in Seller Hub.

**Item JSON addition**: `ebay_promo.{promo_id, event_name, discount_pct, start_date, end_date, applied_at}` written via tgw-api fence; cleared on promo end.

**Config keys** (add to `tgw-api-config.json`): `promo.{enabled, min_days_stale, min_price, max_items, discount_pct, duration_days, start_offset_days, marketplace_id}`. Default `enabled: false` until scope verified.

**Risk**: `ebay_price_reducer` must skip items with active `ebay_promo` block (R2 in design doc); wire this in P3 before first production use.

| Phase | Scope |
|-------|-------|
| P1 ✅ | Design doc + operator checklist |
| P2 ✅ | `tgw promo draft` CLI (read-only); `tgw promo list` scope check |
| P3 | `tgw promo apply`: Promotions API write + item JSON writeback; `ebay_price_reducer` promo-skip |
| P4 | `tgw promo end` / `tgw promo status` lifecycle |

**P3 blocked** on P2 scope verification (run `tgw promo list` in production — 200 → scope confirmed → P3 unblocked).

### PP-REPRICE-001 ✅ INITIAL COMPLETE (2026-06-03)
`ebay_price_reducer` worker: launch (day 0, 110%→.99) → retail (p75, day 3) → move (p25, day 17). `reprice_stages` array configurable; `to_99()` rounding; `reprice_skip: true` to exclude. Self-scheduling every 6h. `reprice_schedule` in item JSON tracks stage history.

### PP-REPRICER-001 — Market-aware dynamic repricer (design pending)
- Distinct from `ebay_price_reducer` (scheduled markdown): this watches market prices and adjusts dynamically
- Inputs: sold-price data (needs `buy.marketplace_insights` or Finding API), sell-through rate, days listed, competition count
- Design deferred until sold-price API access obtained — Browse API asking prices are the wrong signal for dynamic repricing
- Will consume `reprice_schedule` as floor (never price below the move price)

#### Sold price data landscape (PERPLEXITY-003, 2026-06-05)
All external options researched; none are clean substitutes for a true sold-data API:

| Source | Status | Verdict |
|--------|--------|---------|
| `buy.marketplace_insights` | Official docs: "restricted, not open to new users" — limited release, no roadmap | Effectively unavailable for independent devs; Dave applied via new keyset |
| Finding API `findCompletedItems` | Dead since early 2025 (error 10001) | Do not use |
| 130Point.com | Acquired MAGPIE (Mar 2025); shows "recent sales history"; **no documented public API** | Manual/semi-manual only; not suitable for automation |
| ZIK Analytics ($39–89/mo) | UI + CSV exports; no confirmed developer API | Seller research tool, not a pricing data backend |
| PriceCharting | Public API exists but **"only current item values — historic prices not supported"** | Good for games/cards/collectibles vertical only; use as supplement |
| Apify eBay sold scraper | $4/1K results; unofficial extractor layered on eBay search | ToS risk; fragile; not recommended for core pricing |
| Terapeak | UI only in Seller Hub; 3-year data; no API | Manual spot-checks on high-value items only |

**Decision:** PP-REPRICER-001 remains blocked on `buy.marketplace_insights` scope.
**Interim strategy:** Browse API p25 + velocity data (PP-PRICE-004) + own sales history as pricing signals.
**PriceCharting integration:** Worth adding to `apis/lookup/` for game/card/collectibles vertical — has free API tier, returns "current market value" derived from eBay sold data. Add as PP-UPC-001 Tier 2 source.

**Architecture note (from Perplexity):** When `buy.marketplace_insights` eventually arrives, wrap it
behind a `market_data` provider interface with fallback to Browse API comps + own sales history.
The `comps` DB table + pluggable provider pattern is the right design (see perplexity folder for full schema).

### PP-PRICE-003 ✅ COMPLETE (2026-06-04)
`pricing.py`: stage-0 product_lookup query (`brand+mpn` tightest); condition-filtered comps (same-or-worse rank only, 15-entry `_BROWSE_CONDITION_RANK`); price confidence H/M/L (`draft_listing.price_confidence`, `tgw staged` PC column).

### PP-PRICE-005 ✅ COMPLETE (2026-06-06) — Category Groups Taxonomy
`/opt/TGW/config/category-groups.json` — 24 groups covering 65+ eBay category IDs from velocity data.
Each group: `name`, `store_category` (fill in when eBay store configured), `ebay_categories` (all IDs in group),
`size_class` (flat/packet/small_box — semi-chaotic storage class), `ai_hint` (product description terms for ai_identify),
`pricing.floor` / `pricing.typical_used` / `pricing.typical_new` (seeded from velocity p25).
Top-level: `condition_factors` dict (new=1.50 … for_parts=0.30), `global_floor: 0.99`.
Integration: `suggest_price()` Stage 4 uses group typical × condition_factor when Browse API has insufficient comps.
Hard floor applied to ALL prices (even Browse API results). `tgw category-groups [--list | cat_id | --reseed]`.
**Store category**: fill `store_category` in each group after `tgw store-categories` confirms your store layout.
**Self-updating**: `tgw category-groups --reseed` recomputes typical_used from current velocity-stats.json.
**Design note**: `size_class` encodes semi-chaotic physical storage class — see PP-STORAGE-001.

### PP-STORAGE-001 — Semi-Chaotic Storage System
Inspired by Amazon chaotic storage. Items stored by SIZE not category — no two items in a location look the same.
Size class at intake gives: shipping profile match (flat→FC4/envelope; packet→Priority; small_box→Priority/FRPRI),
physical location hint (which shelf tier), and a visual distinctiveness constraint.
**Components:**
- `size_class` field in item JSON (flat/packet/small_box/medium_box/large_box) — set at intake or derived from weight+category group
- `category-groups.json` size_class = default for items in that group
- Weight hint: ~1 oz → flat/packet; 4+ oz → packet/small_box
- Shipping profile lookup: `size_class` → fulfillment_policy_id override
- Future: intake UI prompts photographer for size_class when item is unusual for its group
**Connection to PP-VISION-001**: same photo set used for visual inventory matching.

### PP-VISION-001 — Visual Physical Inventory Matching
Use item photos to visually match items to their physical location (inventory reconciliation).
Core idea: photo of shelf/item → vision model → match to SKU in catalog.
System design:
- Catalog thumbnails (already built: 54K thumbnails) = visual fingerprint database
- Vision model (Ollama or cloud) queries a candidate set, ranks by visual similarity
- Operator reviews ranked matches → confirms → system self-improves (correct matches become training signal)
- Size class constrains the search space (only look at items with matching size_class for that shelf)
- Semi-chaotic storage constraint (no two similar items together) naturally improves visual matching uniqueness
**Status**: ✅ **Phase 1 DONE (session 18)** — `src/tgw/fingerprint.py` (Pillow-only dHash + RGB
histogram), `tgw build-fingerprints` (full index = 54,314 rows), `tgw locate <image>`. Baseline
matcher; self-match distance 0.0000 verified. **Phase 2+ (embedding/CLIP model + ANN index)
blocked on GPU upgrade.** ⚠ `--size-class` filter inert until `size_class` is populated (0/83,520
items currently) — see PP-STORAGE-001 backfill follow-up.
**Dependency**: PP-STORAGE-001 (size_class field), PP-PRICE-005 (category-groups size_class lookup).

### PP-PRICE-004 ✅ COMPLETE (2026-06-05)
`tgw/velocity.py` + `velocity_stats` nightly worker (✅ enabled 2026-06-05). `tgw velocity-report` CLI. `velocity-stats.json` in catalog_root (1,540 categories). `suggest_price()` gains `velocity_hint: 'hold_launch'` for fast-moving categories. Stage breakdown (launch/retail/move%) populates as new-pipeline items sell.

### PP-LISTING-001 — Description footer and picklist line ✅ DONE (2026-06-04)
- Implemented in `workers/ebay_draft.py` — footer + picklist line built into `draft_listing.description`
- Seller boilerplate text + SKU/location picklist line; config keys: `description_footer`, `picklist_line_format`
- Future: QR code image (generate locally, upload to eBay EPS, embed in HTML) — deferred

### PP-STAGE-001 ✅ COMPLETE (2026-06-03)
`ebay_stage` creates UNPUBLISHED Seller Hub offer; `ebay_price` auto-enqueues it. `stage_draft()` + `publish_offer()` split in `sync.py`. `tgw staged` → operator review → `tgw publish <sku>`.

### PP-REVISION-001 — Live listing revision / update draft (design open)

**Governing principle (Dave, 2026-06-11 18:12 — candidate for Settled architecture once
the first implementation proves it):** for any editable record, changes are made to a
**draft**, never to the curated data directly. Draft → review → queue for application →
applied after approval. New listings approved this way enter the **Ready queue** and are
listed at the configured dole-out rate (PP-EDITOR-001). This holds for every surface —
TGW item data, eBay, Facebook Marketplace, whatever comes: **the current curated data
never has changes applied without review. Agents may update an assigned draft and pull
attributes from any source, but never write to the item's data directly.** Possibly
multiple drafts per record. (Context: the shipping-data recovery process will inform
shipping pricing for new items/revaluation, but that recovery flow is an exception, not
the normal processing pattern.)

- Three distinct workflows identified: new listing draft | live listing revision | ended→relist
- Revision needs: known baseline (live state synced from eBay), proposed delta, drift visibility
- Draft for new listing (`draft_listing`) is a historical record after publish — not the revision staging area
- ✅ **DECIDED 2026-06-12 (session 28): revision payload = sparse delta + pinned baseline.** The
  draft stores only the changed fields plus a snapshot/hash of the live-mirror state it was
  computed against. Apply = drift check (current mirror vs pinned baseline; drift on overlapping
  fields → review flag, never silent) → compose fresh live state + delta → full eBay PUT
  (Inventory API PUT is full-replace, so composition happens at apply time, never earlier).
  The applied-delta list IS the revision history (`revision_history`, the `identification_history`
  pattern). First buildable slice: **dry-run delta computer** — `tgw revise <sku> --set field=value`
  writes the draft + shows the diff vs live mirror, applies nothing (Round 7 todo #111)
- Relist: inventory item already exists on eBay; need fresh pricing + new offer; structurally re-create not update
- `ebay_offer` block now established (PP-PRICE-001) — proceed when ready
- Auto-sync: when offer fields are edited locally (price, condition, aspects), changes should push to eBay without requiring manual Seller Hub edits — design must prevent overwriting live state not yet pulled (depends on PP-SYNC-001 sync pass being authoritative first)
- Note (2026-06-11): the draft-review-apply principle above intersects PP-EDITOR-001
  (Ready state, rate-limited dole-out) and PP-DOCFLOW-001 (agents-write-drafts-only) —
  whichever is designed first carries the principle into code

### PP-FREESHIP-001 — Free Shipping Pricing Mode

**Origin:** Dave suggestion 2026-06-12T19:58. **Status:** todo #123.

**Problem:** Shipping rate increases require manual price edits across all free-shipping offers.
A dedicated mode absorbs shipping cost into the item price automatically.

**Design:** `tgw price-freeship <sku> [--apply]` — sums `ebay_offer.price` + shipping cost,
rounds to nearest `.99`, prints result; `--apply` writes combined price + sets `free_shipping: true`.
Config flag `free_shipping_enabled` (default off): when on, `ebay_stage`/`ebay_price` auto-compute
the free-shipping price. eBay fulfillment: `shippingCostOverrideType: NONE` in offer body.

---

### PP-OFFER-001 — eBay Best Offer Management

**Origin:** Dave suggestion 2026-06-12T19:59. **Status:** todo #124 (design first).

**Problem:** No tooling to view or respond to incoming Best Offers; they expire silently.

**Design:** `tgw offers [--pending] [--sku SKU]` — `GetBestOffers` list (offer ID, title, SKU,
buyer price, expiry); `tgw offers respond <id> --accept|--counter <price>|--decline` via
`RespondToBestOffer`. Auto-accept config (`min_price_pct`, default off — accept only, never
decline automatically). Responses logged in item JSON `offer_history`.

---

### PP-GIT-001 — Git / GitHub + Python Tutorial Resource

**Origin:** Dave suggestion 2026-06-12T18:50. No urgency — track for a future round.

Platform-first tutorial (TGW repo workflow, PR discipline) → generic Git best practices →
Python conventions tied to TGW patterns (pyproject/ruff/pytest). Likely a Gemini authoring
task from a rich context file.

---

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
| `etc/interfaces/keyd/tgw-macroboard.conf` | keyd config — device target + layer definition |
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
sudo nano /opt/TGW/src/trader-grims-warehouse/etc/interfaces/keyd/tgw-macroboard.conf
# Replace "413c:2105" in [ids] with the full unique ID from step 2.

# 4. Install and reload:
sudo cp /opt/TGW/src/trader-grims-warehouse/etc/interfaces/keyd/tgw-macroboard.conf /etc/keyd/
sudo systemctl reload keyd
# OR use the unified installer:
# sudo bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/install.sh

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

## PP-MCP-001 — TGW Model Context Protocol Server

### Vision
Expose TGW capabilities as an MCP (Model Context Protocol) server so Claude Code (and other
MCP clients) can query item data, trigger pipeline actions, and inspect system health as
native tools — without subprocess calls or shell scripts.

### Status: ✅ CODE DONE (session 13) — awaiting operator MCP registration

### MCP tools implemented
| Tool | Description |
|------|-------------|
| `tgw_get_item` | Fetch full item JSON for a SKU |
| `tgw_search_items` | Search catalog by text, location, or status |
| `tgw_queue_status` | Return current job counts per queue + state |
| `tgw_health` | Platform health summary |
| `tgw_enqueue` | Enqueue a pipeline action for a SKU |
| `tgw_get_todo` | List open TODO items for a given agent |
| `tgw_add_suggest` | Append to SUGGESTIONS.md (same as `tgw suggest`) |
| `tgw_hint_trail` | Return identification history for an item |
| `tgw_catalog_verify` | Scan ItemData for assumption violations |

### Architecture
- `src/tgw/mcp_server.py` — FastMCP server calling TGW internals directly
- `tgw-mcp-server` console script in pyproject.toml
- `mcp>=1.0` added to dependencies (installed 2026-06-08)
- Runs as a local stdio process; no external network exposure
- Config: import from TGW_CONFIG env (default: `/opt/TGW/config/tgw-api-config.json`)

### Value
- Claude Code can query live queue state and item data mid-session without shell escapes
- Enables Claude-native tooling loops: identify failures, re-enqueue, verify fix — all in one session
- Sets foundation for other MCP clients (custom dashboard, VS Code extension)

### Registration (operator action — see Track 4 Priority 1b)
Add to `~/.claude/settings.json`:
```json
"mcpServers": {
  "tgw": {
    "command": "sudo",
    "args": ["-u", "tgw", "/opt/TGW/.venvironments/tgw/bin/python", "-m", "tgw.mcp_server"],
    "env": {}
  }
}
```

### Dependencies
- `tgw-http` FastAPI service ✅ running
- `mcp` Python SDK ✅ installed 2026-06-08
- Claude Code MCP registration ⬅ **operator action pending**

---

## PP-FULFILLMENT-001 — Fulfillment Hardware Integration

### Problem
Shipping, labeling, and order packing still require manual steps outside TGW. USB scale and
barcode printing are natural integration points that reduce fulfillment time.

### Components

#### USB Scale
- `weight()` / `get_weight()` already in `tgw.source` — reads USB HID device
- Port to Python: `hid` library or `/dev/usb/hiddev` direct read
- Use at intake (size_class derivation) + at shipping (label weight verification)
- See PERPLEXITY-005 for USB HID library options

#### Barcode / SKU label printing
- Target: thermal printer (Dymo 4XL or Zebra ZPL) connected via USB
- SKU barcode label: Code128 + human-readable SKU + item title + location
- CLI: `tgw print-label <sku>` — generates and sends to printer
- Library: `python-barcode` (Code128) + `cups` or direct ZPL for Zebra

#### Shipping label printing
- eBay shipping labels via eBay Shipping API or browser fallback (Seller Hub)
- `tgw print-shipping <order_id>` — fetches label PDF from eBay API, sends to printer
- Requires `sell.fulfillment.readonly` scope (in desired scope list — not yet approved)

#### Packing list
- ⚠️ **CORRECTION (session 15 audit)**: `tgw picklist` does **NOT** exist yet — only
  `picklist_line()` in `ebay/description.py` (one line per eBay description). Track 1 round-2
  rank 7 builds the real location-sorted `tgw picklist` CLI; this print action extends it.
- Print-ready PDF: location-sorted, grouped by order, checkboxes per item
- QR code on packing list: encodes SKU or order ID for scan-to-confirm

### Dependencies
- USB scale: HID library (PERPLEXITY-005 research covers this)
- Label printing: thermal printer hardware (operator purchase)
- Shipping labels: `sell.fulfillment.readonly` eBay scope
- PDF generation: `reportlab` or `weasyprint`

---

## PP-TASKER-001 — Android Tasker + Join Integration

### Goal
Evaluate and design TGW automation opportunities using Tasker (automation app) and Join
(Tasker's push-notification sibling, similar to KDE Connect). Dave has a Tasker license
and a Join license.

### Join evaluation
- Join is an alternative to KDE Connect for Android↔desktop push/pull
- Capabilities: push notifications, clipboard sync, SMS forwarding, file transfer, URL open
- TGW use: push "item staged for review" notifications to phone; receive barcode scans
- Compare to KDE Connect: Join works via cloud (not LAN); better when phone not on same network
- Evaluate: which offers better reliability for `SETTEMPLATE:` clipboard relay from tgw.source?

### Barcode scanner — confirmed available
Dave has a fast commercial barcode scanner app on the camera phone. The existing Tasker app
can already open it. Need to audit available broadcast/activity intents to capture scan output
(likely Intent → StartActivity or BroadcastReceiver → Tasker Variable). Actionable first step:
check what intents the scanner exposes; wire to Tasker → Join/KDE Connect → tgw-http intake.

### Tasker opportunities
- **Barcode scan → intake**: Tasker opens commercial barcode scanner (intent audit needed) → capture result → POST to tgw-http intake endpoint
- **Voice → suggest**: Tasker microphone → Whisper → `tgw suggest`; or Tasker built-in voice
- **Photo trigger**: Tasker camera trigger → sends image to TGW intake folder via Join/KDE Connect
- **Notification response**: tgw-http push → Tasker task (tap "approve" → POST publish action)
- **USB scale auto-read**: Tasker OBD plugin or USB serial reader for scale integration on Android
- **Custom intake flow**: Tasker UI screen with SKU scan + template select + size entry; posts to tgw-http

### Tasker vs KDE Connect architecture decision
Currently KDE Connect is primary (clipboard relay, file share). Evaluate whether Join can
replace or supplement it. Key question: does Join support `wl-copy` / `wl-paste` clipboard
injection the same way KDE Connect does? If not, KDE Connect stays for clipboard relay.

### Dependencies
- PP-REMOTE-001 (tgw-http reachable from phone)
- PERPLEXITY-005 (Syncthing + KDE Connect research may cover Join as well)

---

## PP-PERP-AUTO-001 — Perplexity Semi-Automation Interface

### Problem
Submitting research briefs to Perplexity requires manual copy-paste: open brief → copy prompt
→ switch to browser → paste → wait → copy result → save to inbox. For 5+ briefs, this is 30+
minutes of mechanical work. Even Perplexity's API doesn't expose the Pro search quality.

### Simplified workflow (session 10 — no scraping required)
Perplexity's three-dot menu → "Download as Markdown" is the key insight. No HTML scraping needed:
1. Paste prompt → press Enter → wait for completion (watch browser)
2. Three-dot menu → Download as Markdown
3. Move `.md` file to `inbox/` → PM-intake processes automatically
4. For multi-turn: download → read result → ask follow-up → download again

This is already low-friction. ydotool can automate steps 1–2 (paste + submit + trigger download)
but step 3 (moving the downloaded file) can be handled by a file watcher on `~/Downloads/`.

### Automation approach: ydotool + file watcher
Semi-automate using `ydotool` (Wayland) or `xdotool` (X11):
1. `tgw perp-run PERPLEXITY-001` — reads brief, extracts prompt, pastes + submits via ydotool
2. Operator watches Perplexity complete (automation cannot reliably detect this)
3. Operator triggers download (three-dot menu or keyboard shortcut)
4. File watcher (`inotifywait` on `~/Downloads/`) moves `*.md` to `inbox/` automatically
5. PM-intake picks it up on next session startup

### Infrastructure recommendation (session 10)
Use the **tmux/ltsp/qtile/ssh stack** for dependability:
- Run the Perplexity browser tab in a dedicated Qtile workspace (workspace 3 "ebay" or a new "research" workspace)
- A dedicated Qtile scraping layout can control window focus and viewport for automation
- SSH + tmux enables remote triggering without being at the physical machine
- LTSP: remote desktop to the Perplexity workspace from tablet during coffee sessions

### Qtile scraping layout concept (session 10)
A custom Qtile layout that locks focus to the browser window and exposes automation hooks:
- Super+T → p: enter "Perplexity mode"; bar shows brief name; chord keys: `r`=run, `d`=download, `n`=next brief
- Could also handle token renewal automation (paste token, confirm) — same ydotool pattern

### Limitations
- Perplexity completion detection is not automated — operator confirms when done
- ydotool approach is best-effort; window focus can break if anything else steals focus
- Iterative research (ask → download → read → ask more → download) is semi-manual but fast

### Track 4 (Operator) task
This is an operator tool, not a background worker. Priority 3 in Track 4.

---

## PP-EMAIL-001 — Email Integration

### Problem
eBay sends automated emails for: buyer messages, order notifications, case alerts, policy
violations, and payment updates. Currently these require manual Seller Hub monitoring.
Outgoing communication to buyers is also manual.

### Inbound — auto-processing
- Monitor eBay buyer message inbox (eBay Messages API or email forward to IMAP inbox)
- Parse and categorize: order question, tracking request, return request, feedback reminder
- Route to TGW: match to order → attach to item JSON event log; generate suggested response
- Alert operator for messages requiring human response; auto-reply for simple FAQ patterns
- Integration: eBay Messaging API (part of `sell.fulfillment` scope family)

### Outbound — free SMTP
- Gmail "Send Mail As" feature: use a Gmail account to send from a custom address
  (e.g. `support@yourdomain.com`) via Gmail SMTP without a paid mail server
- Investigate: `smtplib` + Gmail SMTP with app password; or `gmail-send` Python wrapper
- Use case: order confirmation, tracking number follow-up, buyer communication

### Dependencies
- eBay messaging scope (new keyset request covers this)
- Gmail account with "Send Mail As" configured
- IMAP library: `imaplib` (stdlib) or `imapclient` package

---

## PP-CLAUDE-HELP-001 — tgw claude-help Troubleshooting Mode

### Vision
`tgw claude-help [issue description]` launches Claude Code with a CLAUDE.md specifically
tuned for fast, accurate issue diagnosis on the TGW platform — narrower context, focused
on error resolution rather than feature development.

### Design
A separate `CLAUDE-TROUBLESHOOT.md` lives alongside `CLAUDE.md`. It contains:
- System architecture in dense summary form (worker → queue → database flow)
- Common failure modes and their symptoms (ISSUES.md condensed)
- Diagnostic commands (health, queue check, journal, systemctl status)
- Decision tree: "if you see X, check Y first, then Z"
- Zero planning overhead — diagnose, fix, verify, done

The command:
```bash
tgw claude-help                    # launch claude with troubleshooting CLAUDE.md
tgw claude-help "token expired"    # include the issue as initial context
tgw claude-help --worker ebay_stage # narrow context to a specific worker
```

Implementation: `CLAUDE-TROUBLESHOOT.md` symlinked or passed as `--context` to claude CLI.
Alternatively: a dedicated `.claude/` project config directory pointed at a minimal CLAUDE.md.

### Value
Reduces time-to-diagnosis for operational issues. Operator doesn't need to explain the full
project history — the troubleshooting CLAUDE.md has a compressed but complete system view.
Especially useful under duress (down worker, stuck token, dead-letter flood).

### Dependencies
- Claude Code CLI installed (✅ available)
- `CLAUDE-TROUBLESHOOT.md` authored (one session of work)

---

## PP-OPS-001 — Operational Prerequisites and Unblocking Tasks

Catch-all anchor for one-off setup, infrastructure, and credential tasks that unblock feature
work but don't belong to a specific PP-* project. These are not a project with phases — they are
discrete operator actions required to keep the platform running or to gate a feature todo.

### Scope
- API key / credential provisioning (eBay, Google, OpenRouter, Discogs, etc.)
- Secrets-root file setup and permissions
- System service installs or OS-level configuration
- Hardware or external-account setup steps
- Any `[admin]`-agent or operator-only prerequisite that gates a `[claude]` feature todo

### Policy
Todos linked here have `pp_ref = PP-OPS-001` and `plan_anchor = PP-OPS-001`.  The brief will
extract this short section — not a multi-page design document — so the operator sees only what
they need to execute the task.

---

## Phase 7 — Vault Synchronization
Syncthing operational. Conflict resolution protocol and git backing details: `OPERATIONS-vault-sync.md`.
