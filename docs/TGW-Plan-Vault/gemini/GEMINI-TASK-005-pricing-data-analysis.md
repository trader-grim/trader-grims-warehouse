# Gemini Task 005 — Pricing Data Analysis

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Expected output:** `GEMINI-005-result.md` in `docs/TGW-Plan-Vault/inbox/`

> Why you (Gemini): this is a large-context numeric crunch over the full velocity dataset (1,540
> categories) + the 25-group taxonomy — a 1M-context strength. Read the data files locally.

---

## Context

TGW prices items in stages: **launch** = 110% of the max comp, rounded to `.99`; then a scheduled
markdown reducer walks the price from ~p75 (day 3) down toward ~p25 (day 17). When comps are thin,
`suggest_price()` falls back to the item's **category group** typical price × a condition factor,
with a hard `floor`. So pricing quality depends on (a) per-category sold-price reality and (b)
how well the 25 group buckets are calibrated to it.

## Data (read these files locally — they are the ground truth)

1. **`/opt/TGW/data/ItemCatalog/velocity-stats.json`** (~620 KB, 1,540 eBay categories). Per
   category: items sold, items active, sold-price median + p25 (and where present p75), sell-
   through signal. This is actual TGW sales history, the ground truth.
2. **`/opt/TGW/config/category-groups.json`** (25 groups). Each group: `ebay_categories` (the
   eBay category IDs it covers), `size_class`, `ai_hint`, and `pricing` (`floor`, `typical_used`,
   `typical_new`, `source`). Top of file: `condition_factors` and `global_floor`.
3. (Optional, if useful) the catalog DB `/opt/TGW/data/ItemCatalog/tgwcatalog.db` table `catalog`
   for current live prices per category (`price` column, `data` JSON has `ebay_category_id`).

## Your task — find the pricing signal

1. **Group calibration audit.** For each of the 25 groups, compare its `pricing.typical_used` /
   `floor` against the actual velocity p25/median of the eBay categories it contains. Flag groups
   that are **mis-seeded** (group typical materially above or below real sold prices → we're
   over/under-pricing the comps-thin tail). Quantify the gap and recommend a corrected number.
2. **High-opportunity categories.** Rank categories by *sell-through × price* to surface where
   volume and margin coincide — and the inverse: categories with lots of active inventory but low
   sell-through (dead stock / over-priced).
3. **Condition-factor sanity.** Are the `condition_factors` (new 1.5 … for-parts 0.3) consistent
   with the spread you can infer between new vs used sold prices per category? Recommend tweaks if
   the data disagrees.
4. **Pricing tier patterns.** Cluster categories into pricing tiers (e.g. sub-$10 quick-move,
   $10–30 core, $30+ considered) and describe how reducer strategy should differ per tier (the
   operator has said low-priced items should move aggressively/fast).
5. **Floors.** Identify categories where the current group `floor` is too low (we list below true
   market) or too high (we never sell).

## Output (`GEMINI-005-result.md`)
- **Mis-seeded groups table**: group | current typical_used/floor | data-derived value | gap | rec.
- **Opportunity ranking**: top 20 high sell-through × price categories; bottom 20 dead-stock.
- **Condition-factor recommendation** with the evidence.
- **Pricing-tier model** + per-tier reducer guidance.
- **Concrete `category-groups.json` edits** as a list of `group → field → new value` so Claude
  can apply them (and a `tgw category-groups --reseed` note where a reseed is the right tool).

## Constraints
- Read-only. Produce a report + concrete recommended edits; do **not** modify
  `category-groups.json` or the DB. Claude applies edits through the platform.
- Show your arithmetic / methodology briefly so the recommendations are auditable.
- Don't commit to git.
