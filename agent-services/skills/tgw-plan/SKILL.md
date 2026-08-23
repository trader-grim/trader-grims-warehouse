---
name: tgw-plan
description: Plan, reconcile, resolve, or launch TGW work using the standalone tgw-plan/v2 capability graph. Use when creating or revising a Plan or PP, selecting a Plan/PP/Todo execution root, reconciling existing implementations, resolving interconnected PP dependencies, producing bounded planning leaves, or generating compact execution cards. Applies to every harness; never use an embedded Plan copy or CLAUDE.md as Plan authority.
---

# TGW Plan

Use the standalone Plan as canonical intent and the capability resolver as execution
authority. Default to the approved Plan as the execution root. Use a PP or Todo root
only when the operator explicitly narrows execution.

## Establish session generation

Before any Plan-role work on the first use in an ordinary harness session, call the
installed `tgw_context_status` tool with no arguments. Surface its exact
`generation_status.line` to the operator before reading or resolving Plan work. A
`CURRENT` result permits the role to proceed under its bound evidence. An
`UPDATE_PENDING`, `RESTART_REQUIRED`, `MIXED`, or `HOLD` result constrains claims and
actor-side effects to that reported state; it never blocks, delays, or overrides an
explicit owner command. If the status call is unavailable or malformed, hold governed
Plan dispatch and report the missing binding rather than substituting launcher stderr,
conversation memory, or a filesystem guess.

## Establish the binding

1. Call `tgw_context_bundle` for the actual Plan task and require its exact approved
   Plan, solution, evidence-head Plan/tree, source/tree, catalog, and generation
   binding to agree with `tgw_context_status`.
2. Resolve the selected root and capability closure with `tgw_context_plan_graph`.
   Keep the immutable approved Plan and the current evidence head distinct; never
   silently move the approved ref when evidence advances.
3. Retrieve each required Plan, PP, target, process, or amendment source through
   `tgw_context_plan_source`, choosing its exact `approved-plan` or `current-plan`
   authority. Use `tgw_context_runbooks` only for admitted runbook paths. Require each
   response's authority, commit, tree, confined path, blob/content hash, and complete
   paginated content to match the bundle before using it.
4. Refuse direct filesystem or Git reads, embedded source-tree Plan copies,
   conversation memory, worktrees, releases, archives, and local verification scripts
   as Plan authority or fallback. A separately routed implementation or recovery card
   may authorize bounded source/filesystem inspection as implementation evidence; it
   never changes or reconstructs Plan authority.

## Select the root

- **Plan** (default): solve and execute the complete selected target.
- **PP** (explicit): execute a coherent narrower capability program without implying
  Plan completion.
- **Todo** (explicit): make a bounded immediate fix, diagnostic, or enabling change.
  Treat its receipt as partial installed-state evidence. The later Plan must retain,
  extend, migrate, supersede, or replace it to meet the full specification.

## Reconcile before planning new work

Use the authority-bound bundle and Plan graph to enumerate admitted implementation,
runtime, receipt, supersession, and reconciliation evidence before classifying a
provider as absent. Retrieve cited Plan/runbook material only through the bounded
Context tools above. When the graph routes a separate implementation or recovery
capability, that card may inspect its exact admitted source and bounded recovery
locations; never search arbitrary files, worktrees, archives, or Git history from the
Plan role. Compare semantics and evidence, not filenames, branch names, or age. Keep
design, implementation, test, review, admission, deployment, live, and operator-
acceptance evidence separate.

If recovered intent conflicts materially, present the smallest exact choice to the
operator. Do not invent a resolution. Missing implementation detail under already
approved intent is build work, not an operator decision.

## Format and resolve

Follow `references/plan-v2.md`. Preserve global alternatives until the complete
closure is evaluated. Distinguish `UNKNOWN_CAPABILITY`, `UNSATISFIED`, `BLOCKED`, and
`CONTRADICTORY_RESOLUTION`.

Require a real `tgw-plan-solution/v1` artifact bound to the exact Plan commit before
calling a target solved or dispatchable. Narrative and a valid YAML graph are not a
solution. Until an admitted resolver exists, label the run `UNSOLVED` and create the
resolver as an authorized bootstrap work unit; never fabricate a solution hash.

Unplanned nodes outside the selected closure remain visible and do not block. A
required unplanned node becomes one bounded planning leaf with its dependency path,
known evidence, missing intent, and exact decision. Pause only its dependent branch.

## Launch

Plan approval authorizes all effects explicitly declared by the exact approved Plan
commit and complete solution. Do not insert ceremonial approvals between build,
review, integration, and immutable candidate installation. Hold undeclared external,
provider, destructive, or broadened effects.

Emit compact hash-bound execution cards rather than copied context. Each card points
to the Plan input and commit, solution, Plan Graph, CodeGraph, source tree, environment,
authority/conditions, receiver profile, worktree generation, and receipt sink. Missing
or stale resources hold dispatch.

Use harness-neutral roles. Any qualified provider may implement, review, verify, or
operate; independence is an evidence/context property, not a vendor assignment.

Candidates are closed commits. Install the exact reviewed candidate for operator
testing. A defect produces a new commit and review. Complete means the function meets
the specification, not that a Todo or stage ended.

## Preserve and retire

Archive remnants only after survivor admission, evidence ingestion, runtime-reference
cutover, and operator acceptance. Create immutable manifests and leave no duplicate
Plan authority, candidate worktree, skill, service, or stale operational reference
outside its canonical location.
