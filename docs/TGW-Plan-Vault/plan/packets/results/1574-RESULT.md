# Result: todo #1574 simple-llm-jobs-mcp-tool
Status: partial
Todo: #1574   PP: PP-SIMPLEJOBS-001

Files touched:
- `src/tgw/mcp_server.py` — new `tgw_simple_llm_jobs` MCP tool (operations:
  summarize, compress_context, extract_fields, classify, rewrite,
  rank_snippets, log_summary), per-operation system/user prompt builders,
  registered unconditionally (matches `tgw_search_full` — read-only, no
  TGW_MCP_READONLY gate).
- `tests/test_mcp_server.py` — 11 new tests for the tool (operation
  validation, per-op prompt content, JSON-fence stripping, non-JSON/
  exception error paths, READONLY-registration, capitalized-arg alias
  boundary test); `EXPECTED_TOOLS`/count updated 14→15.
- **NOT applied** (see Deviations): `/opt/TGW/config/tgw-models.json` —
  the `simple_llm_jobs: {provider: deepseek_direct, model: deepseek-v4-flash}`
  task entry the spec's step 1 requires. This file lives outside the git
  repo entirely (`/opt/TGW/config/`, live shared production config, not
  under version control) — attempting the `Edit` was intercepted by the
  worktree-guard hook's `ask` gate (correctly: it's outside both allowed
  worktree roots and is a genuinely shared-checkout-adjacent file) and the
  permission was declined in this non-interactive run. Per the hook's own
  guidance ("if this is meant to be a genuinely shared-checkout change,
  confirm explicitly with Dave/Tigwa before proceeding") and Prime
  Directive 3, I did not force it. **Someone with authority to edit that
  live file needs to apply this one-line addition** (exact JSON, insert
  before the closing `}`, after the `pricing_comp_filter` entry):
  ```json
  "simple_llm_jobs": {
    "provider": "deepseek_direct",
    "model": "deepseek-v4-flash"
  }
  ```
  Until this lands, `tgw_simple_llm_jobs` will raise
  `KeyError: No models['simple_llm_jobs'] entry in tgw-models.json` when
  called for real (get_task_model()'s designed fail-loud behavior — not a
  bug, just blocked pending this config step). All acceptance evidence
  below was captured by injecting this exact entry into an in-process
  `cfg['models']` dict (never writing to the live file), which exercises
  the tool's actual code path end-to-end (real HTTP call to
  api.deepseek.com, real quota/ai_usage recording) — only the "does
  get_task_model find the entry" wiring is unverified pending the manual
  config step above.

Live evidence:
- Live DeepSeek V4-Flash calls (worktree copy verified — `mcp_server.__file__`
  printed `/opt/TGW/var/worktrees/1574-simple-llm-jobs-mcp-tool/src/tgw/mcp_server.py`
  before the calls):
  - `operation=summarize` on a real multi-paragraph vintage-mixer item
    description → `{"summary": "Late 1970s Kenmore stand mixer in good
    working condition with cosmetic wear.", "key_points": [...]}`
  - `operation=extract_fields` + schema
    `{brand, product_type, condition, capacity_quarts}` →
    `{"brand": "Kenmore", "product_type": "stand mixer", "condition":
    "Used - Good", "capacity_quarts": 4.5}` — all 4 requested fields
    present, no extras.
  - `operation=classify` + `label_set=["NEW","USED_EXCELLENT","USED_GOOD",
    "FOR_PARTS"]` → `{"label": "USED_EXCELLENT", "confidence": 0.92,
    "reason": "..."}`  — returned label is one of the allowed set.
  - All three responses were clean, directly-parseable JSON with no
    chain-of-thought/reasoning leakage — confirms (live, not assumed) that
    `_call_deepseek_direct()`'s current bare `{model, messages}` payload
    is already sufficient for non-thinking-mode-equivalent behavior with
    `deepseek-v4-flash`; per spec step 4, since this held true live, no
    change was made to `_call_deepseek_direct()`'s payload construction
    (preserves pm_intake/suggestions_classify/pricing_comp_filter exactly).
- Quota/usage attribution (Postgres `ai_usage` table, confirmed via
  `psql state_machine`):
  ```
  task             | provider        | model              | success | prompt_tokens | completion_tokens | total_tokens
  simple_llm_jobs  | deepseek_direct | deepseek-v4-flash  | t       | 258           | 227               | 485
  simple_llm_jobs  | deepseek_direct | deepseek-v4-flash  | t       | 256           | 156               | 412
  simple_llm_jobs  | deepseek_direct | deepseek-v4-flash  | t       | 254           | 127               | 381
  ```
  and `/opt/TGW/var/run/quota-state.json`'s `pools.llm_deepseek.spent`
  incremented by 3 for this run's caller (`pid:433013`), landing in the
  same shared pool as the existing `worker:ebay_price:operator` caller —
  confirms attribution to `llm_deepseek`, not a new untracked quota path.
  Real per-call cost at DeepSeek's ~$0.14/M blended rate: ~485 tokens ≈
  $0.00007 (summarize call) — negligible, as expected.
- `pytest -q` (worktree copy, `PYTHONPATH`+`LD_LIBRARY_PATH` overridden per
  contract): 2641 passed, 1 skipped, offline, full suite — including the
  11 new `tgw_simple_llm_jobs` tests and the updated tool-count guard.

Deviations from spec:
- **Step 1 (tgw-models.json config entry) not applied to the live file** —
  see Files touched above for the exact reasoning and the one-line diff
  someone with shared-config edit authority needs to apply. This is the
  reason for `Status: partial` rather than `done`.
- **Step 4 (thinking-mode param)**: verified live that no `extra_body`/
  `response_format` change to `_call_deepseek_direct()` was needed —
  confirmed by observation, not left unverified. No code change made
  here, as instructed when the assumption doesn't hold.
- `max_output_tokens` argument is accepted by the tool's schema (per spec)
  but is currently advisory-only — not yet wired into
  `_call_deepseek_direct()`'s request payload (that function has no
  `max_tokens` param at all; adding one would be a second, separately-
  reviewable change to a function shared by three existing callers, and
  the packet's Out-of-scope explicitly excludes "any change to
  pm_intake/suggestions_classify/pricing_comp_filter's existing behavior
  beyond what step 4 requires" — step 4 only covers the thinking-mode
  param, not max_tokens). Flagging this explicitly rather than silently
  ignoring the parameter.

Out-of-scope findings filed: none — no adjacent broken thing found during
this task. (The tgw-models.json live-edit gap above is not an adjacent
finding, it's this packet's own step 1 blocked in-run; recorded here, not
filed as a new todo, since resolving it is simply "someone applies the
one-line edit above," not new work requiring its own PP/todo.)
