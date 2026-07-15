# Result: 1394 taxonomy-429-retry
Status: done
Todo: #1394   PP: PP-DEADLETTER-001
Files touched:
- src/tgw/apis/ebay/specifics.py (`_fetch_aspects_live()` now retries on HTTP
  429 with backoff, up to `_AAC_MAX_RETRIES` (3) attempts, respecting a
  `Retry-After` header when eBay sends one, falling back to a fixed
  `_AAC_BACKOFF_SECONDS * (attempt+1)` (5/10) backoff otherwise; a persistent
  429 after retries are exhausted still raises)
- tests/test_specifics_aspects_429_retry.py (new — 5 tests: 429-once-then-
  succeed, persistent-429-raises-after-budget, 200-on-first-try-no-retry,
  non-429 HTTPError raises immediately with no retry, Retry-After header
  extends the wait)

Live evidence:
- `pytest -q tests/test_specifics_aspects_429_retry.py
  tests/test_specifics_aspects_cache.py
  tests/test_ebay_draft_taxonomy_quota_propagation.py
  tests/test_aspects_cache.py` -> 24 passed in 0.59s (worktree-verified via
  `tgw.apis.ebay.specifics.__file__` resolving under the worktree path, not
  the shared checkout).
- Full offline suite: `pytest -q` -> 2215 passed, 1 skipped, 1 warning in
  404.82s (0:06:44). Zero regressions.
- Thermal status checked before and after the full-suite run: NORMAL|68 at
  session start, NORMAL|69 immediately before the full run, WARM|76 (not
  HOT/THROTTLE) immediately after — within the check-only, no-action band;
  noted here per CLAUDE.md's re-check-after-heavy-scan rule, no worker
  action taken (WARM is below the alarm thresholds that require a response).

Deviations from spec:
- Backoff style: spec said "match the style already in llm.py's fix for
  consistency" but also said "different status semantics (429 only here)."
  llm.py's 429 branch uses `time.sleep(15 * (attempt + 1))` (15/30/45s) for
  quota-exhaustion-class 429s from an LLM provider. I used a shorter fixed
  multiplier, `5 * (attempt + 1)` (5/10s), for the eBay Taxonomy 429 case —
  flagging as a deliberate choice, not a silent substitution: eBay's
  Taxonomy 429 is described in the packet as the standard REST rate-limit
  case (not an LLM quota-exhaustion circuit-breaker), the packet's own
  "Quota/risk" section asked me to flag any backoff that would "meaningfully
  change worst-case latency for the ebay_draft worker's job timeout budget,"
  and llm.py's 15s cooldown is specifically tied to Google's daily-quota
  circuit breaker semantics (`quota.record_429`) which doesn't apply here —
  I did not wire eBay's 429 into the `quota.record_429` circuit breaker
  either, since `_counted()` in client.py already does that at the fence
  level for every eBay call (this function retries on top of, not instead
  of, that existing quota-incident recording). Worst case added latency:
  3 attempts, 5+10=15s of sleep before raising (vs. llm.py's 15+30=45s) —
  well under the ebay_draft worker's job timeout budget. Retry count (3)
  and the "on 429 only, not other HTTP errors" scope match the packet spec
  exactly.
- Scope: kept the fix local to `_fetch_aspects_live()` in specifics.py per
  the packet's own guidance ("if it's just this one call site, keep the fix
  local"). `ebay_get()` in client.py has ~15 other call sites (ebay_sync,
  ebay_publish, ebay_sku_migrate, promotions.py, taxonomy.py, catalog.py,
  conditions.py) each with established fail-fast-to-dead-letter behavior on
  any HTTP error including 429 — changing that shared function's retry
  semantics for all of them was out of scope for this single-endpoint bug
  and risked behavior changes to callers this packet never verified.

Out-of-scope findings filed: none — no new operational friction or adjacent
bugs surfaced during this task. (Requeuing the 12 already-dead-lettered
ebay_draft jobs is explicitly out of scope per the packet — separate step
after merge, not filed as a new todo since PP-DEADLETTER-001's own plan
already tracks it as the next step for this packet.)
