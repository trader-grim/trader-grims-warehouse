# DONE — todo #1153: ebay-pull sold-orders CreateTimeFrom bug

## Root cause

`sync_sold_orders()`'s first-sync path set `scan_from = now - 365 days`
(`SOLD_INITIAL_LOOKBACK_DAYS`), then chunked into 90-day windows. But
eBay's `GetOrders` has a rolling constraint — `CreateTimeFrom` can never be
more than 90 days before *now*, regardless of chunk width. The very first
90-day chunk (already ~275-365 days in the past) violated that boundary
before any chunking logic could help, producing exactly the reported error.

## Fix

Clamp `scan_from` itself to `now - 89 days` (one-day safety margin) right
after it's computed, for BOTH the first-sync path and a long-stale
incremental resume (if the sync hasn't run in months, `last_synced_at`
itself could predate the 90-day boundary too). A normal recent resume is
unaffected.

## Live evidence

- `pytest -q tests/test_sync_sold_orders_lookback_clamp.py` — 3 passed:
  first-sync clamp, stale-resume clamp, recent-resume unaffected.
- `pytest -q` (full suite) — 2049 passed, 1 skipped (was 2046 — 3 new tests).
- `ruff check` — clean.

## Note

No existing test coverage existed for `sync_sold_orders()` at all before
this — the 3 new tests are the first for this function.
