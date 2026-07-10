<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# trying to develop a strategy to automatically price used goods for sale using ai. Looking for some resources for sold price comps. Any ideas to accomplish the goal of setting a reasonable price automatically?

Yes — the practical way to automate pricing for used goods is to combine **sold comps + item matching + guardrails**, not let a model invent prices from scratch. For your workflow, the strongest foundation is eBay sold data from Product Research/Terapeak for ground truth, with an optional API/scraper layer for machine ingestion and a rules engine that decides the final list price.[^1_1][^1_2][^1_3][^1_4]

## Best comp sources

eBay Product Research (formerly Terapeak) is still the cleanest first-party source for sold comps because it exposes sold price, average sold price, sold price range, shipping info, and trend data, and eBay says sellers can access it in Seller Hub and the mobile app.[^1_2][^1_3]
eBay’s Marketplace Insights API is the official API-shaped option for historical sold-item marketplace data, but access has been treated as a more restricted path than normal seller APIs, so it is risky to build around unless you already have access.[^1_5][^1_4]

If you need machine-readable comps now, the realistic stack is:

- First-party UI: Seller Hub Product Research/Terapeak for manual validation and spot checks.[^1_3][^1_2]
- Your own historical sold listings: use your own transaction and ended-listing data to calibrate category-specific pricing behavior.[^1_6][^1_7]
- Third-party ingestion or controlled scraping: tools in the market now advertise sold-listing extraction with medians, shipping, sold date, and variant grouping, but these should be treated as replaceable inputs rather than core infrastructure.[^1_8][^1_9]


## Pricing architecture

For used goods, I would structure pricing as a pipeline: 1) classify and normalize the item, 2) retrieve likely sold comps, 3) filter bad comps, 4) compute a target price, 5) clamp it through business rules, 6) monitor sell-through and revise. That fits your operator-reviewed AI-first listing flow and your move toward rule-based automation over blind model output.[^1_1][^1_10]

A strong auto-pricer usually has these layers:

- Identity layer: brand, model, part number, UPC/EAN, condition, completeness, lot size, accessories.
- Comp layer: recent sold listings, active competitor listings, your own sell-through history.
- Adjustment layer: condition downgrade, missing parts, shipping burden, category liquidity, seasonality.
- Guardrail layer: minimum margin, minimum dollar profit, maximum stale-inventory markup, strike-through/promo strategy where relevant.[^1_1]


## Practical model

The model should not answer “what is the price?” directly. It should answer narrower questions like “what item is this,” “which sold listings are true matches,” and “what condition adjustments apply,” because those are the hard unstructured parts.[^1_11]

Then compute final price with something like:

- Base comp = median of filtered sold comps.
- Condition factor = multiplier or delta, for example used-fair vs used-good.
- Completeness factor = reduce for missing manual, remote, box, cables, etc.
- Market factor = nudge upward if active supply is thin and sell-through is strong; downward if there are many similar active listings.
- Guardrails = never go below cost floor or below your minimum acceptable profit.[^1_12][^1_1][^1_3]

A simple first formula could be:

$$
\text{target\_price} =
\text{median\_sold\_comps}
+ \text{shipping\_adjustment}
+ \text{condition\_adjustment}
+ \text{completeness\_adjustment}
+ \text{scarcity\_adjustment}
$$

Then list price could be set slightly above target if you expect offers, or closer to target if the category is fast-moving. eBay Product Research explicitly includes accepted Best Offer prices in sold data, which makes that median more useful than ordinary visible sold prices alone.[^1_3]

## Implementation approach

Given your stack, I’d build this as a pricing service with a confidence score and full provenance trail per item. Store the normalized query, raw comps, excluded comps, chosen pricing method, and final recommendation in the item JSON so the reviewer can see why the number was suggested.

A practical rollout would be:

1. Start with 20–30 categories where title normalization is easiest, such as media, common electronics models, branded housewares, and toys with clear identifiers.
2. Build a comp retriever that tries exact identifiers first, then normalized title tokens, then category-specific fallback search.
3. Compute median, trimmed mean, and comp count; reject low-confidence outputs.
4. Route low-confidence items to a review queue instead of auto-pricing.
5. Back-test against your own sold history and tune by category.[^1_1][^1_6]

Confidence should drive automation:

- High confidence: exact model match, enough recent sold comps, consistent price band.
- Medium confidence: close keyword matches, some variance, minor condition ambiguity.
- Low confidence: vague collectible, bundle, rare item, or noisy title space, so require human pricing.[^1_1][^1_3]


## Good resources

Here are the most useful resources to anchor the system:

- eBay Product Research / Terapeak in Seller Hub: best first-party source for sold comps and trends.[^1_2][^1_3]
- eBay mobile Product Research: useful if you want staff to validate comps quickly from the floor.[^1_3]
- eBay official material describing Product Research metrics and filters, including sold and active listing analysis.[^1_12]
- Marketplace Insights API docs/listing: useful to monitor if official sold-data API access becomes viable for your account.[^1_4]
- Your own sold/ended listing archive: essential for category tuning and for learning how your own pricing behaves versus marketplace averages.[^1_7][^1_6]

The highest-leverage next step is to build a **hybrid comp engine**: exact-match comps first, fuzzy comps second, and category rules last. Would you like a concrete schema and scoring design for a `pricing_worker` that fits your PostgreSQL queue/state-machine setup?
<span style="display:none">[^1_13][^1_14][^1_15][^1_16][^1_17][^1_18][^1_19][^1_20][^1_21]</span>

<div align="center">⁂</div>

[^1_1]: https://www.perplexity.ai/search/940b7670-deee-450e-ae1b-def10e4f1243

