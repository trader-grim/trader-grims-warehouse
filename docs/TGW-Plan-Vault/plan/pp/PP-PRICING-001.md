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

