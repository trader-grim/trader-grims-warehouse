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
- Key: `secrets_root/serpapi-credentials.json` → `{"api_key": "..."}`
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
- Key: `secrets_root/bing-search-credentials.json` → `{"subscription_key": "..."}`
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
- [ ] Write keys to `secrets_root/` (chmod 600)
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

