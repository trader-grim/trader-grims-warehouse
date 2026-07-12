# DONE — todo #1169 (audit#1143)

`ebay_sku_migrate.py`'s `handle()` only classified a migration failure as
permanent (blocking further reprocessing) via `_is_permanent_failure(error_text)`
— a hardcoded list of known eBay `errorId` substrings. When eBay-side
migration succeeded but the local folder rename then failed (`result['ok'] ==
False, result['ebay_done'] == True`, first surfaced at `_migrate_legacy`
line 252, but the same `ebay_done: True` shape appears at ~10 call sites
across `_migrate_inventory`/`_migrate_inventory_live`/`_recover_partial`),
the resulting error text (a local filesystem rename error) never matches any
of those eBay-errorId signals — so the item was never blocked. It would be
silently reprocessed every scheduled cycle forever: `revise_item_sku()`
re-run against a listing whose custom label is already the new SKU, the
local rename re-attempted against whatever local condition is still
failing (very unlikely to self-heal on its own), with no alert ever raised
to the operator.

## Fix
Fixed at the single shared choke point all ~10 `ebay_done: True` sites funnel
through — `handle()`'s failure-classification check, now:
`if result.get('ebay_done') or _is_permanent_failure(error_text):`. Any
failure where eBay's side already changed is now unconditionally treated as
permanent — reprocessing can't fix a local rename failure, and the
local/eBay SKU are now provably out of sync, which needs a human
(`tgw migrate-unblock <sku>`), not another automatic cycle. This reuses the
existing blocking infrastructure (`sku_migrate_skip`, `sku_migrate_blocked`,
`review_block`, the blocked-items registry) rather than inventing a new one.

Also added a `LOCAL_RENAME_FAILED_AFTER_EBAY_DONE` entry to `_REASON_CODE_MAP`
so the persisted `review_block.reason_code` is specific and searchable
rather than falling through to `UNKNOWN_ERROR`.

## Tests
New `tests/test_ebay_sku_migrate_ebay_done_blocking.py` (this 913-line
worker had zero prior test coverage):
- an `ebay_done=True` local-rename-failure result blocks the item
  (`sku_migrate_skip`/`sku_migrate_blocked`/`review_block` all written,
  registry updated) even though the error text matches no known
  `_is_permanent_failure` signal
- an ordinary transient failure (no `ebay_done`) is still NOT blocked,
  confirming the fix doesn't over-broadly block every failure

`pytest -q tests/test_ebay_sku_migrate_ebay_done_blocking.py`: 2/2 pass.
Full suite: 1967 passed, 1 skipped, 2 failed (both pre-existing/unrelated
in `test_invariants_pricing.py`).

## Live verification (read-only)
- Confirmed directly: `_is_permanent_failure('local rename failed (eBay
  already done): [Errno 13] Permission denied')` still returns `False` —
  proving the old single-gate check really did miss this class, and the
  new `reason_code` resolves to `LOCAL_RENAME_FAILED_AFTER_EBAY_DONE`.
- Grepped the real `/opt/TGW/var/migrate-blocked.json` registry and
  `/opt/TGW/var/log/*.log*` for "local rename failed": zero historical
  hits. `ebay_sku_migrate` is currently inactive per CLAUDE.md's worker
  status, so this exact gap hasn't yet fired in observed history — the fix
  closes a real dormant hole before the worker is next reactivated, rather
  than repairing an already-stuck item.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
`ebay_sku_migrate` remains inactive (not restarted).
