# IN PROGRESS — Track 2: local-infra queue (parallel to Track 1 photosync/R1.8)

Dave-approved second work track, entirely separate from the eBay-quota-sensitive
photosync/R1.8 critical path (Track 1, still running #1122 snapshot + gated
#1131 Motors census). All Track 2 items are local-only, zero eBay/quota calls.
Same commit-per-packet + push pattern as Track 1, confirmed by Dave 2026-07-03.

## Queue (order)
1. #1053 — data-scrub: strip legacy Magento fields from item JSONs
2. #1079 — PP-CATPICK-001 Phase 1: backfill category_candidates from tree cache
3. #1104 — enforce invariant E5 in code (archive-before-delete/overwrite at fence)
4. #1112 — 'Eligible for listing' filter: feature already implemented (SQL +
   frontend chip both wired) — gap is zero test coverage; this packet adds tests only
5. #1086 — PP-CLIP-001 conceptual planning pass (design doc, unblocks frozen
   rofi picker phase) — last item; if usage remains after this, next track TBD

## Reconciliation note
Dave will review/verify everything (both tracks) at the 2pm 2026-07-04 planning
session with Fable. Don't wait for approval mid-stream — proceed per this queue.
