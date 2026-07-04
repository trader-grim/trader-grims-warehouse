# DONE — ebay_draft 402 requeue: paused, real root cause found (R1.3)

Follow-up to the archived `INPROGRESS-ebay-draft-402-requeue.md` (1,291-job
requeue applied 2026-07-04 morning, believed unblocked by a resolved
OpenRouter billing gap).

**Live monitor (`bbjcp1g3a`) caught a continuous dead-letter climb**
(2689 → 2762+) and investigation found the requeue was NOT actually
unblocked. Real cause: OpenRouter's *account* has $30 total credit ($13.85
unspent), but the specific API key TGW uses has a **self-imposed weekly
spend limit of $15**, already at $14.98 used this week
(`limit_remaining: $0.0218`, `limit_reset: weekly` — confirmed via
`GET openrouter.ai/api/v1/auth/key`, read-only, no secret printed to
output). Every `ebay_draft` job hits Google Gemini's free-tier cap (20
requests/day for `gemini-2.5-flash-lite` specifically — not an eBay limit)
and falls back to OpenRouter, which 402s against this near-exhausted weekly
key cap regardless of the account's real balance. Dave's belief that
"OpenRouter has had credits and seen little use" was true of the account,
not of this key's weekly ceiling — a genuinely new constraint, not the
billing gap we thought was resolved.

Before catching it, 1,110 queued jobs were burning Google's scarce 20/day
free quota on guaranteed-failure fallbacks — a real quota-waste incident
(Prime Directive 2).

**Action taken (Dave approved via AskUserQuestion, chose "pause queued
jobs"):** cancelled all 1,110 still-`queued` jobs tagged
`bulk_requeue_reason=openrouter_402_2026-07-02_resolved`, scoped by tag
(the auto-mode classifier correctly blocked the unscoped `cancel_queued()`
helper first — re-did it as a tag-scoped UPDATE instead). Rows preserved as
`cancelled`, not deleted — re-enqueueable anytime. 38 of the batch had
already succeeded before the pause; those stand. Queue now clean: 2 organic
queued, 1 running, 63244 succeeded, 2762 dead_letter, 1662 cancelled.

**Follow-up, not yet done:**
- Requeue the remaining ~2,724 402-tagged dead-letters only after the
  OpenRouter weekly key-limit resets, or Dave raises the per-key weekly
  limit on openrouter.ai (real account headroom exists: $13.85 unspent).
- The other ~657 dead-letters (non-402 causes) still not investigated.
- Consider adding an OpenRouter `auth/key` weekly-limit check to `tgw
  health`'s quota section so this doesn't require log-diving to find next
  time.
