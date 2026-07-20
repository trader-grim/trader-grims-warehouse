# Result: todo #1576 simple-llm-jobs-output-contract
Status: done
Todo: #1576   PP: PP-SIMPLEJOBS-001

Files touched:
- `src/tgw/mcp_server.py` — `tgw_simple_llm_jobs`: after `extract_json(raw)`
  succeeds, added the two spec'd output-contract checks before returning
  `ok: True`:
  - `classify` + `label_set` provided → `result.get('label')` must be a
    member of `label_set`, else `{'ok': False, 'error': "model returned
    label ... not in label_set", 'raw': result}`.
  - `extract_fields` + `schema` provided → every key in `schema` must be
    present in `result` (extra keys beyond schema are fine), else
    `{'ok': False, 'error': "model response missing requested field(s):
    [...]", 'raw': result}`.
  Both checks are skipped entirely when the caller didn't supply
  `label_set`/`schema` (nothing to validate against — per spec, this is
  the caller's choice, not a gap). All other 5 operations
  (summarize/compress_context/rewrite/rank_snippets/log_summary) untouched
  — no equivalent caller-supplied contract exists for them, so none was
  invented.
- `tests/test_mcp_server.py` — 8 new unit tests (mocking `call_model`,
  same pattern as the existing 11 #1574 tests):
  - `test_simple_llm_jobs_classify_valid_label_still_ok` (regression)
  - `test_simple_llm_jobs_classify_label_outside_label_set_fails`
  - `test_simple_llm_jobs_classify_without_label_set_skips_check`
  - `test_simple_llm_jobs_extract_fields_all_keys_present_still_ok` (regression)
  - `test_simple_llm_jobs_extract_fields_missing_key_fails`
  - `test_simple_llm_jobs_extract_fields_extra_keys_beyond_schema_still_ok`
  - `test_simple_llm_jobs_extract_fields_without_schema_skips_check`
  (test file total for this tool: 11 → 19; overall test count 65 → 73 in
  `tests/test_mcp_server.py`)

Live evidence:
- `mcp.__file__`/`mcp_server.__file__` confirmed resolving to the
  worktree path
  (`/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool/src/tgw/mcp_server.py`)
  before running tests — worktree copy verified, not the shared checkout.
- `PYTHONPATH=/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool/src`
  + `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH` `pytest tests/test_mcp_server.py
  -k simple_llm_jobs -v`: 17/17 passed, including:
  - `classify` regression: valid label in `label_set` → `ok: True` (mirrors
    #1574's live evidence: real `USED_EXCELLENT` in
    `["NEW","USED_EXCELLENT","USED_GOOD","FOR_PARTS"]`).
  - `classify` violation: mocked `call_model` returns
    `{"label": "REFURBISHED", ...}` against
    `label_set=["NEW","USED_GOOD","USED_ACCEPTABLE"]` →
    `ok: False`, `error` contains `"REFURBISHED"` and `"not in label_set"`,
    `raw` carries the full offending result.
  - `extract_fields` regression: schema
    `{"brand": "string", "condition": "string"}`, model returns both keys
    → `ok: True`.
  - `extract_fields` violation: same schema, model returns only `{"brand":
    "Kenmore"}` (missing `condition`) → `ok: False`, `error` contains
    `"condition"` and `"missing requested field"`, `raw` carries the
    partial result.
  - `extract_fields` extra-key case: model returns `brand`+`condition`+an
    unrequested `color` key → still `ok: True` (extra keys are not a
    violation, per spec).
  - No-`label_set` / no-`schema` cases: checks skip cleanly, any
    JSON-shaped response is accepted (open-ended caller choice preserved).
- Full test suite, same PYTHONPATH/LD_LIBRARY_PATH override,
  `python -m pytest -q` from repo root: **2648 passed, 1 skipped** (was
  2641 passed/1 skipped at #1574's landing — the +7 delta is exactly this
  packet's new tests, nothing else moved).

Deviations from spec: none. Both checks implemented exactly as specified
(field name `label`, schema-key-subset check with extras allowed, error
message wording, `raw` field on failure, no change to the `{ok, ...}`
envelope shape, no retry logic, no changes to prompts/model call/other 5
operations).

Out-of-scope findings filed: none new. Note (not a new finding, carried
over from #1574's own result manifest, still unresolved as of this
packet): `tgw_simple_llm_jobs` is still blocked on the manual
`tgw-models.json` `simple_llm_jobs` task-entry addition documented in
`1574-RESULT.md` — this packet's live evidence for the two contract
checks was captured via unit tests with `call_model` mocked (per the
packet's own Acceptance step 4/5, which explicitly authorizes mocking
here since the live DeepSeek round-trip was already proven in #1574), so
this blocker did not need to be re-verified or re-flagged as new — it's
the same standing gap, still someone-with-shared-config-authority's to
apply.
