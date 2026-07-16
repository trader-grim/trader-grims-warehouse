Todo #1314 (PP-COHESION-001, invariant C11) — DONE, stitched. offers.py now
persists Best-Offer responses that succeeded against eBay's live API but
couldn't resolve a local SKU, to a retry-friendly JSON registry
(offers-unresolved.json), instead of a bare log.warning + silent drop.
Reviewed clean, full suite green. Filed follow-up todo #1373 (build the
actual repair/retry worker — explicitly out of this packet's scope).
