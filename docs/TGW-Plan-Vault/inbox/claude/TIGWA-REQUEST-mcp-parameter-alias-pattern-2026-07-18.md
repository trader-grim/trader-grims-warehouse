# Request: standard MCP parameter-alias pattern

**From:** Tigwa, at Dave's direction
**Scope:** TGW MCP server (`src/tgw/mcp_server.py`) and its tests only

## Why

Agents may receive title-cased parameter labels from an MCP client/tool schema even when the TGW server's canonical JSON property is lowercase. This caused real friction:

- `tgw_get_todo`: `Agent` was silently ignored and returned all todos instead of the requested agent.
- `tgw_add_suggest`: `Text` failed validation because the server requires `text`.

Tigwa has fixed these two observed cases with Pydantic `AliasChoices`, preserving lowercase canonical keys while accepting the title-cased aliases. The FastMCP-boundary regression tests were RED first, then green; `tests/test_mcp_server.py` is currently 23/23 passing. A fresh SSH-stdio MCP client call with `{"Agent":"__mcp_case_probe__"}` returned that exact agent.

## Requested follow-up

Please establish a reusable TGW MCP parameter-alias pattern so agents do not have to fumble for the exact field case.

Requirements:

1. Canonical/public MCP JSON-schema property names remain lowercase and stable.
2. Tool validation accepts the corresponding title-cased form as an alias where a client presents it.
3. Prefer a small shared helper/annotation pattern over repeated ad hoc signatures when it is genuinely cleaner; do not over-engineer a global argument-rewriting layer without need.
4. Add FastMCP-boundary regression coverage that invokes tools with both canonical and title-cased keys. Test behavior, not only generated schema snapshots.
5. Apply the pattern deliberately to existing MCP tool parameters that can be affected; do not alter business authority, queue behavior, SSH credentials, or tool permissions as part of this request.
6. Return a reviewable patch plus test evidence and state which tool parameters are covered.

This is a usability/IPC contract improvement, not a request for broad new capabilities.
