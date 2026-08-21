# tgw-context harness interface

Use [the context MCP runbook](../../../docs/runbooks/tgw-context-mcp-v1-20260815.md)
to register `tgw.context_mcp_server` for each harness account on tgw-lib.

The interface is local stdio by default.  Its configuration must bind an exact
approved Plan worktree and the canonical tgw-lib application repository.  Do
not point it at tgw-prod, an actor worktree, an application release, or an
embedded Plan copy.

All harnesses call `tgw_context_onboarding` for the declared actor and then
`tgw_context_bundle` before Plan-derived coding. Tool output is source
navigation and evidence; it grants no approval or effect authority.

Never work around a stale Plan or MCP projection. Repair it and preserve the
predecessor binding as the rollback position. Before the W19 governed coding
fleet is active, that projection maintenance does not require quiescence.
