# Packet: output-contract validation for tgw_simple_llm_jobs
Todo: #1576 (depends on #1574)   PP: PP-SIMPLEJOBS-001

## Context budget (ALL the model may load)
This packet + `src/tgw/mcp_server.py`'s `tgw_simple_llm_jobs` function and its two
helpers `_simple_llm_jobs_system_prompt`/`_simple_llm_jobs_user_prompt` (already in
the existing `todo/1574-simple-llm-jobs-mcp-tool` branch/worktree — work IN that
same worktree, don't create a new one) + the relevant slice of
`tests/test_mcp_server.py` (the existing 11 tests for this tool). Nothing else.

## Verified live before this packet was written
Read directly from the merged code (branch `todo/1574-simple-llm-jobs-mcp-tool`,
commit `24674d1`): the function currently does
`result = extract_json(raw)` then unconditionally
`return json.dumps({'ok': True, 'operation': operation, 'result': result})` —
no check that `result` actually honors what was asked. A `classify` call could
return a label outside `label_set`, or `extract_fields` could omit a requested key
or add an unrequested one, and it would still report `ok: True`.

## Spec
In `tgw_simple_llm_jobs`, after `result = extract_json(raw)` succeeds and before
returning `ok: True`, add operation-specific validation:

1. **`classify`**: if `label_set` was provided, verify `result.get('label')` is a
   member of `label_set`. If not, return
   `{'ok': False, 'error': f"model returned label {result.get('label')!r} not in label_set", 'raw': result}`.
   If `label_set` was NOT provided (caller's choice, open-ended classification),
   skip this check — there's nothing to validate against.
2. **`extract_fields`**: if `schema` was provided, verify every key in `schema`
   is present in `result` (missing key → contract violation). Do not require
   exact key equality — extra keys beyond `schema` are fine (the model adding
   more detail isn't a violation of what was asked; missing a requested field is).
   If any `schema` key is absent from `result`, return
   `{'ok': False, 'error': f"model response missing requested field(s): {sorted(missing)}", 'raw': result}`.
   If `schema` was NOT provided, skip this check.
3. All other operations (`summarize`, `compress_context`, `rewrite`,
   `rank_snippets`, `log_summary`) have no equivalent caller-supplied contract to
   check against in this packet — leave them as-is. Don't invent a contract for
   operations that don't have one; this packet only closes the two gaps named
   above.
4. On success (contract satisfied, or no contract to check), return exactly what
   the function returns today: `{'ok': True, 'operation': operation, 'result': result}`.

## Dataset
None — no ItemData/pipeline writes, this is a pure validation-logic addition.

## Out of scope
- Any change to the model call itself, prompts, or the other 5 operations.
- Retrying the LLM call on a contract violation — just report the failure; a retry
  policy is a separate, later decision if Dave wants one.
- Changing the `{ok, ...}` shape itself — this packet only tightens when `ok` is
  True vs False, not the envelope.

## Acceptance (live)
1. Real `classify` call with a `label_set` — confirm normal case still returns
   `ok: True` when the model picks a valid label (regression check against #1574's
   existing live evidence).
2. Construct a case (e.g. mock/monkeypatch `call_model` to return a label outside
   `label_set`, or find a live prompt that provokes it) showing the new check fires:
   `ok: False`, clear error naming the bad label.
3. Same pair of checks for `extract_fields`: one real call with `schema` where the
   model returns all requested keys (`ok: True`), and one constructed/mocked case
   where a key is missing (`ok: False`, error names the missing key).
4. Add these cases as new tests in `tests/test_mcp_server.py` (unit-level, mocking
   `call_model` is fine here — the live DeepSeek round-trip was already proven in
   #1574, this packet is testing the validation logic, not the model call).
5. Full test suite still green.

## Quota/risk
No new API surface — same `llm_deepseek` pool as #1574. This packet only adds
local validation logic, no new calls.
