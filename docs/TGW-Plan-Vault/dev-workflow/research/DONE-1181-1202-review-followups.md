# DONE — code-review follow-ups on todos #1181/#1202

`/code-review` (medium effort, 8 finder angles) on the commits for #1181
and #1202 surfaced 3 confirmed findings, all related to one root issue: the
#1181 fix's core purpose — letting `quota.QuotaBudgetExceeded` propagate
out of `best_category()` so the caller's worker requeues transiently
instead of silently degrading — was defeated at both of its real
production call sites, and the fix itself was also missing half of the
exception-handling precedent it cited.

## Fix 1 & 2 — both callers were swallowing the propagated exception
`taxonomy.py`'s `best_category()` correctly re-raises
`QuotaBudgetExceeded`, but:
- `ai_identify.py`'s taxonomy-lookup call site had a pre-existing
  `except Exception as exc: log.warning(...)` around it (unrelated to
  #1181, never touched by that fix) that caught it right back — the item
  proceeded with no eBay category after the vision-AI call had already run
  and been billed, instead of the job requeuing.
- `ebay_draft.py`'s taxonomy-retry call site had the same pattern, worse:
  swallowing the exception let control fall through to the `'99 Everything
  Else'` catch-all category fallback, so a draft could be built and
  published under a low-quality placeholder category purely because of a
  transient quota condition.

Fixed by adding `except quota.QuotaBudgetExceeded: raise` before each
broad `except Exception` at both call sites, so the exception now reaches
`worker_base.py`'s `classify_dead_letter()` (which matches `'quota budget
exhausted'` for a 1800s transient requeue) instead of being caught and
logged as a warning.

## Fix 3 — best_category() was missing the expired-token re-raise
`best_category()`'s new exception handling cited #1173's `catalog.py` fix
as its precedent, but only ported half of it: `catalog.py`'s fix re-raises
both `QuotaBudgetExceeded` *and* a bare `RuntimeError` (client.py's
`load_token()` raises this proactively for an expired token, so it can
reach `worker_base`'s dedicated 900s `'token is expired'` transient-requeue
rule). `taxonomy.py`'s fix only re-raised the former. Added the matching
`except RuntimeError: raise` clause, ordered after the more-specific
`QuotaBudgetExceeded` clause (which is itself a `RuntimeError` subclass, so
it still matches first — same ordering `catalog.py` already uses).

## Tests
- `tests/test_best_category_fallback.py`: updated the two generic-failure
  tests to use `ValueError` instead of `RuntimeError` (since `RuntimeError`
  now has its own re-raise semantics); added `TestBestCategoryTokenExpiry`
  covering the new expired-token propagation.
- New `tests/test_ai_identify_taxonomy_quota_propagation.py`: confirms
  `QuotaBudgetExceeded` from `best_category()` propagates all the way out
  of `AIIdentifyWorker.handle()`; confirms an ordinary (non-quota) taxonomy
  failure still degrades gracefully as before.
- New `tests/test_ebay_draft_taxonomy_quota_propagation.py`: same
  confirmation for `EbayDraftWorker.handle()` (the taxonomy retry happens
  early enough in `handle()` that no further mocking was needed to reach
  it, keeping the test narrowly scoped).

`pytest -q tests/test_best_category_fallback.py
tests/test_ai_identify_taxonomy_quota_propagation.py
tests/test_ebay_draft_taxonomy_quota_propagation.py
tests/test_ai_identify_reidentify_flag.py`: 10/10 pass. Full suite: 2002
passed, 1 skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification
Confirmed the real `QuotaBudgetExceeded` message text
(`'quota budget exhausted for {pool}: ...'`) still matches
`worker_base.py`'s `'quota budget exhausted'` transient-requeue pattern, so
now that the exception actually reaches `worker_base` from both real
callers, a live quota exhaustion during `ai_identify` or `ebay_draft` will
genuinely requeue with a 1800s delay instead of silently degrading.

No deviations. No config/secrets/OAuth scopes touched.
