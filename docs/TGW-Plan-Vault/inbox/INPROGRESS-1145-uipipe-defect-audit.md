# IN PROGRESS — #1145 PP-UIPIPE-001: web UI listing pipeline defect audit

Dave's feedback (2026-07-04, s45): recent real listings made through the web
UI pipeline came out wrong — "the web ui pipeline ain't cutting it":
1. Wrong shipping policy on a new listing
2. Item PUBLISHED with no price set in the interface (C1 violation — guard
   bypassed, or price existed in draft_listing but never rendered = display
   bug masquerading as guard failure)
3. Only one photo uploaded, no reattempt (NB: #1115 P1 completion-guard fix
   is marked done — check deploy timing vs listing timing, and whether the
   fix covers the new-listing path or only repair)
4. "etc." — full list to be captured with Dave.

Plan: evidence assembly per item (item JSON + queue_jobs history + journal +
raw E7 fence captures) → defect→root-cause→packet map with Dave → invariant/
detector per root cause (PD5). Awaiting SKUs/item numbers or listing window
from Dave. Possibly some defects predate today's API-provider chaos but Dave
says none are explained by it.

## Evidence sweep (2026-07-04, pre-walkthrough) — Dave's hypothesis CONFIRMED

Dave's guess: "all of them boil down to pipeline order or function combined
and especially mishandling of the draft vs ebay offer." Sweep of the 10
distinct SKUs published in the last 14 days:

1. NO-PRICE PUBLISH confirmed: tgw202605052336026 — local draft_listing.price
   = None, live offer 265455614018 at $40.99 (raw fence capture 2026-07-04).
   UI renders draft (empty); eBay serves offer ($40.99). Local dataset does
   NOT hold the live price — Data Charter inversion.
2. PHOTO PARTIAL confirmed: tgw202605060125081 — 8 local photos, 1 on eBay,
   published 07-04 AFTER #1115 P1 fix marked done → fix gap or new-listing
   path not covered.
3. SHIPPING POLICY pattern: 9/10 items share fulfillment policy 199931446015
   regardless of category (1133367 has 199931450015) → selection likely
   ignores per-category overrides. Awaiting Dave's pointer to the wrong one.
4. SYSTEMIC: same SKUs have dozens of succeeded ebay_publish jobs (07-01/02)
   — C3 says publish is one-time operator-gated; it is silently re-runnable.
5. SYSTEMIC: no published item has a published/live status locally (None/
   Ready/New; three items lack the field entirely) — state machine never
   advanced on publish.

Unifying diagnosis: draft_listing (local intent) vs eBay offer (remote
reality) have no reconciliation discipline in the new-listing path. Proposed
spine for the plan item: publish-time draft==offer verification invariant +
catalog-verify divergence detector + per-defect packets.
