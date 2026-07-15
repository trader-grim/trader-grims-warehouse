# TGW Item JSON Schema Reference

Each item lives at `ItemData/<SKU>/<SKU>.json`. Fields accumulate as the item moves through the
pipeline — later workers read earlier workers' output. Stages are additive; an item at stage N has
all fields from stages 0..N-1 plus its own.

## Pipeline stages

| Stage | Trigger | Key fields added |
|-------|---------|-----------------|
| **intake** | photo drop → pm_intake / bundle_intake | `sku`, `title`, `location`, `#STATUS`, `#VERIFIED`, `ai_hint` |
| **identified** | ai_identify completes | `title` (overwrite), `category`, `description`, `condition`, `ebay_category_id`, `ebay_category_name`, `ai_identified`, `product_lookup` (if barcode hit) |
| **drafted** | ebay_draft completes | `draft_listing`, `ebay_category_id/name` (may update), `offline_draft` |
| **uploaded** | ebay_upload completes | `ebay_photos`, `draft_listing.imageUrls` |
| **priced** | ebay_price completes | `ebay_offer.price/comps/priced_at`, `draft_listing.price`, `draft_listing.quality` |
| **staged** | ebay_stage completes | `ebay_offer.offer_id/status/staged_at`, `epid` (if found) |
| **published** | ebay_publish completes (operator runs `tgw publish`) | `ebay_listing`, `ebay_offer.published_at`, `reprice_schedule` |
| **synced** | ebay_sync runs periodically | `ebay_listing.live_price/synced_at/listing_status`, `ebay_offer.category_id/quantity` |

---

## Top-level fields

