# DONE — eBay API quota drain fix (session 41, 2026-07-02)

**Root cause found and fixed for all three drains — see below. Also found via
transcript search of session 39 (05405d23-...) that the aspects-warmup timing bug
was a self-inflicted regression: Dave asked for "crawl it at the end of every day,
then our limit resets" (once-daily, pre-reset), session 39 silently substituted
"every 6h ebay_sync cycle" without flagging the deviation, and that's what fired at
04:50am today and burned quota before Dave's day started. See
feedback-implement-as-specified memory.**


Dave reported hitting "api token limit exhausted" on the first item he opened this
morning, and asked for a real fix (not another wait-24h cycle). This is at least the
third eBay API exhaustion; session 39 already produced an audit
(project-api-data-reuse-audit memory / handoff notes) that predicted two of these
exact failure modes and left them unfixed pending Dave's go-ahead. Both are now
confirmed live in today's logs:

1. **Taxonomy API 429** — `ebay_draft.py:_validate_category_suggestion()` fires a live
   `get_category_suggestions` call on every drafted item purely for QA telemetry
   (`category_agreement` field), duplicating `ai_identify`'s category call moments
   earlier. Confirmed hitting 429 repeatedly in `worker_ebay_draft.log` at 08:05 today.
   This is almost certainly what Dave saw on the first item.
2. **Sell Inventory API drain** — `ebay_sync.py` bulk offer list has been blocked since
   at least 15 consecutive 6h cycles (~90h) by one orphaned offer with a bad SKU (eBay
   error 25707, todo #1077, previously p45/low-pri). The code already detects this and
   falls back to a per-SKU loop (~2,000+ individual GETs per run, confirmed in
   `worker_ebay_sync.log` — "checked 3900 SKUs..." this morning), but only *logs* the
   persistent-fallback warning — nothing stops it from repeating the ~2,000-call fallback
   every 6 hours indefinitely.

Also noticed but out of scope for this fix: OpenRouter 402 Payment Required (LLM
billing, unrelated to eBay) causing ebay_draft dead-letters, and repeated EPS 503s in
ebay_upload — flagging separately, not touching in this pass.

**Plan:**
- Remove/disable the live category-suggestion telemetry call in `ebay_draft.py` (non-
  essential, fail-soft already, safe to cut).
- Add a circuit breaker in `ebay_sync.py` so the per-SKU fallback runs at most once per
  24h instead of every 6h cycle once persistent, cutting the drain ~4x until the
  orphaned offer is actually cleared.
- Bump todo #1077 priority — it's now a confirmed live production quota drain, not a
  low-priority cleanup item.
