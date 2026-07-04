# DONE — PP-PHOTOSYNC-001 P5 (todo #1120): operator price never machine-overridden

`workers/ebay_price.py::handle` now refuses to compute/overwrite a price when
`price_history[-1].source == 'operator'` and the job lacks `origin: 'operator'`
— a chain-enqueued auto-price job can no longer clobber a manually-typed
price. The Re-price button's own `origin: 'operator'` stamp is the consent
signal that still lets it override its own prior entry. Skip persists a
durable finding (`ebay_offer.price_guard_skipped`, invariant C11) instead of
a log-only no-op. New invariant C6.5 in invariants.md. 4 new tests in
`tests/test_invariants_pricing.py`, all pass.

**Flagged gap:** could not additionally demo this against a real production
SKU via the authenticated HTTP path — finding the API key to call the live
endpoint was correctly blocked by the session's credential-exploration guard.
Worker-level tests exercise the real `ebay_price.py` code path (only fence
I/O + comps HTTP faked, per this repo's existing worker-test convention).
Flagging for Dave in case a real-SKU live-fire demo is wanted before this is
considered fully closed. Full detail in `plan/pp/PP-PHOTOSYNC-001.md` P5.
