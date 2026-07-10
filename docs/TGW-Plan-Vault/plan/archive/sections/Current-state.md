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

Session 38 (2026-06-30) — Dead-letter triage, ebay_sync 25707 fix, SEO title filler demotion — see dev-workflow/research/SESSION38-done-deadletter-ebay-sync-seo.md
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

### Session 38 — 2026-06-22 (PP-HM-001 Phase 1 + dress rehearsal readiness)

- **PP-HM-001 Phase 1 DONE** — Home Manager wired into flake (release-25.05); `nix/home/db.nix`
  manages Qtile config, fish shell (primary), bash (fallback), XDG dirs. systemd.tmpfiles
  Qtile hack removed. Qtile cheatsheet added to flake (`nix/qtile/cheatsheet.txt`);
  Super+Alt+Ctrl+H binding already in config.py.
- **fish shell for `db` operator** — `programs.fish.enable` in base.nix; `shell = pkgs.fish`
  in users.nix; aliases (`tgwlog`, `tgwps`, `ll`), TGW venv in PATH. bash kept as fallback.
- **tgw-push-config.sh validated end-to-end** — normal mode (`nixos-rebuild --target-host`) and
  `--bootstrap` mode (rsync + local rebuild for first push before `trusted-users` lands).
  `nix.settings.trusted-users = ["root" "@wheel"]` in base.nix enables normal mode permanently.
- **TGW-VAULT USB cold-start kit** — 16 GB Ventoy + 10 GB btrfs `TGW-VAULT` partition with
  `secrets/`, `dumps/`, `flake/` subvolumes. `scripts/tgw-usb-stamp.sh` populates on demand;
  `nix/tgw/usb-vault.nix` auto-stamps via udev on insertion (production only).
  First stamp: 1% full. Committed `b933b64` + `34e18a8`.
- **Dress rehearsal code-side READY** (`872585e`):
  - `nix/hosts/tgw-test-rehearsal.nix` — master.nix server profile on tgw-test hardware;
    inference + Syncthing disabled; R7 mask commands documented inline.
  - `tgw-db-init` now applies schema SQL (schema.sql, sku_history.sql, image_hashes.sql)
    with WAL-recovery guard and idempotent ON_ERROR_STOP=1 execution (Phase 0.2 complete).
  - Pillow promoted to base dep in pyproject.toml (Phase 0.1 complete).
- **Session 39 — 2026-06-22 (cutover aborted — boot incident)**:
  - ✅ Phase 0.6 DONE — `tgw` migrated to uid/gid 900; full chown + permissions check clean
  - ✅ All workers + tgw-http stopped; `pg_dump` complete: `data/dumps/db-backup-PRE-NIXOS-20260622T164601.dump` (6.2M, 84 objects)
  - ❌ ISO bake (`TGWMX25-FINAL-BEFORE-NIXsnapshot-20260622_1707.iso`) **removed kernel + initrd
    from live `/boot/` mid-creation**. KDE desktop died (icons/menus gone). Machine became
    unbootable. pg_dump and ItemData (sde1) are intact. NixOS cutover prerequisites still met.
  - Repair attempts 1–3 across sessions: GRUB rebuilt; 5 kernel versions restored to /boot/;
    `update-grub` clean; EFI entry present. Kernel now loads.
  - Repair attempt 4 applied (2026-06-22 session 39): symlinks `initrd.img`/`vmlinuz.old`
    restored at MX root; `default.target → multi-user.target`; `ifupdown-wait-online` conflict
    removed.

- **Session 40 — 2026-06-23 (MX DR abandoned → NixOS cutover)**:
  - MX booted to text login but root remains read-only; all repair attempts exhausted
  - **Dave: dd image of nvme0n1p2 (rootMX25) taken as rollback artifact** — NixOS cutover proceeding
  - `disko` config fixed: `device = "/dev/nvme0n1"`, LVM size 500G → 200G (disk is ~477G)
  - Inbox notes from sessions 39–40 processed and incorporated
  - **PP-NIXOS-001 Phase 1 complete** (pg_dump `db-backup-PRE-NIXOS-20260622T164601.dump` done 2026-06-22)
  - ✅ **Phase 5 cutover COMPLETE** — NixOS 25.05 live on nvme0n1; all 19 workers running; tgw uid=900, db uid=1000
  - ✅ **tgw-catio-nix 0.0.1 alpha** (2026-06-24, session 42) — CatioNIX/TGW HM layer split; backup timers declarative in Nix; TGW-SNAPSHOT-0 mount declared; trader-grims-backup retired; PR #8 open