| Field | Type | Stage set | Writer | Notes |
|-------|------|-----------|--------|-------|
| `sku` | str | intake | pm_intake / bundle_intake | `tgwYYYYMMDDHHMMSSmmm` format |
| `title` | str | intake → identified | pm_intake, **ai_identify** (overwrites) | AI title; SEO-enhanced copy in `draft_listing.title` |
| `location` | str | intake | pm_intake / bundle_intake | Bin label, e.g., `FF0792` |
| `category` | str | identified | ai_identify | Human-readable category, e.g., `Magazines & Books` |
| `description` | str | identified | ai_identify | AI-generated description (short, 2–3 sentences) |
| `condition` | str | identified | ai_identify | Human-readable: `Like New`, `Good`, `Used`, etc. |
| `ebay_category_id` | str | identified | ai_identify (may update in ebay_draft) | eBay category number |
| `ebay_category_name` | str | identified | ai_identify (may update in ebay_draft) | eBay category name |
| `ai_identified` | bool | identified | ai_identify | Always `true` when present; guards against re-processing |
| `product_lookup` | dict | identified | ai_identify via lookup dispatcher | Present only on barcode hit; see sub-fields below |
| `ai_hint` | str | intake | pm_intake / operator | Free-text hint fed to ai_identify prompt |
| `ai_reidentify` | bool | operator | manual / pm_intake | Set `true` to force ai_identify to re-run |
| `upc` | str | intake | pm_intake / bundle_intake | Barcode if scanned at intake |
| `draft_listing` | dict | drafted | ebay_draft | See `draft_listing` sub-fields below |
| `draft_listing_state` | str | any | http PATCH hook, draft_sync | Draft lifecycle (broker B1a): `editing` = AI/operator manipulation in flight (set automatically on any `draft_listing` write); `baseline` = draft re-pinned to the offer (publish success, Reset Draft). Broker drift repair may only touch `baseline` items |
| `baseline_at` | str | any | draft_sync | UTC ts of the last re-baseline; dead_letter jobs older than this are superseded history |
| `pipeline_error` | dict | staged/published | ebay_stage, ebay_publish | Canonical schema `{code, detail, ts, source, raw?}` — guard findings (e.g. `no_price_set`, C11) and eBay rejections (`code: ebay_rejected`, raw body). Legacy `{worker, error, raw, at}` still on old items (reader shim in http_server). Cleared only when resolved: guard findings self-clear when a draft edit fixes the condition; rejections clear on re-pin |
| `ebay_photos` | list[dict] | uploaded | ebay_upload | See `ebay_photos` entries below |
| `offline_draft` | bool | drafted | ebay_draft | `true` if draft built without eBay API (taxonomy offline) |
| `ebay_offer` | dict | priced → staged → published → synced | multiple | See `ebay_offer` sub-fields below |
| `ebay_listing` | dict | published → synced | ebay_publish, ebay_sync | See `ebay_listing` sub-fields below |
| `reprice_schedule` | list[dict] | published | ebay_publish | See `reprice_schedule` entries below |
| `price_history` | list[dict] | published → markdown | ebay_price_reducer | See `price_history` entries below; appended per applied reduction |
| `epid` | str | staged | ebay_stage | eBay Catalog product ID (requires `commerce.catalog.readonly` scope) |
| `#STATUS` | str | intake → operator | pm_intake, manual | `new` (default), `SOLD`, `MISSING`, etc. |
| `#VERIFIED` | str | intake | pm_intake | Verification state; `new` at intake |
| `legacy_listing_resolved` | bool | operator | manual | Set `true` once legacy Trading API listing is ended/migrated |
| `reprice_skip` | bool | operator | manual | Set `true` to stop price_reducer from reducing price |
| `source_sku` | str | migration | ebay_sku_migrate | Original SKU when item was migrated from legacy system |
| `item_attributes` | dict (envelope) | identified | ai_identify (fills gaps only); `tgw.inventory_record` is the ONLY sanctioned accessor | **Set A — "Inventory Record."** Universal, marketplace-agnostic facts (Type, Brand, Metal, Department, ...) — the record meant to translate across eBay and any future marketplace. NOT what gets pushed to eBay directly (that's `draft_listing.item_specifics`, Set B, below). See "Field-set envelope shape" below. |
| `item_attributes_history` | list[dict] | identified onward | `tgw.inventory_record.set_inventory_fields` | Append-only provenance trail for Set A edits. See "Field-set envelope shape" below. |

### Set A vs. Set B — the two-set rule (todo #1418/#1416/#1417, invariant C12)

`item_attributes` (Set A, this table) and `draft_listing.item_specifics`
(Set B, see below) look superficially similar — both are "a dict of
aspect-like facts" — but are deliberately separate and must never be
merged key-by-key. Set A is the universal record; Set B is the
eBay-specific, category-mapped values actually pushed to eBay's Inventory
API (`tgw.ebay.sync._build_offer_bodies` reads ONLY Set B for the live
push). Two prior sessions (#1291, #1313/#1316) each fixed a real bug in
this territory without ever noticing the set-boundary problem underneath,
because the old bare-dict shape had no self-identifying marker — see
`invariants.md` C12 for the full "why" and enforcement.

**Read/write ONLY through the sanctioned accessor modules:**
- Set A: `tgw.inventory_record` (`get_inventory_field(s)`, `set_inventory_fields`)
- Set B: `tgw.ebay.draft_specifics` (`get_ebay_aspect(s)`, `set_ebay_aspects`)

Cross-set moves (Set A → Set B translation, or the reverse-flow diff) are
a separate, explicit, named function built on top of these accessors
(#1416/#1417) — never a per-key merge or `{**a, **b}` spread performed
locally in a display or save handler.

### Field-set envelope shape

Both `item_attributes` and `draft_listing.item_specifics` are
self-describing envelopes, not bare dicts — this is the third application
of the "cheap current value + append-only history array" shape already
established in this codebase by `price_history` (session 42) and
`vision_results`/`alt_text_results` (raw AI-call preservation), not a new
invention:

```json
"item_attributes": {
  "_set": "inventory_record",
  "version": 1,
  "updated_at": "2026-07-15T12:00:00+00:00",
  "updated_at_backfilled": false,
  "fields": {"Type": "Brooch", "Brand": "Unbranded"}
}
```

`_set` is a literal, hardcoded, self-describing string — its whole purpose
is that someone looking at raw JSON with zero other context knows
immediately which set they're looking at (`grep '"_set": "inventory_record"'`
finds every instance directly, independent of nesting). `updated_at_backfilled`
is `true` only for items migrated from the old bare-dict shape whose real
edit timestamp was never recorded — the migration uses the best available
proxy timestamp (or the migration run time) rather than fabricate false
precision (Prime Directive 1).

**Back-compat note:** items not yet migrated (see
`scripts/migrate_field_set_envelope.py`) still carry `item_attributes` /
`item_specifics` as a bare `{key: value}` dict with no `_set` tag — the
accessor modules read both shapes transparently. The full 55k-item catalog
migration is a separate, explicit go/no-go decision, not bundled into the
envelope shape landing (todo #1418).

Provenance history — append-only, never edited or truncated (same
discipline as `price_history`):

```json
"item_attributes_history": [
  {"ts": "...", "key": "Type", "value": "Brooch", "previous_value": "Lapel Pin",
   "source": "ai_identify", "applied_by": "system"}
]
```
`draft_listing.item_specifics_history` is the Set B equivalent, nested
alongside `item_specifics` inside `draft_listing` (see below).

### Legacy-only fields (old Trading API items; not written by current workers)

These appear only on items imported from pre-pipeline Seller Hub exports. New items never have them.

`Item number`, `eBay category 1 name`, `eBay category 1 number`, `Condition`, `price`, `qty`,
`weight`, `image`, `manufacturer`, `attribute_set`, `category_ids`, `ebay_condition_number`,
`status`, `sku_old`, `country_of_manufacture`, `title_history`, `description_history`,
`location_history`, `C:Brand`, `ebay_sale`

---

## `product_lookup` sub-fields

Written by `ai_identify` when barcode lookup returns a hit (upcitemdb primary, go-upc secondary).
Stored as-is from `LookupResult.to_dict()`.

| Field | Type | Source | Notes |
|-------|------|--------|-------|
| `title` | str | lookup API | Product title from database |
| `brand` | str | lookup API | |
| `mpn` | str | lookup API | Manufacturer part number |
| `ean` | str | lookup API | EAN barcode |
| `description` | str | lookup API | Product description (used by ebay_draft Phase 5 enrichment) |
| `category` | str | lookup API | Product category hint |
| `retail_price` | float | lookup API | MSRP / list price |
| `source` | str | dispatcher | Which API returned the hit: `upcitemdb`, `go_upc`, etc. |
| `cached_at` | str (ISO8601) | dispatcher | When the result was cached |
| `barcode` | str | dispatcher | The barcode that was looked up |

---

## `draft_listing` sub-fields

Written entirely by `ebay_draft`. `price` and `imageUrls` are filled in later by other workers.

| Field | Type | Writer | Notes |
|-------|------|--------|-------|
| `title` | str | ebay_draft | SEO-enhanced title (≤80 chars); may differ from top-level `title` |
| `title_ai` | str | ebay_draft | Original AI title before SEO enhancement (present only if enhanced) |
| `title_flags` | list[str] | ebay_draft | SEO actions taken, e.g., `['brand_added', 'mpn_added']` |
| `category_id` | str | ebay_draft | eBay category number |
| `category_name` | str | ebay_draft | eBay category name |
| `category_confidence` | str | ebay_draft | `low` when product_lookup and AI category disagree |
| `condition` | str | ebay_draft | Human-readable condition string |
| `condition_id` | int | ebay_draft | eBay conditionId number (e.g., 3000) |
| `condition_label` | str | ebay_draft | eBay condition label (e.g., `Used`) |
| `condition_enum` | str | ebay_draft | Inventory API enum (e.g., `USED_EXCELLENT`) |
| `format` | str | ebay_draft | Always `FixedPrice` |
| `quantity` | int | ebay_draft | Always 1 |
| `price` | float \| null | **ebay_price** | Set after draft; null until priced |
| `item_specifics` | dict (envelope) | ebay_draft | **Set B — "eBay Draft."** Aspect name → value; the ONE set that actually reaches eBay's Inventory API (`tgw.ebay.sync._build_offer_bodies` reads only this). `tgw.ebay.draft_specifics` is the ONLY sanctioned accessor. See "Field-set envelope shape" above. |
| `item_specifics_history` | list[dict] | ebay_draft onward | `tgw.ebay.draft_specifics.set_ebay_aspects` | Append-only provenance trail for Set B edits, nested alongside `item_specifics`. |
| `description` | str | ebay_draft | Short AI description (may be enriched from product_lookup) |
| `description_source` | str | ebay_draft | `enriched` if product_lookup description was used as base |
| `listing_description` | str | ebay_draft → ebay_publish | Full eBay HTML description (AI text + footer + picklist line) |
| `imageUrls` | list[str] | **ebay_upload** | eBay EPS hosted photo URLs |
| `aspects_required_total` | int | ebay_draft | Count of REQUIRED aspects for this category |
| `aspects_required_filled` | int | ebay_draft | How many REQUIRED aspects were filled |
| `aspects_recommended_total` | int | ebay_draft | Count of RECOMMENDED aspects for this category |
| `aspects_recommended_filled` | int | ebay_draft | How many RECOMMENDED aspects were filled |
| `quality` | dict | ebay_draft, **ebay_price** (re-score) | Quality score block; see `tgw.listing_quality.DraftScore.to_dict()` |
| `alt_text` | str | **tgw alt-text** | Accessibility alt text for primary image (max 150 chars); no "image of" prefix |
| `seo_caption` | str | **tgw alt-text** | 1–2 sentence SEO caption including brand/model/key features |

---

## `ebay_offer` sub-fields

Built up across multiple workers. Fields accumulate; earlier fields are never deleted.

| Field | Type | Writer | Notes |
|-------|------|--------|-------|
| `price` | float | ebay_price → ebay_price_reducer → ebay_publish | Current offer price |
| `target_price` | float | ebay_price | p25 comp price — eventual markdown target |
| `price_source` | str | ebay_price | e.g., `browse:full_title`, `browse:category_only`, `default` |
| `price_comps` | dict | ebay_price | `{count, min, p25, median, p75, max}` from Browse API |
| `priced_at` | str (ISO8601) | ebay_price | When price was computed |
| `offer_id` | str | ebay_stage → ebay_sync | eBay Inventory API offer ID |
| `status` | str | ebay_stage → ebay_publish → ebay_sync | `UNPUBLISHED` → `PUBLISHED` |
| `staged_at` | str (ISO8601) | ebay_stage | When offer was created on eBay |
| `published_at` | str (ISO8601) | ebay_publish | When offer was published |
| `category_id` | str | ebay_sync | Category ID synced back from eBay |
| `quantity` | int | ebay_sync | Available quantity synced from eBay |

---

## `ebay_listing` sub-fields

Written by `ebay_publish`, then kept current by `ebay_sync`.

| Field | Type | Writer | Notes |
|-------|------|--------|-------|
| `listing_id` | str | ebay_publish → ebay_sync | eBay item number (shown in URL and Seller Hub) |
| `listing_url` | str | ebay_publish → ebay_sync | `https://www.ebay.com/itm/{listing_id}` |
| `offer_id` | str | ebay_publish → ebay_sync | eBay Inventory API offer ID (same as `ebay_offer.offer_id`) |
| `status` | str | ebay_publish → ebay_sync | `Active`, `Ended`, `Out of Stock`, etc. |
| `listing_status` | str | ebay_sync | eBay API listing-level status |
| `api` | str | ebay_publish | `inventory` for Inventory API listings; `trading` for migrated legacy |
| `published_at` | str (ISO8601) | ebay_publish | |
| `live_price` | float | ebay_sync | Current live price from eBay (may differ from offer price during repricing) |
| `synced_at` | str (ISO8601) | ebay_sync | Last successful sync timestamp |

---

## `ebay_photos` list entries

Each entry is a dict with two fields. Order matches photo upload order.

| Field | Type | Notes |
|-------|------|-------|
| `local` | str | Absolute path to local photo file (e.g., `/opt/TGW/data/ItemData/<SKU>/tgw*.jpg`) |
| `url` | str | eBay EPS hosted URL (permanent, used in listing) |

---

## `reprice_schedule` list entries

Written by `ebay_publish`, consumed and updated by `ebay_price_reducer`.
One entry per configured reprice stage. Stage 0 is always `launch`.

| Field | Type | Notes |
|-------|------|-------|
| `stage` | int | Stage index (0 = launch) |
| `label` | str | Human-readable label, e.g., `launch`, `p25_day3`, `p25_day17` |
| `price` | float \| null | Target price for this stage; null if no comps data |
| `due_at` | str (ISO8601) | When to apply this price change |
| `done_at` | str (ISO8601) | When price_reducer actually applied it (absent until done) |

---

## `price_history` list entries

Written by `ebay_price_reducer` (2026-06-10, docs/invariants.md C4) — one entry per
price reduction actually applied to eBay. Stages satisfied without an eBay call
(price already at/below the stage) do **not** append an entry.

| Field | Type | Notes |
|-------|------|-------|
| `ts` | str (ISO8601) | When the reduction was applied |
| `price` | float | New price sent to eBay |
| `previous_price` | float \| null | `ebay_offer.price` before the change |
| `stage` | int | reprice_schedule stage index that triggered it |
| `label` | str | Stage label, e.g. `retail`, `move` |
| `source` | str | Writer identifier; currently always `ebay_price_reducer` |

---

## Field flow diagram

```
intake
  sku, title, location, #STATUS, #VERIFIED, ai_hint, upc
    ↓
ai_identify
  title (overwrite), category, description, condition
  ebay_category_id, ebay_category_name, ai_identified
  product_lookup (barcode hit only)
    ↓
ebay_draft
  draft_listing.{title, category_id, condition_*, format, quantity,
                 item_specifics, description, listing_description,
                 aspects_*, quality, title_ai, title_flags}
  offline_draft (if API was unavailable)
    ↓ (parallel)
ebay_upload                     ebay_price
  ebay_photos                     ebay_offer.{price, target_price,
  draft_listing.imageUrls           price_source, price_comps, priced_at}
                                  draft_listing.price
                                  draft_listing.quality (re-score)
    ↓
ebay_stage  (operator reviews via `tgw staged`, then system runs stage)
  ebay_offer.{offer_id, status=UNPUBLISHED, staged_at}
  epid (if commerce.catalog.readonly scope granted)
    ↓
ebay_publish  (operator runs `tgw publish <SKU>`)
  ebay_listing.{listing_id, listing_url, offer_id, status=Active, api, published_at}
  ebay_offer.{status=PUBLISHED, published_at}
  reprice_schedule[{label, price, due_at}, ...]
    ↓
ebay_sync (periodic)
  ebay_listing.{live_price, synced_at, listing_status}
  ebay_offer.{category_id, quantity}

ebay_price_reducer (scheduled, reads reprice_schedule)
  ebay_offer.price (markdown)
  reprice_schedule[i].done_at
  price_history[] (append per applied reduction)
```

---

## Notes for future work

- **PP-GLOBALS-001**: Fields like `condition`, `ebay_category_id`, `location`, `weight` are offer-invariant (they'd be the same across all offers for this item). Design a `globals` block to avoid these being buried in `draft_listing`.
- **`ebay_sale`**: Field exists in some legacy items; not written by any current worker. Appears to be a legacy sold-price record.
- **`quality.to_dict()`**: See `src/tgw/listing_quality.py` for `DraftScore` field names inside the quality block.
- **Condition enum path**: `condition` (human) → `draft_listing.condition` → `draft_listing.condition_enum` (Inventory API) → `draft_listing.condition_id` (eBay numeric). `ebay_sync` uses the Inventory API status; `ebay_legacy_sync` uses `ebay_condition_number` from legacy items.
