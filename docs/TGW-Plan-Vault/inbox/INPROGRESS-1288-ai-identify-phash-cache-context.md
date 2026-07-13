# INPROGRESS: todo #1288 — ai_identify pHash cache key includes prompt context

Working in worktree `/opt/TGW/var/worktrees/1288-ai-identify-phash-cache-context`
on branch `todo/1288-ai-identify-phash-cache-context`. Fixed
`src/tgw/workers/ai_identify.py`'s cache-check block (~line 209-249): the
pHash cache key now folds `product_context`/`hint` into a composite
`cache_key = f"{img_hash}:{context_sig}"` (sha256[:16] of the context, or
`"no_context"` when both are empty) before calling `lookup_hash`/
`store_hash`, so a re-identify with newly available context no longer
silently returns a stale result from a different-context scan. `img_hash`
itself is unchanged everywhere else (logging, vision_record). Added
`tests/test_ai_identify_phash_cache_context.py` (3 new tests covering the
packet's 3 acceptance scenarios). Full offline suite passes: 2049 passed,
1 skipped. Result manifest written to
`docs/TGW-Plan-Vault/plan/packets/results/1288-RESULT.md`. Status: done.
