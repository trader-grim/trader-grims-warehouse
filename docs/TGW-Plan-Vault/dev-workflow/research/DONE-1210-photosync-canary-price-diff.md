# DONE — todo #1210 (audit#1143)

`scripts/photosync_canary_probe.py`'s `_live_snapshot()` stringified the
price field (`str(price) if price is not None else None`) while
`_intent_snapshot()` left it numeric (`dl.get('price') or item.get('price')`).
`live_price`/`price` are stored as `float` throughout the codebase (confirmed
in `ebay_stage.py:263`, `ebay_sync.py:406`) — so `_diff()` compared e.g.
`19.99 != '19.99'` and reported a spurious mismatch on every priced item,
even when the price genuinely matched. Per the todo brief, this trains
operators to ignore canary alerts.

## Fix
- `_live_snapshot()`: stopped stringifying — `price` stays the same numeric
  type as everywhere else it's stored.
- `_diff()`: added `_normalize_price()` (round to cents, `None`-safe) and
  compare price through it rather than raw `==`, mirroring the existing
  `_normalize_aspects()` pattern — guards against float-precision noise
  (e.g. `9.99` vs `9.990000001`) without reintroducing a string/numeric
  type mismatch.

## Tests
Added to `tests/test_photosync_canary_probe.py`:
- `_normalize_price` treats numeric and string forms of the same value as equal
- `_normalize_price(None) is None`
- `_diff()` no longer flags a genuinely matching numeric price (the
  regression case for #1210)
- `_diff()` still catches a real price drift

`pytest -q tests/test_photosync_canary_probe.py tests/test_ops_digest_canary_probe.py`:
12/12 pass. Full suite: 1950 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (read-only, no mutation)
Ran `_intent_snapshot`/`_live_snapshot`/`_diff` directly against real item
JSON docs with a live `ebay_listing.live_price` set:
- 4 of 5 checked items have a genuinely matching intent/live price — under
  the old bug all 4 would have shown as a spurious price mismatch
  (`str(live_price) != intent_price`); with the fix, `_diff` correctly
  reports `[]` for price on all 4.
- The 5th item has a real price discrepancy (intent 19.99 vs live 14.99) —
  `_diff` still correctly flags it, confirming the fix didn't mask real
  drift while eliminating the false positives.

No deviations from the todo brief. No config/secrets/OAuth scopes touched.
