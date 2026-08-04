# DONE — todo #1240: fix 9 pre-existing broken tests

Triggered by checking a CI failure on open PR #9, which was hitting the
same 2 pre-existing `test_invariants_pricing.py` failures confirmed
pre-existing (via `git stash`) multiple times earlier this session.

## Root cause + fix (2 test_invariants_pricing.py failures)

`ebay_price.py`'s operator-price-history guard (the block that must skip
pricing when `price_history[-1].source == 'operator'` and the job isn't
itself an operator re-price) wrote the `price_guard_skipped` marker but then
fell through to the rest of `handle()` instead of returning — so it still
called `suggest_price()` unconditionally. That either burned a comps query
it shouldn't have made (`test_chain_enqueued_price_skips_when_operator_set_last`
expected `called['n'] == 0`), or crashed with `TypeError: 'NoneType' object
is not subscriptable` when the test's mock correctly returned `None` for a
call that should never happen
(`test_already_priced_item_still_idempotent_with_operator_history`).

Fix: added `return` immediately after the guard's `fence_ebay_write` call —
one line, `src/tgw/workers/ebay_price.py`.

## Pre-flight finding (7 test_model_routing.py failures)

Per the packet skill's live-verification step: re-ran
`tests/test_model_routing.py` before touching anything — all 8 tests
already pass. This part of the todo's premise no longer matches reality;
it was evidently already fixed in an earlier session (most likely the
2026-07-08 LLM direct-routing flip — `tgw-models.json` moved several tasks
to `google_direct`/`deepseek_direct`/`anthropic_direct`, and the test
expectations were presumably updated in that same session) without this
todo being marked done to reflect it. No code change needed for this half.

## Live evidence

- `pytest -q tests/test_invariants_pricing.py` — 29 passed (was 27 passed +
  2 failed).
- `pytest -q tests/test_model_routing.py` — 8 passed (already passing,
  confirmed not touched).
- `pytest -q` (full suite) — **2046 passed, 1 skipped, 0 failed** (was 2044
  passed + 2 failed before this fix).
- `ruff check src/tgw/workers/ebay_price.py` — clean.
