---
title: eBay API Landscape
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 2
updated: 2026-06-04
---

# eBay API Landscape

## Rate Limits

- **Daily quotas reset at 00:00 PST (midnight Pacific)** — verified by Dave 2026-07-01.
  When a quota is exhausted (Taxonomy 429s, EPS upload cap, etc.), that's when it clears.
  Host timezone is America/Los_Angeles, so local midnight ≈ reset time.

## Auth & Scopes

### ✅ Have
- sell.inventory — Inventory API (items, offers)
- sell.account — Account API (policies, locations)
- sell.marketing — Marketing API

### ⏳ Apply Next
- buy.marketplace_insights — sold price data
  - Contact eBay Developer Support directly (no self-service)
  - Frame as resale automation platform use case
- commerce.catalog.readonly — eBay Catalog API (EPID lookup by UPC/EAN)
  - Enables EPID association at staging → eBay auto-fills verified item specifics
  - Highest-leverage SEO action for branded items with barcodes (PP-SEO-001 Phase 3)
  - Apply alongside marketplace_insights request
- sell.analytics.readonly — Analytics API (per-listing traffic data)
  - Impressions, clicks, sold per listing → SEO feedback loop (PP-SEO-001 Phase 6)
  - Also enables stale listing detection (no views in 14 days → reprice or end)

### ❌ Blocked / Unavailable
- Finding API findCompletedItems — discontinued early 2025
- Terapeak — UI only; API removed when eBay acquired

---

## REST APIs

### Buy APIs

#### Browse API ✅ IN USE
- item_summary/search
  - Active listing prices (asking, not sold)
  - Used for pricing comps in ebay_price worker
  - Three-query fallback: full title → brand+short → category
- Scope: none (App ID only)
- Rate limit: 5,000 calls/day default
- Gap: asking prices only; p25 is a floor estimate, not market-clearing

#### Marketplace Insights ⏳ PENDING APPROVAL
- item_sales/search
  - Actual sold prices, sale dates, quantities
  - Filter by category, condition, date range
- Scope: buy.marketplace_insights (limited-release)
- Gain: replace Browse API asking prices → accurate p25/p75 pricing
- Unlocks: PP-REPRICER-001 (market-aware dynamic repricer)

#### Feed API
- Bulk item data snapshots (category-level)
- Scope: buy.item.feed
- Gain: offline category market data at scale; complements Browse API

#### Deal API
- eBay deals, flash sales, coupons
- Scope: buy.deal
- Low priority for TGW

#### Order API (Buy side)
- Buyer-facing order management
- Not relevant to seller operations

### Sell APIs

#### Inventory API ✅ IN USE
- PUT inventory_item — create/update inventory item
- POST offer — create offer
- PUT offer — update offer fields
- POST offer/publish — publish to live listing
- GET offers — paginated fetch, all offers
- Scope: sell.inventory
- Note: all PUT/POST require Content-Language: en-US header
- Note: condition granularity — many categories only accept conditionId 3000

#### Account API ✅ IN USE
- GET fulfillment_policy — fetch shipping policies (FC4 default)
- GET merchant_location — fetch warehouse location key
- Scope: sell.account
- Cached per process; policies rarely change

#### Fulfillment API ✅ PARTIAL
- GET orders — active order list (REST equivalent of GetOrders)
- POST orders/{id}/shipping_fulfillment — mark shipped
- Scope: sell.fulfillment
- Note: currently using Trading API GetOrders; migrate to REST when convenient

#### Analytics API
- GET traffic_report — impressions, clicks, sold per listing
- GET seller_standards_profile — defect rate, late shipment
- Scope: sell.analytics.readonly
- Gain
  - Per-listing traffic data → stale listing detection (no views in 14 days → reprice or end)
  - Sell-through rate per listing supplements PP-PRICE-004 velocity stats

#### Marketing API
- POST promotions — sale events, order discounts, coupons
- POST ad_campaign — Promoted Listings Standard
- GET ad_report — campaign performance
- Scope: sell.marketing
- Gain
  - Auto-enroll slow-movers in Promoted Listings (cost only on sale)
  - Feeds PP-REPRICER-001: advertise instead of markdown as alternative strategy

#### Recommendation API
- GET listing_recommendations — eBay's own quality flags per listing
- Scope: sell.inventory (reuse — no new scope needed)
- Gain
  - eBay's quality signals as a second opinion alongside PP-QUALITY-001 score
  - Catches issues our scorer misses (image quality, title keywords eBay dislikes)

#### Negotiation API
- POST send_offer_to_interested_buyers — best-offer to watchers/viewers
- Scope: sell.negotiation
- Gain
  - Automated best-offer to watchers on listings stalled past retail stage
  - No markdown needed; buyer feels they got a deal

#### Finances API
- GET transactions — all financial events (sales, refunds, fees, payouts)
- GET payouts — actual payout history
- Scope: sell.finances
- Gain
  - Reconcile GetOrders sold records against actual payouts
  - Fee visibility: FVF + promoted listings ad spend per item
  - True net margin per SKU

#### Compliance API
- GET listing_violations — policy violations by listing
- Scope: sell.inventory (reuse)
- Gain: proactive violation detection before eBay suppresses listings

