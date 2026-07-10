# INPROGRESS: PP-PHOTOSYNC-001 P9 — bulk truth source within existing scopes

Todo: #1125. Per packet spec in `plan/pp/PP-PHOTOSYNC-001.md`.

## What I'm doing
Researching + live-verifying the cheapest bulk eBay read source reachable with
EXISTING scopes (sell.inventory, sell.account, sell.marketing, Trading IAF —
LOCKED, never request new). Candidates to check, in order: Feed API
ACTIVE_INVENTORY_REPORT (sell.inventory family, ~3 calls for the whole site),
Trading GetSellerList/GetMyeBaySelling (paged, ~100 calls, includes photo
details), Inventory API paged getInventoryItems (already used by R1.8, ~100
calls, no photo/listing-status truth beyond what we already pull).

## Where I am
Starting: checking current scope grants + existing eBay API landscape docs,
then attempting a live Feed API task creation as the cheapest-candidate test.

## Next step if interrupted
Check `reference/eBay-API-Landscape.md` for what's already documented about
Feed API. If a live Feed API call was attempted, check
`/opt/TGW/incoming/ebay/<date>.jsonl.gz` for a captured response and quota
state for the call count. Deliverable is a ranking note + a re-pointed P7
live-source check — not a big worker rewrite.

## Done
Winner: Inventory API bulk `getInventoryItems` (paged, limit=200) — ~98 calls
for all 19,486 items, live-verified to already include `product.imageUrls` per
item in the bulk LIST response (no per-SKU offer call needed). ~200x cheaper
than R1.8's per-SKU approach for photo-truth specifically.

Feed API `ACTIVE_INVENTORY_REPORT` confirmed BLOCKED on scope (`sell.item.feed`,
not granted) — the packet's own hypothesis was wrong, recorded so it isn't
re-proposed. `GetMyeBaySelling` is already live daily but narrower than
assumed: skips inventory-API items entirely and never extracts PictureDetails
from its own already-captured raw XML.

Full ranking: `reference/eBay-Bulk-Audit-Sources.md`. Follow-up filed as todo
#1127 (not built yet): re-point P7's `photos_short_on_ebay` at the bulk list
instead of the local mirror.

## Live verification (PD4)
Live call against real eBay: `total: 19486` (matches expected), sample items
show real imageUrls counts (24, 8, 5). Captured via E7 (confirmed in today's
incoming/ebay/*.jsonl.gz). Cost: 1 call, visible in quota-state.json under
caller `p9-live-verify`, against a 2,000,000/day budget.

## Status: COMPLETE (research + live verify, no code changes — pure research
## packet as scoped). Todo #1125 done. Todo #1127 filed for the follow-up.
## Next: no more open packets ahead of P2/P3/P4/P5/P6/P8/#1126/#1127 in this
## track (all remaining are either gated on P4 or independent XS/S items).
