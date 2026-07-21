# IN PROGRESS — #1613, PP-ADD-005: 427-item ai_identify reidentify batch

Resuming the batch queued at end of the 2026-07-20 statemachine/hooks session
(see `INPROGRESS-2026-07-20-ai-identify-batch-and-statemachine.md`, memory
`project-2026-07-20-statemachine-and-hooks-incident`).

## State

- 427 SKUs confirmed live via the corrected query (2026-added, not sold, not
  listed on eBay via `ebay_listing.status`/`listing_status`) — count matches
  last session's estimate exactly. List staged at
  `scratchpad/427-batch-skus.txt` (session-local, not durable — regenerate
  from the SQL in the prior inbox note if lost).
- Mechanism: `tgw hint --force <sku> "<existing title>"` (plain
  `enqueue-sku ai_identify` no-ops since most already have
  `ai_identified: true`).
- Approach: couple-at-a-time validation per Dave's stated preference, review
  results before continuing to the next chunk. No cost concern (Google key
  funded for this specifically).
- Model is now `gemini-3.1-flash-lite` (migration landed same prior session)
  — all results in this batch use the current model, no stale-model split
  concern like last session had.

## If interrupted

Re-run the SQL from the prior inbox note (still present) to regenerate the
SKU list, cross-reference against which SKUs already got a fresh
`ai_reidentify`/updated `ai_identified_at` timestamp to see how far the loop
got.
