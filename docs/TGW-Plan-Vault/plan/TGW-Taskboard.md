# TGW Taskboard

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `todo_items` table by
> `tgw plan render` / the `plan_render` worker (PP-PLANDB-001 Phase 2).
> Edit tasks with `tgw todo …` — manual edits here are overwritten.

_Rendered 2026-06-13 15:45 UTC — 38 open, 98 done in the last 7 days._

## admin (13 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 79 | 12 |  | Answer eBay Developer Support 8 open questions re buy.marketplace_insights scope — unblocks PP-REPRICER-001 live repricing; check DS inbox for ticket | [[TGW-Master-Plan#PP-REPRICER-001 — Market-aware dynamic repricer (design pending)\|PP-REPRICER-001]] |  |
| 117 | 13 |  | Round7: Aider setup (COMMITTED, amended 2026-06-12) — create Anthropic API key + HARD billing cap (~$40/mo) in console; tell Claude when done so #118 onboarding files get exercised on a first task. Aider = mechanical code-edit tier alongside Antigravity as primary agent manager |  |  |
| 61 | 15 |  | Round6 #55: PP-BACKUP-001 Phase A operator (plan APPROVED 2026-06-11) — gpg passphrase custody (off-machine, 2 rotated USB keys); GATE: rclone server-side preserve dbukove:TGW -> TGW-historypoint-20260514 BEFORE any new sync; install 3 timers after #60 builds them; first manual sync off-hours + quota check; A5 restore drill, record RTO; check DLT tape interface | [[TGW-Master-Plan#PP-BACKUP-001 — Organized Backup and Disaster Recovery Architecture\|PP-BACKUP-001]] |  |
| 7 | 20 |  | IGDB credentials — Twitch dev account → register app → save client_id/client_secret to secrets_root/igdb-credentials.json |  |  |
| 105 | 22 |  | Round7: Add dev_id to /opt/TGW/secrets/ebay-credentials.json (developer.ebay.com -> Application Keys -> DevID) — ISS-005 code done; this gates webhook infra todo #16 |  |  |
| 80 | 30 |  | Run tgw history-index --target all as tgw user in screen session — hours for 32K zips; output: var/history-itemdata-index.jsonl; command built + smoke-tested [PP-HISTORY-001 done] | `PP-HISTORY-001` |  |
| 11 | 40 |  | tgw ebay-sweep → physical inventory review (run after Perplexity brief results arrive) |  |  |
| 12 | 45 |  | Fix 9 wrong-shipping Seller Hub listings flagged in sweep |  |  |
| 81 | 50 |  | PP-NIXOS-001: review plan/PLAN-nixos-migration.md phases and signal go — Phase 0 items (nix-shell + flake scaffold) become Claude todos on approval | [[TGW-Master-Plan#PP-NIXOS-001 — NixOS Migration Evaluation\|PP-NIXOS-001]] |  |
| 15 | 60 |  | Second keyboard wired up as macroboard (see etc/interfaces/keyd/tgw-macroboard.conf) |  |  |
| 16 | 65 |  | eBay webhook endpoint — nginx/cloudflared so PP-SOLD-001 Tier 4 webhook can receive notifications | [[TGW-Master-Plan#PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)\|PP-SOLD-001]] |  |
| 139 | 66 |  | PP-INTAKE-002 camera app design review — read reference/PP-INTAKE-002-camera-app-design.md and answer 3 questions before build: (1) root strategy: su polling inside app vs separate background shell script? (2) target device for rooting — Pixel/Xiaomi/other? (3) confirm Syncthing path /sdcard/Pictures/TGW_Sync/ matches current folder mapping. Signal go to seed flutter scaffold build todo. |  |  |
| 140 | 67 |  | PP-INTAKE-003 xmouse replacement design review — read reference/PP-INTAKE-003-xmouse-replacement-design.md; Flutter recommended (Apache-2.0, shares tgw_app code, no NDK); 3-phase: (1) macro-pad grid + HTTP/SSH dispatch (2) inline web form panel via flutter_inappwebview (3) embedded VNC via flutter_rfb. Signal go on phase(s) to start to seed build todo. |  |  |

## agy (7 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 101 | 20 |  | Round7 p20: ebay_draft aspect-fill audit — per-category Required/Recommended specifics fill rates from a draft_listing extract; identify worst-coverage categories + prompt-tuning recommendations; result to inbox/. Self-contained brief; route via Antigravity after 2026-06-18 |  |  |
| 141 | 25 |  | PP-VERIFY-001 catalog baseline rescan — rerun catalog quality scan (follow CATALOG-BASELINE-SCAN.md pattern) on current ItemData sample; compare fill rates vs prior baseline; identify new gaps; output updated markdown table to inbox/. AGY large-context data task |  |  |
| 142 | 28 |  | eBay DS ticket follow-up — check Developer Support inbox for responses to the 8 buy.marketplace_insights scope questions (todo #79); summarize any answers or next steps; update plan accordingly. Browser task via Seller Hub / DS portal |  |  |
| 102 | 30 |  | Round7 p30: GDrive dedupe assist (PLAN-backup-dr A8) — chunked rclone dedupe strategy for same-name-same-dir duplicates that time out on the full dataset; produce chunk plan + exact commands; operator supervises execution |  |  |
| 108 | 55 |  | Round7: Verify eBay strikethrough pricing access in Seller Hub (Sale Price section in Edit Listing form), then enable strikethrough_enabled config flag — code shipped + tested session 15, off pending verification |  |  |
| 124 | 72 |  | PP-OFFER-001 design: offer management CLI — tgw offers [--pending]: list incoming best-offer requests (GetBestOffers); respond --accept/--counter/--decline; auto-accept config (min_pct of current price); design doc before build |  |  |
| 143 | 73 |  | PP-OFFER-001 GetBestOffers API research — look up Trading API GetBestOffers call signature, pagination, response fields, and rate limits; verify against TGW's current OAuth scopes; output spec to inbox/ for Claude to code. Feeds todo #133 build |  |  |

## ai_studio (2 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 145 | 45 |  | AI Studio: ItemArchive resurrection triage — feed full GEMINI-007 archive folder inventory (ItemArchive/ 163G, 54K zips, only 40% indexed) into 1M-context window; identify highest-value zips to index first by SKU prefix/date range; output prioritized ingestion plan to inbox/ |  |  |
| 144 | 65 |  | AI Studio: full alt-text batch via Gemini Batch API — upload itemdata image manifest to AI Studio, run gemini-2.5-flash-lite batch job across all ~8350 SKU folders; structured JSON output per item; feeds alt_text ledger. Reference todo #137 for batch architecture spec. Use when Batch API quota allows |  |  |

## claude (15 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 125 | 45 |  | Apply plan/STORE-CATEGORY-MAPPING.csv store category research (from gemini todo #83) to category-groups.json — update store_category field per group; tgw category-groups --reseed; config edit only, no API writes. Aider-eligible |  |  |
| 126 | 48 |  | tgw search --empty FIELD — add --empty FIELD flag to cmd_search returning items where the named field is null/empty-string/missing; 'tgw search --empty location' finds unlocated items; additive filter on resolve_items(); 2 tests. From SUGGESTIONS 2026-06-13 |  |  |
| 95 | 50 | XS | Round7 p50 XS (Aider-eligible): ISS-003 + ISS-004 config hygiene — align full_catalog_path JSON value with code default; surface ebay_sku_migrate block through load_config() instead of cfg[raw] |  |  |
| 127 | 50 |  | catalog-verify leading_space_title rule — add warning in _verify_item() for title.startswith(' '); safe auto-fix in --fix pass via str.lstrip(); 1–2 tests; from SUGGESTIONS 2026-06-13. Aider-eligible | [[TGW-Master-Plan#PP-VERIFY-001 — Catalog Assumption Verification + Hall Pass Flag\|PP-VERIFY-001]] |  |
| 128 | 52 |  | PP-PROMO-001 Phase 1 build — tgw sale-event [create\|list\|end] --input EVENT.md --dry-run: parse markdown sale-event file -> Promotions API createPromotion/updateItemPriceMarkdown; operator-review gate before --apply; no live eBay writes without Dave sign-off; design doc at reference/PP-PROMO-001-sale-event-design.md (session 29) | [[TGW-Master-Plan#PP-PROMO-001 — Sale Event Automation (design complete)\|PP-PROMO-001]] |  |
| 129 | 55 |  | tgw ai-usage --by-sku SKU — per-SKU cost breakdown in AI usage report (sum calls/tokens/cost where job payload contains SKU); feeds cost-per-item goal (Phase 5 #2). Additive to existing ai_usage ledger (session 29) |  |  |
| 136 | 56 |  | pHash image dedup in alt_text + ai_identify workers — compute perceptual hash (imagehash.phash) before vision API call; check image_hashes table (phash -> sku + result_json); on hit copy cached result, skip API; on miss store hash + result after successful call; prevents redundant calls for duplicate photos within or across SKU folders. From PERPLEXITY-007 batch pipeline research. Aider-eligible |  |  |
| 130 | 57 |  | PP-MULTIMODEL-001 cheap model routing — add google/gemini-2.5-flash-lite to tgw-models.json for vision tasks (alt-text, ai_identify: /bin/bash.10//bin/bash.40 per 1M, 60% cheaper than 3.1 Flash-Lite); add google/gemini-2.0-flash-lite for bulk classification; add deepseek/deepseek-v4-flash (~/bin/bash.098/1M input via OpenRouter) for text/classification tasks (pm_intake, classify-suggestions); reference PERPLEXITY-007 cost comparison at perplexity/PERPLEXITY-007_LLM-model-comparisons-and-cascading-strategy.md | [[TGW-Master-Plan#Priority 6 — External AI tooling (PP-MULTIMODEL-001)\|PP-MULTIMODEL-001]] |  |
| 112 | 58 | S | Round7 p58 S: PP-PLANDB-001 Phase 3 — tgw plan check: reconcile plan<->tracker both directions (pp_ref/plan_anchor vs plan sections; round-tagged todos vs plan round summaries); report orphans + status mismatches; add to session-start ritual in CLAUDE.md; mismatch reports feed the improve-the-admin loop (PP-DOCFLOW) | [[TGW-Master-Plan#PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)\|PP-PLANDB-001]] | ✓ deps done |
| 131 | 59 |  | tgw ebay-pull scoping — add --sku SKU [SKU...] / --location LOC / --status STATUS filters to cmd_ebay_pull so operators can pull-sync a subset of listings without a full sweep; wraps existing pull.sync_item() per-item path; from PP-SHELL-001 Round 5 deferred item |  |  |
| 132 | 61 |  | PP-PLANDB-001 Phase 4 — tgw plan status [PP-REF]: one-line status summary per PP-* item (open/done/blocked todo counts + latest activity); feed into session-start output in CLAUDE.md. Requires Phase 3 (#112 open) | [[TGW-Master-Plan#PP-PLANDB-001 — Database-Driven Plan Builder (design discussion needed)\|PP-PLANDB-001]] | ⛔ #112 |
| 137 | 63 |  | Gemini Batch API path for full-catalog alt-text sweep — tgw alt-text --batch --api-mode batch: chunk ~8350 SKUs into 40-image arrays (~5 SKUs each), submit to Gemini Batch API async, poll completion, write results back via existing alt_text ledger; resumable via ai_usage/image_hashes state; replaces serial live-API calls for full-catalog runs; dramatically reduces rate-limit pressure and cost. Reference: PERPLEXITY-007 batch pipeline research + gemini-2.5-flash-lite model |  |  |
| 113 | 72 | M | Round7 p72 M (GATED: after admin #20 Qtile install): PP-CLIP-001 daemon — dual-backend watcher per settled design (2026-06-12): backend-agnostic core (on change -> classify -> SQLite -> socket push); X11/XFixes backend (default/stable) + Wayland wl-paste --watch backend; session-type autodetect; PRIMARY+CLIPBOARD; feeds existing tgw clip store; Unix socket for TGWSKUWidget | [[TGW-Master-Plan#PP-CLIP-001 — TGW-Aware Clipboard Manager\|PP-CLIP-001]] | ✓ deps done |
| 133 | 74 |  | PP-OFFER-001 Phase 1 build — GetBestOffers Trading API polling; tgw offers [--pending] lists incoming offer requests; tgw offers --respond ID --accept/--counter PRICE/--decline; auto_accept_min_pct config flag for batch auto-accept; dry-run default; tests. Build after design in #124 settled | [[TGW-Master-Plan#PP-OFFER-001 — eBay Best Offer Management\|PP-OFFER-001]] | ⛔ #124 |
| 134 | 76 |  | PP-REVISION-001 apply path — ReviseFixedPriceItem call with pinned-baseline drift-gate (apply only when live mirror matches baseline hash); --dry-run default; NO eBay write until Dave confirms sparse-delta apply design settled. Continuation of #111 dry-run delta | [[TGW-Master-Plan#PP-REVISION-001 — Live listing revision / update draft (design open)\|PP-REVISION-001]] | ✓ deps done |

## sokoban (1 open)

| ID | Pri | Size | Task | Plan | Blockers |
|---:|----:|:----:|------|------|----------|
| 17 | 20 |  | PP-SOLD-001 Tier 3 — physical sweep checklist after full-history CSV import; run tgw ebay-sweep | [[TGW-Master-Plan#PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)\|PP-SOLD-001]] |  |

## Done this week (98)  — showing 15 most recent

| ID | Agent | Done | Task |
|---:|-------|------|------|
| 114 | admin | 2026-06-13 | Round7: Antigravity code-task trial during #78 validation week — run todo #95 (ISS-003/004 config hygiene) via agy as the bite-sized code trial; compare review burden vs a Claude session. Purpose: ROUTING CALIBRATION between Antigravity (primary agent manager) and Aider (committed regardless — see #117). Amended decision 2026-06-12, next-process.md §2 |
| 86 | gemini | 2026-06-13 | DEADLINE 2026-06-18: export Gemini CLI config, custom commands, skill definitions, and any hooks before shutoff — save to docs/TGW-Plan-Vault/reference/gemini-cli-export.md for reference during Antigravity setup |
| 78 | admin | 2026-06-13 | HARD DEADLINE 2026-06-18: Antigravity/Gemini migration validation — 5 steps while both CLIs still live: (1) confirm skills/hooks/subagents carried over to Antigravity (2) test headless/scripted use of Antigravity (3) re-run one Gemini brief in Antigravity, diff quality (4) export Gemini CLI config/custom commands/history before shutoff (5) observe compute-cap refresh behavior on one bite-sized task [plan/next-process.md §3] |
| 104 | admin | 2026-06-13 | Round7: Register TGW MCP server in ~/.claude/settings.json (Track 4 Priority 1b, 2 min) — mcpServers block per master plan; restart Claude Code; unlocks live queue/item/health tools every session |
| 107 | admin | 2026-06-13 | Round7: Run PERPLEXITY-001..004 research briefs (perplexity/ folder) before subscription expiry ~2026-12; save each result .md to inbox/ for pm_intake. 001 eBay scopes first (informs DS response #79) |
| 138 | claude | 2026-06-13 | Qtile cheatsheet — write ~/.config/qtile/cheatsheet.txt with key bindings (mod key, windows, layouts, screens, groups, scratchpads, TGW widgets); add Qtile keybinding ctrl+alt+super+h to display it (e.g. spawn('xterm -e "cat ~/.config/qtile/cheatsheet.txt; read"') or notify-send/rofi); reload Qtile config after |
| 135 | admin | 2026-06-13 | Investigate keyd q-key stuck — check /etc/keyd/*.conf for q remapping; keyd -l for active layer bindings; evtest /dev/input/eventN to verify hardware key registration; compare against working key for stuck-modifier pattern. From SUGGESTIONS 2026-06-13 |
| 20 | admin | 2026-06-13 | PP-WM-001: Install Qtile WM — run: bash /opt/TGW/src/trader-grims-warehouse/etc/interfaces/qtile/install.sh (as desktop user, not root). Then log out and select Qtile at login screen. |
| 120 | admin | 2026-06-13 | Enable ebay_dole worker (needs root): sudo systemctl enable --now tgw-worker@ebay_dole.service — rate-limited ready-pool publishing (1/60 per hour cycle); until then 'tgw ready set' items sit in the pool and tgw publish remains the only listing path |
| 119 | admin | 2026-06-13 | Enable plan_render worker (needs root): sudo systemctl enable --now tgw-worker@plan_render.service — until then the taskboard only refreshes via 'tgw plan render'; PP-PLANDB-001 Phase 2 code is live |
| 121 | claude | 2026-06-13 | CANCELLED: garbled stub — agy code-task trial already covered by admin #114 |
| 123 | claude | 2026-06-13 | PP-FREESHIP-001 design + build: free shipping mode — combine item price + shipping_cost rounded to nearest .99 as new listing price with free shipping offer; config flag free_shipping_enabled (default off); useful for absorbing shipping rate increases |
| 98 | claude | 2026-06-13 | Round7 p65 S (Aider-eligible): Discogs adapter migration — apis/lookup/discogs.py from deprecated discogs_client to direct httpx; same adapter surface, same tests + live-shape fixtures. PERPLEXITY-005 finding |
| 96 | claude | 2026-06-13 | Round7 p55 S (Aider-eligible): PP-SHELL-001 Tier 3 — grouped tgw --help (Read/Search, Write, Pipeline, eBay, Context, Catalog, Ops) + requeue -> requeue-identify rename with deprecated alias |
| 122 | claude | 2026-06-12 | PP-TODO-001: tgw todo brief --clip + --next flags — --clip copies brief to clipboard; --next --agent gemini outputs top task for that agent |
| … | | | _…and 83 more — run `tgw todo --all` to see everything_ |