[^1_2]: https://export.ebay.com/en/resources/important-updates/ebay-news-archive/terapeak/

[^1_3]: https://innovation.ebayinc.com/stories/ebays-mobile-app-offers-a-goldmine-of-insights-for-sellers/

[^1_4]: https://apis.io/apis/ebay/marketplace-insights-api/

[^1_5]: https://www.perplexity.ai/search/a16b81c4-4c06-4dea-ba20-4cfac560a090

[^1_6]: https://www.perplexity.ai/search/5550bde8-cb93-49c6-9ae1-891626805556

[^1_7]: https://www.perplexity.ai/search/cd5a363e-70db-4114-b401-7d1220102669

[^1_8]: https://apify.com/midwest_united/ebay-sold-comps

[^1_9]: https://apify.com/skootle/ebay-sold-comps/api/mcp

[^1_10]: https://www.perplexity.ai/search/b36503e9-53eb-4d96-b88e-38d3c012660b

[^1_11]: https://www.perplexity.ai/search/fe9f1534-3b15-42eb-a64c-d5ea4f908638

[^1_12]: https://www.youtube.com/watch?v=C0AHdxwv0cw

[^1_13]: https://www.youtube.com/watch?v=BtufGwRVw1s

[^1_14]: https://www.youtube.com/watch?v=kY_amjUmXwg

[^1_15]: https://closo.co/blogs/community/the-ebay-crystal-ball-how-to-master-terapeak-ebay-research-in-2026

[^1_16]: https://www.youtube.com/watch?v=157m0psx7Rg

[^1_17]: https://www.frooition.com/terapeak/

[^1_18]: https://www.youtube.com/watch?v=z394H-Aq_bM

[^1_19]: https://www.youtube.com/watch?v=6jtgqRPavwQ

[^1_20]: https://export.ebay.com/ua/marketing/ebay-services-and-tools-help-seller/terapeak/

[^1_21]: https://apify.com/yumitori/soldradar


---

# Our problem is not finding any good source of comps. google model results grounded in search are worse than live listings. The scraping tools have their own issues with captchas and such and I cannot afford to get banned for suspicious activity. Maybe a hybrid approach using terrapeak and ebay solds with operator supervision then scraping the result to speed the process. That would be a valuable tool even ig I can get marketing insights approval. Still waiting 3 weeks for ebay business division approval. The developer division says ok.

Yes — a supervised hybrid is probably the safest and most realistic path right now. eBay’s own materials still make Product Research/Terapeak the most trustworthy sold-price source available to normal sellers, while Marketplace Insights remains restricted and “not open to new users,” so it makes sense to design as if approval may stay slow or uncertain.[^2_1][^2_2][^2_3][^2_4]

## Why the hybrid makes sense

Regular eBay sold search is weaker than Product Research because Product Research shows paid transactions, actual sold price including accepted offers, and up to three years of data, while normal sold search is much thinner and less reliable for true realized price.[^2_2][^2_5][^2_6]
That means operator-guided Product Research plus lightweight capture of the reviewed result is materially better than relying on Google-grounded answers or aggressive scraping of eBay pages.[^2_6][^2_2]

## Safe operating model

I would avoid any scraping pattern that looks like broad marketplace harvesting, because your risk is not just captchas but account or network trust issues. A better design is “human opens comp session, system assists, operator confirms, system stores structured result,” which keeps the human as the primary actor and turns automation into note-taking plus normalization rather than autonomous extraction.[^2_7][^2_8]

A practical flow:

- Operator searches Product Research/Terapeak for the item and sets the right filters.[^2_2]
- Your tool captures the chosen comp set metadata after review: search phrase, timeframe, condition bucket, comp count, median, low/high, shipping pattern, and a confidence note.[^2_2]
- Optionally add current active listing counts from normal eBay search as a separate competitive signal, not as sold-price truth.[^2_5][^2_6]
- Store the result as a reusable comp record tied to your normalized item identity.


## What to automate

The highest-value automation is not raw comp acquisition; it is **post-comp structuring**. Once an operator identifies the right comp view, your system can save and reuse:

- Canonical item key: brand, model, MPN, UPC, platform, edition, bundle/completeness markers.
- Comp snapshot: median sold, average sold, sold range, date window, marketplace, comp count.
- Pricing decision: suggested list price, offer policy, floor price, rationale, confidence.
- Drift logic: when to refresh, for example after 30 days or when no sale occurs.[^2_9][^2_2]

That gives you a comp library that gets stronger over time, especially for repeatable SKUs or families of similar items. It also fits your AI-prefill workflow, where AI can propose likely matches but the operator remains arbiter on noisy used-goods listings.[^2_10]

## Tool design

The tool I would build first is a “comp capture console,” not a scraper. It should let the operator paste or enter the final Product Research numbers and attach normalized descriptors, then let your pricing worker apply rules to produce a listing recommendation.[^2_9]

Useful fields:

- Query used.
- Category and condition.
- Exact match vs fuzzy match.
- Number of comps reviewed.
- Median and trimmed mean.
- Shipping-normalized estimate.
- Completeness penalties, for example missing remote, box, cables, inserts.
- Confidence level and reviewer initials.[^2_9]

Then your pricing engine can do:

- High confidence exact-match items: auto-suggest price.
- Medium confidence items: suggest price with operator review.
- Thin-market or collectible items: comp-assist only, no autoprice.[^2_9]


## Approval reality

