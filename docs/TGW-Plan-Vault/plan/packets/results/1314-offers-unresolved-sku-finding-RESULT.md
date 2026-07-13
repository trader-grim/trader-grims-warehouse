# Result: 1314 offers-unresolved-sku-finding
Status: done
Todo: #1314   PP: PP-COHESION-001 (invariant C11)

Files touched:
- src/tgw/offers.py
- tests/test_offers.py

## What was wrong
`src/tgw/offers.py::_log_offer_history()` is called from
`cmd_offers_respond()` only *after* `respond_to_best_offer()` (the live
Trading API RespondToBestOffer call) has already succeeded. If the
subsequent SQLite catalog lookup (`_find_item_by_listing_id`) failed to
resolve the `listing_id` to a local SKU, the function did `log.warning(...)`
and returned — no durable record, no way for an operator to notice, no way
to retry resolution later. This is exactly the class of bug invariant C11
was written for: a skip/guard on a real recurring condition, persisted only
to journald.

## Fix
`src/tgw/offers.py`:
- Added `_UNRESOLVED_REGISTRY = Path('/opt/TGW/var/offers-unresolved.json')`
  and `_record_unresolved_offer()` / `_resolve_unresolved_offer()`.
- `_log_offer_history()` now calls `_record_unresolved_offer()` (in
  addition to the existing `log.warning`) when `_find_item_by_listing_id`
  returns `None`.

**Persistence mechanism chosen and why:** a plain JSON registry file,
mirroring `src/tgw/workers/ebay_sku_migrate.py::_BLOCKED_REGISTRY`
(`/opt/TGW/var/migrate-blocked.json`) exactly — same shape (dict keyed by
an identifier that IS known, atomic tmp-file + `.replace()` write, `except
Exception: log.warning/error` around the I/O so a persistence failure never
crashes the caller). The packet flagged this as an explicit choice point
since there's no resolved SKU/item to attach a field to (the reference C11
implementation, `ebay_stage.py`'s legacy-listing guard, writes onto an
already-known item's JSON, which doesn't apply here). I considered a
`state_machine`/Postgres table instead, but rejected it as over-scoped for
this packet: it would need a new schema migration + accessor module for a
single small registry, whereas the existing JSON-registry convention
already has a proven twin in this exact codebase area (`ebay_sku_migrate`)
and is trivially monkeypatchable in tests (confirmed via
`tests/test_ebay_sku_migrate_ebay_done_blocking.py`'s existing pattern).
Flagging this as the one deviation from spec: the packet said "if genuinely
uncertain, the simpler JSON-registry option is fine" — I judged this to
qualify and took that path rather than building a DB table.

**Retry-friendliness of the data shape:** registry is keyed by `offer_id`,
entry = `{offer_id, listing_id, action, counter_price, by, first_seen_at,
last_attempt_at, attempts, resolved}`. A repeat occurrence of the same
`offer_id` (e.g. a future repair pass re-running `_find_item_by_listing_id`
and failing again) bumps `attempts`/`last_attempt_at` rather than
clobbering history — `first_seen_at` is preserved. `_resolve_unresolved_offer()`
is the symmetric "clear on success" op, provided for a future repair worker
to call (not wired into anything now, per the packet's "don't build the
retry worker" constraint — no repair worker/detector was added this
packet).

## Test added
`tests/test_offers.py`:
- `TestUnresolvedOfferRegistry` (new class, 5 tests):
  - `test_live_success_with_unresolvable_sku_persists_finding` — the exact
    scenario from the packet: `respond_to_best_offer` mocked to succeed,
    SQLite catalog present but has no row for the listing_id, asserts the
    registry file exists and contains the correct entry (not just a log
    line).
  - `test_record_unresolved_offer_writes_new_entry`
  - `test_record_unresolved_offer_bumps_attempts_on_repeat` — retry-friendliness
  - `test_resolve_unresolved_offer_removes_entry`
  - `test_resolved_offer_found_by_listing_id_does_not_touch_registry` — sanity
    check that the happy path is unaffected.
- Updated the pre-existing `test_noop_when_listing_id_not_found` (previously
  asserted only "no exception, just a warning") to also assert the registry
  file is now written — this test's old assertion was itself testing the
  bug's silent-drop behavior.

## Live evidence (offline test run, per packet's acceptance)
```
PYTHONPATH=/opt/TGW/var/worktrees/1314-offers-unresolved-sku-finding/src pytest -q tests/test_offers.py
44 passed in 0.45s

PYTHONPATH=/opt/TGW/var/worktrees/1314-offers-unresolved-sku-finding/src pytest -q
1 failed, 2155 passed, 1 skipped, 1 warning in 55.82s
```
The one failure is `tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter`
— exactly the known pre-existing flake named in the packet (todo #1370,
shared quota-state pollution in full-suite runs, unrelated to this change).
Confirmed `python3 -c "import tgw.offers as m; print(m.__file__)"` under
the pinned PYTHONPATH resolves to this worktree's copy, not the shared
checkout's, before trusting the run.

Note: this packet's own acceptance step is the pytest run above (no live
eBay call is authorized or needed — the fix only persists fallout of an
already-successful call, per the packet's constraint against adding new
live eBay API calls).

## Deviations from spec
- Persistence mechanism: chose the JSON-registry option over a
  `state_machine`/Postgres table, per the packet's own fallback guidance
  ("if genuinely uncertain, the simpler JSON-registry option is fine") —
  see reasoning above.
- No `catalog-verify` detector rule was added for this registry (the C11
  reference implementation pairs the durable write with a `catalog-verify`
  rule, e.g. `legacy_listing_unrepaired`). The packet's constraints said
  "don't over-engineer... do NOT build the actual retry/repair worker in
  this packet — that's future scope." I read a `catalog-verify` detector
  as part of that same future-scope repair-pass work (it would need to
  cross-reference a non-item-JSON registry, a different code path from
  existing catalog-verify rules which all check item JSON fields) rather
  than this packet's minimum bar, so I did not add one. Flagging this
  explicitly since the C11 rule's stated bar is "queryable by
  catalog-verify" — if that's read as mandatory-now rather than
  future-scope, this is the gap to close next. Filed as a todo (see
  below) rather than silently deciding it either way.

## Out-of-scope findings filed
- #1373 (PP-COHESION-001): offers.py unresolved-Best-Offer registry
  (offers-unresolved.json) has no catalog-verify detector or repair pass
  yet — add a rule + retry worker so unresolved entries get regularly
  checked/repaired, matching invariant C11's full pattern.
