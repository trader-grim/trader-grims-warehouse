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
