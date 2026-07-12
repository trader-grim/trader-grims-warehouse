# PP-PRICING-001 (promoted from archive on touch, session 42 — design unchanged)

### PP-PRICING-001 — Image + title price comps via Google Shopping / Bing Visual Search

Interim substitute for `buy.marketplace_insights`. Not sold prices, but active listing prices
across multiple marketplaces (eBay, Amazon, Walmart, etc.) give a strong pricing floor signal
and significantly improve identification accuracy for unknown items.

#### Phase 1 — Title-based Shopping SERP (runs in `ai_identify` after Ollama step)

- Module: `apis/lookup/shopping_search.py` → `search_by_title(title, api_key) -> ShoppingResult`
- API: SerpApi `engine=google_shopping` with the AI-identified title
- Returns: prices across Google Shopping (eBay, Amazon, Walmart, etc.)
- Output written to item JSON:
  ```json
  "price_comps": {
    "shopping_search": {
      "source": "google_shopping", "query": "...", "fetched_at": "...",
      "prices": [29.99, 34.99, 45.00], "p25": 29.99, "p50": 34.99, "count": 12
    }
  }
  ```
- Integration: `suggest_price()` Stage 1.5 — use `shopping_search.p25` alongside Browse API p25
- Key: `secrets_root/tgw.env` (`SERPAPI=...`), read via
  `tgw.apis.secrets.get_api_key("serpapi")` — **not** a new
  `serpapi-credentials.json` (corrected 2026-07-12, Fable independent review
  #1338: this doc still pointed at the per-provider-JSON pattern banned
  2026-07-09/todo #1252, see CLAUDE.md settled architecture)
- Cost: ~$0.001/item (SerpApi pro plan); free tier 100 searches/month
- Graceful skip if key absent (same pattern as `igdb.py`, `discogs.py`)

#### Phase 2 — Image-based Visual Search (concurrent with Phase 1 in `ai_identify`)

- Module: `apis/lookup/visual_search.py` → `search_by_image(image_bytes, api_key) -> VisualResult`
- API: **Bing Visual Search API** — accepts multipart image upload, no public URL required
  - Endpoint: `https://api.bing.microsoft.com/v7.0/images/visualsearch`
  - Auth: `Ocp-Apim-Subscription-Key` header
  - Cost: $1.50/1000 queries; free tier 1,000/month (Azure Cognitive Services)
- Returns: `visualSearchTags` (product ID) + `ShoppingSource` actions (merchant prices)
- Output written to item JSON:
  ```json
  "price_comps": {
    "visual_search": {
      "source": "bing_visual", "fetched_at": "...",
      "identified_title": "Sony WH-1000XM4 Wireless Headphones",
      "prices": [34.99, 39.99], "p25": 34.99, "count": 8
    }
  }
  ```
- If Bing's identified title confidence exceeds Ollama's: write to `ai_identify_result.lens_title`
  (stored alongside `ai_identify_result.title`; operator sees both in review queue)
- Key: `secrets_root/tgw.env` (`BING_SEARCH=...`), read via
  `tgw.apis.secrets.get_api_key("bing_search")` — not a per-provider JSON file
  (same correction as the SerpApi key above)
- Graceful skip if key absent

#### Integration in `ai_identify` worker

```
1. Ollama vision → title, category, condition  (existing)
2. Barcode lookup → product_context            (PP-LOOKUP-001, existing)
3. Phase 1 + Phase 2 fire concurrently as asyncio tasks after step 1
4. Results merged into item JSON via fence call before job completes
```

- Both phases are additive — they never overwrite Ollama's title/category output
- `identification_history` event type `image_search` added (source, query, identified_title)

#### Feeds into PP-REPRICER-001

- `ShoppingSearchProvider` added as a fourth `MarketDataProvider` in `market_data.py`
- Plugs into `recommend_price()` blend alongside `BrowseCompsProvider` and own sales
- When `buy.marketplace_insights` arrives it slots in as the authoritative sold-price signal
  and `ShoppingSearchProvider` drops to a supplementary role

#### Operator checklist

- [ ] Sign up for SerpApi (serpapi.com) — free tier is enough for evaluation
- [ ] Create Azure Cognitive Services resource → get Bing Search V7 subscription key
- [ ] Write keys to `secrets_root/tgw.env` (`KEY=value`, chmod 600) — never a new
      per-provider `<name>-credentials.json`
- [ ] Restart `ai_identify` worker after keys land

---

### Phase 0 — Comping interface (research inbox, 2026-07-04, todo #1109 validates the premise)

**Origin:** Perplexity research thread dropped in inbox (`pricing-research-ui.md`),
processed 2026-07-04. Directly confirmed by the PP-REPRICER-001 eval packet run
the same day (todo #1109): Gemini + Google Search grounding *underperformed*
the existing free Browse comps signal (45.3% vs 30.4% mean abs error against
10 real sold items) — it kept finding plausible-but-wrong comps for
near-generic items. This validates the research's core thesis: **don't let a
model invent prices from scratch; comp retrieval + human supervision beats
model-grounded search.**

**Core idea — supervised hybrid, not autonomous scraping:** operator opens
eBay Seller Hub Product Research (Terapeak) inside a browser pane, reviews
comps like they already do, then hits "Capture snapshot" — the system stores
the *reviewed, structured result* rather than trying to scrape or invent it.
Marketplace Insights API access (still pending business-division approval,
3+ weeks) becomes a drop-in upgrade path later, not a dependency — same
schema, better-sourced data.

**Proposed UI: 3-pane web editor, not Flutter.** Decision already reasoned
through in the research thread and matches TGW's existing web/Flutter split
(web = universal/comprehensive surface, Flutter = event-driven premium
client over the same backend state — no forked business logic): the browser
pane is the entire point of this tool, and Flutter's Linux desktop webview
story is CEF-plugin-dependent and immature. Layout:

| Region | Width | Purpose |
|---|---|---|
| Item pane | 28% | title draft, category, brand/model/MPN/UPC, condition, completeness flags |
| Browser pane | 44% | embedded eBay Product Research/sold search, operator-driven, quick-query chips (exact title / brand+model / MPN / UPC / broad fallback) |
| Comp pane | 28% | structured capture form + pricing recommendation + approve/override |

**Data model (draft, needs Dave's review before building):**
- `comp_snapshot` — source, query, match_assessment, market_stats (comp_count,
  sold_median, sold_mean_trimmed, sold_low/high, avg_shipping), operator_notes,
  exclusions. One per research session, timestamped, reviewer-attributed.
- `pricing_recommendation` — based_on_comp_snapshot_id, strategy, inputs
  (base_price + adjustments), outputs (target/list/floor/auto-accept prices),
  guardrails (cost_floor, min_profit_abs, min_margin_pct), review status.
- Main item JSON keeps only current-state pointers (`pricing.current`,
  `pricing.latest_comp_snapshot_id`) — full history lives in the item's
  history sidecar, matching TGW's existing current-vs-history split
  (E5/archive-before-overwrite pattern already enforced elsewhere).
- Status ladder: `uncomped → researching → captured → priced → approved`
  (+ `override_required`, `manual_only` for low-confidence/thin-market items).

**v1 guardrails (from the research, matches TGW's existing C9 operator-gate
philosophy):** no autonomous scraping — human must initiate every search and
confirm every capture. Auto-price only when match is exact/near-exact AND
comp count clears a threshold; everything else routes to manual pricing.
First-cut formula: `list_price = round_up(target_price * 1.12)`,
`offer_floor = max(cost_floor, target_price * 0.92)`, disable auto-price if
comp_count < 5 or confidence < 0.70 (starting point, tune per category).

**Relationship to the rest of PP-PRICING-001:** this is a *third*, distinct
pricing-signal source alongside Phase 1 (SerpApi, still blocked on #1110's
key) and Phase 2 (Bing Visual Search) — and per the eval, it may end up the
*most trustworthy* of the three since it's grounded in an operator-reviewed
first-party source rather than a model's own web search. Feeds
`recommend_price()` in `market_data.py` the same way the other phases do,
as a `MarketDataProvider` once built.

**Not started — this is a design capture, not a build commitment.** Needs
Dave's go/no-go and priority slotting before any web-UI/editor code is
written. Open questions: which existing web-UI framework/editor surface
does the 3-pane view attach to (extend PP-EDITOR-001's existing item-detail
view, or a new standalone route?); does the browser pane need to be a real
embedded iframe/webview, or is "open Product Research in a new tab +
paste-back the numbers" an acceptable v1 shortcut that avoids embedding a
browser at all (simpler, avoids the exact Linux-webview friction the
research flagged for Flutter — worth asking whether the web UI needs true
embedding either, or whether tab-switch + paste is good enough for v1).

---

### Phase -1 — Self-powered comp engine, own dataset (Dave request, 2026-07-04, todo #1134)

**"Let's use the pricing research web ui and our own dataset to build our
own self powered comp engine. Target our largest categories first."**

**The infrastructure already exists and is running — this is a data-density
problem, not a missing-feature problem.** `OwnSalesProvider`
(`src/tgw/ebay/market_data.py`) already reads per-category sold-price stats
from `velocity-stats.json`, produced by the live `tgw-worker@velocity_stats`
worker, and already plugs into `recommend_price()`'s comp blend alongside
`BrowseCompsProvider`. It has been running this whole time.

**Checked the real numbers (2026-07-04):**
- `velocity-stats.json` tracks 1,316 distinct category IDs, but only
  **~12 clear the `MIN_SAMPLES = 3` threshold** to count as usable comps at
  all. The single best-covered category has **18** sold items ever.
- Root cause, not a velocity-worker bug: **39,224 of 55,419 items (71% of
  the whole catalog) have no category (`attribute_set`) recorded at all.**
  Category-keyed comps are structurally starved because most of the
  catalog was never categorized in the first place — this is the same
  underlying gap PP-CATPICK-001 (smart category picker) already targets.
- **Real "largest categories" by current inventory** (the ones worth
  targeting first, since they have both volume AND, being accessory/media
  categories, high item-to-item similarity — the ideal case for a
  same-item comp engine): **Collectibles (2,432 items)**, **AC Adapter
  (2,059)**, **Arts and Crafts (1,261)**, **DVD (1,245)**, **Magazines
  (954)**, Toys and Games (763), Tools and Hardware (728), Computer (691).
  AC Adapter and DVD stand out — generic/repeat SKUs (the same charger or
  disc model gets bought and resold repeatedly) build strong same-item
  comp history *faster* than one-off collectibles ever will, even at lower
  per-category item counts.

**Proposed near-term plan (bootstraps the existing engine, doesn't replace
it):**
1. **Backfill category on the 71% uncategorized items** — this is the
   single highest-leverage fix; it's the actual bottleneck, not the comp
   math. Ties directly into PP-CATPICK-001; worth reprioritizing that
   project higher given this new finding.
2. **Feed the Phase 0 comping interface's captured snapshots into
   `velocity-stats.json`'s same schema** — every operator-reviewed
   Product Research capture is *also* a same-item/near-item comp,
   independent of whether TGW has sold that exact item before. This
   compounds faster than waiting for organic TGW sales alone, especially
   for AC Adapter/DVD-style repeat-SKU categories.
3. **Lower or replace the flat `MIN_SAMPLES = 3` gate with a confidence
   score** (matches the comping interface's `confidence: high/medium/low`
   language already proposed in Phase 0) so a 1-2-sample comp isn't binary
   discarded, just weighted down and flagged for operator review instead
   of auto-priced.
4. Once (1) lands, re-run `tgw-worker@velocity_stats` and re-check
   coverage — the 12-category number should jump substantially just from
   having a category to key on.

**Not started — needs Dave's priority call**, especially on (1) since it's
really "reprioritize PP-CATPICK-001," not a standalone build.

**UPDATE 2026-07-04 (todo #1135) — step 1 done, corrected + completed.**
The initial `attribute_set`-based analysis above was checking the wrong
field (Magento warehouse taxonomy, which the pricing engine never reads).
The real field is `ebay_category_id`/`draft_listing.category_id`
(`_category()` in `velocity.py`). Corrected numbers: **28,710/55,419
items (52%) already have a real category** — much better than first
thought. Of the 26,709 that don't, checked three structured sources
(historical-tgwcatalog.json, historical-master-catalog.json via sku_old,
and `searchcatalog.csv`'s `ebaycat` column — a genuinely distinct
eBay-only export, its `'uncategorized'` placeholder on 34,478/55,347 rows
excluded as noise) — **5,367 recoverable (20%), 21,342 genuinely
unrecoverable from flat exports.**

Per Dave's direction ("we have better tools and more data and we can
recompile a better dataset... build it like we are going to go back in
with a stronger dataset every so often") this was built as a **repeatable
recompile job**, not a one-shot fix: `scripts/recompile_category_backfill.py`
(dry-run default, `--apply` to write), sources as modular functions so a
future run with a new/better source just adds one more. Writes to
`ebay_category_id`/`ebay_category_name` (never `draft_listing.category_id`
— that field means "this item's actual current eBay draft," a stronger
signal that should only come from the real drafting pipeline). Additive
only via the new `items.set_fields(only_if_absent=True)` fence helper —
never overwrites an item that already has a category from anywhere.

**Live-verified and APPLIED 2026-07-04:** 5,367/5,367 items updated, 0
errors. Re-run confirmed idempotent (`already had a real category` jumped
28,710→34,077, exactly +5,367; second run reports 0 recoverable). 8 new
tests (4 for `items.set_fields`, 4 for the script's source loaders), full
suite 1810 passed.

The remaining 21,342-item gap is the real target for (2) above — the
comping interface — plus whatever a live eBay Taxonomy sweep or the
Amazon comp-data integration eventually contribute. Re-run this script
after any of those land; it'll pick up newly-recoverable items
automatically without reprocessing what's already fixed.

---

### Related — Amazon FBM (books/media) exploration

See new `pp/PP-AMAZON-001.md` — a second data source (Amazon's own
comps/pricing for books/media) and a second sales channel, researched
2026-07-04 per Dave's same request. Kept as a separate PP item since it's
a marketplace-expansion decision, not a pricing-signal addition, but the
two are related: Amazon FBM sales would also feed this same
own-dataset comp engine once live.