Your current situation lines up with the broader pattern: developer-side signals may look encouraging, but Marketplace Insights access is still documented as restricted and unavailable to new users, so business-side approval delays are not surprising.[^2_3][^2_4][^2_1]
I would treat any future approval as an upgrade path, not a dependency, and design your comp store so Marketplace Insights data can later populate the same schema you use for operator-captured Terapeak results.[^2_11][^2_7][^2_3]

The smartest near-term move is to build the supervised capture layer first, because it gives you immediate value, low platform risk, and a migration path if business approval eventually lands. Would you like a concrete schema for a `comp_snapshot` table plus a `pricing_recommendation` worker that fits your existing PG/queue architecture?
<span style="display:none">[^2_12][^2_13][^2_14][^2_15][^2_16][^2_17][^2_18][^2_19][^2_20]</span>

<div align="center">⁂</div>

[^2_1]: https://www.perplexity.ai/search/dee916a0-eb69-49a1-ad8a-ba5031112549

[^2_2]: https://innovation.ebayinc.com/stories/ebays-mobile-app-offers-a-goldmine-of-insights-for-sellers/

[^2_3]: https://developer.ebay.com/api-docs/buy/static/ref-marketplace-supported.html

[^2_4]: https://www.edp.ebay.com/develop/buying-apps/research-apis

[^2_5]: https://www.flippertools.com/tools/ebay-best-offer-actual-price

[^2_6]: https://community.ebay.com/t5/Seller-Tools/Sold-Items-Missing-from-Seller-Hub-Research/td-p/35053921/

[^2_7]: https://www.perplexity.ai/search/a16b81c4-4c06-4dea-ba20-4cfac560a090

[^2_8]: https://www.perplexity.ai/search/2a092f02-4d95-4fd1-911e-3f2d85beb2e5

[^2_9]: https://www.perplexity.ai/search/940b7670-deee-450e-ae1b-def10e4f1243

[^2_10]: https://www.perplexity.ai/search/b36503e9-53eb-4d96-b88e-38d3c012660b

[^2_11]: https://apis.io/apis/ebay/marketplace-insights-api/

[^2_12]: https://www.youtube.com/watch?v=_yPVJ-dVMYk

[^2_13]: https://closo.co/blogs/community/the-ebay-crystal-ball-how-to-master-terapeak-ebay-research-in-2026

[^2_14]: https://www.youtube.com/watch?v=csio9Z6Ti1A

[^2_15]: https://www.youtube.com/watch?v=I9dMB8un800

[^2_16]: https://www.reddit.com/r/Ebay/comments/1n2s59d/how_to_make_research/

[^2_17]: https://www.ecommercebytes.com/2018/06/17/ebay-gives-competitive-insight-tool-to-select-partners/

[^2_18]: https://www.reddit.com/r/Ebay/comments/uaxcul/has_anyone_gained_access_to_ebays_marketplace/

[^2_19]: https://developer.ebay.com/develop/get-started/get-started-on-a-buying-application

[^2_20]: https://www.zdnet.com/article/ebay-ramps-up-developer-strategy-with-series-of-api-updates/


---

# yes. a comping interface. Browser in a pane of the data editor.

Yes — that is the right UX. A comping interface embedded as a browser pane inside the data editor fits your operator-reviewed workflow much better than a separate research tool, because the operator can inspect the item record, run Product Research, and save a structured comp snapshot without context switching.[^3_1]

## Layout

I would make the editor a 3-pane layout:

- Left: item identity and draft data, including title draft, category, condition, brand, model, MPN/UPC, completeness flags, and photos.
- Center: embedded browser pane for Seller Hub Product Research or normal sold search, with a quick-launch query bar.[^3_1][^3_2]
- Right: comp capture and pricing decision pane, where the operator saves structured comp results into the item record.

This works because Product Research on Seller Hub is already the place where sellers can search, sort, filter, and inspect sold data, including actual sold prices and up to three years of history.[^3_1]
Your pane should treat that browser as the research surface and your own UI as the structured data layer.

## Browser pane behavior

The browser pane should open with prebuilt search actions based on the current item record. Product Research supports keyword-driven search and filtering, so your editor can generate the best starting query from brand, model, platform, part number, and condition cues.[^3_2][^3_1]

Useful controls beside the pane:

- “Open Product Research.”
- “Open sold search.”
- “Search exact title.”
- “Search brand + model.”
- “Search MPN/UPC.”
- “Search broad fallback.”[^3_2][^3_1]

I would also add a one-click “copy query” and “paste selected title into research” flow, since eBay’s own mobile and desktop research flow is built around pasting a title or product phrase and refining from there.[^3_3][^3_1]

## Capture pane

The right pane should not try to scrape the whole page first. It should let the operator record the comp result in a fast structured form, then optionally add page-assisted extraction later.[^3_4]

Core fields:

- Query used.
- Source type: Product Research, sold search, active listings.
- Time window.
- Condition bucket.
- Comp count.
- Median sold.
- Average sold.
- Low/high range.
- Shipping note.
- Exact match, close match, or broad market reference.
- Confidence score.
- Reviewer note.[^3_5][^3_6][^3_1]

Then derive:

- Target price.
- Suggested list price.
- Offer floor.
- Auto-accept threshold.
- Recheck date.[^3_6]


## Suggested workflow

The operator flow should be very short:

1. Open item in editor; AI-prefilled title, category, and identifiers are already visible.
2. Launch Product Research from inside the browser pane with a generated query.[^3_1][^3_2]
3. Review sold comps and refine query or filters manually.[^3_1]
4. Hit “Capture comp,” which stores the structured snapshot and proposed pricing decision on the item JSON or linked history record.
5. Accept, adjust, or reject the recommended price before listing.

That keeps the human in control while making comping much faster than freehand manual research.[^3_6]

## Data model

