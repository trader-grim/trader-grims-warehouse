# PERPLEXITY-001 — eBay API Scope Expansion

**How to use:** Paste the prompt below into Perplexity. Save the result as a `.md` file in
`docs/TGW-Plan-Vault/inbox/` and PM-intake will file it into the plan.

---

## Prompt

I'm building a resale inventory automation platform on eBay's API. I need to expand my
access to three additional scopes and need current, cited information on the process for each.

**My current context:** I have approved scopes `sell.inventory`, `sell.account`,
`sell.marketing`. I am a small seller running a single-account resale operation. My app is
not publicly listed — it's a private tool.

**Research the following three scopes and answer with citations:**

### 1. `buy.marketplace_insights` (sold price data)
- What is the current approval status as of 2025–2026? Is it still "limited release / select
  partners only"?
- What is the official application path? Is there a self-service option or does it require
  contacting eBay Developer Support directly?
- Have independent developers (not large enterprises) reported successfully obtaining this
  scope? Any community reports from eBay developer forums or Stack Overflow?
- Is there an alternative endpoint within eBay's current API stack that provides sold/
  completed listing prices without this scope?

### 2. `commerce.catalog.readonly` (eBay Catalog / EPID lookup)
- What does this scope grant access to? Can it be used to look up EPIDs by UPC/EAN barcode?
- Is the approval process self-service (standard OAuth app credential request) or does it
  require a special application?
- Are there any usage restrictions for small private automation tools?

### 3. `sell.analytics.readonly` (listing performance data)
- What impression/traffic data does this scope expose at the per-listing level?
- Is this scope self-service for any seller with an eBay developer account?
- What is the Analytics API endpoint and what fields are available per listing?

**Format your answer** as one section per scope, with a "Current status" summary, "How to
apply" steps, and any relevant caveats. Include dates on all sources.
