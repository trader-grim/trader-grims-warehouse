# Gemini Task 002 — Data Scrub Analysis

**Date prepared:** 2026-06-06
**Expected output:** Save your analysis as a Markdown file and drop it in `docs/TGW-Plan-Vault/inbox/`
**Output filename:** `GEMINI-002-result.md`

---

## Context

TGW (Trader Grim's Warehouse) is an eBay resale business running a custom pipeline.
Items live as JSON files at `ItemData/<SKU>/<SKU>.json`. Fields accumulate as each worker
processes the item — an item at stage N has all fields from earlier stages plus its own.

There are two item populations with very different structures:

**Legacy items** (~50,000): Imported from pre-pipeline eBay exports. Have fields like
`Item number`, `eBay category 1 name`, `Condition` (capital C), `status`, `price` (lowercase),
`ebay_listing.api = "trading"`. Many are actively listed on eBay. They went through a different
(now-retired) manual workflow and were never processed by the new workers.

**New-pipeline items** (~5,000+): Created by the new intake system. Have `ai_identified: true`,
`draft_listing` block, `ebay_offer` block, `ebay_listing.api = "inventory"` when published.
Pipeline: intake → ai_identify → ebay_draft → ebay_upload+ebay_price (parallel) → ebay_stage
→ operator review → ebay_publish → live.

The goal of this analysis is to design a `tgw catalog-verify` CLI command that scans all
~55,000 item JSON files and reports on field completeness, pipeline staleness, and data quality
violations. Think of it as a health check for the catalog.

---

## Data: Item JSON schema (pipeline stages and all fields)

```
Pipeline stages (additive — later stages have earlier fields too):

intake:      sku, title, location, #STATUS, verified, ai_hint, upc (optional)
identified:  + category, description, condition, ebay_category_id, ebay_category_name, ai_identified
drafted:     + draft_listing.{title, category_id, condition, condition_id, condition_enum,
               format, quantity, price(null), item_specifics, description, listing_description,
               aspects_required_total, aspects_required_filled,
               aspects_recommended_total, aspects_recommended_filled, quality}
uploaded:    + ebay_photos[], draft_listing.imageUrls
priced:      + ebay_offer.{price, target_price, price_source, price_comps, priced_at},
               draft_listing.price (filled in)
staged:      + ebay_offer.{offer_id, status=UNPUBLISHED, staged_at}
published:   + ebay_listing.{listing_id, listing_url, offer_id, status=Active, api=inventory,
               published_at}, ebay_offer.{status=PUBLISHED, published_at}, reprice_schedule[]
synced:      + ebay_listing.{live_price, synced_at, listing_status}, ebay_offer.{category_id, quantity}

Top-level fields reference:
  sku              str   Always present. Format: tgwYYYYMMDDHHMMSSmmm
  title            str   Intake title; ai_identify overwrites
  location         str   Bin label (e.g. FF0792)
  #STATUS          str   new / SOLD / MISSING / Out Of Stock / etc.
  verified         str   new at intake; operator can update
  ai_hint          str   Free text hint for ai_identify
  upc              str   Barcode if scanned
  category         str   Human-readable (written by ai_identify)
  description      str   AI short description
  condition        str   Human: Like New / Good / Acceptable / etc.
  ebay_category_id str   eBay numeric category
  ebay_category_name str
  ai_identified    bool  true when ai_identify has run
  product_lookup   dict  Present only on barcode hit
  offline_draft    bool  true if draft built without eBay API (taxonomy missing)
  draft_listing    dict  Written by ebay_draft
  ebay_photos      list  Written by ebay_upload
  ebay_offer       dict  Written by ebay_price, ebay_stage, ebay_publish, ebay_sync
  ebay_listing     dict  Written by ebay_publish, kept current by ebay_sync
  reprice_schedule list  Written by ebay_publish
  epid             str   eBay catalog product ID (rare; needs special scope)
  ai_reidentify    bool  Operator sets true to force re-run
  reprice_skip     bool  Operator sets true to pause markdowns
  legacy_listing_resolved bool  True once legacy listing ended/migrated
  source_sku       str   Original SKU if migrated

Legacy-only fields (never written by new workers):
  Item number, eBay category 1 name, eBay category 1 number, Condition (capital C),
  price (lowercase), qty, weight, image, manufacturer, status (lowercase),
  attribute_set, category_ids, ebay_condition_number, C:Brand, ebay_sale,
  title_history, description_history, location_history, sku_old

draft_listing sub-fields (all written by ebay_draft):
  title, category_id, category_name, condition, condition_id, condition_enum,
  format (always FixedPrice), quantity (always 1), price (null until ebay_price fills it),
  item_specifics (dict), description, listing_description,
  aspects_required_total, aspects_required_filled,
  aspects_recommended_total, aspects_recommended_filled,
  quality (DraftScore block), imageUrls (filled by ebay_upload),
  title_ai (present if SEO enhanced), title_flags (list of SEO actions)

ebay_offer sub-fields:
  price, target_price, price_source, price_comps{count,min,p25,median,max},
  priced_at, offer_id, status, staged_at, published_at, category_id, quantity

ebay_listing sub-fields:
  listing_id, listing_url, offer_id, status, listing_status, api,
  published_at, live_price, synced_at
```

---

## Data: four representative item samples

### Sample A — New pipeline, stalled at "identified" (no draft yet)

This item was identified by ai_identify but `ebay_draft` has not yet run. Note `offline_draft: true`
means the draft system ran but couldn't get eBay taxonomy (API was down), so no draft was written.
This item is stuck.

```json
{
  "location": "FF0797",
  "verified": "new",
  "sku": "tgw202604281515436",
  "title": "Ceramics Monthly September 1995 - Pottery Artist Cover",
  "#STATUS": "new",
  "ai_hint": "Ceramics Monthly September 1995",
  "category": "Magazines & Books",
  "description": "A vintage issue featuring a pottery artist at work, showcasing ceramic tools and finished pieces.",
  "condition": "Good",
  "ebay_category_id": "280",
  "ebay_category_name": "Magazines",
  "ai_identified": true,
  "offline_draft": true
}
```

**Stage assessment:** Identified. `offline_draft: true` is a stall signal — ebay_draft ran but
produced nothing usable. This item needs ebay_draft to re-run when API is available.

---

### Sample B — New pipeline, drafted + uploaded, stalled before pricing

Photos uploaded, draft complete, but no `ebay_offer` (not yet priced). This is normal if the
item is freshly drafted; it becomes a problem if it's been sitting for days.

```json
{
  "location": "ENV0505",
  "verified": "new",
  "sku": "tgw202604291752572",
  "title": "Vintage Tokyo Walking Map Summer 1984",
  "#STATUS": "new",
  "ai_hint": "Tokyo Walking Mat Summer 1984",
  "category": "Travel Maps",
  "description": "A vintage Tokyo Walking Map from Summer 1984, featuring a photo of traditional performers.",
  "condition": "Good",
  "ebay_category_id": "165865",
  "ebay_category_name": "Japan",
  "ai_identified": true,
  "draft_listing": {
    "title": "Vintage Tokyo Walking Map Summer 1984",
    "category_id": "165865",
    "category_name": "Japan",
    "condition": "Good",
    "format": "FixedPrice",
    "quantity": 1,
    "price": null,
    "item_specifics": { "Handmade": "No", "Type": "Vintage Map" },
    "description": "A vintage Tokyo Walking Map from Summer 1984.",
    "imageUrls": [
      "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/tJwAAeSwcgFqH8zJ/$_12.JPG?set_id=880000500F"
    ]
  },
  "ebay_photos": [
    { "local": "/opt/TGW/data/ItemData/tgw202604291752572/tgw20260429_175320.jpg",
      "url": "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/tJwAAeSwcgFqH8zJ/$_12.JPG?set_id=880000500F" }
  ]
}
```

**Stage assessment:** Uploaded/drafted, awaiting pricing. Missing: `ebay_offer`, `draft_listing.price`.
Note: `draft_listing` is missing `aspects_required_total`, `aspects_required_filled`,
`aspects_recommended_total`, `aspects_recommended_filled`, `quality`, `listing_description` —
these are new fields added after this item was drafted; older drafts predate them.

---

### Sample C — New pipeline + legacy hybrid: has draft + offer but api=trading

This item was a legacy eBay listing (Trading API), got an `ai_identify` pass and a
`draft_listing` (via re-identification), and has an `ebay_offer` (pricing run). But the
live listing is `api: "trading"` — it was never republished through the Inventory API.
It is live on eBay via the old system. `legacy_listing_resolved: true` means the Trading
API listing was intentionally kept.

```json
{
  "location": "FF0794",
  "verified": "new",
  "sku": "tgw202604271737443",
  "title": "Ariens Tractor Attachments Brochure",
  "#STATUS": "new",
  "Item number": "227329568675",
  "eBay category 1 name": "Heavy Equipment Manuals & Books",
  "eBay category 1 number": "257888",
  "Condition": "Like New",
  "title_history": ["Ariens Tractor Attachments Brochure", "A Reinstructor Brochure"],
  "ai_hint": "Ariens Tractor Attachments Brochure",
  "legacy_listing_resolved": true,
  "ebay_listing": {
    "listing_id": "227329568675",
    "listing_url": "https://www.ebay.com/itm/Ariens-Tractor-Attachments-Brochure-/227329568675",
    "status": "Active",
    "live_price": 24.99,
    "api": "trading",
    "synced_at": "2026-06-05T14:56:41.588858+00:00"
  },
  "category": "Tools & Equipment",
  "description": "Catalog showcasing various attachments for Ariens tractors.",
  "condition": "Good",
  "ai_identified": true,
  "offline_draft": true,
  "draft_listing": {
    "title": "Ariens Tractor Attachments Brochure",
    "category_id": "99",
    "category_name": "",
    "condition": "Good",
    "format": "FixedPrice",
    "quantity": 1,
    "price": 8.57,
    "item_specifics": {},
    "description": "Catalog showcasing various attachments for Ariens tractors."
  },
  "ebay_offer": {
    "price_source": "browse:category+short",
    "price_comps": { "count": 20, "min": 6.75, "p25": 8.57, "median": 12.32, "max": 60.0 },
    "priced_at": "2026-06-03T13:17:41.997702+00:00",
    "price": 8.57
  }
}
```

**Stage assessment:** Mixed. Legacy active listing + new pipeline partial processing.
Issues: `draft_listing.category_id = "99"` (invalid/placeholder), empty `item_specifics`,
`offline_draft: true` (draft is incomplete), `ebay_offer` has no `offer_id`/`staged_at`
(never staged through Inventory API). Live price ($24.99) >> computed price ($8.57) — divergence.

---

### Sample D — Bare legacy item, minimally migrated

A legacy active listing with only the fields written by the migration sync worker.
No new-pipeline processing at all. Represents the bulk (~50k) of the catalog.

```json
{
  "location": "FF0749",
  "verified": "new",
  "sku": "tgw202604042045164",
  "title": "Lost Beneath The Feather By Bill Talbitzer",
  "#STATUS": "new",
  "Item number": "327095605541",
  "eBay category 1 name": "Antiquarian & Collectible",
  "eBay category 1 number": "29223",
  "title_history": [
    "Lost Beneath The Feather By Bill Talbitzer",
    "Last Beneath The Feather By Bell Talbitzer"
  ],
  "ebay_listing": {
    "listing_id": "327095605541",
    "listing_url": "https://www.ebay.com/itm/Lost-Beneath-The-Feather-By-Bill-Talbitzer-/327095605541",
    "status": "Active",
    "live_price": 25.49,
    "api": "trading",
    "synced_at": "2026-06-05T14:56:41.588858+00:00"
  }
}
```

**Stage assessment:** Legacy only. Missing everything: `ai_identified`, `draft_listing`,
`ebay_offer`, `category`, `description`, `condition`, `ebay_category_id`. The only
structured data is the eBay legacy listing block and a title. This is the baseline
for ~50k items.

---

## Catalog-scale summary stats

- **Total items:** ~55,351
- **Items with `ai_identified: true`:** estimated ~5,000–8,000 (new-pipeline + re-identified legacy)
- **Items with `draft_listing`:** subset of above (some stalled at identified)
- **Items with `offline_draft: true`:** unknown count — these are stalled
- **Items with `ebay_offer`:** subset of drafted items
- **Items with `ebay_listing.api = "inventory"`:** published new-pipeline items
- **Items with `ebay_listing.api = "trading"`:** legacy listings (~8,350 remain live; being migrated at ~5/hr)
- **Items with `#STATUS = "SOLD"`:** ~3,083 recorded
- **Items with no `location`:** unknown — location is required for operator retrieval

---

## Your analysis tasks

### 1. Completeness matrix

Build a table of which fields are **expected** at each pipeline stage for new-pipeline items.
Use the schema above. For each field, note: Required / Optional / Should-not-be-present.

Example structure:
```
| Field              | intake | identified | drafted | uploaded | priced | staged | published | synced |
|--------------------|--------|------------|---------|----------|--------|--------|-----------|--------|
| sku                | REQ    | REQ        | REQ     | REQ      | REQ    | REQ    | REQ       | REQ    |
| title              | REQ    | REQ        | REQ     | ...      |        |        |           |        |
| draft_listing      | -      | -          | REQ     | REQ      | REQ    | REQ    | REQ       | REQ    |
| draft_listing.price| -      | -          | null    | null     | REQ    | REQ    | REQ       | REQ    |
...
```

### 2. Stall pattern identification

For each of the four sample items, identify:
- Which pipeline stage it is actually at (based on fields present)
- Which fields are missing relative to where it should be
- Whether it is **stalled** (has a blocking gap) or **in-flight** (gap is expected/transient)
- The likely cause and recommended remediation action

### 3. Legacy item scrub rules

Define a set of rules for detecting problems in legacy items (Sample D type):
- What makes a legacy item "clean enough" to leave alone?
- What makes one "needs attention" (e.g., missing location, no live_price, status mismatch)?
- Which legacy fields should be flagged as junk/ignored in any quality check?

### 4. `tgw catalog-verify` command design

Design the CLI interface and output format for a `catalog-verify` command to be added to the
TGW CLI tool. This command should scan all item JSON files and report on catalog health.

**Design requirements:**
- Must run without touching eBay APIs — reads local JSON only
- Must be usable as both a human report (`tgw catalog-verify`) and machine-parseable
  (`tgw catalog-verify --json`)
- Must support filtering: `--status new`, `--pipeline new|legacy|all`, `--stalled-only`,
  `--category <ebay_cat_id>`
- Should exit non-zero if violations found (useful for scripting)
- Should be fast enough to run on ~55k files (< 60 seconds)
- Should produce per-violation output AND a summary

**Design the following:**

a) **Check list** — enumerate all checks the command should perform, grouped by severity:
   - CRITICAL: data that will cause a pipeline worker to crash or produce wrong output
   - WARNING: data quality issues that may cause poor listings or pricing errors
   - INFO: cosmetic issues or improvement opportunities

