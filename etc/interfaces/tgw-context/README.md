# tgw-context harness interface

Register `tgw.context_mcp_server` only with an exact approved standalone Plan
worktree and the canonical tgw-lib application repository, following
[`docs/runbooks/tgw-context-mcp-v1-20260815.md`](../../../docs/runbooks/tgw-context-mcp-v1-20260815.md).

The default transport is local stdio. Do not point the service at tgw-prod, an
actor worktree, a release, or an embedded Plan copy.
