# TGW agent entry point

1. Execute the operator's requested objective within its stated scope.
2. At startup, call the registered `tgw_context_status` and `tgw_context_current_task` MCP tools for the declared actor.
3. Use the shared `tgw-plan` skill for Plan, PP, Todo, reconciliation, and launch work.
4. Use the shared `tgw-review` skill for independent candidate and remediation review.
5. Work from the source, environment, Plan citations, and execution card bound by the current task context.
6. Report Plan, implementation, review/admission, deployment, live verification, and operator acceptance as separate states.
7. Record durable task and generational updates through the MCP and Plan records so every harness can resume current state.
8. Apply the active harness's own configuration for harness-specific behavior.
