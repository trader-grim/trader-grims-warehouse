# Packet: ai_identify.py stops clobbering the LLM provenance model id
Todo: #1287   PP: PP-COHESION-001   Track: framework batch (PP-HERMES-EA-001), first run of new sequence (cadence rule)

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/ai_identify.py` (the `process()`/`run()`
method spanning roughly lines 120-420, and its existing test file if one
exists) + the todo brief (`tgw todo brief 1287`). Nothing else.

## Spec
Two distinct concepts share the local variable name `model`:
1. The LLM provider's model id — set at line ~187
   (`provider, model = get_task_model(self.config, "ai_identify")`),
   read at lines ~225, ~226, ~234, ~235 (availability check, logging, the
   actual model call), and meant to also be read at lines ~339 and ~358
   (`"model": f"{provider}/{model}"` — provenance on
   `identification_history`/`vision_results` entries: which LLM produced
   this identification).
2. The AI-extracted item's product-model field — set at line ~262
   (`model = _str("model")`), meant to feed the canonical item field at
   line ~307 (`("model", model)`) and the `extracted` sub-dict at
   line ~368 (`"model": model or None`).

Line 262 reassigns the SAME local name, so by the time lines 339/358
execute, `model` holds the extracted product-model value, not the LLM
provider id — the provenance record silently records the wrong thing
(e.g. `"openrouter/PS5-CFI-1215A"` instead of
`"openrouter/gpt-4o-vision"`).

Fix: rename the extracted-item variable to `item_model` (parallel to the
existing pattern in this same file where `mpn` already maps to the
`model_number` field name, line ~309 — variable name and field name
already legitimately differ elsewhere in this function):
1. Line ~262: `model = _str("model")` → `item_model = _str("model")`
2. Line ~307: `("model", model)` → `("model", item_model)`
3. Line ~368: `"model": model or None,` (inside the `extracted` sub-dict)
   → `"model": item_model or None,`

Do NOT change lines ~225, ~226, ~234, ~235, ~339, ~358 — those correctly
reference the LLM provider `model` and must keep doing so now that it's
no longer being overwritten.

## Dataset
This IS a dataset-integrity fix, not dataset-neutral: it restores correct
provenance metadata (which LLM model actually produced each
identification) going forward. No backfill of already-corrupted historical
`identification_history`/`vision_results` entries — out of scope, flag as
a separate finding if you want it tracked, don't do it inline.

## Out of scope
- Any other function in `ai_identify.py`.
- Backfilling/correcting already-written historical provenance data.
- The `_str()` helper itself, or any other extracted field.

## Acceptance (live)
1. Construct/mock a `result` dict with a `"model"` key (e.g.
   `{"model": "PS5-CFI-1215A", "title": "...", ...}`) and run the
   identification logic (mocked LLM call is fine — this is testing local
   variable flow, not the LLM itself) with `provider="openrouter"`,
   provider-model `"anthropic/claude-4.5-vision"` (illustrative).
2. Confirm the resulting `identification_history` entry's `"model"` field
   equals `"openrouter/anthropic/claude-4.5-vision"` (the LLM provenance),
   NOT `"openrouter/PS5-CFI-1215A"`.
3. Confirm the resulting `vision_results` entry's top-level `"model"`
   field shows the same correct LLM provenance, AND its nested
   `extracted.model` field still correctly shows `"PS5-CFI-1215A"` (the
   product model — this value must NOT be lost, just correctly separated).
4. Confirm `item["model"]` (the canonical field written via the
   `_field`/`_val` loop) is still populated with the extracted product
   model, unaffected by the rename.

## Quota/risk
None — no new API calls, pure local-variable fix.