I would separate the record into `comp_snapshot` and `pricing_recommendation`. That matches your pattern of storing current state in the main JSON and keeping history separately.

Example shape:

- `comp_snapshot`: source, query, filters, comp stats, notes, timestamp, reviewer.
- `pricing_recommendation`: chosen strategy, target price, list price, floor price, confidence, provenance.
- `pricing_history`: previous snapshots and prior accepted recommendations.

For the editor itself, I’d also track:

- `browser_context.last_url`
- `browser_context.last_query`
- `browser_context.last_source`
- `comping.status` = none, in_progress, captured, approved


## Automation later

Once the pane workflow is working, you can add low-risk automation around it:

- Pre-fill the search query from normalized item identity.
- OCR or DOM-assist for fields the operator explicitly confirms before save.
- Reuse past comp snapshots for similar items in the same family.
- Queue low-confidence items for mandatory manual comp review.[^3_6]

I would not start with full autonomous scraping of the browser pane. Start with an embedded browser plus structured capture, because that gives you immediate speed gains without depending on brittle extraction or risky traffic patterns.[^3_4][^3_1]

A solid first implementation would be: embedded browser pane, quick-launch queries, one-button comp snapshot form, and a pricing recommendation card beneath it. Would you like me to sketch the actual pane layout and JSON schema next?
<span style="display:none">[^3_10][^3_11][^3_12][^3_13][^3_14][^3_15][^3_16][^3_17][^3_7][^3_8][^3_9]</span>

<div align="center">⁂</div>

[^3_1]: https://innovation.ebayinc.com/stories/ebays-mobile-app-offers-a-goldmine-of-insights-for-sellers/

[^3_2]: https://www.youtube.com/watch?v=I9dMB8un800

[^3_3]: https://www.youtube.com/watch?v=HdxyB446t1Y

[^3_4]: https://www.perplexity.ai/search/a16b81c4-4c06-4dea-ba20-4cfac560a090

[^3_5]: https://community.ebay.com/t5/Selling/Product-research-is-every-result-and-actual-sold-item/td-p/34619688

[^3_6]: https://www.perplexity.ai/search/940b7670-deee-450e-ae1b-def10e4f1243

[^3_7]: https://www.youtube.com/watch?v=_yPVJ-dVMYk

[^3_8]: https://www.instagram.com/ebayforsellers/reel/C6rX6-OpwQB/

[^3_9]: https://www.youtube.com/watch?v=aKBZHL41FJo

[^3_10]: https://www.youtube.com/watch?v=csio9Z6Ti1A

[^3_11]: https://www.flippertools.com/tools/ebay-best-offer-actual-price

[^3_12]: https://www.hustleandslow.com/best-offer-sold-price-ebay/

[^3_13]: https://innovation.ebayinc.com/stories/new-improved-terapeak-research-2-0-in-ebay-seller-hub/

[^3_14]: https://www.youtube.com/watch?v=1qQvCZ7VPWY

[^3_15]: https://www.reddit.com/r/eBaySellerAdvice/comments/1ow21xu/ebay_product_research/

[^3_16]: https://closo.co/blogs/blog/how-to-see-sold-items-on-ebay-unlocking-the-real-price-in-2025

[^3_17]: https://www.underpriced.app/blog/how-to-use-ebay-sold-listings-price-research-guide


---

# yes

Yes — here’s a concrete pane layout and JSON shape that fits the editor you described and your current item/history split. The key idea is: the browser pane stays human-driven, while your editor captures normalized comp data and pricing decisions as structured records tied to the item.

## Pane layout

Use a 3-column workspace with a thin action bar across the top. That matches your operator-review flow, where AI prefills the item, the operator researches comps, and then confirms pricing before listing.

### Suggested screen regions

| Region | Width | Purpose |
| :-- | --: | :-- |
| Item pane | 28% | Canonical item data, AI draft, identifiers, condition, completeness, category. |
| Browser pane | 44% | Embedded eBay Seller Hub Product Research or sold search, operator-driven. [^4_1][^4_2] |
| Comp pane | 28% | Structured comp capture, pricing engine output, approval controls. |

Top action bar:

- Back / next item.
- Comp status badge.
- Open Product Research.
- Open sold listings.
- Generate query.
- Capture snapshot.
- Approve price.
- Send to review / return to queue.


## UI sketch

A rough layout:

```text
┌───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Item XYZ-123 | status: draft | comp: in_progress | [Product Research] [Sold] [Capture]     │
├───────────────────────┬──────────────────────────────────────────────┬────────────────────────┤
│ ITEM                  │ BROWSER                                      │ COMP / PRICE           │
│                       │                                              │                        │
│ Photos                │ Query bar: [Sony Walkman WM-FX195 ____ ]     │ Comp source            │
│ Title draft           │ [Exact] [Broad] [MPN] [Category] [Run]       │ Product Research       │
│ Category guess        │                                              │                        │
│ Brand / model / MPN   │ ┌──────────────────────────────────────────┐ │ Query used             │
│ Condition / defects   │ │ Seller Hub / Product Research page       │ │ sony walkman...       │
│ Completeness toggles  │ │                                          │ │                        │
│ Shipping weight       │ │ operator browses / filters manually      │ │ Time window           │
│ Cost / min margin     │ │                                          │ │ 90 days               │
│                       │ │                                          │ │                        │
│ Suggested queries     │ └──────────────────────────────────────────┘ │ Comp stats             │
│ - exact title         │                                              │ median / avg / range   │
│ - brand + model       │                                              │ sold count             │
│ - MPN only            │                                              │                        │
│ - fallback query      │                                              │ Adjustments            │
│                       │                                              │ condition / missing    │
│                       │                                              │ shipping / scarcity    │
│                       │                                              │                        │
│                       │                                              │ Final recommendation   │
│                       │                                              │ list / offer / floor   │
│                       │                                              │ [Approve] [Override]   │
└───────────────────────┴──────────────────────────────────────────────┴────────────────────────┘
```