b) **Output format** — show an example of the human-readable output for a catalog with
   a few violations. Include a summary block at the end.

c) **Python scaffold** — write the skeleton of `src/tgw/commands/catalog_verify.py` with:
   - `run(args)` entry point
   - A `Check` dataclass (or namedtuple) for individual violations
   - Stub functions for each check group (new_pipeline_checks, legacy_checks, universal_checks)
   - The summary/report rendering logic
   - `--json` output path
   
   The TGW CLI dispatches to `run(args)` where `args` is an `argparse.Namespace`. The command
   is registered in `src/tgw/commands/__init__.py` (you don't need to write that part).
   
   Existing patterns to follow: `tgw health` runs a set of checks and reports pass/fail/warn.
   Workers use `tgw.api` for item reads (but catalog-verify should read JSON directly for speed).

d) **Priority order** — which checks should be implemented first for maximum immediate value?
   Rank the top 5 by (impact × ease) and explain why.

### 5. Data quality observations

Note any patterns or surprises you see in the sample items that aren't covered by the
above checks — things a future developer should know about the catalog's actual state.

---

## Output format

```markdown
# Data Scrub Analysis — 2026-06-06

## Completeness Matrix
[Table as designed in task 1]

## Stall Pattern Analysis
[Per-sample: stage, gaps, stalled/in-flight, cause, remediation]

## Legacy Item Scrub Rules
[Numbered rule list with severity]

## catalog-verify Command Design

### Check List
[Grouped by CRITICAL / WARNING / INFO]

### Example Output
[Sample terminal output showing violations + summary]

### Python Scaffold
[Full catalog_verify.py skeleton with stubs]

### Implementation Priority
[Top 5 checks ranked by impact × ease]

## Additional Observations
[Anything else worth noting]
```
