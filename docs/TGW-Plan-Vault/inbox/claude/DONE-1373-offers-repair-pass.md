Working todo #1373 (PP-COHESION-001, invariant C11, follow-up from #1314) in
isolated worktree `/opt/TGW/var/worktrees/1373-offers-repair-pass` on
branch `todo/1373-offers-repair-pass`. Task: offers.py's unresolved-Best-Offer
registry (offers-unresolved.json, #1314) has no catalog-verify detector or
repair pass yet — add a detector + retry mechanism so unresolved entries get
regularly checked/repaired, matching invariant C11's full pattern (as built
for legacy_listing_blocked/legacy_listing_unrepaired). Part of the follow-up
cleanup batch (#1369-1374).
