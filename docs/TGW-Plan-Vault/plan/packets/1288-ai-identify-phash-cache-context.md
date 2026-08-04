# Packet: pHash cache key includes prompt context, not just photo+task
Todo: #1288   PP: PP-COHESION-001   Track: concurrent batch 1 of 3 (PP-HERMES-EA-001)

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/ai_identify.py` (the cache-check block
around lines ~209-249 only) + `src/tgw/image_hash.py`
(`lookup_hash()`/`store_hash()` signatures only, read-only reference —
do not modify) + the todo brief (`tgw todo brief 1288`). Nothing else.

## Spec
`lookup_hash(img_hash, "ai_identify")` / `store_hash(img_hash, sku,
"ai_identify", result)` key the cache on `(phash, task)` only. If a SKU
is re-identified later with a newly available `ai_hint` or a
`product_context` (from a barcode/product lookup) that wasn't present on
the first scan, the cache still returns the FIRST result for that photo,
silently ignoring the new context — and the history/vision record then
falsely reports the current run's `prompt_type` even though the actual
data returned came from the old, different-context scan.

Fix WITHOUT a schema/database change: fold the prompt context into the
cache key string itself, computed alongside `img_hash` (around line 214,
`img_hash = compute_dhash(img_path)`):

```python
import hashlib
context_sig = hashlib.sha256(
    (product_context or hint or "").encode("utf-8")
).hexdigest()[:16] if (product_context or hint) else "no_context"
cache_key = f"{img_hash}:{context_sig}" if img_hash else None
```

Then use `cache_key` (not bare `img_hash`) as the first argument to both
`lookup_hash(...)` and `store_hash(...)` calls. `img_hash` itself keeps
being used everywhere else it already is (e.g. `tgw_logging.log_event`,
`vision_record["photo_hash"]`) — only the two cache calls' first argument
changes.

## Dataset
This restores correct cache-hit/miss behavior — no data is lost, this
prevents returning a stale result under a new context. No backfill of
already-cached stale entries; they'll simply not be hit under the new
composite key and a fresh call will happen next time, which is correct.

## Out of scope
- `image_hash.py` itself (`lookup_hash`/`store_hash` internals, the
  `image_hashes` table schema) — not touched, no migration.
- Any other part of `ai_identify.py`.
- Backfilling/invalidating existing cache rows.

## Acceptance (live)
1. Construct two calls with the SAME `img_hash` but different `hint`
   values (e.g. `hint="Nike shoe"` vs `hint="Adidas shoe"`) — confirm the
   computed `cache_key` differs between them.
2. Same `img_hash`, both `hint` and `product_context` empty/None in both
   calls — confirm `cache_key` is identical both times (`no_context`
   suffix), preserving today's correct behavior for the no-context case.
3. Simulate (mock `lookup_hash`/`store_hash`) a scenario: first call with
   no context stores under key A; second call for the same photo but with
   a real hint now present must NOT hit key A's cached result (different
   key) — confirms the actual bug scenario is fixed.

## Quota/risk
None — no new API calls; if anything this REDUCES unnecessary stale-cache
LLM calls being skipped when they shouldn't be, since a genuinely
different context will now correctly trigger a fresh call instead of a
false cache hit.