Product Research supports search, sort, filter, trend data, sold prices, average sold prices, sold-price range, shipping metrics, and actual sold prices including Best Offer, so it is a strong fit for the center pane.[^4_2][^4_1][^4_3]

## Item pane fields

The left pane should hold only the pricing-relevant identity and state, not every possible item field. That keeps comping fast and consistent with your category-agnostic main JSON plus marketplace-specific nested data.

Recommended visible fields:

- `item.sku`
- `item.title_draft`
- `classification.category_guess`
- `identity.brand`
- `identity.model`
- `identity.mpn`
- `identity.upc`
- `condition.overall`
- `condition.notes`
- `completeness.flags`
- `shipping.weight_estimate`
- `acquisition.cost`
- `pricing.guardrails.min_margin_pct`
- `pricing.guardrails.min_profit_abs`

Suggested quick-query buttons:

- Exact title
- Brand + model
- Brand + model + MPN
- UPC only
- Broad fallback
- Remove stopwords
- Add category keyword[^4_4]


## Comp pane fields

The right pane should separate raw observed market data from your pricing decision. That makes the tool auditable and lets you later swap in Marketplace Insights or another source without changing the pricing layer.[^4_5]

### `comp_snapshot`

```json
{
  "id": "comp_2026-07-04T08-29-12Z_01",
  "source": {
    "kind": "ebay_product_research",
    "marketplace": "EBAY_US",
    "url": null,
    "captured_by": "operator_username",
    "captured_at": "2026-07-04T08:29:12Z"
  },
  "query": {
    "text": "sony walkman wm-fx195",
    "mode": "brand_model",
    "category_hint": "Portable Cassette Players",
    "time_window_days": 90,
    "condition_filter": "Used",
    "notes": "Filtered out bundles and broken units"
  },
  "match_assessment": {
    "match_type": "exact",
    "confidence": 0.88,
    "variant_notes": "same model, mixed colorways"
  },
  "market_stats": {
    "comp_count_seen": 14,
    "comp_count_used": 9,
    "sold_median": 39.99,
    "sold_mean_trimmed": 41.27,
    "sold_low": 24.99,
    "sold_high": 59.95,
    "avg_shipping": 8.12,
    "free_shipping_pct": 0.22,
    "active_count_observed": 17
  },
  "operator_notes": "Excluded for-parts units and lots of 2",
  "exclusions": [
    "for parts",
    "bundle/lot",
    "new old stock"
  ]
}
```

This maps well to what Product Research exposes: sold price, average sold price, sold-price range, shipping metrics, sorting/filtering, and recent history windows.[^4_1][^4_2]

### `pricing_recommendation`

```json
{
  "id": "price_2026-07-04T08-30-10Z_01",
  "based_on_comp_snapshot_id": "comp_2026-07-04T08-29-12Z_01",
  "strategy": "median_plus_adjustments",
  "inputs": {
    "base_price": 39.99,
    "condition_adjustment": -4.00,
    "completeness_adjustment": -3.00,
    "shipping_adjustment": 0.00,
    "scarcity_adjustment": 2.00
  },
  "outputs": {
    "target_price": 34.99,
    "list_price": 39.99,
    "offer_floor": 31.99,
    "auto_accept_price": 33.99
  },
  "guardrails": {
    "cost_floor": 8.00,
    "min_profit_abs": 10.00,
    "min_margin_pct": 0.35
  },
  "review": {
    "status": "proposed",
    "reviewed_by": null,
    "reviewed_at": null,
    "override_reason": null
  },
  "confidence": 0.84
}
```


## Main item JSON

In the main item file, keep only the current pricing state and pointers to the latest comping records. That matches your preference to keep current truth in the main JSON and history in a sidecar.[^4_6]

Example current-state block:

```json
{
  "item": {
    "sku": "XYZ-123"
  },
  "pricing": {
    "status": "comped",
    "latest_comp_snapshot_id": "comp_2026-07-04T08-29-12Z_01",
    "latest_pricing_recommendation_id": "price_2026-07-04T08-30-10Z_01",
    "current": {
      "list_price": 39.99,
      "offer_floor": 31.99,
      "target_price": 34.99,
      "confidence": 0.84,
      "source_kind": "ebay_product_research",
      "approved": false
    },
    "guardrails": {
      "min_profit_abs": 10.0,
      "min_margin_pct": 0.35,
      "do_not_auto_price": false
    }
  },
  "ui_state": {
    "comping": {
      "status": "captured",
      "last_query": "sony walkman wm-fx195",
      "last_source": "ebay_product_research",
      "browser_context": "product_research"
    }
  }
}
```


## History sidecar

The sidecar should hold the full event trail so you can revisit how a price was chosen. That is especially useful when operators override recommendations or when categories behave differently over time.

Example sidecar sections:

- `pricing.comp_snapshots[]`
- `pricing.recommendations[]`
- `pricing.overrides[]`
- `pricing.events[]`

Example event:

```json
{
  "ts": "2026-07-04T08:31:00Z",
  "event": "pricing_approved",
  "actor": "operator_username",
  "recommendation_id": "price_2026-07-04T08-30-10Z_01",
  "final_list_price": 39.99,
  "notes": "Accepted suggested list price"
}
```


## Interaction model

The fastest workflow is:

