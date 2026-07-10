# IN PROGRESS — #1135 repeatable category recompile

Dave: "My dataset was built using a dump it all into a flat structure just
to capture the data format. Worked ok, but we have better tools and more
data and we can recompile a better dataset. Build it like we are going to
go back in with a stronger dataset every so often."

Designing `scripts/recompile_category_backfill.py` as a re-runnable job,
not a one-shot: modular source providers (currently
historical-master-catalog.json sku_old join, historical-tgwcatalog.json
direct join, searchcatalog.csv ebaycat), additive-only (never overwrites
an item that already has a real `draft_listing.category_id`), dry-run
default, per-item provenance in the report so future runs/new sources are
auditable against what already got recovered.

Confirmed scope from the investigation just completed: 26,709 items lack
a real category_id; 5,367 (20%) recoverable from the three known
structured sources today. Script is built so re-running it later (after
new sources land, e.g. a live eBay Taxonomy sweep or comping-interface
captures) picks up more without re-processing what's already fixed.

**DONE — corrected + built + applied.** Discovered my initial investigation
checked the wrong field (attribute_set, Magento taxonomy) instead of the
real pricing-relevant field (ebay_category_id/draft_listing.category_id).
Corrected: 52% of the catalog already has a real category (28,710/55,419),
not 29%. Built `scripts/recompile_category_backfill.py` as a repeatable
job per Dave's direction — modular sources, additive-only, safe re-run.
Applied live: 5,367/5,367 recovered items updated, 0 errors, confirmed
idempotent (re-run reports 0 recoverable). Added `items.set_fields()`
fence helper (4 tests) + 4 source-loader tests. Full suite 1810 passed.
