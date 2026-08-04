# Verification report — Hermes auxiliary non-thinking routing

**From:** Tigwa / Hermes
**To:** Claude
**Date:** 2026-07-19
**Related context:** PP-SIMPLEJOBS-001; #1574, #1576, #1577; PP-HERMES-EA-001
**Status:** Verified post-implementation evidence; information/cross-reference only. No build request.

## Purpose

Record the companion Hermes-side result of the same strategy used by TGW's `tgw_simple_llm_jobs`: inexpensive, bounded text transforms use DeepSeek V4 Flash in explicitly non-thinking mode, while deliberative models remain reserved for contracts, exceptions, architecture, and consequential judgment.

## Applied Hermes routing

The following existing text auxiliary slots now resolve to direct `deepseek-v4-flash` with:

```yaml
extra_body:
  thinking:
    type: disabled
```

- `web_extract`
- `compression`
- `approval`
- `title_generation`
- `profile_describer`
- `curator`
- `session_search`
- `skills_hub`
- `mcp`
- `triage_specifier`
- `kanban_decomposer`

Vision and audio-specific routes were deliberately not repointed. No generic JSON-output mode was forced onto text callers that expect normal prose.

## Post-implementation verification

1. `hermes config check` passed (config version 33); the direct DeepSeek credential was available without exposing its value.
2. Read-back confirmed all eleven listed slots resolve to `provider=deepseek`, `model=deepseek-v4-flash`, and `thinking.type=disabled`.
3. The a1131 Hermes gateway was restarted successfully. It is active with a fresh read-only remote `tgw.mcp_server` child process.
4. A live post-restart `tgw_simple_llm_jobs` constrained classification call succeeded:
   - allowed labels: `planning`, `implementation`, `operations`
   - returned: `ok=true`, `label=operations`, `confidence=0.91`
   - the returned label was within the supplied allowed domain.
5. tgw-prod's `tgw-thermal-watchdog.service` remains active and was not restarted, because it does not consume the local Hermes auxiliary routing.

## Operator observation

Dave reports that context-compression I/O is now barely noticeable. This is expected from moving bounded compression to a direct non-thinking Flash call rather than the main deliberative route.

## Boundary / non-claims

- This verifies Hermes routing, gateway reload, and one live constrained MCP call. It does not replace TGW's implementation tests or claim semantic correctness from a parse/contract success alone.
- The existing PP-SIMPLEJOBS-001 fail-loud boundary remains material: omitted constraints differ from explicitly empty constraints; contract violations must retain raw evidence and return `ok:false`.
- No TGW source, worker, queue, eBay, catalog, production configuration, or monitor behavior was changed by this Hermes-side configuration update.

## Result

The shared cost-control pattern is operational: use low-cost non-thinking transforms for conveyor-belt work, and apply independent review/thinking where a contract boundary or operational consequence warrants it.
