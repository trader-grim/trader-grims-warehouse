# DONE — todo #1182: audit#1143 apis/ebay/ cohesion findings

## Shipped

1. **`src/tgw/apis/ebay/conditions.py`** — `_get_policies()` now memoizes the
   parsed policy table in a module-level `_policies_mem_cache`, matching the
   sibling-cache pattern already used by `taxonomy._tree_id_cache` and
   `specifics._aspects_mem_cache`. Previously it re-read and re-parsed the
   ~2.7MB `ebay-condition-policies.json` disk cache on every
   `best_condition()` / `allowed_conditions_for_category()` /
   `best_condition_for_enum()` call. `refresh_condition_policies()` (the
   on-demand refresh path) now also updates the in-memory copy, so an
   explicit refresh is never shadowed by a stale in-process cache.

2. **`src/tgw/apis/ebay/trading.py`** — extracted the 429/call-limit
   retry+backoff logic that previously lived only inline in
   `get_best_offers()` into a shared `_trading_call_retrying()` helper
   (delays `[1, 4, 16]`, retries on `429` or eBay call-limit error code
   `21919188`). Applied it to `get_orders()` and `get_my_ebay_selling()`,
   which hit the same `trading_call()` choke point but previously raised on
   the first rate limit.

## Live evidence

- `pytest -q` (as `db`, offline) — 2024 passed, 1 skipped, 2 pre-existing
  failures in `tests/test_invariants_pricing.py` (unrelated bug in
  `ebay_price.py::suggest_price` — confirmed present on `HEAD` via `git
  stash` before this change too, not introduced by this packet).
- New tests added and passing:
  - `tests/test_conditions_policy_memoization.py` (2 tests) — disk cache
    read exactly once across 3 `_get_policies()` calls; explicit
    `refresh_condition_policies()` updates the in-memory copy.
  - `tests/test_trading_retry_backoff.py` (5 tests) — `get_orders`,
    `get_my_ebay_selling`, and `get_best_offers` all retry on 429 and
    recover; retries are exhausted correctly; non-rate-limit errors are not
    retried.
- Full scoped run: `pytest -q tests/test_trading_retry_backoff.py
  tests/test_conditions_policy_memoization.py tests/test_trading_site_id.py
  tests/test_best_condition.py tests/test_condition_remap.py
  tests/test_category_context_conditions.py tests/test_ebay_sync.py
  tests/test_listing_policies.py` → 75 passed.

## Note

`tests/` could not be collected running as `sudo -u tgw` in this session —
`PermissionError` on the `nix` symlink (`nix -> /home/db/tgw-flake/nix`,
owned by `db`) even though `pyproject.toml` already excludes `nix` via
`norecursedirs`; pre-existing environment issue, unrelated to this packet.
Ran tests as `db` instead. Worth a separate look if `tgw`-run pytest is
needed going forward.

## Out of scope (not touched)

None surfaced — the packet's two findings were independent and both fully
addressed within scope.
