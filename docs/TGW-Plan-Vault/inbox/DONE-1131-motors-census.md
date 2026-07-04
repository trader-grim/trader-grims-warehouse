# DONE — todo #1131: Motors census from R1.8 capture

`scripts/ebay_motors_census.py` parsed all 48,885 offer records across every
`incoming/ebay/*.jsonl.gz` capture file (R1.8 snapshot #1122 + prior
captured activity) — zero eBay API calls. Census written to
`reference/ebay-marketplace-census-2026-07-04.md` (the 2pm planning input
for PP-EBAY-MOTORS-001).

**Findings:**
- 19,448 unique SKUs with a marketplaceId; EBAY_US 48,455 offer records,
  EBAY_MOTORS 430.
- **202 distinct EBAY_MOTORS SKUs** — full list in the census doc, each
  with its offer_id(s).
- **Zero cross-marketplace duplicate-listing risk** — no SKU was seen under
  more than one marketplaceId across any captured record.

**Applied:** all 202 Motors SKUs patched with `marketplace_id: EBAY_MOTORS`
via the fence (`items.update_item`) — E5-protected (each write archived
before overwrite). 0 not found in ItemData. Dataset growth per Prime
Directive 1: this field didn't exist anywhere in ItemData before.

3 unit tests for the capture-parsing logic. Full suite: 1779 pass /
1 skipped / 0 fail / 0 errors (was 1776).

This closes out the overnight photosync/R1.8 track entirely — #1122 and
#1131 were the last two items.
