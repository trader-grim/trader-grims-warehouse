# INPROGRESS: ebay_sku_migrate blast — 3,203 non-canonical SKU migrations

Session 33 — 2026-06-28

## What was done

- Confirmed eBay backfill is complete: 19,785 items have listing_id (matches eBay's ~19,736 active+sold)
- Identified ebay_sku_migrate was crawling at 5/batch per hour (~multi-month pace)
- Added `ebay_sku_migrate` config block to `tgw-api-config.json`: batch_size=100, interval_hours=0.05
- Started worker at ~13:56 — worker is running, processing at ~8 seconds/item via inventory_live path
- 3,203 live non-canonical SKUs to migrate; estimated ~7-8 hours to complete
- todo #1069 tracks this work

## Current state

Worker is running under systemd. Check with:
```
sudo journalctl -u tgw-worker@ebay_sku_migrate.service -f | grep -E "OK |FAILED|batch complete|no live"
```

When complete, the worker logs: `no live non-canonical items remain — done` and stops self-scheduling.

## What is still open

- **83 skip-flagged items** (permanent failures: Best Offer policy, qty=0 errors) — need operator review after migration completes
- **All other workers still stopped** — do NOT restart until migration is done
- **ebay_sync still returns 0** — separate issue, tackle after workers restart
- **Worker restart sequence** (once migration done): catalog_rebuild → thumbnail_gen → pm_intake → plan_render → ai_identify → ebay_draft → ebay_upload → ebay_price → ebay_stage → ebay_publish

## Next step

Wait for migration to complete, then restart workers in sequence and verify each.
Check #STATUS normalization (todo #1053) and the 83 blocked items.
