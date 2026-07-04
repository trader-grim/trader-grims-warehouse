# DONE — PP-PHOTOSYNC-001 P9 follow-up (todo #1127): photos_short_on_ebay re-pointed at live truth

`photos_short_on_ebay` now prefers a `sku -> live photo count` index built from
the freshest R1.8-style whole-site capture (`incoming/ebay/*.jsonl.gz`) over
the local `draft_listing.imageUrls` mirror, falling back per-SKU when a SKU
is missing from the capture or the capture is >24h stale. No live eBay call
happens inside the scan path (kept P7's zero-API-cost invariant).

Live-verified 2026-07-04 against the real in-progress R1.8 capture (#1122):
all 19,486 SKUs indexed correctly; `tgw catalog-verify --limit 500` ran clean.
5 new unit tests. Full detail + one flagged scoping deviation (no automatic
live-refresh fallback) in `plan/pp/PP-PHOTOSYNC-001.md` P9 follow-up section.

Open question for 2pm triage: should refreshing this capture become a
recurring nightly job, or was R1.8 a one-time backfill? Not filed as a new
todo — flagging for Dave's call.
