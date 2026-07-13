Status: cleared
Reviewer: Claude (runner-review)
Todo: #1314   PP: PP-COHESION-001
Checked: diff (`git diff 714de85 todo/1314-offers-unresolved-sku-finding`)
against the todo brief's stated bug, scope (offers.py + test file only),
result manifest completeness. Independently verified the premise: traced
`_log_offer_history`'s sole call site (cmd_offers_respond, line ~203) —
confirmed it fires only after `respond_to_best_offer(...)` returns without
raising, i.e. only on eBay-side success, exactly as claimed.
Summary: new `_UNRESOLVED_REGISTRY` JSON file
(`/opt/TGW/var/offers-unresolved.json`), same pattern as
`ebay_sku_migrate._BLOCKED_REGISTRY`, keyed by offer_id, tracks
attempts/first_seen_at/last_attempt_at/resolved — explicitly retry-friendly
per the packet's requirement. `_resolve_unresolved_offer()` provided
(unused by current code, intended for a future repair pass) — reasonable
to include since it's the symmetric clear-path and is itself tested.
Correctly did NOT build the retry worker itself (out of scope per packet);
filed follow-up todo #1373 for that rather than deciding silently. Registry
write wrapped in try/except so a persistence failure can't crash the
already-succeeded eBay response path — sound design (matches this
project's "persisting the finding must never break the caller" pattern
seen elsewhere in this batch). Test coverage is thorough: end-to-end
success-with-unresolved-SKU scenario through cmd_offers_respond, retry
bump, resolve/clear, and a negative check that resolved lookups touch
nothing. Full suite green modulo the known #1370 flake. No triggers fired.
Cleared for stitch.
