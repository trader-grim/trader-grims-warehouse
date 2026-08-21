# TGW authoritative context MCP — v1 (2026-08-15)

This runbook installs the read-only planning and coding context used by Codex
and other coding harnesses.  It does not replace the production TGW MCP.

## Authority boundaries

- Plan repository: `tgw-lib:/opt/TGW/library/plans`
- Approved Plan materialization:
  `tgw-lib:/opt/TGW/library/approved/<full-approved-commit>`
- Application source: `tgw-lib:/opt/TGW/tgw-lib/src/trader-grims-warehouse`
- Generated context runtime: `tgw-lib:/opt/TGW/tgw-lib/var/context`
- Production inventory MCP: `tgw-prod`, separately configured

No Plan checkout, CodeGraph source, or context runtime is created on tgw-prod.
The context MCP runs as a local stdio service for harnesses executing on
tgw-lib.  A future authenticated network adapter may proxy the same read-only
tools, but it must preserve these exact source bindings.

## Why this service exists

The governed execution platform Plan and the TGW Master Plan are different
scopes.  Reaching W11 in
`plan/execution/GOVERNED-EXECUTION-PLATFORM-v1.yaml` does not mean that every
Master Plan capability has been implemented.  Likewise, completing a PP or
Todo does not imply completion of its parent.

The `tgw-context` MCP makes that distinction machine-visible and supplies one
hash-bound task bundle containing:

1. the exact approved standalone Plan and derived Plan Graph;
2. current canonical Plan runbooks, the immutable approved-Plan variants, and
   relevant runbooks from the committed application source;
3. a CodeGraph snapshot of the committed canonical source tree; and
4. explicit Plan/PP/Todo/source/review/deployment/live scope semantics.

## Create or advance the approved materialization

Verify the approved commit first:

```bash
python3 /home/codex/.codex/skills/tgw-plan/scripts/verify_plan_root.py \
  /opt/TGW/library/plans <full-approved-commit> \
  sha256:<approved-solution-hash>
```

Then create a distinct detached worktree on tgw-lib:

```bash
git -c safe.directory=/opt/TGW/library/plans \
  -C /opt/TGW/library/plans worktree add --detach \
  /opt/TGW/library/approved/<full-approved-commit> \
  <full-approved-commit>
```

Never move an existing approved materialization to a new commit.  Create a new
path, verify it, update the MCP registration, then retire the old path only
after all harnesses report the successor binding.

## Required environment

```text
PYTHONPATH=/opt/TGW/tgw-lib/src/trader-grims-warehouse/src
TGW_CONTEXT_PLAN_ROOT=/opt/TGW/library/approved/<full-approved-commit>
TGW_CONTEXT_PLAN_REPOSITORY=/opt/TGW/library/plans
TGW_CONTEXT_PLAN_COMMIT=<full-approved-commit>
TGW_CONTEXT_PLAN_SOLUTION=sha256:<approved-solution-hash>
TGW_CONTEXT_SOURCE_ROOT=/opt/TGW/tgw-lib/src/trader-grims-warehouse
TGW_CONTEXT_RUNTIME_ROOT=/opt/TGW/tgw-lib/var/context
TGW_CONTEXT_ENVIRONMENT_CATALOG=/etc/tgw/execution-environment-catalog.json
TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH=sha256:<verified-catalog-hash>
```

`TGW_CONTEXT_PLAN_SOLUTION` must be the exact solution bound to the approved
Plan commit. `TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH` must be computed from the
installed catalog bytes and must match the signed environment receipt.  An
operator must never copy either value from conversation text or an unbuilt
candidate catalog.  The context MCP refuses startup when either binding is
missing or inconsistent.

Launch command:

```text
/opt/TGW/tgw-lib/src/trader-grims-warehouse/.venv/bin/python \
  -m tgw.context_mcp_server
```

The server is read-only and exposes no queue, repository mutation, deployment,
inventory mutation, or provider-effect tool.

## Codex registration

Remove only a stale `tgw-context` registration, then add the exact replacement:

```bash
codex mcp remove tgw-context
codex mcp add tgw-context \
  --env PYTHONPATH=/opt/TGW/tgw-lib/src/trader-grims-warehouse/src \
  --env TGW_CONTEXT_PLAN_ROOT=/opt/TGW/library/approved/<full-approved-commit> \
  --env TGW_CONTEXT_PLAN_REPOSITORY=/opt/TGW/library/plans \
  --env TGW_CONTEXT_PLAN_COMMIT=<full-approved-commit> \
  --env TGW_CONTEXT_PLAN_SOLUTION=sha256:<approved-solution-hash> \
  --env TGW_CONTEXT_SOURCE_ROOT=/opt/TGW/tgw-lib/src/trader-grims-warehouse \
  --env TGW_CONTEXT_RUNTIME_ROOT=/opt/TGW/tgw-lib/var/context \
  --env TGW_CONTEXT_ENVIRONMENT_CATALOG=/etc/tgw/execution-environment-catalog.json \
  --env TGW_CONTEXT_ENVIRONMENT_CATALOG_HASH=sha256:<verified-catalog-hash> \
  -- /opt/TGW/tgw-lib/src/trader-grims-warehouse/.venv/bin/python \
  -m tgw.context_mcp_server
```

Each individual harness account needs the equivalent registration.  Shared
source and Plan access comes through `tgw-coders`/`tgw-access`; credentials and
mutable user configuration remain per account.

## Required task startup

Before coding, reconciliation, or a completion claim:

1. call `tgw_context_bundle` with the actual task;
2. inspect its Plan commit, source commit/tree, and CodeGraph freshness hash;
3. retrieve the cited Plan/runbook chunks needed for the work;
4. query CodeGraph for affected symbols, dependencies, invariants, and receipts;
5. keep Master Plan, selected Plan/PP/Todo, implementation, review, deployment,
   live verification, and operator acceptance separate in the final report.

Missing or stale Plan/context is a repair HOLD. Advance or repair the canonical
Plan/evidence repository and refresh its MCP projection before resuming; never
work around the stale projection. Conversation memory, `CLAUDE.md`, an
embedded `docs/TGW-Plan-Vault`, a production release, or an actor worktree is
not a fallback Plan authority.

For a declared actor, call `tgw_context_onboarding` before enrollment. It returns
one hash-bound bundle containing the current canonical onboarding and manual
recovery runbooks, exact approved Plan/solution, current descendant evidence
HEAD, source, environment catalog, bootstrap policy, launcher, and all required
Context MCP environment values. A result of `SEED_REQUIRED` is intentional: the
bundle supplies immutable context but grants no enrollment effect. Only the
orchestrator's expiring one-use `actor-onboarding-seed/v1` may authorize the
bounded installation procedure.

## Verification

`tgw_context_status` must report:

- `host_role=tgw-lib-authoritative-context`;
- the exact approved Plan commit and a separately reported evidence HEAD;
- the canonical application commit/tree;
- a CodeGraph freshness hash bound to that same source commit/tree;
- `platform_w11_completion_implies_master_plan_completion=false`; and
- `narrow_plan_pp_or_todo_completion_implies_parent_completion=false`.

Run an MCP protocol smoke and confirm the seven-tool read-only surface:

- `tgw_context_status`
- `tgw_context_bundle`
- `tgw_context_plan_graph`
- `tgw_context_plan_source`
- `tgw_context_runbooks`
- `tgw_context_onboarding`
- `tgw_context_code_graph`
