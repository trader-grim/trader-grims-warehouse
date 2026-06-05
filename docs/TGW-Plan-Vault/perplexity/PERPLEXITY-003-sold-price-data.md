# PERPLEXITY-003 — eBay Sold Price Data Alternatives

**How to use:** Paste the prompt below into Perplexity. Save the result as a `.md` file in
`docs/TGW-Plan-Vault/inbox/` and PM-intake will file it into the plan.

---

## Context (not part of prompt — read before pasting)

eBay's `buy.marketplace_insights` scope (sold price data) is limited-release and we haven't
obtained it yet. eBay's Finding API `findCompletedItems` was discontinued early 2025.
Terapeak (eBay's own tool) has no API. We need alternative sources for sold/completed item
pricing data to power automated pricing decisions.

---

## Prompt

I'm building an automated pricing engine for an eBay resale business. I need programmatic
access to historical eBay sold prices to price new listings accurately. eBay's own API access
for sold data (`buy.marketplace_insights`) is limited-release and hard to obtain. The Finding
API was discontinued early 2025. Terapeak is UI-only with no API.

**Research the following alternatives and provide cited answers:**

### 130Point.com
- What data does 130Point provide? Is it eBay-specific sold price data?
- Is there an API? What does access cost? Any rate limits?
- How current is the data — is it near-real-time or lagged?
- Is it suitable for automated pricing (programmatic queries by keyword/category)?

### ZIK Analytics
- What eBay data does ZIK Analytics provide?
- Is there an API for programmatic access, or is it UI/export only?
- What is the pricing model? Is it accessible to small individual sellers?

### PriceCharting.com
- Does PriceCharting have a public API?
- What categories does it cover? (Video games, trading cards, collectibles?)
- Is it suitable for eBay sold-price lookups or only its own marketplace data?

### Other sources
- Are there other services (not eBay itself, not scraping, legal) that provide completed/
  sold eBay listing data via API in 2025?
- Any services that combine eBay sold data with Amazon price history?

### eBay Marketplace Insights — current status
- As of 2025–2026, is `buy.marketplace_insights` still limited-release?
- Has eBay published any roadmap for broader access?
- Any reports of small sellers or independent developers getting approved?

**Format:** One section per service/topic. Include pricing, API availability, data freshness,
and suitability for automated use. Include dates on all sources.
