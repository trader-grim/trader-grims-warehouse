# Gemini Task 006 — Marketing & Category Insights

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Expected output:** `GEMINI-006-result.md` in `docs/TGW-Plan-Vault/inbox/`

> Companion to GEMINI-TASK-005 (pricing). Same dataset, different lens: 005 asks "are we priced
> right"; 006 asks "where should we point inventory effort and listing quality." Run 005 first if
> you can — you can reuse its category clustering.

---

## Context

TGW is inventory-constrained by operator time, not by stock (there's a 163 GB archive and ~55K
listings). The lever that matters is **where to spend listing/photography/pricing effort** for the
best return. Velocity data tells us what actually sells; the category groups tell us how we bucket
it. We want the marketing/merchandising signal hiding in that data.

## Data (read locally)
- `/opt/TGW/data/ItemCatalog/velocity-stats.json` — per-category sold count, active count, sold-
  price stats, sell-through.
- `/opt/TGW/config/category-groups.json` — the 25 groups (store_category, ai_hint, pricing).
- `/opt/TGW/data/ItemCatalog/tgwcatalog.db` (table `catalog`) — current inventory distribution by
  category/status (`data` JSON carries `ebay_category_id`, `category_group`, draft quality fields
  where present).

## Your task — extract the merchandising signal
1. **Untapped velocity.** Categories with high sell-through but where TGW currently lists few
   items relative to demand — i.e. "list more of this." Quantify the gap (sold rate vs our active
   count).
2. **Price elasticity by category.** Where the data suggests buyers are price-sensitive (sell-
   through collapses above a threshold) vs price-insensitive (sells regardless) — informs whether
   to chase the launch-high or move to floor fast.
3. **Category-group store mapping.** Which groups lack a `store_category` mapping but have enough
   volume to deserve a dedicated eBay Store category for browsability/SEO.
4. **Seasonality / timing hints** — if the velocity data carries any time signal, note categories
   with seasonal demand (toys/holiday, etc.) worth timing.
5. **Listing-quality leverage.** Cross-reference (where draft quality scores exist in the item
   JSON) which high-velocity categories have the *weakest* listing quality — those are the highest
   ROI for title/specifics/photo improvement.
6. **SEO term opportunities per top category** — common high-value keywords/aspects worth baking
   into titles for the top 15 categories (TGW also gets SEO value from its globally-unique SKUs —
   note where SKU-as-search-term matters).

## Output (`GEMINI-006-result.md`)
- **"List more of this" ranked table** (top 20) with the demand-vs-supply gap.
- **Price-elasticity notes** per tier/category cluster.
- **Store-category recommendations** (which groups need one).
- **Listing-quality ROI list** — high-velocity + weak-quality categories.
- **Per-category SEO term suggestions** for the top 15.
- **A short "if you only do five things" merchandising action list** for the operator.

## Constraints
- Read-only, report only. No file edits, no git.
- Keep recommendations concrete and ranked — the operator works from a phone/tablet and needs a
  short prioritized list, not an essay.
