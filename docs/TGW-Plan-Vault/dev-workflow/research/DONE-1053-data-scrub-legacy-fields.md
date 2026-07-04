# DONE — todo #1053: data-scrub legacy eBay Trading API fields

Confirmed with Dave 2026-07-04: the 15 target fields (`Item number`,
`#STATUS`, `attribute_set`, `m2_categories`, `category_ids`,
`ebay_condition_number`, `eBay category 1 name`, `eBay category 1 number`,
`C:Brand`, `C:Type`, `C:MPN`, `C:Model`, `C:Language`, `C:Movie/TV Title`,
`input_voltage`) are legacy eBay Trading API fields, not Magento — safe to
drop from the base record. Explicitly excluded 3 fields the task brief
originally listed (`title_history`, `description_history`,
`location_history`) after finding they're NOT legacy: 0/55,347 and
1,114/55,347 historical records have them respectively — they're live TGW
audit trails just started, not migration artifacts. Nothing under
`ebay_offer`/`ebay_listing`/`ebay_submitted`/`ebay_live` touched.

**Method:** `scripts/data_scrub_legacy_ebay_fields.py` — per-item, per-field
verification against `historical-tgwcatalog.json`/`historical-master-catalog.json`
before any deletion (never strips a field without a matching historical
value). Writes via `items.strip_fields()` (new helper, one archive entry
per item — invariant E5/#1104 protects every change).

**Result:** 20,419/55,419 items modified (37%, essentially all 2020+ stock —
the historical catalogs are ~70-100% complete for 2020+, ~0-1% for
2014-2019). **Zero real exceptions** — every field checked against history
either matched exactly or had no historical record at all; no contradictions
found anywhere. Full report: `/opt/TGW/var/log/data-scrub-1053-report.json`.

**Investigated extending coverage to 2014-2019 (three additional sources
checked, none panned out):** `/home/db/devices/porche/history/ItemData`,
`porche/history/ItemArchive`, and `/home/db/devices/blk1tb/ItemData` were
all checked — each draws from the same underlying ~55K-item snapshot
(only ~136-137 SKUs overlap the 33,516-item 2014-2019 cohort in each case).
Not a bug — Dave confirmed the porche consolidation ("nice full index") is
still in progress. Noted in `plan/PLAN-backup-dr.md` §6.5 for revisiting
once that lands.

**Side fixes made along the way:**
- `historical-master-catalog.json` permission aligned 600→664 to match its
  sibling `historical-tgwcatalog.json` (Dave-approved) — was blocking
  non-tgw reads of non-sensitive catalog data.
- 20,579/55,419 (37%) ItemData JSONs found at 0600 (tgw-only) vs the normal
  0664 fence-write default — pre-existing, unrelated to tonight's changes.
  Aligned to 0664 (Dave-approved) — unblocks any db-level tooling from
  reading the full dataset; no production impact since workers always ran
  as tgw regardless.

Full suite: 1771 pass / 1 skipped / 0 fail / 0 errors (was 1761 before —
+10 from #1104's E5 tests already counted; strip_fields itself reuses
existing test coverage, no separate script-level tests added given it's a
one-shot operational script, not a persistent code path).