1. Open item; left pane already has AI-derived title, identifiers, and condition cues.
2. Click a query chip; center pane opens Product Research with the best starting query.[^4_1][^4_4]
3. Operator refines filters manually.
4. Click **Capture snapshot**; right pane fills with structured numbers.
5. Pricing engine computes recommendation instantly.
6. Operator approves or overrides.
7. Result is written to main JSON plus history sidecar.

## Rules for v1

For a first version, keep it very strict:

- No full autonomous scraping.
- Human must initiate search.
- Human must confirm capture.
- Auto-pricing only when `match_type` is exact or near_exact and comp count exceeds your threshold.
- Low-confidence items route to manual-only pricing.[^4_7]

I’d use these status values:

- `uncomped`
- `researching`
- `captured`
- `priced`
- `approved`
- `override_required`
- `manual_only`


## Recommendation logic

A simple initial ruleset is enough:

- Base on median sold from accepted comp set.[^4_1]
- Subtract for defects, missing parts, or weak completeness.
- Add modestly for scarce supply or strong demand.
- Never violate cost and margin guardrails.[^4_7]

One good first policy:

- `list_price = round_up(target_price * 1.12)`
- `offer_floor = max(cost_floor, target_price * 0.92)`
- disable auto-price if comp count < 5 or confidence < 0.70.[^4_7]

This is enough to build a usable comping pane without overcommitting to fragile automation. The next concrete step should be either the actual JSON Schema draft or the frontend component tree for the 3-pane editor.
<span style="display:none">[^4_10][^4_11][^4_12][^4_13][^4_14][^4_15][^4_16][^4_17][^4_18][^4_8][^4_9]</span>

<div align="center">⁂</div>

[^4_1]: https://www.ecommercebytes.com/2024/05/07/ebay-sellers-can-now-check-3-years-of-sold-prices-on-mobile/

[^4_2]: https://export.ebay.com/en/services-tools/product-research/

[^4_3]: https://www.flippertools.com/tools/ebay-best-offer-actual-price

[^4_4]: https://www.youtube.com/watch?v=I9dMB8un800

[^4_5]: https://www.perplexity.ai/search/dee916a0-eb69-49a1-ad8a-ba5031112549

[^4_6]: https://www.perplexity.ai/search/aa4fee66-7931-4446-bd17-3c93cd389c9f

[^4_7]: https://www.perplexity.ai/search/940b7670-deee-450e-ae1b-def10e4f1243

[^4_8]: https://techecomm.com/best-ebay-product-research-tools/

[^4_9]: https://www.youtube.com/watch?v=_yPVJ-dVMYk

[^4_10]: https://getprivy.co.uk/articles/ebay-product-research-beginners

[^4_11]: https://www.youtube.com/watch?v=HdqlbQVC-kY

[^4_12]: https://note.com/chic_kalmia8751/n/na37ee770f638?hl=en

[^4_13]: https://www.reddit.com/r/eBaySellerAdvice/comments/1lsfz2d/sold_value_standard_search_filter_vs_seller_hub/

[^4_14]: https://www.youtube.com/watch?v=YxvaG-0YXaA

[^4_15]: https://www.youtube.com/watch?v=nomhxGtqVHI

[^4_16]: https://www.frooition.com/terapeak/

[^4_17]: https://www.zikanalytics.com/blog/ebay-product-research/

[^4_18]: https://www.youtube.com/watch?v=csio9Z6Ti1A


---

# would a web ui/ux or our flutter ui/ux be a better platform?

For this specific comping interface, I’d lean **web first**, unless your Flutter desktop shell already exists and you are comfortable accepting weaker embedded-browser ergonomics on Linux. The deciding factor is the center-pane browser: a web UI can use a normal full browser session with fewer integration headaches, while Flutter desktop on Linux still tends to rely on third-party WebView packages or CEF-based workarounds rather than a clean, first-party desktop webview story.[^5_1][^5_2][^5_3][^5_4]

## Best fit

Your comping tool wants an embedded browser pane inside a data editor, with the operator moving between item data, eBay Product Research, and captured pricing state. That interaction pattern is naturally browser-centric, so a web app is usually the path of least resistance.[^5_5]

Flutter is still attractive for your broader portable client strategy and event-driven app model, especially since you already discussed a Flutter sync service reacting to JetStream/NATS events. But that is a different strength than “embed a robust desktop browser pane and work with live eBay sessions all day.”[^5_6][^5_7][^5_8]

## Web advantages

A web UI gives you:

- Native browser behavior for login/session/cookies/tabs.
- Easier split-pane editor layouts.
- Faster iteration for internal tools.
- Simpler reuse of existing web auth/session context.[^5_3]

Most importantly, your comping workflow is operator-supervised and browser-driven anyway, so a web app aligns with the real task rather than fighting it. Product Research already lives in the browser and is meant to be searched and filtered interactively.[^5_5][^5_9]

## Flutter tradeoffs

Flutter desktop itself is supported on Linux, but embedded webview support on desktop has historically depended on plugins and platform-specific packages rather than a mature built-in path. Current Linux options include CEF-backed or WebKitGTK-based packages, which adds dependency and maintenance risk.[^5_2][^5_10][^5_1][^5_3]

That does not make Flutter wrong. It just means Flutter is better when the app is mostly your own UI and only occasionally opens web content, not when one of the core panes is effectively a live browser-based research surface.[^5_4][^5_11]

## Recommendation

I would choose:

- **Web UI** for the comping/data-editor tool.
- Flutter for the portable client, mobile/field tools, or event-driven local client later.[^5_6]

A strong hybrid architecture would be:

