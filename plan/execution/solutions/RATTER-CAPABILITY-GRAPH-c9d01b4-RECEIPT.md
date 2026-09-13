# Ratter capability graph — UNSOLVED (governed), partial closure

Todo 2008 (item R). Real, non-fabricated solve of the unified `ratter`
capability graph. Result is **not** dispatchable — this is the correct,
expected, and honestly-labeled outcome per the LEAF-11-RATTER reconciliation
(`§1`: "label the run UNSOLVED... never fabricate a solution hash").

- Plan-vault commit solved against: `c9d01b47212befee587bc421a46f47dcd9bbbc8c`
  (`ratter resolver-bootstrap: compose the unified ratter execution graph`,
  the commit that composed and contains
  `plan/execution/RATTER-CAPABILITY-GRAPH-v1.yaml` in
  `/opt/TGW/library/plans`).
- Provider catalog: `agent-services/catalogs/ratter-v1.json` (this repo),
  `plan_commit` bound to the same commit above.
- Native resolver: `tgw-native-exact@1` (`src/tgw/plan_solver.py`).
- Conformance resolver: pinned Luet `0.9.26-g`
  (`sha256:c227742324a92eef4767961a9e49f687195b13356881336cc83d006e43d86c87`,
  `/opt/TGW/tgw-lib/development-tools/luet-0.9.26-g`) via
  `src/tgw/plan_luet.py::conform`.
- Result: `complete=false`, `conformance_verified=false`,
  `dispatchable=false`. Luet reports `disagreement` because an incomplete
  native closure cannot claim agreement — this is `conform()`'s own defined
  behavior for a non-complete native solve, not a resolver bug.
- Unresolved (4 of 6 target capabilities; `code=UNSATISFIED`,
  `reason=MISSING_PROVIDER_DECLARATION`):
  - `plan.luet-canonical-resolution@1` — LEAF-11-4 (`provisioning_keeper` /
    Luet-as-producer) is `defined-not-dispatched`
    (`LEAF-11-RATTER-RECONCILIATION-20260908-v1.md` §3 row F): no build
    exists. `plan_luet.py::conform` is a different capability (pinned-Luet
    conformance check on the plan-resolver's own closure) and is not cited
    as evidence for this one.
  - `generation.atomic-identity@1` — LEAF-11-5 (`generation_microchip`) is
    `defined-not-dispatched` (row G): `context_generation_status.py` binds
    only 4 of 7 required elements and needs 11.4 + 11.3 first.
  - `workflow.per-job-change-handling@1` — LEAF-11-6 is
    `defined-not-dispatched` (row D): EVALUATE completed, buildable-now slice
    not yet built.
  - `workflow.role-identity-model@1` — PP-ROLES-001 is `status: PROPOSAL`;
    its own capability block states it "is not yet reconciled into the Plan
    capability graph" and "carries no solution hash and no execution card."
    No provider declared, per this task's instruction not to treat it as
    more solved than its own evidence supports.
- Satisfied by real providers (2 of 6 target capabilities, plus their 2
  direct dependency capabilities — none required by `target`, so absent from
  `unresolved`, but real and cited in `agent-services/catalogs/ratter-v1.json`):
  `continual-harness.core@1` (`operationally_verified`), plus
  `queue.durable-claims@1` / `evidence.immutable-receipts@1`
  (`operationally_verified`), and `workflow.model-selection-and-research@1`
  (`partial` — LEAF-11-8 near-term slice landed and tested, remaining scope
  tracked in the reconciliation doc row E).

## Plan-commit decision (explicit, per Todo 2008 step 3)

The ratter graph is **not** under the approved `GOVERNED-EXECUTION-PLATFORM`
Plan commit (`058e2f980201cc78245358e4901cf007063f2c29`) — that commit's own
`required_capabilities` do not mention any LEAF-11/ratter capability, and
binding to it would misrepresent an unrelated, already-ratified closure as
covering this graph. No operator-ratified Plan commit exists yet for
`PLAN-RATTER-CAPABILITY-GRAPH` — ratifying one is a separate operator
decision this task does not make. `scripts/solve_ratter_graph.py` was
therefore run with `--plan-commit` bound to the plan-vault commit that
contains the exact `RATTER-CAPABILITY-GRAPH-v1.yaml` document being solved
(`c9d01b47212befee587bc421a46f47dcd9bbbc8c`, current vault HEAD at the time
of this solve) — the honest choice available without inventing Plan
authority that does not exist.

## Worktree-boundary note

This session's actor contract confines all writes to the
`todo-2008` source-repo worktree. The Plan vault
(`/opt/TGW/library/plans`) was read from only (`git show`, read-only, to
load `RATTER-CAPABILITY-GRAPH-v1.yaml`) and was not written to. This
solution JSON and receipt are therefore staged here, in the source repo's
own `plan/execution/solutions/`, rather than committed into the vault's
`plan/execution/solutions/` alongside the other Plan solutions (e.g.
`GOVERNED-EXECUTION-PLATFORM-058e2f9.json`) — copying it there is a
follow-up step for an actor with write access to that separate repo.

This receipt records a solved, honestly-partial closure only. It does not
move any approved ref, materialize anything, or claim dispatch authority.
