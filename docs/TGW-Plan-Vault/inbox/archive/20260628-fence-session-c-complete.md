# PP-FENCE-001 Session C — COMPLETE

**Date:** 2026-06-28  
**Status:** DONE — workers now unblocked

## What was done

All 18 inline atomic_write_json call sites in http_server.py action handlers and photo
management endpoints consolidated through three internal fence functions:

- _apply_patch(json_path, fields) — core patch with deep-merge for dict fields, None=delete
- _apply_ebay_write(json_path, sku, *, ebay_offer, ebay_listing, ...) — eBay block merge with protection
- _enqueue_catalog_rebuild(reason) — coalesced 30s-delayed catalog rebuild

The canonical endpoints (PATCH /api/items/{sku}, POST /append, POST /ebay-write, POST /api/items)
now delegate internally to these same helpers. No code in http_server.py calls atomic_write_json
outside these three functions and the create endpoint.

## Smoke test passed

- Archive action on tgw202605082015286 via live server -> status=archived, catalog_verified stripped
- http_server.py syntax OK, 27+ test passes (item_detail failures pre-existing/unrelated)

## Workers now unblocked

Proceed with worker restart sequence:
1. catalog_rebuild — already running
2. thumbnail_gen
3. pm_intake
4. plan_render
5. ai_identify (verify one job)
6. ebay_draft -> ebay_upload -> ebay_price -> ebay_stage -> ebay_publish

## Still outstanding

- #STATUS normalization migration (todo #1053 - strip Magento fields)
- ebay_sync fix (fetch_all_offers returns 400/25707 - replace with inventory_item paging)
- End+relist gate test: end a live listing via UI, relist it
