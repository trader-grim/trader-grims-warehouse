# TGW Plan v2 working reference

The normative specification is
`/opt/TGW/library/plans/plan/SPEC-plan-capability-graph-v2.md` at the exact Plan commit.
Read it completely. This file is routing guidance, not a substitute authority.

## Required projections

- Capability catalog: stable capabilities, versions, providers, alternatives,
  requirements, conflicts, replacements, and preferences.
- Observed state: separately bound source, test, review, admission, deployment, live,
  and operator-acceptance evidence.
- Execution graph: bounded state transitions required to satisfy the selected target.
- Solution: complete selected closure, rejected alternatives/reasons, installed-state
  reuse, unresolved nodes, work units, Plan commit, and deterministic solution hash.

## Root semantics

Plan is the default root. PP and Todo are explicit narrower roots. A narrow root limits
execution but cannot imply parent completion. Todo results are partial providers when
the Plan is later solved.

## Execution-card fields

Bind identities and hashes for: card, root, solution, role/provider, Plan input and
commit, Plan Graph, CodeGraph, source commit/tree, environment manifest, authority,
conditions, receiver profile, worktree/object generation, lease/expiry/stop policy,
and receipt sink. Retrieve content from registered services on demand.

## Fail-closed conditions

Hold on missing/stale/mismatched resources, incomplete closure, unresolved conflict,
resolver disagreement, stale Plan commit, absent authority for declared effects, or
ambiguous external state. Never fill gaps from chat or embedded Plan copies.
