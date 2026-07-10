# Session 38 — Dead-letter triage, ebay_sync 25707 fix, SEO title filler demotion

**Date:** 2026-06-30  
**Status:** DONE — committed as `382f3f0`

## What was done

### SEO title filler demotion (`seo/title.py`)
- `_demote_leading_filler()` implemented: Case A moves pure filler leads (Vintage, Antique, etc.) to end; Case B moves year+filler cluster (e.g. "1983 Vintage") to positions 4–5 so the first three words remain content-rich.

### eBay status record fixes
- `ebay_stage.py`: force-update of a live listing now preserves `ebay_offer.status = 'PUBLISHED'` instead of unconditionally writing `UNPUBLISHED`.
- `ebay_publish.py`: `ebay_offer.price` now set from `staged_price` (what was actually PUT to eBay) instead of the reprice schedule's launch_price.

### ai_identify dead-letter fix (`ai_identify.py`)
- `_USER_PROMPT_HINTED` and `_USER_PROMPT_ENRICHED` contain literal JSON `{}` braces. `.format()` treated them as placeholders → KeyError. Switched to `.replace('{hint}', hint)` / `.replace('{product_context}', product_context)`.

### ebay_sync 25707 workaround (`ebay_sync.py`)
- `fetch_all_offers()` bulk GET fails globally with eBay error 25707 (non-alphanumeric SKU pre-flight validation).
- Root cause: orphaned draft offer on eBay with SKU `"Murder on the Middle Fork by Don Ian Smith and Naida West"` (sku_old of `tgw201607172015419`). No backing inventory_item; not visible in Seller Hub.
- Fix: catch 400/25707 and fall back to `_fetch_offers_by_local_skus()` — iterates all local items with offer_ids and fetches each individually. Slower but correct.
- todo #1077 created: contact eBay support to purge the orphaned offer (Inventory API DELETE also rejected with 25707).

## What is still open

- **todo #1077** — eBay support contact to purge orphaned offer.
- `ebay_stage`/`ebay_upload` KeyError('api_key') for `tgw202605051933258` — not investigated.
- `ebay_stage` "ImageLinks cannot exceed" for same item — not investigated.
- 15 stale `catalog_rebuild` dead-letters from 2026-06-28/29 — likely path errors; may need cleanup.

## Next step

Run `tgw dead-letter` to check current queue state. Investigate `tgw202605051933258` api_key error. Consider flushing the stale catalog_rebuild dead-letters.
