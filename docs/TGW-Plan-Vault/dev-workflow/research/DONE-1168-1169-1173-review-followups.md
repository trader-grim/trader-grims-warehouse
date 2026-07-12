# DONE — code-review follow-ups on todos #1168/#1169/#1173

`/code-review` (medium effort, 8 finder angles + verification) on the commits
for #1168, #1169, #1173 surfaced 3 confirmed findings (one candidate — a
suspected sibling `QuotaBudgetExceeded` swallow in `taxonomy.py` — was
REFUTED: that fail-open behavior is a deliberate, documented design decision
for taxonomy-tree lookups, not an oversight).

## Fix 1 — ebay_sku_migrate.py over-blocked a self-healing partial state
The #1169 fix made `handle()` block on *any* `ebay_done=True` failure, but
`ebay_done=True` covers two very different situations:
- **Local rename failed after eBay fully succeeded** — no automatic
  self-heal path exists; correctly stays blocking.
- **Publish failed after the old offer was already deleted** (in
  `_migrate_inventory`, `_migrate_inventory_live`, and `_recover_partial`'s
  own publish retry) — this is exactly the partial state `_recover_partial()`
  exists to auto-heal on the next scheduled run (`migrate_one()` routes back
  to it via `_find_offer(new_sku)` when no local `offer_id` exists). The
  original fix silently disabled this self-healing path on the very first
  transient publish failure.

Fixed: added an explicit `'recoverable': True` flag to all 6 publish-failure
return sites (3 in `_migrate_inventory`, 1 in `_migrate_inventory_live`, 3 in
`_recover_partial`'s condition-retry branches). `handle()`'s gate is now
`if (result.get('ebay_done') and not result.get('recoverable')) or
_is_permanent_failure(error_text):` — recoverable failures stay retryable,
non-recoverable ones (local rename) still block. Also split the log message
so a recoverable case logs "will retry" instead of "manual local fix
required", and the `LOCAL_RENAME_FAILED_AFTER_EBAY_DONE` reason code now
only ever applies to genuine local-rename failures (it's excluded from the
recoverable path entirely).

## Fix 2 — catalog.py still swallowed a token-expired RuntimeError
The #1173 fix stopped swallowing `QuotaBudgetExceeded` but the same bare
`except Exception` still caught a plain `RuntimeError('eBay access token is
expired...')` raised proactively by `client.py`'s `load_token()`.
`worker_base.py` has a dedicated `'token is expired'` → 900s transient-requeue
pattern that never got the chance to fire. Added a specific `except
RuntimeError: raise` clause (ordered after the more-specific
`QuotaBudgetExceeded` clause, which is itself a `RuntimeError` subclass, so
it still matches first).

## Fix 3 — ebay_publish.py hardcoded condition triple
The #1168 fix hardcoded `condition_id='3000'`, `condition_label='Used'`,
`condition_enum='USED_EXCELLENT'` instead of using `conditions.py`'s
canonical mapping. Verified against the real cached
`ebay-condition-policies.json`: conditionId 3000's real per-category label
varies (`Used`, `Pre-owned`, `Pre-owned - Good`, `Open Box/Used` all appear)
— the hardcoded `'Used'` would have been wrong for most of them. Now calls
`conditions.condition_enum('3000')` for the enum and
`conditions.allowed_conditions_for_category(self.config, cat_id)` for the
real per-category label, falling back to `'Used'` only if that lookup fails
(wrapped in try/except so a policy-cache hiccup can't fail the
already-succeeded publish).

## Tests
- `tests/test_ebay_sku_migrate_ebay_done_blocking.py`: added
  `test_recoverable_ebay_done_publish_failure_is_not_blocked` (the
  regression case).
- `tests/test_catalog_epid_lookup.py`: added
  `test_epid_lookup_propagates_expired_token_runtime_error`.
- `tests/test_ebay_publish_condition_fallback.py`: added tests for
  category-specific label resolution and safe fallback on lookup failure;
  updated the existing test to mock `conditions.allowed_conditions_for_category`.

`pytest -q tests/test_ebay_sku_migrate_ebay_done_blocking.py
tests/test_catalog_epid_lookup.py tests/test_ebay_publish_condition_fallback.py`:
14/14 pass. Full suite: 1972 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (read-only, no eBay/quota calls made)
- Checked the real `/opt/TGW/var/migrate-blocked.json`: all 29 currently
  `ebay_done=true` entries already match a known `_PERMANENT_ERROR_SIGNALS`
  code (25019/25021/25002/25005) — none represent an item that was wrongly
  blocked by the over-broad #1169 fix (the worker has been inactive, so that
  bug never got a chance to fire in production; caught before reactivation).
- Confirmed the new gate logic directly: a recoverable publish-failure
  result no longer blocks, a local-rename failure still does, an ordinary
  transient failure still doesn't — matches intent exactly.
- Confirmed `'eBay access token is expired — token_refresh worker should fix
  this'` contains the exact substring `worker_base.py` matches
  (`'token is expired'`).
- Confirmed against real cached condition-policy data that
  `allowed_conditions_for_category()` correctly resolves category `261588`'s
  conditionId-3000 label to `'Pre-owned'`, not the old hardcoded `'Used'`.

No deviations. No config/secrets/OAuth scopes touched.