- Python/Postgres/NATS backend as system authority.
- Web data editor as the main desktop operations console.
- Flutter client later for portable/offline or specialized workflows.[^5_7][^5_8]

If you want one sentence: the comping interface should probably be a web app, because the browser pane is not incidental — it is the heart of the workflow.[^5_5]

Would you like a concrete architecture comparison next, with columns for embedded browser quality, Linux friction, offline behavior, and fit for TGW?
<span style="display:none">[^5_12][^5_13][^5_14][^5_15][^5_16][^5_17][^5_18][^5_19][^5_20]</span>

<div align="center">⁂</div>

[^5_1]: https://pub.dev/packages/flutter_linux_webview

[^5_2]: https://github.com/anlix-io/flutter-webview-desktop/blob/main/README.md

[^5_3]: https://docs.flutter.dev/platform-integration/desktop

[^5_4]: https://github.com/flutter/flutter/issues/99597

[^5_5]: https://www.ecommercebytes.com/2024/05/07/ebay-sellers-can-now-check-3-years-of-sold-prices-on-mobile/

[^5_6]: https://www.perplexity.ai/search/b193a74e-f7f4-469d-b378-0cb62635d500

[^5_7]: https://www.perplexity.ai/search/fe6da258-6e71-4eed-9aeb-2361db8dfc4c

[^5_8]: https://www.perplexity.ai/search/abe0e437-ac61-4e21-979a-eb90671ace06

[^5_9]: https://export.ebay.com/en/services-tools/product-research/

[^5_10]: https://github.com/flutter/flutter/issues/84635

[^5_11]: https://pub.dev/packages/webview_win_floating

[^5_12]: https://github.com/hlwhl/webview_cef

[^5_13]: https://pub.dev/documentation/webview_all_linux/latest/

[^5_14]: https://pub.dev/packages/flutter_linux_webview/example

[^5_15]: https://stackoverflow.com/questions/72241723/does-flutter-3-support-webview-on-desktop

[^5_16]: https://fluttergems.dev/webview/

[^5_17]: https://github.com/flutter/flutter/issues/141082

[^5_18]: https://groups.google.com/g/flutter-dev/c/-pA_I1VNYy0

[^5_19]: https://www.reddit.com/r/flutterhelp/comments/1syo0lv/webview_on_linux_desktop/

[^5_20]: https://docs.flutter.dev/platform-integration/linux/setup


---

# we do already have a flutter app, but we also have a web ui. Web ui makes sens due to the browser restrictions. I don't want to have to maintain a browser if I can avoid it.

Then I think your answer is basically settled: build the comping interface in the **web UI**, and let Flutter stay focused on the parts it is already good at. Since you already have both platforms, the cleanest split is to use the web UI for browser-heavy operator workflows and keep Flutter for portable/client-facing workflows that do not require you to embed and maintain a browser stack.[^6_1]

## Why web wins here

Your stated goal is to avoid maintaining a browser, and Flutter desktop browser embedding on Linux still tends to mean adopting CEF- or plugin-based solutions rather than relying on a stable built-in path. Several current Flutter desktop webview options explicitly depend on Chromium Embedded Framework, and some are still described as work in progress or unstable.[^6_2][^6_3][^6_4]

That makes the web UI the lower-friction choice for a comping tool whose core interaction is “operator uses browser-based eBay research in a pane.” In the web UI, the browser is already there, which removes an entire class of packaging, plugin, and runtime issues.[^6_5][^6_6]

## Recommended split

I’d divide responsibilities like this:

- **Web UI**: data editor, comping pane, eBay Product Research workflow, listing review, admin/operator workflows.
- **Flutter app**: portable client, local cache/projection consumer, event-reactive UI, lightweight operational tools.[^6_1][^6_7]

That split also matches your broader architecture, where the authoritative system is the Postgres/state-machine backend and clients consume projections or events rather than each client becoming its own complex platform runtime.[^6_7][^6_8]

## Practical design choice

I would not try to force parity between Flutter and web for this feature. Make comping a web-first module inside the existing data editor, then expose only the resulting structured pricing state back to Flutter if Flutter ever needs to display it.

In other words:

- Research and capture happen in web.
- Price recommendation is stored centrally.
- Flutter can read approved pricing results later without reproducing the browser workflow.


## Implementation direction

For v1, I’d treat the comping interface as a specialized web editor view with:

- left item identity pane,
- center browser/research pane,
- right comp snapshot and pricing pane.[^6_5]

That gets you the speed benefit immediately and avoids browser-maintenance debt inside Flutter. Your existing Flutter app still remains valuable, just not as the host for the browser-centric comping experience.[^6_1]

So yes: use the web UI for this one. The clean boundary is that Flutter consumes pricing outcomes, while the web UI owns comp research and operator capture.
<span style="display:none">[^6_10][^6_11][^6_12][^6_13][^6_14][^6_15][^6_16][^6_17][^6_18][^6_19][^6_20][^6_9]</span>

<div align="center">⁂</div>

[^6_1]: https://www.perplexity.ai/search/b193a74e-f7f4-469d-b378-0cb62635d500

[^6_2]: https://github.com/hlwhl/webview_cef

[^6_3]: https://github.com/access-company/flutter_linux_webview

[^6_4]: https://gitlab.com/adrianjagielak/webview_cef

[^6_5]: https://www.ecommercebytes.com/2024/05/07/ebay-sellers-can-now-check-3-years-of-sold-prices-on-mobile/

[^6_6]: https://export.ebay.com/en/services-tools/product-research/

[^6_7]: https://www.perplexity.ai/search/fe6da258-6e71-4eed-9aeb-2361db8dfc4c

