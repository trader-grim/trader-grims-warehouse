# DONE — todo #1113: ebay_dole worker not installed, misleading UI

## Findings

Most of the interim fix requested was already shipped by an earlier
session — the checkbox already renders "(inactive)" with an accurate
tooltip (`http_server.py:5236-5245`), and the `set_ready` backend action
already returns an accurate note that nothing publishes yet
(`http_server.py:1511-1514`).

The one remaining problem: `approveForListing()`, a JS function still
emitted on every item-detail page, still had a `confirm()` dialog claiming
"It will go live at the next dole cycle (up to 1 hour)." — but this
function was **dead code**, never called anywhere (the checkbox actually
uses `toggleApprove()`). Removed it.

The actual "install the worker vs. remove the checkbox" decision is
explicitly deferred to volume time per the todo's own text — noted as
riding along with PP-BULKLIST-001's design pass in the master plan rather
than decided here.

## Live evidence

- New test `test_item_detail_no_stale_dole_cycle_claim` — confirms neither
  the stale claim nor the dead function name appear in the rendered page.
- `pytest -q` — 2052 passed, 1 skipped (was 2051).
- `ruff check` — clean.
- `tgw plan check` — clean.
