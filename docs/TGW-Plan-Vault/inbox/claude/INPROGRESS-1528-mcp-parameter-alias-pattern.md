tgw-coder, todo #1528, PP-KNOWLEDGE-001, branch todo/1528-mcp-parameter-alias-pattern.
Generalized Tigwa's existing per-field AliasChoices pattern (tgw_get_todo's
`agent`/`Agent`, tgw_add_suggest's `text`/`Text`) into a shared
`alias_field(name, *extra_aliases)` helper in src/tgw/mcp_server.py, applied
it to every scalar parameter on all 13 @mcp.tool()-decorated functions where
a title-cased client label is plausible, and refactored the two existing
cases plus tgw_mailbox_send's richer alias set (`To`/`Type`/`Todo` etc,
already Tigwa's own prior work) onto the same helper without changing any
canonical key or behavior. Added FastMCP-boundary `tool.run({...})`
regression tests (matching tests/test_mcp_server.py's existing convention)
for every newly-covered tool, plus a mailbox_send regression test proving
the extra shorthand aliases survived the refactor. 42/42 tests green in
tests/test_mcp_server.py; live stdio MCP round-trip verified
`tgw_get_item({"Sku": ...})` against a real ItemData SKU. Writing result
manifest now.
