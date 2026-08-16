# TGW authoritative context MCP v1

`tgw.context_mcp_server` is the local, read-only context service for coding
harnesses on tgw-lib. It supplies an exact standalone-Plan commit, a derived
Plan Graph, committed application runbooks, and a CodeGraph snapshot of a
committed application source tree. It does not replace the production TGW MCP
and exposes no queue, repository mutation, deployment, inventory mutation, or
provider-effect tools.

## Required binding

Set the following absolute-path environment variables when registering a
harness. The commit must be the exact approved Plan commit, not merely current
Plan HEAD.

```text
PYTHONPATH=/opt/TGW/tgw-lib/src/trader-grims-warehouse/src
TGW_CONTEXT_PLAN_ROOT=/opt/TGW/library/approved/<approved-commit>
TGW_CONTEXT_PLAN_REPOSITORY=/opt/TGW/library/plans
TGW_CONTEXT_PLAN_COMMIT=<approved-commit>
TGW_CONTEXT_SOURCE_ROOT=/opt/TGW/tgw-lib/src/trader-grims-warehouse
TGW_CONTEXT_RUNTIME_ROOT=/opt/TGW/tgw-lib/var/context
```

Create a new detached approved materialization for a successor commit; do not
move an existing approved worktree in place. The service fails closed when the
materialization is dirty, its commit differs from the configured commit, or the
commit is absent from the standalone Plan repository.

Each coding task begins with `tgw_context_bundle`. Inspect its Plan and source
commit/tree bindings, retrieve cited sources, and keep Plan, PP/Todo,
implementation, deployment, and live acceptance separate. Platform W11 does
not imply completion of the TGW Master Plan.
