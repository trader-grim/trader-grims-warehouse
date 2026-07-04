# DONE — #1114 auto-redraft-clobbers-operator-edit, root-caused and fixed

Investigated per Dave's explicit request ("verify why we did it that way
before changing"). Root cause: the PATCH auto-enqueue trigger in
http_server.py conflated "a raw fact changed, regenerate" with "the
operator polished the final draft directly" under one condition. Grepped
the actual editor UI JS: it only ever PATCHes into `draft_listing.*`
directly (condition, shipping, aspects, price, title, description all
live there) — no code path sends bare top-level title/item_attributes
through this endpoint. So the trigger, as implemented, ALWAYS meant "the
operator just finished editing," never "a raw fact changed" — meaning
regeneration was never the correct response.

Fixed: mirrors the existing "Update Listing" button exactly now — push
(ebay_stage, force=True, origin=operator) instead of regenerate
(ebay_draft). Verified against logs (zero hits for the old auto-enqueue
log line across the full 3-day retained journal — this bug hadn't
actually fired yet in production, so no live listing was harmed by it,
but the code path was real and would have fired on the next operator
edit to a live item).

Cost quantified per Dave's question: each needless regen = 2 wasted AI
calls (primary draft + bulk_classify aspect-fill); a typical 2-3-edit
polish session tripled the cost of a step that should be free.

Live-verified end to end against a real published listing
(tgw201501021970354): real PATCH → confirmed ebay_stage enqueued (not
ebay_draft) → confirmed via fresh uncached API call that the real eBay
listing's title changed → reverted cleanly. 3 new tests, full suite
1817 passed.

Side note: mistakenly marked todo #1084 (PP-LISTEDITOR-001 R1.1
live-fire) done while working this — that was the wrong todo; R1.1's
actual price-only-delta test was never run. Reopened as #1137.
