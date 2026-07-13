# Result: 1288 ai-identify-phash-cache-context
Status: done
Todo: #1288   PP: PP-COHESION-001
Files touched:
- src/tgw/workers/ai_identify.py
- tests/test_ai_identify_phash_cache_context.py (new)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1288-ai-identify-phash-cache-context.md (new, breadcrumb)

Live evidence:
- `PYTHONPATH=/opt/TGW/var/worktrees/1288-ai-identify-phash-cache-context/src python3 -c "import tgw.workers.ai_identify as m; print(m.__file__)"`
  confirmed resolving to the worktree copy before any test run.
- `python3 -m pytest -q tests/test_ai_identify_phash_cache_context.py tests/test_ai_identify_reidentify_flag.py tests/test_ai_identify_taxonomy_quota_propagation.py`
  → `7 passed in 0.50s`.
- Full offline suite: `python3 -m pytest -q` → `2049 passed, 1 skipped, 1 warning in 34.49s`.
- New test file covers exactly the packet's 3 acceptance scenarios:
  1. `test_cache_key_differs_for_different_hints` — same img_hash, hints
     "Nike shoe" vs "Adidas shoe" → different computed `cache_key`.
  2. `test_cache_key_identical_when_no_context` — both hint/product_context
     empty in both calls → identical `cache_key` (`fakehash:no_context`)
     both times, preserving prior no-context behavior.
  3. `test_new_context_does_not_hit_stale_no_context_cache_entry` — first
     call (no context) stores under `fakehash:no_context`; second call for
     the same photo with a real hint now present does NOT hit that stale
     entry (writes to a distinct key, stale entry untouched) — confirms the
     bug scenario is fixed.

Deviations from spec: none — implemented the packet's exact code block
(sha256[:16] context signature, `"no_context"` sentinel, `cache_key` as
first arg to both `lookup_hash`/`store_hash` calls, `img_hash` left
unchanged everywhere else it's used).

Out-of-scope findings filed: none.
