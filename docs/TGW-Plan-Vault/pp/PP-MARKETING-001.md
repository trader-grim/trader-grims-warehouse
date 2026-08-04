# PP-MARKETING-001 — marketing strategy (full detail)

## PP-MARKETING-001 — marketing strategy (pricing, positioning, promotions) — NEW 2026-07-11
**New PP (Dave, 2026-07-11): "pricing is really marketing strategy."**
Umbrella for positioning/pricing-strategy work, previously miscategorized as
part of the repricer tool. PP-PRICING-001 is its first tenant — likely not
its last (comps, listing-copy strategy, promotions could land here too).

**SerpApi key (#1110), 2026-07-16 (Dave): "maybe, let's get the pipeline
restarted in earnest first."** Deferred, not urgent — priority is the
pipeline restart, not a paid-key provisioning decision right now.

### PP-PRICING-001 — Google Shopping comps via SerpApi (paid)
Two candidate data sources for comps, not mutually exclusive:
1. **eBay sold data** via `buy.marketplace_insights` — BLOCKED external: scope
   request in the eBay application review (#79, Dave answers DS questions).
2. **Google Shopping comps via SerpApi (paid)** — the designed interim
   substitute for marketplace_insights, dropped from the s42 redraw index by
   mistake and restored at Dave's flag. Full Phase 1 design (title-based
   Shopping SERP in ai_identify, `apis/lookup/shopping_search.py`, key via
   `secrets_root/tgw.env` per settled architecture, corrected 2026-07-12):
   `pp/PP-PRICING-001.md`.
   Cross-market active prices (Google Shopping: eBay/Amazon/Walmart) — a
   real floor signal, unlike same-marketplace Browse asking prices.
3. **Google-grounded price check** (Dave's 2026-06-09 suggestion, also
   dropped — "not accessible via API" is now stale: Gemini supports Search
   grounding as an API tool on our free-tier direct key). Zero-cost eval
   before paying for SerpApi.

Eval packet (#1109) — DONE 2026-07-04: ran grounded Gemini (gemini-2.5-flash +
Google Search grounding) against 10 real sold TGW items, scored vs the existing
free `BrowseCompsProvider` signal. **Result: Gemini grounding LOST** — 45.3%
mean abs error vs 30.4% for Browse comps; it kept finding plausible-but-wrong
comps for near-generic/vintage items. **Do not wire grounded Gemini as a
pricing signal.** SerpApi (Shopping SERP) still untested — blocked on #1110's
key. Full writeup: `docs/TGW-Plan-Vault/inbox/DONE-1109-repricer-eval.md`,
raw data `/opt/TGW/var/log/repricer-eval-1109.json`.

**Phase 0 comping interface** (research inbox, `pp/PP-PRICING-001.md` Phase 0
section): the #1109 result directly validates a Perplexity research thread's
thesis — don't let a model invent prices, build a supervised capture tool
instead. Proposed: 3-pane web UI (item / embedded eBay Product Research
browser / structured comp+pricing capture), `comp_snapshot` +
`pricing_recommendation` schema, Marketplace Insights as a later drop-in
upgrade to the same schema. Design capture only, not started — needs Dave's
go/no-go. Related: PP-AGENTIC-PRICE-001 candidate-query design composes
with either.

**Phase -1 — self-powered comp engine (Dave request, todo #1134):** the
infrastructure (`OwnSalesProvider` + `velocity_stats` worker) already exists
and runs — this turned out to be a data-density problem, not a missing
feature. **Initial 71%-uncategorized figure was checking the wrong field**
(Magento `attribute_set`, not what the pricing engine reads) — corrected via
todo #1135: the real field (`ebay_category_id`) is already populated on 52%
of the catalog (28,710/55,419).

**Todo #1135 — DONE, applied 2026-07-04.** Built
`scripts/recompile_category_backfill.py` as a **repeatable recompile
job** (Dave: "build it like we are going to go back in with a stronger
dataset every so often") — modular sources, additive-only via the new
`items.set_fields(only_if_absent=True)` fence helper, safe to re-run.
Checked 3 structured sources (historical-tgwcatalog.json,
historical-master-catalog.json via sku_old, `searchcatalog.csv`'s real
`ebaycat` values) against the 26,709 gap: **5,367 recoverable (20%),
applied live, 0 errors, idempotent on re-run.** 21,342 genuinely
unrecoverable from flat exports — that's the real target for the Phase 0
comping interface. Full detail: `pp/PP-PRICING-001.md` Phase -1 section.

