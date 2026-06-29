# INPROGRESS: Data recovery complete → eBay backfill

Session 30 — 2026-06-28

## Completed this session
- 49 May 2021 missing item JSONs recovered from ItemArchive, normalized to current TGW schema, eBay offer/listing data backfilled
- 19 additional uncatalogued archive items restored to ItemData (status: Not Listed)
- 1 empty archive item (`tgw202210091421313`) skipped — no data
- All items checked for ISBN duplicates — only one confirmed duplicate (The Enforcer, two physical copies, different photos/bins, both kept)
- Invariant E5 (no delete without archive) documented in invariants.md
- Thermal watchdog running, 30-min snapshots active

## Where we are now
Tasks 1–3 done. Task 4 (eBay backfill for all 19,366 Inventory API items) is starting.
The 49 normalized items already have their offer_id/listing_id written (done inline).
The broader backfill covers the remaining ~19,317 items that still have empty ebay_offer blocks.

## Next after backfill
PP-FENCE-001 Session A — fence endpoints in http_server.py.
