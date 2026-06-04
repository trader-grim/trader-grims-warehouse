---
title: PP-LOOKUP-001 — Product Enrichment API Stack
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# PP-LOOKUP-001 — Product Enrichment APIs

## Architecture
- Dispatcher: `apis/lookup/lookup_product(item_json) -> LookupResult`
- Routes by field: `upc/ean` → barcode stack; `isbn` → books; category hint → specialty
- Results cached in item JSON: `product_lookup: {source, fetched_at, title, brand, description, mpn, ean, msrp, category, raw}`
- Re-fetch only if absent or `fetched_at` > 30 days
- All keys in `secrets_root/` — missing key = silent skip for that source
- Batch where available; confirm per-source batch limit at implementation time
- Common dataclass: `apis/lookup/base.py → LookupResult`

---

## Tier 1 — Free, Implement Now

### General Barcode (UPC / EAN / ISBN-13)

#### upcitemdb (primary)
- Coverage: 698M+ barcodes
- Free tier: 100 requests/day; batch endpoint (multiple UPCs/request) → ~2,500 UPCs/day effective
- Auth: API key optional (higher burst with key)
- Endpoint: `https://api.upcitemdb.com/prod/trial/lookup?upc=<barcode>`
- Returns: title, brand, description, category, weight, dimension, images, offers (MSRP)
- Key: `secrets_root/upcitemdb-credentials.json`
- Module: `apis/lookup/upcitemdb.py`

#### Go-UPC (secondary / coverage gap)
- Coverage: 1B+ items, different database — worth querying on upcitemdb miss
- Auth: API key bearer token
- Rate: 2 req/sec
- Endpoint: `https://go-upc.com/api/v1/code/<barcode>`
- Returns: name, description, brand, image URL, category
- Key: `secrets_root/go-upc-credentials.json`
- Module: `apis/lookup/go_upc.py`

#### Routing
- upcitemdb first
- On empty result → try Go-UPC
- Cache merged winner; do not re-fetch both on subsequent runs

### Books (ISBN)

#### Open Library
- Coverage: 36M+ books
- Auth: none
- Rate: unlimited (add User-Agent header for better limits)
- Endpoint: `https://openlibrary.org/api/books?bibkeys=ISBN:<isbn>&jscmd=data&format=json`
- Returns: title, authors, publishers, subjects, cover URL, publish date
- Module: `apis/lookup/open_library.py`
- Trigger: `isbn` field in item JSON, or AI category matches Books

### Music / Vinyl / CDs

#### Discogs
- Coverage: 14M+ releases
- Auth: registered API key (OAuth token or personal token, free)
- Rate: 60 req/min authenticated
- Endpoint: `https://api.discogs.com/database/search?barcode=<code>`
- Returns: title, artists, tracklist, label, year, genre, marketplace price stats
- Key: `secrets_root/discogs-credentials.json`
- Module: `apis/lookup/discogs.py`
- Trigger: barcode on known music release, or AI category matches Music/Vinyl/CD

### Video Games

#### IGDB (via Twitch)
- Coverage: games across all platforms
- Auth: Twitch developer account → client_id + client_secret → OAuth app token
- Rate: 4 req/sec; 500M points/month
- Endpoint: `https://api.igdb.com/v4/games` (POST with Apicalypse query)
- Returns: title, platforms, genres, cover art URL, release year, summary
- Key: `secrets_root/igdb-credentials.json`
- Module: `apis/lookup/igdb.py`
- Trigger: AI category matches Video Games / Gaming

### Trading Cards

#### JustTCG
- Coverage: MTG, Pokémon, Yu-Gi-Oh, others
- Auth: none required (free tier)
- Endpoint: `https://api.justtcg.com/` (check current docs at implementation)
- Returns: card name, set, rarity, market price
- Module: `apis/lookup/justtcg.py`
- Note: TCGPlayer API closed to new signups — do not use
- Trigger: AI category matches Trading Cards / CCG

### Food / Beverage / Household

#### Open Food Facts
- Coverage: 3M+ products, 150+ countries
- Auth: none
- Rate: unlimited
- Endpoint: `https://world.openfoodfacts.org/api/v2/product/<barcode>.json`
- Returns: product name, brand, ingredients, allergens, categories, image, nutrition
- Module: `apis/lookup/open_food_facts.py`
- Trigger: category matches Food/Beverage/Household, or barcode lookup returns food category hint

---

## Tier 2 — Paid / Decision Required

### Keepa — Amazon Price History
- What: ASIN lookup → full Amazon price history, sales rank, title, brand, specs
- Cost: €19/month minimum (token-based; tokens replenish per plan)
- Decision: worth it for significant Amazon-sourced SKU volume (electronics, toys, media)
- Key: `secrets_root/keepa-credentials.json`
- Module: `apis/lookup/keepa.py` — stub, not implemented until subscribed

### Barcode Lookup — Richer UPC Data
- What: 30+ fields including pricing, full descriptions, product images
- Cost: subscription, month-to-month (free trial available)
- Decision: evaluate only if upcitemdb + Go-UPC free tiers prove insufficient at scale
- Module: `apis/lookup/barcode_lookup.py` — stub, not implemented until subscribed

### eBay Catalog API — eBay's Own Product Records
- What: structured product data by EPID (eBay Product ID) — brand, MPN, descriptions, images
- Scope: `commerce.catalog.readonly`
- Decision: apply for scope; complements upcitemdb with eBay-specific product data
- Gain: EPID → pre-filled aspects without Browse API enrichment step
- Module: `apis/lookup/ebay_catalog.py`

---

## Avoid / Do Not Implement
- Amazon PAAPI — sunset April 30, 2026; gone
- GoodReads API — discontinued Dec 2020; use Open Library instead
- TCGPlayer API — closed to new signups; use JustTCG
- CamelCamelCamel — no public API, UI only

---

## Integration Point in Pipeline
- Called in `ai_identify` worker before vision model runs
- Product data (title, brand, description) prepended to AI prompt context
- Also called in `ebay_draft` for specifics pre-fill (brand, MPN, model)
- Also called in `ebay_price` for comp search query (PP-PRICE-003)
- CLI: `tgw lookup <SKU>` — manual enrichment trigger
