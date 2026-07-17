# Result: 1405 google-direct-thinking-config
Status: done
Todo: #1405   PP: PP-DEADLETTER-001

## Files touched
- `src/tgw/apis/llm.py` — added `get_task_generation_config(cfg, task)`;
  `_call_google_direct()` now accepts `max_output_tokens`/`thinking_budget`
  params and only adds `max_output_tokens`/`thinking_config` keys to the
  Gemini `generate_content()` config when set (default `None` = unchanged
  behavior for every other task); `call_model()`'s two `google_direct`
  call sites (primary path + operator-emergency-reserve fallback) now read
  `get_task_generation_config(cfg, task)` and pass it through. This is a
  general per-task field — no `bulk_classify`-specific branch in the code.
- `tests/test_llm_google_direct.py` — 15 new tests: `_call_google_direct`
  omits both keys by default, passes `max_output_tokens` through, passes
  `thinking_config={'thinking_budget': N}` through; `get_task_generation_config`
  (absent/unknown task → `{}`, present → dict passthrough); `call_model()`
  dispatch wiring (config reaches `_call_google_direct`, absent config →
  `None`s passed).

## Live evidence

**Pre-flight verification (live, before any code change) — confirms the
todo's root-cause claim, corrected for actual current distribution:**
```
SELECT count(*) FROM queue_jobs WHERE queue_name='ebay_draft'
  AND state='dead_letter' AND error_detail LIKE '%non-JSON%';
--> 96 (todo said 95 -- one more accumulated since 2026-07-14, not a
    discrepancy worth blocking on)
avg(length(error_detail))=217.26, max=891, min=85
```
(error_detail includes a ~94-char fixed prefix + SKU, so real raw-response
lengths run roughly 0–800 chars — wider tail than the todo's "avg ~127,
max 200" but same shape: genuinely truncated mid-JSON, not a fenced/full
response tripped up only by markdown fences.) Sampled 5 raw `error_detail`
values directly — every one is JSON cut off mid-key/mid-string
(`"Features": nh,` — an invalid unquoted token consistent with truncation
landing mid-word), confirming #1393's fence-stripping fix alone cannot
recover these. `_call_google_direct()` read in full before any edit:
confirmed zero `max_output_tokens`/`thinking_config` plumbing existed —
`config={'system_instruction': system_prompt}` was the entire config dict
passed to `generate_content()`.

**Post-fix live acceptance — real dead-lettered SKU `tgw201704281425329`
(Dancing and Romancing Through the '40s cassette, category 176983, 27
aspects, 4 real photos), run through the ACTUAL `ebay_draft` code path
(`_build_prompt` → `_aspect_fill_photos` → `_encode_resized` →
`call_model('bulk_classify', ...)`), as `tgw` user, with
`PYTHONPATH`/`LD_LIBRARY_PATH` pointed at the worktree:**
- Baseline call (no generation config, replicating today's live behavior):
  returned complete valid JSON this run (785/775 chars) — confirms the
  failure is intermittent/stochastic (Gemini's thinking-budget consumption
  varies call to call), matching the DB evidence that only 96 of many more
  ebay_draft aspect-fill calls hit this, not all.
- Fixed call (`thinking_budget=0, max_output_tokens=4096` via the new
  `_call_google_direct` params, exercised through the real code path):
  returned complete valid JSON (787/802 chars), `extract_json()` parsed
  cleanly both times.
- Direct SDK inspection (`usage_metadata.thoughts_token_count`) on this
  particular call returned `None` (not populated for this response) —
  could not directly confirm non-zero thinking-token consumption on the
  specific runs made live here. The plumbing is unconditionally correct
  either way (verified by the unit tests asserting the exact `config`
  dict keys sent to `generate_content()`); whether "thinking budget" is
  the literal mechanism or Gemini's non-determinism has some other
  truncation trigger, `thinking_budget=0` + `max_output_tokens` are the
  standard Gemini 2.5 flash-lite mitigations for this class of gotcha and
  cost nothing to apply.
- Unit tests: `pytest tests/test_llm_google_direct.py` and
  `pytest tests/ -k "llm or google or model"` (excluding pre-existing
  unrelated collection failures, see below) — 24 / 64 passed respectively,
  0 failures, confirmed run against the worktree copy
  (`tgw.apis.llm.__file__` under
  `/opt/TGW/var/worktrees/1405-google-direct-thinking-config/`).

## Deviations from spec
- **Config value NOT applied to the live `tgw-models.json`.** The todo's
  body explicitly frames the actual `thinking_budget=0`/`max_output_tokens`
  value for `bulk_classify` as "a CONFIG change once the plumbing exists,"
  but this task-brief's generic Constraints section says "Never touch
  config files, secrets, or eBay OAuth scopes" — and `tgw-models.json`
  lives outside git (`/opt/TGW/config/tgw-models.json`, not tracked in
  this repo), so touching it is a live production change, not something
  a branch commit captures or reverts. Given that conflict, I took the
  conservative reading and left the live config untouched — the plumbing
  is fully inert for every existing task until a task's `generation` field
  is actually set, so this doesn't change current production behavior.
  **Recommended config to apply once this branch is reviewed/merged**
  (Dave/stitcher's call, not mine):
  ```json
  "bulk_classify": {
    "provider": "google_direct",
    "model": "gemini-2.5-flash-lite",
    "generation": {
      "max_output_tokens": 4096,
      "thinking_budget": 0
    }
  }
  ```
- Full repo `pytest -q` could not be run to completion: (1) it hangs/times
  out past 2 minutes even with the broken modules excluded (pre-existing,
  unrelated to this change — not investigated further, out of scope for
  this packet); (2) 6 test files fail to collect at all due to an
  unrelated missing module (see Out-of-scope findings). Ran the narrowest
  correct-scope subset instead (`test_llm_google_direct.py` in full, plus
  a `-k "llm or google or model"` sweep across the whole suite) — 0
  failures in either.
- 96 dead-letters found live vs. the todo's "95" — one more accumulated
  since 2026-07-14; not a meaningful discrepancy, noted per invariant C11
  (verify live, don't trust the number silently).

## Out-of-scope findings filed
- #1496 (PP-DEADLETTER-001): `src/tgw/ebay/category_aspect_migration.py`
  is untracked in git in the shared checkout (never committed) — any
  fresh `git worktree` fails to collect 6 test files
  (`test_category_context_conditions.py`, `test_condition_options.py`,
  `test_condition_remap.py`, `test_fence.py`, `test_http_server.py`,
  `test_local_ts.py`) that import `tgw.http_server`.

## Quota/usage flag
Live acceptance testing made **3 real `google_direct`/`gemini-2.5-flash-lite`
paid-API calls** against SKU `tgw201704281425329`'s real photos (2 via
`_call_google_direct`/`call_model`, 1 raw SDK inspection call) — small,
paid direct-key usage (not the free-tier operator reserve), flagged per
feedback-api-quota-flagging. No config or code was pushed to production;
these were read-only LLM calls, no eBay writes.