[^6_8]: https://www.perplexity.ai/search/abe0e437-ac61-4e21-979a-eb90671ace06

[^6_9]: https://fluttergems.dev/webview/

[^6_10]: https://blog.csdn.net/gitblog_01222/article/details/143048866

[^6_11]: https://blog.scottlogic.com/2023/02/01/webview2-electron-challengers-and-slightly-lighter-desktop-web-applications.html

[^6_12]: https://blog.csdn.net/gitblog_01247/article/details/143047708

[^6_13]: https://magpcss.org/ceforum/viewtopic.php?f=6\&t=19276

[^6_14]: https://www.reddit.com/r/flutterhelp/comments/1syo0lv/webview_on_linux_desktop/

[^6_15]: https://pub.dev/packages/webview_cef/example

[^6_16]: https://github.com/hlwhl/webview_cef/blob/main/CHANGELOG.md

[^6_17]: https://pub.dev/packages/flutter_linux_webview/changelog

[^6_18]: https://windowsforum.com/threads/windows-11-memory-bloat-why-electron-and-webview2-apps-hog-ram.394695/

[^6_19]: https://fluttergems.dev/packages/webview_cef/

[^6_20]: https://pub.dev/packages/webview_cef/versions


---

# on one hand not looking for parity. on the other, the web ui is the universally accessible surface, so it will be comprehensive. The flutter app will be the event driven version of that, a fairly univerrsal buit more powerful tool for the same tasks..

Yes — that is a better framing than “web for one thing, Flutter for another.” What you’re really describing is **two clients over one operational model**: the web UI is the comprehensive, universally reachable surface, and the Flutter app is the richer event-driven client for the same domain workflows.

## Platform roles

That means the web UI should be treated as the canonical full-featured surface, not a fallback. It needs to cover the whole workflow because it is the one interface you can reliably reach anywhere and the one that naturally accommodates browser-based tasks like comping.

The Flutter app then becomes a first-class client with stronger local responsiveness, deeper event handling, and better “workbench” behavior, while still operating on the same tasks and state model. That aligns with your existing idea of Flutter as an event-driven client with a sync service reacting to backend events rather than owning separate business logic.[^7_1][^7_2]

## Shared model

So I would design both surfaces around a single contract:

- Same domain objects.
- Same commands.
- Same event stream.
- Same state machine.
- Same permissions and review rules.[^7_3]

In event-driven architecture terms, this is a classic split between an authoritative write model and client-specific read models or projections, where each UI consumes optimized state rather than inventing its own workflow rules. That pattern is widely used because projections can be tailored to each client while preserving one source of truth.[^7_4][^7_5][^7_6]

## Practical split

For the comping tool specifically:

- Web UI owns the browser-embedded research experience because it is the natural host for that workflow.
- Flutter should still expose the same pricing, comp status, recommendations, and approval results, but only host research directly if that later proves worthwhile.[^7_1]

That is not lack of parity. It is **functional parity over a shared backend**, with selective UX specialization where platform constraints differ.[^7_7]

## Design rule

A good rule is:

- Commands and workflow semantics live in backend/domain services.
- Web and Flutter each render their own optimized UX.
- Client differences are presentation and capability differences, not business-rule forks.[^7_8][^7_7]

So your web UI becomes the comprehensive reference client, and Flutter becomes the higher-powered reactive client over the same state machine. That is a strong architecture, because it preserves one operational truth while letting each surface play to its strengths.[^7_1]

I’d explicitly document the system this way: **web is the universal surface; Flutter is the premium event-driven client for the same workflows**. That wording fits what you’re building much better than “web vs Flutter.”
<span style="display:none">[^7_10][^7_11][^7_12][^7_13][^7_14][^7_15][^7_16][^7_17][^7_18][^7_9]</span>

<div align="center">⁂</div>

[^7_1]: https://www.perplexity.ai/search/b193a74e-f7f4-469d-b378-0cb62635d500

[^7_2]: https://www.perplexity.ai/search/fe6da258-6e71-4eed-9aeb-2361db8dfc4c

[^7_3]: https://www.perplexity.ai/search/080e13d7-af2a-4e83-ba47-eaec7b75cbb7

[^7_4]: https://medium.com/@TonyBologni/live-projections-for-read-models-with-event-sourcing-and-cqrs-42dd18b19fd0

[^7_5]: https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing

[^7_6]: https://event-driven.io/en/projections_and_read_models_in_event_driven_architecture/

[^7_7]: https://docs.flutter.dev/app-architecture/concepts

[^7_8]: https://docs.flutter.dev/app-architecture/guide

[^7_9]: https://github.com/JCKodel/eva

[^7_10]: https://github.com/rrousselGit/flutter_hooks/discussions/352

[^7_11]: https://www.youtube.com/watch?v=SF9r2DnSdg0

[^7_12]: https://www.youtube.com/watch?v=HSyE9nS5mMs

[^7_13]: https://pub.dev/packages/flutter_event_component_system/versions/0.0.5

[^7_14]: https://medium.com/@ahmadexe/flutter-bloc-building-block-for-event-driven-flutter-apps-191f11437b0

[^7_15]: https://github.com/giorgiogross/flutter_journeys

[^7_16]: https://medium.com/design-microservices-architecture-with-patterns/event-sourcing-pattern-in-microservices-architectures-e72bf0fc9274

[^7_17]: https://newsletter.systemdesigncodex.com/p/must-know-event-driven-architectural

[^7_18]: https://medium.com/@alaxhenry0121/the-event-sourcing-pattern-thats-transforming-how-flutter-developers-handle-complex-business-logic-4f0bf2599a43

