# INPROGRESS: Simplify to core list/edit loop

Dave: two+ weeks stuck on the same problem, wants to strip the system down to
"absolutely required" and get reliable eBay list/edit working before trusting
anything else.

## Trigger
Session investigation of web-UI vs Flutter divergence surfaced `tgw health`:
2,942 total dead-letter jobs, of which **2,771 are ebay_draft** — the core
draft pipeline is effectively broken at scale. Everything else is noise on
top of that.

## Agreed plan (Dave, this session)
- Core loop to KEEP running: token_refresh, pm_intake, multi_intake,
  bundle_intake, ai_identify, ebay_draft, ebay_stage, ebay_upload,
  ebay_publish, ebay_price, plan_render.
- Peripherals to PAUSE (systemctl stop, not disable — reversible):
  ebay_sync, ebay_legacy_sync, ebay_sku_migrate, ebay_price_reducer,
  catalog_rebuild, thumbnail_gen, velocity_stats.
- echo, ebay_repush: Dave not sure — left untouched (echo has no dead-letter
  risk here; ebay_repush has no active systemd unit anyway).
- Operator-approval gate (C9, see feedback-operator-gate-is-the-design) is
  NOT part of this simplification — stays exactly as designed. This is only
  about which background workers run, not the human approval checkpoint on
  live eBay writes.
- Sequencing: pause peripherals FIRST, then diagnose the 2,771 dead-lettered
  ebay_draft jobs (Dave's explicit call — reduce noise before digging in).

## State at time of writing (updated)
- 7 peripherals stopped (ebay_sync, ebay_legacy_sync, ebay_sku_migrate,
  ebay_price_reducer, catalog_rebuild, thumbnail_gen, velocity_stats).
- Diagnosed the 2,771 dead-lettered ebay_draft jobs: 2,658 are
  `HTTPError('402 Client Error: Payment Required ... openrouter.ai')`,
  running 2026-07-01 13:51 UTC → 2026-07-04 08:42 UTC (~3 days, then jobs
  exhausted max_attempts and dead-lettered — OpenRouter has been broken for
  ~a week with nothing surfacing it to Dave). Remainder: ~12 eBay 429s on
  taxonomy API, a few corrupt-image OSErrors. Root cause is OpenRouter
  account billing, NOT a code bug.
- Dave: OpenRouter billing is not being fixed right now ("OpenRouter is
  fine... just turn them off"); he has also paid for the Gemini API
  separately but explicitly said NOT to touch LLM provider config this
  session — just stop the workers that depend on it.
- Stopped `ebay_draft` and `ai_identify` (systemctl stop, reversible).
- **Currently running (10):** token_refresh, pm_intake, multi_intake,
  bundle_intake, ebay_stage, ebay_upload, ebay_publish, ebay_price,
  plan_render, echo.
- This is the narrowest state that can still list/edit on eBay: no new
  AI drafts will be produced, but existing drafted items can still be
  staged/uploaded/published, and prices can still be edited.

## CORRECTION — root cause was wrong, re-diagnosed
Dave immediately corrected the OpenRouter-billing conclusion: OpenRouter has
credits, not near limits. Re-investigation with `tgw ai-usage` + queue_jobs
found the real problem is a **resubmission storm**, not billing:

1. **07-01:** ebay_draft ran 20,780 jobs against 5,666 distinct SKUs in one
   day — one SKU drafted 1,006 times, others 500-600+, all succeeding
   back-to-back every ~4s for hours. Trigger not traced (no matching
   ai_identify job rows for the hot SKU; journal logs don't retain before
   07-02). Dave's belief: probably another remediation script gone wrong;
   checked ebay_audit.py/ebay_backfill_offers.py/ebay_normalize.py/
   ebay_photo_push.py (added around that date) — none enqueue jobs, so not
   confirmed.
2. **07-04→07-05 (confirmed code bug):** `scripts/requeue_ebay_draft_402_dead_letters.py`
   requeues dead-letter jobs with a **fresh timestamp-suffixed dedupe key
   every run**, defeating dedupe-based loop protection. Docstring claims
   2,689 matched; actual count is **6,607** — it ran more than once and
   kept re-matching its own regenerated failures. Matches the 07-05 burst:
   bulk_classify made 17,949 calls, **14,864 errors (83% failure)** —
   almost all wasted spend.
3. Script left in place, untouched, NOT to be run again. Todo #1250 tracks
   hardening/retiring this remediation-script pattern.

This is why "the pipeline has been burning tokens and not doing work" —
confirmed, not just a feeling.

## Next step
Dave wants to see if list/edit is now reliable with the reduced worker
surface (ebay_draft/ai_identify still paused). Do NOT touch LLM provider
config (OpenRouter vs Gemini) or re-run the requeue script without his
explicit go-ahead.

## Todo
#1249 tracks the pause/simplification. #1250 tracks the remediation-script
hardening follow-up.
