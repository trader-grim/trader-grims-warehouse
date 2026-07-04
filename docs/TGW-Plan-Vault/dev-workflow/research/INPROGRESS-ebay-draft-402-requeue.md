# IN PROGRESS — ebay_draft dead-letter 402 requeue (R1.3)

Root-caused 2,582 of 3,239 `ebay_draft` dead-letters to OpenRouter "402
Payment Required" on 2026-07-02 — a billing gap, not a logic bug.
`ebay_draft`'s primary provider is `google_direct` (free tier); OpenRouter
is only touched on a Google failure. Dave confirmed 2026-07-04 that
OpenRouter has had credits and seen little use since — the gap is resolved.

Half (1,291) requeued via `scripts/requeue_ebay_draft_402_dead_letters.py
--apply --limit 1291`, deliberately holding back the other half for quota
headroom (Dave wants troubleshooting-day quota reserved). Zero requeue
errors; `tgw-worker@ebay_draft` active, 0 quota incidents at requeue time.

**Follow-up, not yet done:**
- Remaining 1,291 402-caused dead-letters (same script, no --limit, or a
  smaller batch — Dave's call once today's quota picture is clearer).
- The other ~657 dead-letters (non-402 causes — mostly a handful of
  taxonomy 429s and some "model returned non-JSON" HardFailures) not yet
  investigated or requeued.
- Watch the 1,291 in-flight jobs land as `succeeded`/`dead_letter` again —
  if a meaningful fraction dead-letter again, that's a different problem
  worth investigating before requeuing the rest.
