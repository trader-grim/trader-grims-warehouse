# Result: 1393 ebay-draft-non-json-aspect-fill
Status: done
Todo: #1393   PP: PP-DEADLETTER-001
Files touched:
- src/tgw/apis/ollama.py (extract_json: handle open-only ```/```json fence with no closing marker)
- tests/test_ollama_extract_json.py (new — 6 tests covering closed fence, open fence + complete JSON, open fence + genuine truncation, no fence, bare ``` fence, backtick-in-value edge case)

Live evidence:
- Confirmed live (queue_jobs, state_machine DB, 2026-07-14): 95 `ebay_draft`
  dead-letters with `error_detail` matching `HardFailure(...model returned
  non-JSON...)`. Pulled all 95 raw `error_detail` values via psycopg2
  (`SELECT entity_id, error_detail FROM queue_jobs WHERE queue_name='ebay_draft'
  AND state='dead_letter' AND error_detail LIKE '%non-JSON%'`), parsed each
  `HardFailure('...')` repr with `ast.parse`/`ast.literal_eval` to recover the
  real (unescaped) raw model-response text, then ran both the OLD and NEW
  `extract_json()` against each of the 95 samples:
  - OLD extractor: 0/95 parse (matches observed dead-letter state).
  - NEW extractor (this fix): **0/95 now parse either.** All 95 are
    genuinely truncated JSON, not merely unfenced-but-complete JSON. Raw
    response length across the 95 samples: avg ~127 chars, max 200 chars
    (well under the code's 2000-char truncation-for-logging cap — the
    *actual* model output itself is this short, not a logging artifact).
    Sample tail fragments confirm mid-object/mid-string cutoff, e.g.
    `'...  "Material": null,\n  "Original/Reproduction": "Original",\n
    "State": "California",\n  "Featured'` (cut off mid-key) and
    `'```json\n{\n  "Artist'` (cut off after ~20 chars).
  - New unit tests pass: `PYTHONPATH=.../src pytest -q
    tests/test_ollama_extract_json.py` → `6 passed in 0.47s` (fixed-fence
    regression guard, open-fence-complete success case, open-fence-truncated
    still raises, no-fence unaffected, bare ``` fence, backtick-in-value
    edge case).
  - Full offline suite, run against the worktree copy (verified via
    `tgw.apis.ollama.__file__` resolving under
    `/opt/TGW/var/worktrees/1393-ebay-draft-non-json-aspect-fill/src`, and
    `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=.../src pytest -q`):
    **2216 passed, 1 skipped, 0 failed, 399.30s** — zero regressions.
  - Invariant C11 check: `ebay_draft.py`'s aspect-fill failure path already
    persists a durable, queryable finding on `extract_json()` failure — the
    `HardFailure` is caught by `worker_base.py`'s `run()` loop and written
    via `state_machine.mark_dead_letter(job_id, self.owner, repr(exc))`
    (queryable in `queue_jobs.error_detail`, which is exactly how the 95
    samples were found for this packet) plus `tgw_logging.log_event(
    'job_dead_letter', ...)`. No change needed here — confirmed already
    correct, not just a log line.

Deviations from spec: none. Spec item 2 (investigate whether the aspect-fill
call is token-limited) was completed as an investigation only, per the
packet's explicit instruction not to make a config change myself — see
finding below, filed as its own todo rather than applied.

Out-of-scope findings filed:
- **#1405** (PP-DEADLETTER-001): the fence fix in this packet does NOT
  resolve the underlying dead-letter cause — 0/95 of the 95 sampled
  failures are fixable by fence-stripping alone; all 95 are genuine
  truncation of the model's own output (avg 127 chars / max 200 chars raw
  response). The aspect-fill call routes through `bulk_classify` →
  `google_direct` / `gemini-2.5-flash-lite`
  (`/opt/TGW/config/tgw-models.json`, read-only per packet scope). Read
  `tgw.apis.llm._call_google_direct()`: it passes **no**
  `maxOutputTokens`/`thinkingConfig` to `client.models.generate_content()`
  at all — a plausible root cause is Gemini 2.5's internal "thinking"
  token budget consuming the entire completion before any visible JSON
  text is emitted (a known Gemini 2.5 flash-lite gotcha). This is
  explicitly **not** a simple `tgw-models.json` value edit: the call site
  has no code plumbing today to accept a per-task
  `max_output_tokens`/`thinking_config` parameter at all, so a config-only
  fix isn't possible until that plumbing is added — filed as its own
  finding/todo (#1405) for a follow-up packet rather than touched here
  (out of this packet's declared scope: "do not touch tgw-models.json
  directly," and this isn't a tgw-models.json-only fix anyway).