- **Session 41 — 2026-06-24 (NixOS cutover confirmed complete)**:
  - Production server booted cleanly into NixOS 25.05 after accidental reboot during a1131 work
  - All 19 workers active under systemd; `tgw health` green (backup + NATS are pre-existing warns)
  - `todo_items` restored from `state_machine-20260622.dump` (1,040 rows — was missing from restore)
  - PP-NIXOS-001 Phase 5 cutover **COMPLETE** ✅
  - Cutover friction: LVM block sizing (208+1 blocks in 200G LV), repeated download failures during install
  - 2 commits unpushed to origin/main (tgw-install.sh update, disko fix)

### Session 35/36 — 2026-06-19 (foundation replan + ISS-013 close)

- **ISS-013 CLOSED** — `scripts/photo_repair_iss013.py` renamed 618 misnamed `<sku>-alt.jpg`
  → `<original-photo>-alt.jpg` (rename-only; originals were present). Zero errors. All naming
  formats handled: `tgwYYYYMMDD_HHMMSS`, `a11bYYYYMMDD_HHMMSS`, `IMG_YYYYMMDD_HHMMSS`,
  `cropped-*`, and numeric (`1.jpg`→`1-alt.jpg`). Root cause: `alt_text.py` pre-commit
  `9319e5e` used `rename` instead of `copy`. Archive sweep deferred until Stage 2 CDC.
- **Foldio naming convention defined** — `<sku>-foldioNN.jpg` (2-digit zero-padded; 01–28).
  API will reject bare numeric names at ingest and rename to foldio convention. 232 existing
  items with numeric names deferred until Stage 2 transactional base.
- **PROPOSED-PLAN-2026-06-19 approved and merged** — staged foundation plan adopted:
  Stage 0 (ops fixes) → Stage 1 (API fence) → Stage 2 (PP-AIOPS-001) → Stage 3 (PP-BACKUP-001)
  → Stage 4 (PP-NIXOS-001) → Stage 5 (Phase 5 sandbox) → Stage 6 (PP-DATA-OWN-001 Phases 2–5).
  Data Tracks A/B/C run in parallel. Full spec in `plan/PROPOSED-PLAN-2026-06-19.md`.
- **USB boot media designed** — 2 × 16 GB Ventoy drives (`TGW-BOOT-01` / `TGW-BOOT-02`) with
  400 MB `tgw-kit` ext4 partition (UUID-mounted). Kit: flake, site-config, schema SQL,
  age-encrypted secrets. Prep procedure in `PLAN-nixos-migration.md` Phase 2.5.
  Weekend plan 2026-06-21/22: Dave tests on iMac A1131. Ventoy partition label must stay `ventoy`.
- **PP-DATA-OWN-001 Track C expanded** — category hierarchy (C2a), full aspects per category
  (C2b), EPS URL→local photo correlation (C2c), full raw metadata capture everywhere (C2d).
  Details in PP-DATA-OWN-001 section below.
- **PP-AIOPS-001 anchored** — cat-herding platform (JetStream + audit stream + anomaly detection
  + litterbox). Full spec in `plan/PP-AIOPS-001-cat-herding-platform.md`. Summary in Phase 5 below.

### Phase 2a observation gate ✅ CLEARED 2026-06-02
- `ebay_token_refreshed` observed at 12:07 — full expiry+refresh cycle confirmed
- No separate cron existed to retire; worker is sole token manager
### Retired this session
- `queue-launcher.service` disabled; stub in code preserves the console script
- Filesystem `.queue_worker` / `.queue_worker_config` discovery removed from all code
- eBay credentials removed from `tgw-api-config.json`; now in `secrets_root`

Session 32 — CatioNIX dual-desktop wiring complete: lan-mouse, Wayland tools, Syncthing dual-instance, KDE Connect, Firefox clipboard fix (see DONE-cationix-desktop-wiring-session32.md)
