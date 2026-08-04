# Packet: eBay Taxonomy API 429 on get_item_aspects_for_category has zero retry

Todo: #1394   PP: PP-DEADLETTER-001   Track: dead-letter triage (batch, see
PP-DEADLETTER-001.md — dispatched alongside 7 other packets this round)

## Context budget (ALL the model may load)
This packet + `src/tgw/apis/ebay/specifics.py` (whole file) +
`src/tgw/apis/fence.py` (`ebay_get`'s implementation, to see whether retry
belongs there instead — check before assuming the fix goes in
`specifics.py`) + any existing test file for `specifics.py` or `fence.py`.

## Verified live before this packet was written
- 12 `ebay_draft` dead-letters, `error_code=WORKER_EXCEPTION`, error text
  matching a 429/`RESOURCE_EXHAUSTED` pattern on the eBay Taxonomy API's
  `get_item_aspects_for_category` call.
- `_fetch_aspects_live()` in `src/tgw/apis/ebay/specifics.py:92-99` calls
  `ebay_get(cfg, f'/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category', ...)`
  with no retry/backoff around it — a 429 from eBay's Taxonomy API
  propagates straight up and the caller (`get_aspects()` / the ebay_draft
  worker) treats it as a hard failure → dead_letter.
- This is architecturally the same *shape* of bug already fixed today in
  `src/tgw/apis/llm.py::_call_google_direct()` (todo/commit from earlier
  this session): a rate-limited call was falling straight through with
  zero retry, fixed with `time.sleep(N * (attempt + 1)); continue` inside
  a bounded retry loop, distinguishing quota-exhaustion (longer backoff)
  from transient overload. Read that fix in `llm.py` (git log this file,
  or `tests/test_llm_google_direct.py`'s two new tests) as the pattern to
  follow — do not copy it verbatim, this is a different API (eBay REST,
  not an LLM provider) with different status semantics (429 only here, no
  503-equivalent transient-overload case to distinguish).

## Spec
Add retry-with-backoff around the Taxonomy API call in
`_fetch_aspects_live()` (or inside `ebay_get()` itself if other callers
would also benefit — check how many other call sites hit rate-limited eBay
endpoints without retry before deciding scope; if it's just this one
call site, keep the fix local rather than changing shared `ebay_get()`
behavior for everything).
- On HTTP 429: retry with backoff (e.g. 3 attempts, exponential or fixed
  multiplier — match the style already in `llm.py`'s fix for consistency)
  before raising.
- Respect a `Retry-After` header if eBay sends one; fall back to a fixed
  backoff if not.
- Do not swallow a persistent 429 after retries are exhausted — it should
  still raise/dead-letter, just not on the very first attempt.

## Out of scope
- Any other eBay API endpoint's retry behavior — this packet is scoped to
  the Taxonomy `get_item_aspects_for_category` call only, unless your
  investigation shows the fix cleanly belongs in shared `ebay_get()` with
  no risk to other callers' existing behavior (state that reasoning in the
  result manifest if you go that route).
- Requeuing the 12 dead-lettered jobs — separate step after merge.

## Dataset
None — pure retry-logic change, no stored data touched.

## Acceptance (live)
1. Unit test: mock `ebay_get` (or whatever underlying HTTP call) to raise
   a 429 once then succeed — `get_aspects()`/`_fetch_aspects_live()` must
   return the successful result, not raise.
2. Unit test: mock a persistent 429 (all attempts fail) — must still raise
   after the retry budget is exhausted, not hang or silently return empty.
3. Unit test: a normal 200 response on first try is unaffected (no
   spurious sleep/retry).
4. Run the full offline suite — zero regressions.

## Quota/risk
Low — retry adds latency on the rare 429 case only, no new call volume in
the common case. Flag if your retry count/backoff would meaningfully
change worst-case latency for the ebay_draft worker's job timeout budget.