#### Metadata API ✅ IN USE
- GET sales_tax_table
- GET listing_structure_policies
- GET item_condition_policies — 26 unique sets, 15K categories cached
- Scope: sell.inventory (reuse)
- Cache: ebay-condition-policies.json, refreshed every 7 days

#### Feed API (Sell)
- Bulk listing upload / LMS feed format
- Scope: sell.item.feed
- Gain: mass listing operations without per-item API calls

### Commerce APIs

#### Taxonomy API ✅ IN USE
- GET getCategoryTree — full category hierarchy
- GET getCategorySuggestions — AI title → category candidates
- GET getItemAspectsForCategory — aspects for a categoryId
- Scope: none (App ID)
- Used in: ebay_draft (category selection + specifics)

#### Catalog API
- GET product — structured product data by EPID (eBay Product ID)
- GET product_search — search catalog by title/UPC/EAN/ISBN
- Scope: commerce.catalog.readonly
- Gain
  - eBay's own structured product records (brand, MPN, description, images)
  - Direct competitor/complement to PP-LOOKUP-001 (upcitemdb etc.)
  - EPID → pre-filled aspects without Browse API enrichment step

#### Identity API
- GET user — account info, seller tier
- Scope: commerce.identity.readonly
- Low priority; useful for health checks

#### Notification API
- POST subscription — register webhook for real-time events
- REST alternative to Trading API SetNotificationPreferences
- Scope: commerce.notification.subscription
- Gain: PP-SOLD-001 Tier 4 real-time sold notifications

#### Translation API
- POST translate — title/description translation
- Scope: commerce.translation
- Gain: international listing expansion (future)

---

## Trading API (SOAP/XML)

### ✅ IN USE
- UploadSiteHostedPictures — photo upload → EPS permanent URLs
  - Used by ebay_upload worker; EPS URLs stored in ebay_photos
- GetOrders — sold order history in 90-day windows
  - Used by ebay_legacy_sync _sync_sold(); 365-day initial lookback
- GetMyeBaySelling — active listing sync
  - Used by ebay_legacy_sync; writes ebay_listing block back to item JSON
- SetNotificationPreferences — webhook registration
  - Used by tgw setup-ebay-hooks; infra deployment pending

### Available, Not Yet Used
- GetStore — store category list → PP-STORE-001
- ReviseFixedPriceItem — in-place live listing edit → PP-REVISION-001
- EndItem — delist (used by ebay_sku_migrate for delist step)
- AddFixedPriceItem — relist after delist (ebay_sku_migrate relist step)
- GetItem — single listing full detail (useful for sync verification)
- GetSellerTransactions — transaction-level sold data (alternative to GetOrders)

### Deprecated / Avoid
- findCompletedItems (Finding API) — discontinued early 2025
- AddItem, non-fixed-price variants — irrelevant to inventory model

---

## TGW Pipeline × API Map

### Intake → Identify
- Taxonomy API — getCategorySuggestions from AI title
- Metadata API — condition policies cached
- PP-LOOKUP-001 — upcitemdb / Go-UPC / Open Library / Discogs / IGDB / JustTCG / Open Food Facts
- PP-QUALITY-001 — draft quality score (post-draft)

### Draft → Price
- Taxonomy API — getItemAspectsForCategory (specifics)
- Browse API — comp search for pricing
- PP-PRICE-003 — product-lookup-informed comp query + condition filtering

### Stage → Publish
- Inventory API — inventory item upsert + offer create/publish
- Account API — fulfillment policy + merchant location
- Trading API UploadSiteHostedPictures — EPS photo hosting

### Post-Publish
- GetMyeBaySelling — active listing sync (ebay_legacy_sync, 6h)
- GetOrders — sold order pull (ebay_legacy_sync, daily)
- ebay_price_reducer — scheduled markdown (6h self-scheduling)
- PP-SOLD-001 Tier 4 — real-time webhook (infra pending)

### Planned Next
- Analytics API — traffic per listing → stale detection
- Recommendation API — eBay quality signals → augment PP-QUALITY-001
- Catalog API — EPID structured data → augment PP-LOOKUP-001
- Negotiation API — auto best-offer to watchers on stalled listings
- Marketing API — Promoted Listings auto-enrollment for slow movers
- Finances API — payout reconciliation + true net margin per SKU
- Marketplace Insights — sold prices → PP-REPRICER-001

---

## Key Constraints & Notes

### Condition quirks
- conditionId 3000 has 4 different buyer labels across categories
- best_condition() resolves same-or-worse fallback; never upgrades
- Cache: catalog_root/ebay-condition-policies.json (7-day TTL)

### Shipping policy
- Default: FC4 (199931446015) for most categories
- 7 category overrides in tgw-api-config.json
- 10 legacy items migrated with FRE profile — manual Seller Hub fix pending

### SKU migration
- ~8,370 Class A live listings remaining on 20-char SKUs
- ebay_sku_migrate worker: delist → rename → relist, batch of N/day
- ebay_sku_migrate uses EndItem + AddFixedPriceItem Trading API calls

### Error reference
- 25021 — invalid condition for category → best_condition() fallback handles
- 25002 — Item.Country at publish → shipToLocations.regionIncluded fix applied
- 25709 — missing Content-Language header → fixed globally
