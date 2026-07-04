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

**UPDATE 2026-07-04 ~14:48 UTC — one more finding, not a new incident:**
`tgw health`'s quota check showed `llm_google` spend jumping from 92→973
with a 429 at 13:09 UTC, ~9 min after the pause. Traced it: each successful
`ebay_draft` job fires **two** separate Gemini calls, not one — the primary
draft (`gemini-2.5-flash`) and a secondary vision call for aspect-filling
routed through `bulk_classify` (`gemini-2.5-flash-lite`,
`workers/ebay_draft.py` ~L405-424). The 97 `bulk_classify` calls and 196
`RESOURCE_EXHAUSTED` hits on `gemini-2.5-flash-lite` in the last 3h all
trace to the 38 jobs that succeeded before the pause plus the handful still
`running` when `cancel_queued` fired (which only touches `state='queued'`,
not in-flight jobs) — confirmed quiet since 13:09, no new activity as of
14:48. Not a new runaway, but worth remembering: **this doubles free-tier
pressure per successful ebay_draft job** (two Gemini calls, one on each of
two different models) — a factor for whatever billing/limit fix gets
chosen next.

**UPDATE 2026-07-04 ~15:00 UTC — resumed, unblocked by Dave raising the OpenRouter key limit:**
Dave changed the OpenRouter key's spend limit from $15/week to **$5/day**
(confirmed live via `auth/key`: `limit: 5, limit_reset: daily`). Backlog
sized first: 3,768 jobs total (1,110 previously-paused `cancelled` +
2,658 `dead_letter` matching the 402 pattern) at ~$0.00084/job (primary
draft call + `bulk_classify` aspect-fill call) ≈ **$3.16** — comfortably
inside the new $5/day cap.

Resumed the 1,110 tagged-cancelled jobs back to `queued` (scoped UPDATE,
same tag as the earlier pause). Re-ran
`scripts/requeue_ebay_draft_402_dead_letters.py --apply` (no `--limit`
this time) — 2,658/2,658 requeued, zero errors. Queue now: 3,764 queued +
1 running against 63,247 succeeded / 2,765 dead_letter (old rows, left in
place) / 552 cancelled (pre-existing, untouched, not part of this batch).

Monitor `bmzc3ibf8` watching the drain — alerts on any new (non-402)
dead-letter regression, reports done when the queue empties, and tracks
`limit_remaining` on the OpenRouter key each pass so we can see actual
spend-down against the $5 cap in real time.
