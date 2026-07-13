# Result: 1287 ai-identify-model-var-clobber
Status: done
Todo: #1287   PP: PP-COHESION-001
Files touched:
- src/tgw/workers/ai_identify.py (rename local var `model` → `item_model` for the AI-extracted product-model field at 3 sites: `_str("model")` assignment, the `("model", ...)` tuple in the canonical-field write loop, and the `extracted.model` key in the vision_results record; provider-model `model` references at the availability check, logging, model call, and the two provenance f-strings `f"{provider}/{model}"` are untouched, exactly as specced)
- tests/test_ai_identify_model_provenance.py (new — reproduces the bug scenario per the packet's Acceptance steps: mocked LLM call returns a result dict with a `"model"` key set to a product model id, provider-model is `openrouter/anthropic/claude-4.5-vision`; asserts `identification_history[-1]["model"]` and `vision_results[-1]["model"]` both equal the LLM provenance string and NOT the corrupted `"openrouter/PS5-CFI-1215A"` value, `vision_results[-1]["extracted"]["model"]` still holds the product model, and the canonical `item["model"]` field is still populated with the product model)

Live evidence:
- Confirmed testing the worktree's own code, not the shared checkout:
  `PYTHONPATH=/opt/TGW/var/worktrees/1287-ai-identify-model-var-clobber/src python3 -c "import tgw.workers.ai_identify as m; print(m.__file__)"`
  → `/opt/TGW/var/worktrees/1287-ai-identify-model-var-clobber/src/tgw/workers/ai_identify.py`
- New test + existing ai_identify tests, with PYTHONPATH override:
  `PYTHONPATH=.../src python3 -m pytest -q tests/test_ai_identify_model_provenance.py tests/test_ai_identify_reidentify_flag.py`
  → `3 passed in 0.75s`
- Full offline suite, same PYTHONPATH override:
  `PYTHONPATH=.../src python3 -m pytest -q`
  → `2047 passed, 1 skipped, 1 warning in 33.56s` (no regressions)

Deviations from spec: none — exactly the 3 specified rename sites changed;
lines ~225/226/234/235/335/354 (provider `model`) left untouched, matching
the packet's explicit "do NOT change" list.

Out-of-scope findings filed: none (no new adjacent issues found; the
packet's own out-of-scope items — historical provenance backfill, other
functions in the file, the `_str()` helper — were left untouched per spec).
