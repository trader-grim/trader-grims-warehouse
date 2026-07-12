# DONE — todo #1181 (audit#1143)

`src/tgw/apis/ebay/taxonomy.py`'s `best_category()` docstring documents
"tries each query in order" (title first, then a broader category string as
fallback), but the loop had no exception handling around
`get_category_suggestions(cfg, query)` — any failure on the first (title)
query propagated straight out of `best_category()`, aborting the whole
lookup before the second (broader category) query was ever attempted. Both
callers (`ai_identify.py`, `ebay_draft.py`) wrap the whole call in a broad
`except Exception`, so the item silently ended up with no eBay category at
all, indistinguishable from "no suggestions found" even though the second
query might well have succeeded.

## Fix
Added a per-query try/except inside the loop:
- A generic exception (network error, 4xx/5xx from eBay, etc.) is logged
  and the loop `continue`s to the next query — restoring the documented
  fallback behavior.
- `quota.QuotaBudgetExceeded` specifically is re-raised, not swallowed — a
  second live query would be gated identically, and per the established
  convention from todo #1173's `lookup_epid` fix, quota exhaustion should
  propagate so the caller's worker requeues transiently instead of
  silently degrading to "no category found."

## Tests
New `tests/test_best_category_fallback.py`:
- first-query failure falls through to a successful second query (the
  regression case)
- all queries failing returns `(None, None)` rather than raising
- `QuotaBudgetExceeded` propagates instead of being swallowed
- a successful first query does not try the second (no wasted call)

`pytest -q tests/test_best_category_fallback.py`: 4/4 pass. Full suite:
1995 passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only; reproduced against a real logged failure)
Grepped `/opt/TGW/var/log/worker_ai_identify.log` for `"taxonomy lookup
failed"` and found 3 real historical occurrences (2026-07-02), all HTTP 429
from eBay on the first (title) query — e.g. item titled "Vintage Golden
Fiesta Oroville, Calif. 1975 Gold Nugget Souvenir Plaque" with broader
category "Souvenir Collectibles". Confirmed this is a plain
`requests.exceptions.HTTPError` (from `client.py`'s `resp.raise_for_status()`
on a real 429), not TGW's own `QuotaBudgetExceeded` gate — exactly the case
the new `except Exception` branch catches.

Reproduced the exact scenario with the real title/category strings from the
log: mocked the title query to raise the same 429 and the category query to
succeed. **Before this fix**, `best_category()` would have raised out
immediately (caught by the caller, logged, item left with no category).
**After the fix**: `best_category()` correctly tries the second query and
returns the real resolved category (`('11700', 'Souvenir Collectibles')`).

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no live eBay API calls made during verification (mocked against the real
logged query strings).
