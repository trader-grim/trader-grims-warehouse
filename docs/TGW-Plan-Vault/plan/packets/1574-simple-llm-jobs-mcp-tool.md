# Packet: `tgw_simple_llm_jobs` MCP tool (DeepSeek V4-Flash non-thinking)
Todo: #1574   PP: PP-SIMPLEJOBS-001   Track: new capability (PP-HERMES-EA-001 contract)

## Context budget (ALL the model may load)
This packet + `src/tgw/mcp_server.py` (whole file — it's the pattern reference, ~650
lines) + `src/tgw/apis/llm.py` (`get_task_model()`, `call_model()`,
`_call_deepseek_direct()`, `_load_deepseek_key()` only) + `/opt/TGW/config/tgw-models.json`
(read-only reference for existing task entries — `pm_intake`/`suggestions_classify`/
`pricing_comp_filter` are the deepseek_direct examples) + the research doc at
`docs/TGW-Plan-Vault/inbox/archive/DAVE-RESEARCH-text-processor-mcp-2026-07-19.md`
(has the proposed tool schema + call template — treat as a starting sketch, not gospel).
Nothing else — no master plan, no invariants.md (this is a new tool, not a
pipeline/ItemData/eBay change).

## Verified live before this packet was written
- `_call_deepseek_direct()` (`src/tgw/apis/llm.py`) currently sends only
  `{'model':..., 'messages':...}` — no `response_format` or `thinking` param support.
  Confirm this is still true; if DeepSeek's API rejects unknown fields, adding these
  needs to be additive/optional, not a breaking change to existing callers
  (`pm_intake`, `suggestions_classify`, `pricing_comp_filter` all call through this
  same function today).
- `deepseek_direct` is already configured direct-primary with automatic OpenRouter
  fallback (`call_model()`'s existing try/except path) — do not build a new
  fallback mechanism, reuse it.
- No `llm_deepseek` quota_budget entry was found in `tgw-api-config.json` at packet-
  authoring time — re-verify live; if a budget cap exists it governs this tool too
  (shared `llm_deepseek` quota bucket, not a separate one).
- Secrets: DeepSeek key comes only through `tgw.apis.secrets.get_api_key('deepseek')`
  — never a new reader.

## Spec
1. Add a new task config entry to `/opt/TGW/config/tgw-models.json` for this tool's
   task key (e.g. `"simple_llm_jobs": {"provider": "deepseek_direct", "model":
   "deepseek-v4-flash"}`) — model routing is config, never hardcoded (Settled
   Architecture rule). Pick the task key name to match whatever key
   `get_task_model()` is called with in step 2.
2. Add a new MCP tool `tgw_simple_llm_jobs` to `src/tgw/mcp_server.py`, following the
   existing `@mcp.tool()` pattern used by `tgw_get_item`/`tgw_search_items`/etc.
   Input schema (JSON Schema, matching the research doc's sketch):
   - `operation` (required, enum): `summarize`, `compress_context`, `extract_fields`,
     `classify`, `rewrite`, `rank_snippets`, `log_summary`
   - `text` (required, string)
   - `instructions` (optional, string)
   - `schema` (optional, object) — field spec for `extract_fields`
   - `label_set` (optional, array of strings) — allowed labels for `classify`
   - `items` (optional, array of strings) — candidates for `rank_snippets`
   - `max_output_tokens` (optional, integer)
3. Tool implementation calls `tgw.apis.llm.get_task_model()` + `call_model()` (or
   `_call_deepseek_direct()` directly if `call_model()`'s signature doesn't fit —
   your call, match whichever existing caller's pattern is the closest fit) with a
   system/user prompt built per-operation. Return JSON (a JSON string in the
   `TextContent`, matching how other tools in this file return `str`).
4. If DeepSeek's non-thinking mode requires an explicit request param (per the
   research doc's `extra_body={"thinking": {"type": "disabled"}}` example — verify
   against DeepSeek's actual current API docs, the research doc may be stale), extend
   `_call_deepseek_direct()`'s payload construction to include it **only when passed
   an optional new parameter**, defaulting to whatever preserves current callers'
   exact existing behavior. Do not change behavior for `pm_intake`/
   `suggestions_classify`/`pricing_comp_filter` unless the model's default is already
   non-thinking (verify, don't assume).
5. No new secrets file, no new per-provider reader — reuse `get_api_key('deepseek')`.
6. This is READONLY-safe by nature (text transform, no ItemData/eBay/queue writes) —
   still gate it the same way `tgw_clip_deliver`/`tgw_enqueue` are gated
   (`TGW_MCP_READONLY` check) if the pattern in this file applies uniformly to all
   tools; if it's applied selectively, match whatever the closest analogous tool
   (`tgw_search_full` — also read-only/no side effects) does.

## Dataset
None new to persist — this tool doesn't touch ItemData or the pipeline. Prime
Directive 1 doesn't apply here (no external data being discarded); it's a pure
transform utility.

## Out of scope
- Wiring this tool into any specific worker/queue (e.g. actually using it for
  listing-title generation) — that's separate follow-on work, not this packet.
- Building a "hard reasoning" escalation tool/tier — out of scope, mentioned only as
  a future idea in the research doc.
- Any change to `pm_intake`/`suggestions_classify`/`pricing_comp_filter`'s existing
  behavior beyond what step 4 requires for the shared function.
- Rate-limit/quota tuning beyond confirming what already exists.

## Acceptance (live)
1. Via the `tgw` MCP link (or a direct Python call to the tool function), invoke
   `tgw_simple_llm_jobs` with `operation="summarize"` and a real multi-paragraph text
   (e.g. an existing item description from `/opt/TGW/data/ItemData/`) — observe a
   real DeepSeek V4-Flash response, not a mock/stub.
2. Invoke with `operation="extract_fields"` + a `schema`, and `operation="classify"`
   + a `label_set` — confirm the returned JSON actually conforms to what was
   requested (real fields present, label is one of the allowed set).
3. Confirm via logs/`quota.record` that the call was attributed to `llm_deepseek`
   quota tracking, not a new untracked path.
4. Show the actual request/response (or a log excerpt) as the live evidence, per
   Prime Directive 4 — "tests pass" alone is not acceptance here.

## Quota/risk
Uses the shared `llm_deepseek` quota bucket (2026-07-08: direct-primary, OpenRouter
fallback on failure — see `LLM-Providers-Quotas.md`). No known per-provider free-tier
cliff for DeepSeek direct (unlike Google's ~20/day/project gotcha) but flag if you
find one live. Cost is per-token, ~$0.14/M — orders of magnitude cheaper than
Google/Anthropic/OpenAI equivalents per the research doc; still worth noting real
per-call cost in the acceptance evidence if easily available (response `usage`).
