# DONE — code-review follow-ups on todos #1210/#1211/#1238

`/code-review` (medium effort, 8 finder angles) on the commits for #1238,
#1210, #1211 surfaced 3 confirmed findings after verification (a 4th
candidate — a theorized stale-archive-skip data-loss path in
`photo_repair_iss013.py` — was REFUTED: the content-hash check happens on
the file being deleted itself, before the archive-exists check is ever
consulted, so that path is already safe).

## Fix 1 — EBAY_ENV global mutation never restored
`get_access_token.py`'s auto-refresh branch (added for #1238) bridged
`is_sandbox` into `refresh_access_token()` by mutating process-global
`os.environ['EBAY_ENV']` with no restore — any later call in the same
process (another `get_access_token()` call, a worker, a test) would
silently inherit whatever sandbox/production state was last set.

Root-fixed rather than patched: `refresh_access_token()` and
`get_ebay_config()` in `refresh_access_token.py` now take an explicit
`is_sandbox: bool | None = None` parameter. `None` (the default) falls back
to the existing `EBAY_ENV`-driven behavior, so `token_refresh.py`'s sole
worker call (`refresh_access_token(force=True)`, no `is_sandbox`) is
completely unaffected. `get_access_token.py` now passes
`is_sandbox=is_sandbox` directly — the `os.environ` bridge is gone entirely.

## Fix 2 — _normalize_price() crashes on non-numeric price
`photosync_canary_probe.py`'s `_normalize_price()` (added for #1210) called
`float(value)` with no exception handling. `docs/TGW-Plan-Vault/reference/ISSUES.md`
ISS-011 documents that real item price fields hold `''` for unpriced items —
this would have crashed the entire canary probe run on exactly the items
most likely to need a clean diff report.

Fixed: `''`/`None` are both treated as "unpriced" (→ `None`), and any other
unparseable value is returned as-is (so `_diff()` still reports a mismatch
rather than raising uncaught out of `main()`).

## Fix 3 (same mechanism as Fix 1)
Covered above — `load_config()`'s sandbox-prefix logic still duplicates
`get_ebay_config()`'s (both now take `is_sandbox` explicitly, by design,
since they resolve credentials for two different token files/flows), but
the `os.environ` bridge symptom is eliminated.

## Tests
- `tests/test_get_access_token.py`: replaced the old env-mutation test with
  one confirming `is_sandbox` is passed as a parameter and `EBAY_ENV` is
  left untouched.
- New `tests/test_refresh_access_token.py` (file had zero prior coverage):
  `is_sandbox` explicit param overrides `EBAY_ENV` in both directions;
  `is_sandbox=None` fallback still respects `EBAY_ENV` (protects
  `token_refresh.py`'s worker call); `refresh_access_token()` threads
  `is_sandbox` through to the correct `api_root_ebay`.
- `tests/test_photosync_canary_probe.py`: added empty-string→`None`,
  garbage-string-no-crash, and a `_diff()` no-crash-on-unpriced-item case.

`pytest -q tests/test_get_access_token.py tests/test_refresh_access_token.py
tests/test_photosync_canary_probe.py tests/test_photo_repair_iss013.py`:
31/31 pass. Full suite: 1963 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (read-only, no secrets/tokens mutated)
- `get_ebay_config(is_sandbox=True/False)` against real
  `ebay-credentials.json`: prod and sandbox app_id genuinely differ;
  `EBAY_ENV` remains unset in `os.environ` after explicit-parameter calls
  (confirms the leak is gone).
- `is_sandbox=None` fallback with `EBAY_ENV=sandbox` set matches the
  explicit-sandbox config exactly — `token_refresh.py`'s existing call
  pattern is provably unaffected.
- `_normalize_price('')` → `None`, `_normalize_price('TBD')` → `'TBD'`
  (no crash), and `_diff()` on a `''`/`None` unpriced pair returns `[]`
  instead of raising.

No deviations. No config/secrets/OAuth scopes touched.
