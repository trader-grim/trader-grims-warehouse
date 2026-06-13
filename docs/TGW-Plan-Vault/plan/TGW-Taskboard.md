# TGW Taskboard

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `todo_items` table by
> `tgw plan render` / the `plan_render` worker (PP-PLANDB-001 Phase 2).
> Edit tasks with `tgw todo …` — manual edits here are overwritten.

_Rendered 2026-06-13 01:43 UTC — 31 open, 89 done in the last 7 days._

## admin (19 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 78 | 10 |  | HARD DEADLINE 2026-06-18: Antigravity/Gemini migration validation — 5 steps while both CLIs still live: (1) confirm skills/hooks/subagents carried over to Antigravity (2) test headless/scripted use of Antigravity (3) re-run one Gemini brief in Antigravity, diff quality (4) export Gemini CLI config/custom commands/history before shutoff (5) observe compute-cap refresh behavior on one bite-sized task [plan/next-process.md §3] |  |  |
| 114 | 11 |  | Round7: Antigravity code-task trial during #78 validation week — run todo #95 (ISS-003/004 config hygiene) via agy as the bite-sized code trial; compare review burden vs a Claude session. Purpose: ROUTING CALIBRATION between Antigravity (primary agent manager) and Aider (committed regardless — see #117). Amended decision 2026-06-12, next-process.md §2 |  |  |
| 79 | 12 |  | Answer eBay Developer Support 8 open questions re buy.marketplace_insights scope — unblocks PP-REPRICER-001 live repricing; check DS inbox for ticket | [[TGW-Master-Plan#PP-REPRICER-001 — Market-aware dynamic repricer (design pending)\|PP-REPRICER-001]] |  |
| 61 | 15 |  | Round6 #55: PP-BACKUP-001 Phase A operator (plan APPROVED 2026-06-11) — gpg passphrase custody (off-machine, 2 rotated USB keys); GATE: rclone server-side preserve dbukove:TGW -> TGW-historypoint-20260514 BEFORE any new sync; install 3 timers after #60 builds them; first manual sync off-hours + quota check; A5 restore drill, record RTO; check DLT tape interface | [[TGW-Master-Plan#PP-BACKUP-001 — Organized Backup and Disaster Recovery Architecture\|PP-BACKUP-001]] |  |
| 104 | 18 |  | Round7: Register TGW MCP server in ~/.claude/settings.json (Track 4 Priority 1b, 2 min) — mcpServers block per master plan; restart Claude Code; unlocks live queue/item/health tools every session |  |  |
| 7 | 20 |  | IGDB credentials — Twitch dev account → register app → save client_id/client_secret to secrets_root/igdb-credentials.json |  |  |
| 119 | 20 |  | Enable plan_render worker (needs root): sudo systemctl enable --now tgw-worker@plan_render.service — until then the taskboard only refreshes via 'tgw plan render'; PP-PLANDB-001 Phase 2 code is live | [[TGW-Master-Plan#PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)\|PP-PLANDB-001]] |  |
| 120 | 21 |  | Enable ebay_dole worker (needs root): sudo systemctl enable --now tgw-worker@ebay_dole.service — rate-limited ready-pool publishing (1/60 per hour cycle); until then 'tgw ready set' items sit in the pool and tgw publish remains the only listing path | [[TGW-Master-Plan#PP-EDITOR-001 — Item Editor / Inventory Management App\|PP-EDITOR-001]] |  |
| 105 | 22 |  | Round7: Add dev_id to /opt/TGW/secrets/ebay-credentials.json (developer.ebay.com -> Application Keys -> DevID) — ISS-005 code done; this gates webhook infra todo #16 |  |  |
| 117 | 26 |  | Round7: Aider setup (COMMITTED, amended 2026-06-12) — create Anthropic API key + HARD billing cap (~$40/mo) in console; tell Claude when done so #118 onboarding files get exercised on a first task. Aider = mechanical code-edit tier alongside Antigravity as primary agent manager |  |  |
| 80 | 30 |  | Run tgw history-index --target all as tgw user in screen session — hours for 32K zips; output: var/history-itemdata-index.jsonl; command built + smoke-tested [PP-HISTORY-001 done] | `PP-HISTORY-001` |  |
| 107 | 35 |  | Round7: Run PERPLEXITY-001..004 research briefs (perplexity/ folder) before subscription expiry ~2026-12; save each result .md to inbox/ for pm_intake. 001 eBay scopes first (informs DS response #79) |  |  |
| 11 | 40 |  | tgw ebay-sweep → physical inventory review (run after Perplexity brief results arrive) |  |  |
| 12 | 45 |  | Fix 9 wrong-shipping Seller Hub listings flagged in sweep |  |  |
| 20 | 50 |  | PP-WM-001: Install Qtile WM — run: bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/qtile/install.sh (as desktop user, not root). Then log out and select Qtile at login screen. | [[TGW-Master-Plan#PP-WM-001 — Qtile Tiling Window Manager\|PP-WM-001]] |  |
| 81 | 50 |  | PP-NIXOS-001: review plan/PLAN-nixos-migration.md phases and signal go — Phase 0 items (nix-shell + flake scaffold) become Claude todos on approval | [[TGW-Master-Plan#PP-NIXOS-001 — NixOS Migration Evaluation\|PP-NIXOS-001]] |  |
| 108 | 55 |  | Round7: Verify eBay strikethrough pricing access in Seller Hub (Sale Price section in Edit Listing form), then enable strikethrough_enabled config flag — code shipped + tested session 15, off pending verification |  |  |
| 15 | 60 |  | Second keyboard wired up as macroboard (see etc/interfaces/keyd/tgw-macroboard.conf) |  |  |
| 16 | 65 |  | eBay webhook endpoint — nginx/cloudflared so PP-SOLD-001 Tier 4 webhook can receive notifications | [[TGW-Master-Plan#PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)\|PP-SOLD-001]] |  |

## claude (8 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 95 | 50 | XS | Round7 p50 XS (Aider-eligible): ISS-003 + ISS-004 config hygiene — align full_catalog_path JSON value with code default; surface ebay_sku_migrate block through load_config() instead of cfg[raw] |  |  |
| 121 | 50 |  | agy |  |  |
| 96 | 55 | S | Round7 p55 S (Aider-eligible): PP-SHELL-001 Tier 3 — grouped tgw --help (Read/Search, Write, Pipeline, eBay, Context, Catalog, Ops) + requeue -> requeue-identify rename with deprecated alias | [[TGW-Master-Plan#PP-SHELL-001 — Shell Environment Cleanup (tgw.source / tgw-dev.source)\|PP-SHELL-001]] |  |
| 112 | 58 | S | Round7 p58 S: PP-PLANDB-001 Phase 3 — tgw plan check: reconcile plan<->tracker both directions (pp_ref/plan_anchor vs plan sections; round-tagged todos vs plan round summaries); report orphans + status mismatches; add to session-start ritual in CLAUDE.md; mismatch reports feed the improve-the-admin loop (PP-DOCFLOW) | [[TGW-Master-Plan#PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)\|PP-PLANDB-001]] | ✓ deps done |
| 98 | 65 | S | Round7 p65 S (Aider-eligible): Discogs adapter migration — apis/lookup/discogs.py from deprecated discogs_client to direct httpx; same adapter surface, same tests + live-shape fixtures. PERPLEXITY-005 finding |  |  |
| 123 | 70 |  | PP-FREESHIP-001 design + build: free shipping mode — combine item price + shipping_cost rounded to nearest .99 as new listing price with free shipping offer; config flag free_shipping_enabled (default off); useful for absorbing shipping rate increases |  |  |
| 113 | 72 | M | Round7 p72 M (GATED: after admin #20 Qtile install): PP-CLIP-001 daemon — dual-backend watcher per settled design (2026-06-12): backend-agnostic core (on change -> classify -> SQLite -> socket push); X11/XFixes backend (default/stable) + Wayland wl-paste --watch backend; session-type autodetect; PRIMARY+CLIPBOARD; feeds existing tgw clip store; Unix socket for TGWSKUWidget | [[TGW-Master-Plan#PP-CLIP-001 — TGW-Aware Clipboard Manager\|PP-CLIP-001]] | ⛔ #20 |
| 124 | 72 |  | PP-OFFER-001 design: offer management CLI — tgw offers [--pending]: list incoming best-offer requests (GetBestOffers); respond --accept/--counter/--decline; auto-accept config (min_pct of current price); design doc before build |  |  |

## gemini (3 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 86 | 9 |  | DEADLINE 2026-06-18: export Gemini CLI config, custom commands, skill definitions, and any hooks before shutoff — save to docs/TGW-Plan-Vault/reference/gemini-cli-export.md for reference during Antigravity setup |  |  |
| 101 | 20 |  | Round7 p20: ebay_draft aspect-fill audit — per-category Required/Recommended specifics fill rates from a draft_listing extract; identify worst-coverage categories + prompt-tuning recommendations; result to inbox/. Self-contained brief; route via Antigravity after 2026-06-18 |  |  |
| 102 | 30 |  | Round7 p30: GDrive dedupe assist (PLAN-backup-dr A8) — chunked rclone dedupe strategy for same-name-same-dir duplicates that time out on the full dataset; produce chunk plan + exact commands; operator supervises execution |  |  |

## sokoban (1 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 17 | 20 |  | PP-SOLD-001 Tier 3 — physical sweep checklist after full-history CSV import; run tgw ebay-sweep | [[TGW-Master-Plan#PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)\|PP-SOLD-001]] |  |

## Done this week (89)

| ID | Agent | Done | Task |
|---:|-------|------|------|
| 122 | claude | 2026-06-12 | PP-TODO-001: tgw todo brief --clip + --next flags — --clip copies brief to clipboard; --next --agent gemini outputs top task for that agent |
| 118 | claude | 2026-06-12 | Round7 p62 XS: Aider onboarding — write .aider.conf.yml at repo root (distilled config in next-process.md §2: sonnet-4-6 + haiku weak, architect mode, cache-prompts, auto-test, task-branch commits) + CONVENTIONS.md one-pager (settled architecture bullets, {ok,...} contract, never touch config/secrets/scopes/ebay invariants) + generate spec message-files for #96 and #98 via the brief pattern. No API key needed to author files (gate: #117 before first run) |
| 100 | claude | 2026-06-12 | Round7 p75 S: PP-PROMO-001 design doc — markdown sale-event automation on held sell.marketing scope; consumes dead-stock report; draft->review->apply; design + operator-verification checklist ONLY, no eBay writes |
| 115 | gemini | 2026-06-12 | Round7 p15: TGW-native camera app design/scaffold brief (Dave 2026-06-12) — replace Tasker + stock camera with one Android app: barcode scan, template select with SETTEMPLATE HUD, camera trigger, voice hint, upload via Syncthing folder or tgw-http POST. Deliver: design doc + Flutter (or Kotlin) scaffold proposal, GEMINI-003 pattern; note Foldio360 zip-bypass + future root/custom-ROM path. Self-contained; result to inbox/. Review with Dave before build |
| 116 | gemini | 2026-06-12 | Round7 p16: xmouse replacement app survey + design (Dave 2026-06-12) — open-source Android bases on GitHub for: macro-pad grid (SSH/HTTP command dispatch like xmouse), embedded RDP/VNC client (aRDP/bVNC lineage, license check), form tool surface for tgw-http /form/* pages. Deliver: candidate repo shortlist + license posture + combined-app architecture proposal; result to inbox/. Review with Dave before build |
| 99 | claude | 2026-06-12 | Round7 p70 S: tgw report sales [--stale] — monthly units/revenue by category-group, sell-through, days-to-sale, price-stage-at-sale from ebay_sale + velocity data; dead-stock ranking; markdown/CSV artifact to vault. Read-only. PP-DOCFLOW Phase-3 seed |
| 92 | claude | 2026-06-12 | Round7 p35 S: Picklist/label print Phase 1 (offline) — tgw picklist --pdf (location-sorted, checkboxes, QR per line) + tgw print-label <sku> Code128 label PDF; CUPS send stubbed behind config until printer hardware lands. PP-ADD-009/PP-FULFILLMENT-001 |
| 91 | claude | 2026-06-12 | Round7 p30 S: tgw create-item [--template GROUP] [--count N] — computer-side intake (PP-INTAKE-001 Phase 2.5): pre-create SKU folder + blank JSON with template applied + KDE Connect COMMAND: push; phone becomes purely a camera |
| 93 | claude | 2026-06-12 | Round7 p40 S: Category validation via Taxonomy getCategorySuggestions in ebay_draft — query with drafted title, record category_suggestions + agreement flag in draft_listing; low-confidence mismatch -> catalog-verify rule. Mocked tests; no behavior change to category choice yet |
| 111 | claude | 2026-06-12 | Round7 p22 M: PP-REVISION-001 first slice — dry-run delta computer: tgw revise <sku> --set field=value [--show] writes revision_draft {delta, baseline (live-mirror snapshot/hash), created_at, by} to item JSON; displays diff vs live mirror; drift-detection helper (current mirror vs pinned baseline). NO apply path, NO eBay writes. Decision 2026-06-12: sparse delta + pinned baseline |
| 90 | claude | 2026-06-12 | Round7 p25 S: Alt-text batch path — tgw alt-text --batch [--limit N] [--provider openrouter\|ollama]; OpenRouter free-vision routing (~20 req/min, fail-soft per item, resumable); keeps <SKU>-alt.jpg convention. Offloads CPU-only Ollama. PP-DATALEARN-001 |
| 89 | claude | 2026-06-12 | Round7 p20 M: AI usage ledger — record provider/model/purpose/duration/token-or-char counts for every call_model() + Ollama + OpenRouter call into ai_usage table (state_machine); tgw ai-usage [--since] report per provider/queue/day. Feeds cost-per-item goal (Phase 5 #2) |
| 85 | gemini | 2026-06-12 | DEADLINE 2026-06-18: PP-REF-002 enrichment priority analysis — from category-groups.json + velocity data, rank which categories benefit most from IGDB/Discogs enrichment; identify top 10 fields to pull per source; output brief for Claude implementation [Antigravity data analysis] |
| 84 | gemini | 2026-06-12 | DEADLINE 2026-06-18: ISS-005 signature verification analysis — research eBay marketplace_account_deletion notification HMAC signature format (X-EBAY-SIGNATURE header, SHA256 HMAC, verification key endpoint); produce implementation spec for Claude to code [Antigravity research task] |
| 83 | gemini | 2026-06-12 | DEADLINE 2026-06-18: PP-STORE-001 store category mapping research — for top 20 TGW categories by velocity, find the correct eBay Store Category IDs from live Seller Hub; output CSV: category_group, ebay_store_category_id, ebay_store_category_name [Antigravity browser task] |
| 97 | claude | 2026-06-12 | Round7 p60 S: pm_intake URL/URI submissions — inbox note containing only a URL -> fetch content -> classify/file like a document (same FILING-LOG + review-flag rules). PP-DOCFLOW-001 Phase 3 first slice |
| 82 | gemini | 2026-06-12 | DEADLINE 2026-06-18: Antigravity catalog quality baseline — scan live ItemData sample (500 items) for assumption violations: missing title/price/photos/location/condition; count by category; output markdown table; feeds PP-VERIFY-001 violation rule implementation [Antigravity large-context task] |
| 87 | claude | 2026-06-12 | Round7 p10 S: Sync-conflict resolution worker (file-scan) — scan vault + catalog-export sync roots for *.sync-conflict-*; auto-discard ONLY when provably redundant (byte/JSON compare vs canonical); divergent content -> inbox/review/ + todo, never auto-delete; health surfaces count. Acceptance: classify the live .obsidian/community-plugins.sync-conflict-* artifact. PP-PORTABLE-CATALOG-001 P3. See plan/PLAN-round7-platform-gaps.md |
| 94 | claude | 2026-06-12 | Round7 p45 XS: Dead-letter health split TRANSIENT vs HARD_FAILURE in check_postgres detail + tgw_queue_status; plus zero-work watchdog rule (worker alive, 0 completions over N hours while queue non-empty -> warning). PP-DEADLETTER-001 remainder |
| 88 | claude | 2026-06-12 | Round7 p15 M: Ready state + dole-out — status=ready as post-review done-state; tgw ready [list\|set <sku...>]; self-scheduling ebay_dole worker publishes ready items at configurable rate (default 1/60 of ready pool per cycle); tgw publish stays as List-Now bypass. Carries draft->review->apply principle into code. PP-EDITOR-001/PP-REVISION-001 |
| 110 | claude | 2026-06-12 | Round7 p18 M: PP-PLANDB-001 Phase 2 — tgw plan render: wholly-generated plan/TGW-Taskboard.md (per-agent sections; ID/pri/size/task; blocker badges from depends_on; links to plan anchors via pp_ref; done-this-week section); coalesced plan_render job enqueued on todo mutations (catalog-rebuild pattern, not_before+30s); render timestamp + staleness warning in health. Companion file, never in-place blocks (Syncthing mixed-edit decision) |
| 109 | claude | 2026-06-12 | Round7 p16 S: PP-PLANDB-001 Phase 1 — todo_items schema: pp_ref TEXT, depends_on INT[], plan_anchor TEXT + CLI flags (--pp, --depends, --anchor); tgw todo brief <id> generates self-contained per-agent task spec (Aider message-file pattern, next-process.md) from todo + linked plan-section extract; extend classify-suggestions todo creation to set pp_ref when confident. Decision 2026-06-12: Option C, see plan addendum |
| 103 | admin | 2026-06-12 | Round7: Merge PR #3 (session-27-tasks-70-77, OPEN; review findings addressed in d0d6933) — review diff, merge to main |
| 106 | admin | 2026-06-12 | RESOLVED 2026-06-12: openrouter-credentials.json present in secrets_root (0600, what llm.py reads); key also lives in /home/tgw/.env (OPENROUTER_API_KEY — Dave's noted location, now recorded in memory + reference); .env was 0664 world-readable, chmod 600 applied; pm_intake active, no dead-letters |
| 77 | claude | 2026-06-12 | Aider-ready XS: tgw quiet-check command — read-only queue idle summary; output {ok, queued, processing, dead_letter} JSON; mocked DB tests; see next-process.md Aider template example |
| 76 | claude | 2026-06-12 | Aider-ready XS: tgw command synonyms — health=status, --help=-help=help; wires through argparse in api.py; 2 tests verifying both names work [plan suggestion, session 9] |
| 75 | claude | 2026-06-12 | S: PP-REF-002 Phase 1 — reference data enrichment: IGDB lookup for video games, Discogs for records/media; enrich item JSON title/description at draft time; BLOCKED on IGDB creds (admin #7) [master plan PP-REF-002] |
| 74 | claude | 2026-06-12 | S: PP-CAPTURE-001 Phase 2 — quiet-queue KDE Connect notification: push tgw suggest backlog summary to phone when all workers go idle; uses tgw.apis.kdeconnect (PP-PYIPC-001 done) [master plan PP-CAPTURE-001] |
| 73 | claude | 2026-06-12 | S: PP-VERIFY-001 Phase 1 — tgw catalog-verify command: scan ItemData for assumption violations (missing required fields, no photos, bad price, wrong condition); output markdown checklist; write catalog_verified flag [master plan PP-VERIFY-001] |
| 72 | claude | 2026-06-12 | M: PP-PORTABLE-CATALOG-001 P2 — push portable tgwcatalog.db slice to satellite via Syncthing (tgw.apis.syncthing now done); design complete in PERPLEXITY-006; see master plan PP-PORTABLE-CATALOG-001 |
| 71 | claude | 2026-06-12 | S: PP-STORE-001 Phase 1 — eBay store category support: add store_category_id to category-groups.json schema + ebay_draft worker; tgw store-category set/list subcommands; tests [master plan PP-STORE-001] |
| 70 | claude | 2026-06-12 | S: ISS-005 webhook signature verification — implement dev_id HMAC verification in ebay_sold_sync; replace accept_when_unsigned interim; required before deploying webhook infra (admin #16) [invariants C8, ISSUES.md ISS-005] |
| 69 | claude | 2026-06-11 | IN PROGRESS |
| 68 | claude | 2026-06-11 | IN PROGRESS |
| 67 | claude | 2026-06-11 | IN PROGRESS |
| 57 | claude | 2026-06-11 | Round6 #51: tools/repair_itemdata_json.py py3.12-only f-string syntax (host 3.11) + unused nxt var — fix or archive |
| 56 | claude | 2026-06-11 | Round6 #50: tools/migrate_batch.py broken (8 F821s) — repair or archive (decide: superseded by ebay_sku_migrate worker?) |
| 63 | claude | 2026-06-11 | Round5 #41: category-groups.json store_category mappings (GEMINI-006) — populate store_category for tools_hand, electronics_adapters_chargers, electronics_remotes, kitchen_utensils |
| 62 | claude | 2026-06-11 | Round5 #40: category-groups.json pricing calibration (GEMINI-005) — update electrical_fixtures→12.50, media_records→13.50, collectibles_pins_buttons→10.50; run tgw category-groups --reseed |
| 55 | claude | 2026-06-11 | Round5 #42: description_history boilerplate contamination scrub (John F. Rider, GEMINI-004) — bulk ItemData mutation, dry-run first |
| 54 | claude | 2026-06-11 | Round5 #43: Standard Envelope <=0.25in constraint in _resolve_fulfillment_id() + CATEGORY-QUIRKS.md note (fulfillment resolver — review-flagged) |
| 53 | claude | 2026-06-11 | Round5 #45: TGW-Quickstart.md pipe examples (--skus-only, stdin -, multi-SKU patterns) |
| 52 | claude | 2026-06-11 | Round5 #41: category-groups.json store_category mappings (GEMINI-006: tools_hand, electronics_adapters_chargers, electronics_remotes, kitchen_utensils) |
| 51 | claude | 2026-06-11 | Round5 #40: category-groups.json pricing calibration (GEMINI-005: electrical_fixtures→12.50, media_records→13.50, collectibles_pins_buttons→10.50) then tgw category-groups --reseed |
| 60 | claude | 2026-06-11 | Round6 #54: PP-BACKUP-001 Phase A BUILD (after Dave approves PLAN-backup-dr.md) — tgw-db-backup/tgw-cloud-sync/tgw-secrets-backup units+scripts in etc/systemd/+bin/; check_backups() in health.py (4 freshness ages) + tests. See docs/plans/PLAN-backup-dr.md |
| 59 | claude | 2026-06-11 | Round6 #53: PP-DOCFLOW-001 Phase 1 BUILD — port pm_intake to call_model() + gemini-2.5-flash routing (ollama fallback); actions: file_document (verbatim move + FILING-LOG.md index + related PP-*) / flag_for_review (inbox/review/ + todo) / new_section→review-flag (append-only plan writes); submission-delay gate (~4h, mtime) + tgw admin-file [--now]; audit trail per action; offline tests. Design settled session 24 — see plan PP-DOCFLOW-001 |
| 58 | claude | 2026-06-11 | Round6 #52: PP-DOCFLOW-001 design session WITH DAVE — unified LLM doc/suggestion intake admin (see plan section); scope MVP, pm_intake replace-vs-wrap, provider routing, review-flag surface |
| 14 | admin | 2026-06-11 | nvm + npm install (markmap-cli now done; nvm/npm still needed for future JS tooling if required) |
| 13 | admin | 2026-06-11 | Tailscale install on TGW server |
| 50 | admin | 2026-06-11 | Antigravity CLI INSTALLED 2026-06-10. Remaining before 2026-06-18 (Gemini CLI shutoff): overlap-window validation per docs/dev-workflow/next-process.md §3 — re-run one Gemini brief on Antigravity and compare, verify headless/scripted use, export any Gemini-CLI-only config. |
| 38 | claude | 2026-06-11 | tgw alt-text <sku>: wire local Ollama vision model to generate alt_text + seo_caption per item photo, writing through tgw-api fence into draft_listing. Receiving end of GEMINI-TASK-004; use the prompt template + JSON schema from GEMINI-004-result.md. CPU-only Ollama: keep prompt lean, batch. Naming: <SKU>-alt.jpg sidecar (Dave 17:47) — CONFIRM intent (renamed secondary image vs alt-text derivative) before writing files. |
| 49 | claude | 2026-06-11 | Round 5 #48 (PP-CONTEXT-001) — design tgwset replacement with Dave: idempotent 'tgw context set/get/clear' through the fence, atomic state, compat symlink view; design notes in plan PP-CONTEXT-001 |
| 48 | claude | 2026-06-11 | Round 5 #47 (PP-SHELL-001) — tgw command-set review: arg-order + naming inconsistencies (update vs statusupdate; hyphenated vs concatenated), no top-level search, no nested-field CLI writes, ebay-pull scoping; decide canonical names + deprecation aliases; findings in plan Round 5 #47 |
| 47 | claude | 2026-06-11 | Round 5 #46 — Ledger ops-query ergonomics: add SQL views (v_dead_letters, v_job_history w/ queue_name) and/or 'tgw queue history' subcommand; history joins via queue_jobs USING (job_id); columns are payload_json/error_code/error_detail |
| 39 | claude | 2026-06-11 | Fix 25002 Item.Country dead-letter rejections (categories 34032/14027/13916): tracked known issue at master plan ~L83. Offer body looks correct; investigate category-specific Country/location requirement in ebay/sync.py publish path. 3 real dead-letters waiting. |
| 37 | claude | 2026-06-11 | GET /api/health endpoint in tgw-http (BACKEND-NEEDED from GEMINI-TASK-003 Flutter app): coarse system status (workers, queues, ebay token, dead_letter count) for the Flutter Home/welcome screen + audible-alert logic. Mirror 'tgw health' output as JSON; Bearer-auth. |
| 44 | gemini | 2026-06-10 | GEMINI-TASK-007 Data/archive history consolidation (163GB tree inventory + index plan) → inbox/GEMINI-007-result.md. Brief: gemini/GEMINI-TASK-007-data-history-consolidation.md |
| 36 | claude | 2026-06-10 | size_class backfill: populate size_class from category_group defaults (category-groups.json has per-group size_class) across the 83,520 catalog rows via a tgw-api batch job. Unblocks 'tgw locate --size-class' (PP-VISION-001) + PP-STORAGE-001 fulfillment resolver — both shipped but INERT at 0/83,520 populated. |
| 46 | admin | 2026-06-10 | Run PERPLEXITY-006 (Flutter offline-first + Syncthing SQLite) → paste perplexity/PERPLEXITY-006-flutter-offline-sync.md into Perplexity → save inbox/PERPLEXITY-006-result.md. De-risks the Flutter build. |
| 45 | admin | 2026-06-10 | Run PERPLEXITY-005 (library audit, only unrun brief) → paste perplexity/PERPLEXITY-005-library-audit.md into Perplexity → save inbox/PERPLEXITY-005-result.md. Unblocks PP-PYIPC-001. |
| 43 | gemini | 2026-06-10 | GEMINI-TASK-006 Marketing/category insights → inbox/GEMINI-006-result.md. Brief: gemini/GEMINI-TASK-006-marketing-category-insights.md |
| 42 | gemini | 2026-06-10 | GEMINI-TASK-005 Pricing data analysis (velocity-stats × category-groups) → inbox/GEMINI-005-result.md. Brief: gemini/GEMINI-TASK-005-pricing-data-analysis.md |
| 41 | gemini | 2026-06-10 | GEMINI-TASK-004 Multimodal photo QA + alt-text pilot (vision test) → inbox/GEMINI-004-result.md. Brief: gemini/GEMINI-TASK-004-multimodal-photo-qa-alttext.md |
| 40 | gemini | 2026-06-10 | GEMINI-TASK-003 Flutter app scaffold (PP-EDITOR-001 Phase B+C) → builds apps/tgw_app/; result to inbox/GEMINI-003-result.md. Brief: docs/TGW-Plan-Vault/gemini/GEMINI-TASK-003-flutter-app-scaffold.md |
| 35 | claude | 2026-06-08 | Round 4 #35 — PP-NIXOS-001: update flake.nix + nix/README.md: configure NVM_DIR=/opt/TGW/.nvm and NPM_CONFIG_PREFIX=/opt/TGW/.npm so nvm/npm installs under /opt/TGW/; home-dir-independent platform |
| 34 | claude | 2026-06-08 | Round 4 #34 — PP-TODO-001: GET /form/todos in tgw-http; tablet-friendly HTML table of open todos by agent; auth-gated (network-trust like /form/intake); daily queue review from tablet/phone |
| 33 | claude | 2026-06-08 | Round 4 #33 — PP-PLASMA-001: add formal plan section (done session 18); design notes for Plasma 6 + Qtile dual-desktop; no code this round |
| 32 | claude | 2026-06-08 | Round 4 #32 — PP-PORTABLE-CATALOG-001 Phase 1: tgw export-catalog <dest> command; copies tgwcatalog.db + thumbnails subset to destination; Syncthing handles transport; lays groundwork for spare machine client |
| 31 | claude | 2026-06-08 | Round 4 #31 — PP-VISION-001 Phase 1: phash/histogram fingerprint index over 54K thumbnails (Pillow+numpy); batch build job; tgw locate <image> [--size-class] CLI returning ranked SKU matches; index in SQLite catalog |
| 30 | claude | 2026-06-08 | Round 4 #30 — PP-REF-003: author reference/TGW-Quickstart.md — all tgw CLI subcommands + workers + web forms + MC VFS + Qtile chords organised by workflow; stubs for physical processes |
| 29 | claude | 2026-06-08 | Round 4 #29 — Dead_letter triage: add tgw dead-letter --requeue-transient flag; cancel 27 stale pre-fix-era jobs; re-enqueue 6 no-ebay_category_id items through ai_identify |
| 18 | gemini | 2026-06-08 | PP-GLOBALS-001 — large-context pass over offer-invariant fields once design doc exists |
| 28 | claude | 2026-06-07 | PP-DEPLOY-001 MX image: operator runbook for final MX Snapshot restore image as safety net before NixOS cutover |
| 27 | claude | 2026-06-07 | PP-NIXOS-001: author flake.nix + nix/tgw.nix from pyproject.toml/install.sh/systemd units; Dave validates in VM; NixOS is committed target |
| 26 | claude | 2026-06-07 | PP-SHELL-001 T3: version-control tgw.source into etc/interfaces/shell/; replace remaining ARCH-VIOLATES with tgw one-liners |
| 25 | claude | 2026-06-07 | PP-MC-001 Phase 4: tgwlogs extfs VFS; read-only journalctl per worker; guard injection; cap output |
| 24 | claude | 2026-06-07 | PP-REPRICER-001 read-only: market_data provider interface (OwnSalesProvider + BrowseCompsProvider + stub) + tgw reprice-suggest dry-run; no eBay write |
| 23 | claude | 2026-06-07 | PP-VERIFY-001 Phase 3: tgw catalog-verify --fix; auto-strip stale TEMPLATE: prefix; dry-run default; per-SKU fix log |
| 22 | claude | 2026-06-07 | PP-BULKEDIT-001 Phase 1: web UI at tgw-http /bulk (filter→preview→apply) + tgw bulk CLI; tablet-first; fields: title/location/status/ai_hint/shipping_profile |
| 21 | claude | 2026-06-07 | tgw restart-workers: systemctl restart tgw-worker@* wrapper |
| 4 | claude | 2026-06-07 | PP-HINT-001 — eBay Browse enrichment in ebay_draft; per-SKU hint trail |
| 3 | claude | 2026-06-07 | PP-GLOBALS-001 — analysis only: identify offer-invariant fields; design doc |
| 2 | claude | 2026-06-07 | PP-MC-001 Phase 2 — `tgwitem` copyin + ebay/ + pipeline/ subdirs |
| 1 | claude | 2026-06-07 | PP-SHELL-001 Tier 2 — remove deprecated blocks + replace ARCH-VIOLATES with `tgw` wrappers (coordinate locationupdate arg-order with bash callers) |
| 9 | admin | 2026-06-05 | Go-UPC API key — go-upc.com/api → sign up → save to secrets_root/go-upc-credentials.json |
| 8 | admin | 2026-06-05 | Discogs credentials — discogs.com/settings/developers → generate token → save to secrets_root/discogs-credentials.json |
| 10 | admin | 2026-06-05 | Run Perplexity briefs 001–004 in docs/TGW-Plan-Vault/perplexity/ and drop results to inbox/ |
| 6 | admin | 2026-06-05 | eBay Developer Support — contact for buy.marketplace_insights scope (limited release, no self-service); unblocks PP-REPRICER-001 |
| 5 | admin | 2026-06-05 | New eBay keyset — go to developer.ebay.com → My Account → Application Keys → Create new keyset (App name: TGW-Automation-v2); replace App ID/Cert ID/Dev ID in secrets_root/ebay-credentials.json |
