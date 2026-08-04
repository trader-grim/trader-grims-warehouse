# DONE — todo #1173 (audit#1143)

`src/tgw/apis/ebay/catalog.py`'s `lookup_epid()` had a bare `except
Exception as exc: ... return None` at the end of its exception chain (line
61) that swallowed `quota.QuotaBudgetExceeded` — a `RuntimeError` subclass
raised by `ebay_get()`'s `quota.precheck()` when a background caller's
quota pool is halted (PP-QUOTA-001). This defeated the established
quota-halt/requeue pattern: `worker_base.py`'s `classify_dead_letter()`
specifically recognizes the substring `'quota budget exhausted'` and
requeues transiently (1800s delay, pool resets at 00:00 America/Los_Angeles)
instead of dead-lettering — but only if the exception actually propagates
out of the worker's `handle()`. Here it never got the chance: `lookup_epid`
caught it, logged a warning, and returned `None` as if the EPID just wasn't
found, so `ebay_stage.py` (the sole caller) proceeded to stage the item
without EPID enrichment — silently, during quota exhaustion, with no
requeue and no alert.

## Fix
Added a dedicated `except quota.QuotaBudgetExceeded: raise` clause before
the bare `except Exception`, so quota exhaustion now propagates all the way
up to the worker, where it's classified and requeued exactly like every
other metered-API quota wall in the codebase (LLM calls, direct eBay REST
calls). No caller-side changes were needed: `ebay_stage.py` calls
`lookup_epid()` with no wrapping `try/except`, so the exception reaches
`handle()` cleanly.

## Tests
Added to `tests/test_catalog_epid_lookup.py` (existing file, extending its
established mock-`ebay_get`-and-assert pattern):
- `lookup_epid()` now re-raises `QuotaBudgetExceeded` instead of returning
  `None` — the regression case for #1173.

`pytest -q tests/test_catalog_epid_lookup.py`: 6/6 pass (5 pre-existing +
1 new). Full suite: 1968 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (read-only, no eBay/quota calls made)
Confirmed directly against the real `QuotaBudgetExceeded` message format
(`quota.py`'s `precheck()`): the raised message text
(`'quota budget exhausted for {pool}: ...'`) contains the exact substring
`worker_base.py`'s `_TRANSIENT_ERRORS` table matches (`'quota budget
exhausted'`) — so once this fix lets the exception propagate, a real quota
halt during `ebay_stage` will now genuinely requeue with a 1800s delay
rather than silently skipping EPID enrichment.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no live eBay or quota-state calls made during verification.
