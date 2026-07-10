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

✅ a1131 client desktop setup complete — Plasma 6 + Input Leap + Syncthing deployed to a1131 (session 29, 2026-06-27). See `a1131-client-desktop-setup.md` for details.
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

- Perplexity research note on replacing local Qwen 2.5 with external low-cost models (Google AI Studio, OpenRouter) for eBay listing drafting — filed `docs/TGW-Plan-Vault/reference/RESEARCH-replace-qwen-external-models.md`
