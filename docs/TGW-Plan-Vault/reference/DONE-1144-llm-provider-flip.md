# IN PROGRESS — LLM provider flip: OpenRouter primary, Google free tier = operator emergency reserve

Context: 2,171 llm_google 429s today (all worker:ebay_draft/bulk_classify) —
Google's free tier for gemini-2.5-flash-lite is now 20 requests/day. Every
requeue-backlog job burned a 429 + ~40s retry before falling back to
OpenRouter (jobs succeeded; damage was latency + noise, not data).

Dave's decision (2026-07-04): the 20 free calls/day aren't valuable as a
primary — make them the OPERATOR EMERGENCY RESERVE instead.

Plan:
1. tgw-models.json: ai_identify/alt_text/bulk_classify → openrouter
   google/gemini-2.5-flash-lite; ebay_draft → openrouter google/gemini-2.5-flash.
2. llm.py openrouter path: on failure, if quota context is interactive
   (C10 operator lane) and model is google/*, fall back to google_direct
   free tier. Background failure keeps transient-requeue behavior.
3. llm.py google_direct path: KEEP the existing →openrouter fallback AND add
   quota.precheck('llm_google') gate before the attempt (post-429 stand-down,
   probe-on-expiry). Dave: keep the failover pattern for later, when a paid
   Google API key makes google_direct primary again.
4. quota: llm_google daily budget = 20 (health visibility).
5. Tests, restart tgw-worker@ebay_draft, live-verify against the draining
   ~3,300-job requeue backlog (429s should stop, jobs speed up).

Status: starting implementation.

## Completed 2026-07-04

Built + live-verified: tgw-models.json flipped (4 tasks → openrouter primary,
google/* model ids); llm.py openrouter path falls back to google_direct for
INTERACTIVE callers only (operator emergency reserve); google_direct path now
precheck-gated (circuit breaker kept for future paid Google key, per Dave);
llm_google default budget = 20. 32 tests pass (9 new). ebay_draft +
ai_identify workers restarted.

Live result: 429s stopped at restart (14:30 PST); backlog draining at ~3-5s/job
(was 35-60s). Reserve fallback path is unit-tested but NOT live-fired (needs a
real/simulated OpenRouter failure in interactive context — offer stands to
simulate with a bad key if Dave wants a live drill).

Quota ground truth (same session): observed Google enforcement is ~20/day/
project/model (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier,
quotaValue 20), vs published free-tier 1,000 RPD flash-lite / 250 RPD flash.
Per-project doling confirmed as the operative reality.

## Documentation pass (Dave: "we have found these several times now")

- NEW canonical doc: `reference/LLM-Providers-Quotas.md` (per-project quota
  reality, the fallback-masks-error logging gotcha, settled architecture,
  re-verification recipe). Master plan should link it wherever LLM providers
  come up.
- NEW invariant **E8** in `reference/invariants.md`: Google free tier =
  operator emergency reserve; background never spends it. Enforced in code
  (context gate + precheck + budget 20) with named tests.
- CLAUDE.md reference table row added (read before any LLM provider change).
- Session memory updated (s41 "verified free-tier" note superseded).

**#1141 connection (Dave, s45):** `reference/LLM-Providers-Quotas.md` is a
prime candidate anchor for the Perplexity footnote pass — every plan/PP
mention of LLM providers, ai_identify/ebay_draft models, or AI cost should
footnote to it (plus the external ai.dev/rate-limit link), so the finding
is linked on the surface where it's useful instead of rediscovered.
