---
title: eBay Category Quirks
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# eBay Category Quirks

Living document — add entries as new quirks are discovered. Each entry should note
the category ID, the problem, and how TGW handles it.

---

## Fulfillment Policy Overrides

Default policy is **FC4** (`199931446015`) for most categories.
Seven categories require different policies — configured in `tgw-api-config.json`
under `fulfillment_policy_by_category`.

| Category ID | Category Name | Policy ID | Reason |
|-------------|---------------|-----------|--------|
| `88758` | Stamps | `223550459015` | Lightweight; standard envelope eligible |
| `280` | Postcards | `213431337015` | Flat/envelope items |
| `31740` | Barware | `252109696015` | Fragile; different rate structure |
| `60115` | Kitchenware | `252109696015` | Same fragile policy |
| `2036` | Pottery & Glass | `252109696015` | Fragile |
| `52365` | Figurines | `213957220015` | Collectible fragile |
| `261672` | Decorative Collectibles | `186871591015` | Collectible |

**10 items migrated with wrong FRE profile** (eBay Standard Envelope) in categories
7317 (Game Pieces) and 261068 (Action Figures) — manual Seller Hub fix pending.
See Operator TODO in master plan.

### Standard Envelope gate (PP-FULFILLMENT-001)

eBay Standard Envelope (FRE) requires:
- **Thickness ≤ 0.25 in** (¼ inch) — the most commonly violated limit
- **Uniform thickness** — no lumps, bumps, or hard objects (coins in a sleeve = fail)
- Max size: 6⅛ × 11½ in; weight ≤ 3.5 oz

`_resolve_fulfillment_id()` enforces this as a **size/thickness gate**:
- Config key `fulfillment_policy_envelope` holds the Standard Envelope policy ID
- A `flat` size_class item gets the envelope policy **only if** `thickness_in` is set on
  the item JSON **and** its value is ≤ 0.25
- Items with unknown thickness (field absent or null) fall through to the regular
  `fulfillment_policy_by_size_class` or global default — assign envelope explicitly via
  `shipping_profile` if you've physically verified the item fits
- Items with `thickness_in > 0.25` never receive the envelope policy automatically

**To enable:** add `"fulfillment_policy_envelope": "<policy_id>"` to `tgw-api-config.json`.
**To override per-item:** `tgw set-shipping SKU envelope` (uses `shipping_profile`).

---

## Condition Quirks

### conditionId 3000 — four different buyer labels
The same condition ID displays differently across categories:
- "Used" (general)
- "Pre-owned"
- "Pre-owned - Good"
- "Open Box/Used"

`draft_listing` stores the label alongside the ID so the correct text is shown.
Never assume the label from the ID alone.

### Granular condition rejection (errorId 25021)
Many categories only accept conditionId 3000 and reject more granular IDs
(e.g. `USED_EXCELLENT` = 3010, `USED_GOOD` = 5000, `USED_ACCEPTABLE` = 6000).

**TGW handling:** `best_condition()` in `apis/ebay/conditions.py` walks the condition
rank table same-or-worse until it finds an accepted condition for the category.
`ebay_stage` and `ebay_publish` also retry with `USED_EXCELLENT` on 25021 as a fallback.

### Condition policy cache
- 26 unique condition policy sets covering ~15K categories
- Cached at `catalog_root/ebay-condition-policies.json`
- Refreshed every 7 days by `conditions.py`

---

## Listing / Offer Errors

### errorId 25002 — Item.Country (open issue)
Some categories require `shipToLocations.regionIncluded` in the offer body.
Affected categories observed: **34032**, **14027**, **13916**.
`_build_offer_bodies` now includes `regionIncluded` permanently — if still seeing
this error, check whether the category requires additional location fields.

### errorId 25709 — Missing Content-Language header
All Inventory API PUT/POST calls require `Content-Language: en-US`.
Fixed globally — all workers add this header. If this error recurs, check any new
API call paths that bypass the shared client.

### errorId 10001 — Finding API blocked
Finding API discontinued early 2025. Do not attempt to use `findCompletedItems`.
See PP-PRICE-001 notes for alternatives.

---

## Category-Specific Pricing Notes

Categories with thin Browse API comps — fallback prices in `category_price_defaults`:

| Category ID | Default Price | Notes |
|-------------|---------------|-------|
| `280` (Postcards) | $9.99 | Low per-item value; comps often thin |
| `61312` | $8.99 | |

Expand this table as more thin-comp categories are identified.
Edit `tgw-api-config.json → category_price_defaults` to add entries.

---

## eBay Store Categories (PP-STORE-001 — pending)

Store category support not yet implemented. Items are filed into the default
store section. See PP-STORE-001 in master plan for implementation plan.

---

## Notes on Specific Categories (add as discovered)

### Trading Cards / CCG
- Condition granularity: typically accepts granular conditions (Graded, Near Mint, etc.)
- High sensitivity to specifics completeness (Set, Card Name, Grade)
- Pricing: use JustTCG API (PP-LOOKUP-001 Tier 1) for market price data

### Books / Media (ISBN items)
- ISBN present → use Open Library for structured metadata (PP-LOOKUP-001)
- Condition: "Good" and "Acceptable" are standard and widely accepted
- Specifics: Author, ISBN, Subject all help search placement significantly

### Video Games
- Condition: platform-specific condition policies; most accept granular conditions
- Specifics: Platform, Game Name, Rating all REQUIRED or strongly recommended
- IGDB API provides platform + genre data (PP-LOOKUP-001 Tier 1)

### Vinyl / Music
- Discogs barcode lookup returns full release metadata including tracklist
- Condition: "Very Good Plus (VG+)" is common grading terminology — map to USED_EXCELLENT
