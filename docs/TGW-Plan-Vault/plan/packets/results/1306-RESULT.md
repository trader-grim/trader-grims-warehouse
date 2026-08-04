# Result: 1306 alt-text-raw-preservation
Status: done
Todo: #1306   PP: PP-COHESION-001

Files touched:
- src/tgw/alt_text.py
- tests/test_alt_text.py

What was wrong: `cmd_alt_text()` called the vision model (`raw = call_model(...)`),
parsed `{alt_text, seo_caption}` out of it via `extract_json(raw)`, then wrote only
the parsed fields to `item['draft_listing']` — the raw model response was never
persisted anywhere, violating the Data Charter raw-preservation rule (Prime
Directive 1: "raw is permanent; derived is recomputable").

Exact fix: on every actual (non-cached) model call, `cmd_alt_text()` now appends a
record to `item['alt_text_results']` (a new list field, sibling to
`ai_identify.py`'s `vision_results[]` — same append-only, never-overwritten
pattern) containing: `photo`, `photos`, `photo_count`, `photo_hash`, `provider`,
`model`, `generated_at` (UTC timestamp), `extracted` ({alt_text, seo_caption}),
and the verbatim `raw_response` string. The record is built and appended just
before the existing `atomic_write_json(json_path, item, ...)` call already used
in this file (that call is `items.py`'s fenced atomic-write helper, imported via
`from .items import atomic_write_json` — not a new ad-hoc file, satisfies the
"fenced write path" constraint). A pHash cache hit (`cached is not None`) skips
appending — it reuses a prior call's already-recorded raw response rather than
re-persisting a duplicate; `raw` is `None` in that branch and the append is
gated on `if raw is not None`.

Field-naming choice: used a clearly-named sibling field `alt_text_results`
rather than reusing `ai_identify.py`'s `vision_results` — the two capture
different model calls (identification vs. alt-text/SEO generation) with
different `extracted` shapes, so forcing them into one shared list would mix
unrelated record schemas under one key. Noting this as the deviation-flag
called for by the packet: the packet allowed either choice; I judged a
sibling field cleaner given the differing payload shape.

Test added: `tests/test_alt_text.py::TestAltText::test_success_persists_raw_llm_response`
(asserts `alt_text_results[0].raw_response == _GOOD_RESPONSE` and that
`extracted.alt_text/seo_caption` match what was written to `draft_listing`) and
`test_cache_hit_does_not_append_new_raw_record` (asserts a pHash cache hit does
not add a new `alt_text_results` entry).

Live evidence:
- `PYTHONPATH=.../1306-alt-text-raw-preservation/src python -c "import tgw.alt_text as m; print(m.__file__)"`
  → resolved to the worktree's own copy, confirming tests exercise the edited code,
  not the shared checkout.
- `pytest -q tests/test_alt_text.py tests/test_alt_text_gemini_batch.py` → `72 passed`.
- Full suite: `pytest -q` → `1 failed, 2138 passed, 1 skipped` — the one failure
  (`tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter`)
  reproduces identically on the unmodified branch (`git stash` + rerun, both
  isolated and full-suite runs checked) — pre-existing test-isolation bug (a
  real shared quota-budget state file gets consumed by earlier tests in the
  same pytest process before this test's assertion runs; passes in isolation).
  Filed as todo #1370, unrelated to this packet's change.

Deviations from spec: field name `alt_text_results` chosen as a sibling to
`vision_results` rather than reusing that exact field (see field-naming choice
above) — flagged per packet instructions, not a silent substitution.

Out-of-scope findings filed:
- #1370 (PP-COHESION-001) — pre-existing flaky/order-dependent test in
  test_llm_google_direct.py, unrelated to alt_text.py.
- #1371 (PP-COHESION-001) — the Gemini Batch API path
  (`cmd_alt_text_gemini_batch` / `_apply_alt_text_result`) still discards the
  raw per-image response because `google_genai.parse_batch_results()` only
  returns already-parsed fields; fixing that requires touching
  `google_genai.py`'s batch-output parsing, out of this packet's scope.
- #1372 (PP-COHESION-001) — `alt_text.py`'s `atomic_write_json()` calls never
  pass `archive_root`, so invariant E5 (archive-before-overwrite) is not
  enforced on this file's item-JSON writes; pre-existing gap, unrelated to the
  raw-preservation bug, left untouched per "don't over-engineer."
