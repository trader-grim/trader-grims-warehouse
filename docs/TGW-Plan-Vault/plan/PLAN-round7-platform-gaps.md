# PLAN: Round 7 — Platform gap analysis and execution plan

**Status:** v1, 2026-06-12 (session 28, planning only — no code).
**Method:** full review of `docs/TGW-Plan-Vault/` (master plan 3,463 lines, handoff v5,
ISSUES.md, invariants.md, architecture docs, runbooks, PLAN-backup-dr, PLAN-nixos,
next-process, all PERPLEXITY/GEMINI results, reference library) cross-checked against the
todo tracker (sessions 24–27 drained Rounds 5–6 + tasks 70–77; suite at 614) and the live
queue state. Three questions: what does the platform still need, what is noted in the
docs but never planned or tracked, and what new facilities advance the goals.

Tracker remains canonical for execution; this doc is the reference spec for Round 7.
All Round 7 tasks were seeded into `tgw todo` on 2026-06-12 with `--source round7`.

---

## 1. Gap analysis

### 1a. Designed in the plan, never built (no tracker entry until now)

| Item | Where designed | Why it matters |
|------|---------------|----------------|
| **Sync-conflict resolution worker** | PP-PORTABLE-CATALOG-001 (zero-data-loss design recorded session 19) | The portable catalog + vault channel now both ride Syncthing; conflicts are currently invisible. File-scan version needs no API and is buildable today. Live test artifact left in the vault on purpose. |
| **Ready state + rate-limited dole-out** | PP-EDITOR-001 admin GUI spec + PP-REVISION-001 governing principle | The single biggest operator-workflow piece not yet in code: "set Ready is the default done state; list at 1/60 of ready per cycle; List Now bypasses." Carries the draft→review→apply principle into code first. |
| **AI work-distribution + usage monitoring** | Phase 5 deliverable #2 (never got a PP) | Feeds the stated "cost per item + electricity cost" goals; with 4+ providers now in play (Ollama, OpenRouter, Gemini/Antigravity, Claude), there is no record of who did what at what cost. |
| **Computer-side intake (`tgw create-item`)** | PP-INTAKE-001 Phase 2.5 | Pre-create SKU folder + template + KDE Connect COMMAND: push; makes the phone purely a camera. Architecturally simpler than push-from-camera and probably faster per item. |
| **Picklist PDF + QR / label printing** | PP-ADD-009 + PP-FULFILLMENT-001 | `tgw picklist` (text) exists; print-ready PDF, QR codes, and Code128 SKU labels do not. PDF generation is offline-testable now; printer send waits on hardware. |
| **Alt-text batch backfill** | PP-DATALEARN-001 (provider strategy researched sessions 21–22) | `tgw alt-text <sku>` (single item, Ollama) shipped session 23; the researched OpenRouter-free-vision batch path that offloads the CPU-only Ollama was never tasked. |
| **PP-DEADLETTER-001 remainder** | PP-DEADLETTER-001 "Remaining work" | Health dead-letter breakdown should split TRANSIENT vs HARD_FAILURE so the operator sees real signal vs noise at a glance. |
| **PP-SHELL-001 Tier 3** | Tier 3 open items (2026-06-11) | ~65 subcommands in flat `--help`; `requeue` is ai_identify-only but generically named. Both are friction-under-duress items. |
| **pm_intake URL/URI submissions** | PP-DOCFLOW-001 Phase 3 (decided by Dave 2026-06-11 18:25) | Drop a link in the inbox → admin fetches, classifies, files it like a doc. First Phase-3 slice; the rest of Phase 3 (binaries/vision, whole folders) follows later. |
| **ISS-003 / ISS-004 config hygiene** | ISSUES.md | Two known XS inconsistencies (`full_catalog_path` mismatch; `ebay_sku_migrate` bypasses `load_config`). Cheap to close; both are audit hazards. |
| **discogs_client migration** | PERPLEXITY-005 finding | Library officially unmaintained; adapter exists, direct-httpx migration was recommended and never tasked. Isolates a known breakage risk. |
| PP-MC-001 Phases 3–4; PP-WM-001 Phases 2–3; PP-CLIP-001 Phase 1 daemon | Respective sections | Held: desktop-session/hardware-gated or lower value than the above. PP-CLIP daemon also has a python-xlib-staleness question (see 1b). |
| PP-EMAIL-001, PP-TASKER-001, PP-WHISPER-001 extensions | Respective sections | Held: blocked on scope family / phone intent audit / operator priorities. |
| PP-REVISION-001, PP-PLANDB-001 | Design-open | Need Dave decisions (sparse-delta vs full-replacement; DB-plan scope) before any code. Round 7 schedules the *discussions*, not builds. |
| PLAN-nixos Phases 0–6, PLAN-backup-dr Phases B–C | Approved plans | Gated on operator go (#81) and Phase A completion (#61) respectively. Not re-tasked here. |
| PP-REPRICER-001 live, PP-VISION-001 P2, PP-SOLD-001 Tier 4 deploy | — | Blocked: MI scope (#79), GPU hardware, webhook infra (#16 + dev_id). |

### 1b. Noted in documents but never planned

| Item | Where noted | Assessment |
|------|------------|------------|
| **Taxonomy `getCategorySuggestions`** | eBay-API-Landscape ("AI title → category candidates") | Free accuracy win we already have scope for: cross-check/derive `ebay_category_id` from the drafted title instead of trusting AI output alone. Wrong category is a Cassini relevance killer (PERPLEXITY-002 #1 ranking factor). → Round 7. |
| **eBay Promotions automation** | `sell.marketing` scope held since day 1; only strikethrough was ever wired | Markdown sale events for stale inventory are an unexploited capability of a scope we already hold. Complements `ebay_price_reducer` (price cuts) with visible "Sale" badging. → design-first task. |
| **Photo verification crawler** | Suggestion 2026-06-07 17:52, parked at "Track 1 rank-25+" | Live listings with no/broken EPS photos sell poorly and are invisible locally. → Round 7 (catalog-verify rule + sync-pass check). |
| **Zero-work worker watchdog** | ebay_sku_migrate stall postmortem ("noted as future patterns") | A worker that runs but accomplishes nothing for N cycles is currently silent. One health rule covers every worker. → Round 7 (bundled with dead-letter split). |
| **psycopg3 migration + aiosqlite reads** | PERPLEXITY-005 | Right long-term; not urgent. Defer to a NixOS-adjacent round (natural dependency-refresh moment). |
| **python-xlib staleness / Wayland clipboard** | PERPLEXITY-005 | Blocks committing to PP-CLIP-001 Phase 1 as designed. Fold the decision into PP-NIXOS planning rather than building an X11-only daemon now. |
| **LiteLLM router layer** | LLM-routing research (sessions 21–22) | Adopt only when local+cloud mixing in one pipeline actually hurts; `tgw-models.json` dispatcher is sufficient today. Watch item. |
| **GDrive dedupe (chunked rclone / Antigravity)** | PLAN-backup-dr A8 | Frees ~significant quota for the 3-role GDrive model. Antigravity-assisted = near-zero token cost. → Track 2 task. |
| **`ebay_draft` aspect-fill audit** | Track 2 table (only unfinished non-deadline Gemini task) | Worst-coverage categories → targeted prompt tuning. → Track 2 task. |
| **International listing expansion** | eBay-API-Landscape | Far horizon; revisit after multi-marketplace abstraction is real. Not scheduled. |
| **DLT tape tier** | PLAN-backup-dr 1b | Curiosity tier pending hardware check. Not scheduled. |

### 1c. New facilities proposed by this review

1. **Sales/profit reporting (`tgw report sales`)** — the platform records `ebay_sale`
   blocks, velocity stats, and reprice history but has **no business-level report**:
   units/revenue by month and category-group, sell-through, average days-to-sale, price-stage
   at sale (launch/retail/move). One read-only command writing a markdown/CSV artifact to the
   vault. This is the first concrete instance of the PP-DOCFLOW Phase-3 "admin
   presentation/aggregation" skill, and it feeds pricing strategy with zero eBay calls.
   A future `cost_basis` field (additive) would extend it to true profit; not in scope yet.
2. **Dead-stock surfacing** — a `tgw report stale` view (days listed × category sell-through
   × stage) ranking candidates for markdown events (1b Promotions), bundling, or delisting.
   Cheap once the sales report scaffolding exists; folded into the same task.
3. **PP-PROMO-001 (proposed)** — markdown sale-event automation on the held `sell.marketing`
   scope (see 1b). Design-first: verify Seller Hub promotion surface, then a worker that
   builds a weekly markdown event from the dead-stock report with operator review
   (draft→review→apply principle applies).
4. **Catalog FTS** — SQLite FTS5 over title/description in `tgwcatalog.db` for instant
   fuzzy search across every interface (CLI, tgw-http, Flutter, MC). Deferred — current
   LIKE-based search is adequate; revisit when the Flutter app's search screen lands.

### 1d. Two-surface / operator gaps (in docs, absent from tracker)

These were all seeded as admin todos (the "plan rows not seeded vanish" lesson, handoff risk 8):

- **Merge PR #3** (`session-27-tasks-70-77` is OPEN; review findings already addressed in `d0d6933`).
- **Register the TGW MCP server** in `~/.claude/settings.json` (Track 4 Priority 1b; 2 minutes; unlocks live queue/item/health queries in every future session).
- **Add `dev_id`** to `secrets/ebay-credentials.json` (ISS-005 code is done; this is the remaining gate for webhook infra #16).
- **Verify `openrouter-credentials.json`** exists + 0600 and pm_intake is healthy (handoff risk 6 — pm_intake dead-letters every job without it).
- **Run PERPLEXITY-001–004** before subscription expiry (~2026-12). Briefs are ready in `perplexity/`; results unblock PP-REPRICER fallbacks, SEO tuning, and integration upgrades.
- **Verify strikethrough access** in Seller Hub, then flip `strikethrough_enabled` (code shipped + tested session 15; off pending verification).
- **GPU target of opportunity** (RTX 3090 24GB per HARDWARE-AI-INFERENCE) — watch item; unblocks PP-VISION-001 P2 + PP-INTAKE background inference. No deadline.

---

## 2. Round 7 task list (seeded 2026-06-12, `--source round7`)

### Track 1 — Claude (bounded sessions; Aider-eligible marked per next-process gate)

| Pri | Size | Task | PP |
|-----|------|------|----|
| 10 | S | Sync-conflict resolution worker, file-scan version: scan vault + catalog-export sync roots for `*.sync-conflict-*`; auto-discard only when provably redundant (byte/JSON compare vs canonical); divergent content → `inbox/review/` + todo; health surfaces count. **Zero-data-loss invariant; never auto-delete unique content.** Classify the live `.obsidian/community-plugins.sync-conflict-*` artifact as the acceptance case. | PORTABLE-CATALOG P3 |
| 15 | M | Ready state + dole-out: `status=ready` as post-review done-state; `tgw ready [list\|set <sku…>]`; self-scheduling `ebay_dole` worker publishing ready items at configurable rate (default 1/60 of ready pool per cycle); `tgw publish <sku>` stays as List-Now bypass. Carries the draft→review→apply principle into code. | EDITOR/REVISION |
| 20 | M | AI usage ledger: record provider/model/purpose/duration/token-or-char counts for every `call_model()` + Ollama + OpenRouter call into an `ai_usage` table; `tgw ai-usage [--since]` report (per provider/queue/day). Feeds cost-per-item goal. | Phase 5 #2 |
| 25 | S | Alt-text batch path: `tgw alt-text --batch [--limit N] [--provider openrouter\|ollama]` using the researched OpenRouter free-vision routing (~20 req/min limit, fail-soft per item, resumable); `<SKU>-alt.jpg` convention as shipped in session 23. | DATALEARN |
| 30 | S | `tgw create-item [--template GROUP] [--count N]`: pre-create SKU folder + blank JSON with template applied + KDE Connect COMMAND: push (computer-side intake, Phase 2.5). | INTAKE |
| 35 | S | Picklist/label print Phase 1 (offline): `tgw picklist --pdf` (location-sorted, checkboxes, QR per line) + `tgw print-label <sku>` Code128 label PDF; CUPS send stubbed behind config until printer hardware lands. | ADD-009/FULFILLMENT |
| 40 | S | Category validation via Taxonomy `getCategorySuggestions` in `ebay_draft`: query with drafted title, record `category_suggestions` + agreement flag in draft_listing; low-confidence mismatch → catalog-verify rule. Mocked tests; no behavior change to category choice yet. | SEO/HINT |
| 45 | XS | Dead-letter health split: TRANSIENT vs HARD_FAILURE counts in `check_postgres` detail + `tgw_queue_status`; plus zero-work watchdog rule (worker alive, 0 completions over N hours while queue non-empty → warning). | DEADLETTER |
| 50 | XS | ISS-003 + ISS-004: align `full_catalog_path`, surface `ebay_sku_migrate` block through `load_config()`. *(Aider-eligible)* | — |
| 55 | S | PP-SHELL-001 Tier 3: grouped `tgw --help` (Read/Search, Write, Pipeline, eBay, Context, Catalog, Ops) + `requeue`→`requeue-identify` rename with deprecated alias. *(Aider-eligible)* | SHELL T3 |
| 60 | S | pm_intake URL/URI submissions: inbox note containing only a URL → fetch content → classify/file like a document (same filing log + review-flag rules). | DOCFLOW P3 |
| 65 | S | Discogs adapter: migrate `apis/lookup/discogs.py` from deprecated `discogs_client` to direct httpx (same adapter surface, same tests + live-shape fixtures). *(Aider-eligible)* | LOOKUP |
| 70 | S | `tgw report sales` + `--stale`: monthly units/revenue by category-group, sell-through, days-to-sale, price-stage-at-sale from `ebay_sale` + velocity data; dead-stock ranking; markdown/CSV artifact to vault. Read-only. | DOCFLOW P3 seed |
| 75 | S | PP-PROMO-001 design doc: markdown sale-event automation on held `sell.marketing` scope; consumes the dead-stock report; draft→review→apply; **design + operator-verification checklist only, no eBay writes**. | PROMO (new) |

The four reserved discussion items were decided with Dave the same day — see
**§5 Decisions addendum** below. Follow-on build tasks seeded as todos #109–#114.

### Track 2 — Gemini CLI → Antigravity (self-contained, large-context; bite-sized)

| Pri | Task |
|-----|------|
| 20 | `ebay_draft` aspect-fill audit: per-category Required/Recommended specifics fill rates from a draft_listing extract; worst-coverage categories + prompt-tuning recommendations → inbox. |
| 30 | GDrive dedupe assist (PLAN-backup-dr A8): chunked `rclone dedupe` strategy for the same-name-same-dir duplicates; produce the chunk plan + commands; execution supervised by operator. |

(Existing deadline tasks #82–86 unchanged and still first — hard deadline 2026-06-18.)

### Track 3 — Perplexity (operator-run; expires ~2026-12)

PERPLEXITY-001–004 runs seeded as a single admin todo (1d above). No new briefs needed
this round; PP-PROMO-001 design may generate one later (Promotions API current state).

### Track 4 — Operator (seeded as admin todos)

Merge PR #3 · MCP registration · `dev_id` credential · OpenRouter key verify ·
PERPLEXITY-001–004 · strikethrough verification. (Existing #78/#79/#61/#16/#81 etc.
unchanged; #78 Antigravity validation remains the hard-deadline item.)

---

## 3. Sequencing

1. **This week (deadline-driven, operator):** #78 Antigravity validation (2026-06-18) →
   merge PR #3 → MCP registration → OpenRouter key verify → backup Phase A (#61).
2. **Claude pick-up order:** straight down the priority column. Items 10–25 are the
   high-leverage batch (data-safety worker, listing-flow capstone, cost visibility,
   Ollama offload); 30–45 are operator-time wins; 50–75 are hygiene + new-facility seeds.
3. **Aider gate:** three Aider-eligible tasks queued (pri 50/55/65). If Dave sets the API
   key + billing cap, run the 2–3-task trial per next-process §2 and compare review burden.
4. **After eBay DS answers (#79):** PP-REPRICER-001 unblocks; after webhook infra (#16 +
   dev_id): PP-SOLD-001 Tier 4 goes live; after NixOS go (#81): PLAN-nixos Phase 0 becomes
   todos and the psycopg3/Wayland-clipboard decisions ride along.

## 4. Deliberately not scheduled

Multi-marketplace abstraction, sales website, international expansion (Phase 6 horizons —
prerequisites not met); PP-ADD-003 history merge worker (history-index output should be
populated first — admin #80); Ollama job manager (OpenRouter offload reduced the contention
that motivated it; revisit if local vision load returns); FTS5 search (wait for Flutter
search screen); LiteLLM (no demonstrated need); PP-CLIP daemon (Wayland decision first);
relisting obfuscation (stays shelved — ToS).

---

## 5. Decisions addendum (2026-06-12, session 28 — discussions held with Dave)

1. **PP-REVISION-001 — sparse delta + pinned baseline.** Revision draft = changed fields +
   snapshot/hash of the live-mirror baseline. Apply = drift gate (overlapping-field drift →
   review, never silent) → compose fresh live state + delta → full eBay PUT. Delta list =
   `revision_history`. First slice: dry-run delta computer (`tgw revise --set`, no apply path,
   no eBay writes) — todo #111.
2. **PP-PLANDB-001 — Option C, generated taskboard, companion file.** DB owns tasks; design
   prose stays hand-authored in the master plan; task views render to a wholly-generated
   `plan/TGW-Taskboard.md` (+ `/form/todos` from the same DB). **Gateway clarification from
   Dave:** he no longer edits the plan directly — everything arrives via inbox/`tgw suggest`,
   so the PP-DOCFLOW project admin is the single write-gateway for both surfaces; drift is
   structurally closed. `tgw plan check` becomes a safety net on the admin and feeds the
   improve-the-admin loop. Phases: P1 schema + `tgw todo brief` (#109), P2 render + job +
   health staleness (#110), P3 plan check (#112).
3. **PP-CLIP-001 — dual-backend watcher, both first-class.** Environment is already mixed and
   the world is moving toward Wayland; X11 is the stable platform for now. X11/XFixes backend
   default + Wayland `wl-paste --watch` backend; session-type autodetect; PRIMARY+CLIPBOARD;
   `~/.local/share/tgw-clip/`. Build after Qtile install (#20) — todo #113.
4. **Aider — committed (amended same day).** Aider will be used even if Antigravity becomes the
   primary agent/agent manager. Antigravity-first trial week stands as routing calibration
   (admin #114); Aider setup is unconditional (admin #117, claude #118). Lanes: Antigravity =
   primary agent-manager (bite-sized/browser/analysis), Aider = mechanical code-edit tier,
   Claude = architecture/invariants/planning. Recorded in next-process.md §2.
