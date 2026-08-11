---
schema: tgw-plan/v1
plan_id: PLAN-environment-recovery-readonly-cli
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T10:08:00-07:00
supersedes: null
registry_revision: sha256:5bab6383a444a8e459775388da6ac377edd42dcc7fcc86dbae1ea30c21ca9589
scope_hash: sha256:ba2558340f9dea2e400f133fad311d566654763bd944d60063b51620489ce313
tracks: [server]
dependencies: [PLAN-tgw-steward-context-canary, PLAN-environment-recovery-acceptance-monitor]
---
# TGW environment recovery — read-only steward and audit interface

This unit adds a structured source entry point for current-context queries and
acceptance auditing. It does not install a release, start a service, query history,
execute a procedure, import memory, or perform an external effect.

## Workflow contract
```yaml
exclusions:
  - release installation service activation or configuration deployment
  - procedure execution history lookup memory import or free-form prompt handling
  - production infrastructure repository or satellite mutation
work_units:
  - id: S7-recovery-readonly-cli
    title: Expose structured steward queries and acceptance auditing without effects
    kind: configuration
    requires: []
    owns: [interface:environment-recovery-readonly]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-recovery-readonly-cli
    treatment_version: "1"
    inputs:
      operations: [query, audit]
      history_lookup: absent
      procedure_execution: absent
    outputs:
      - id: environment-recovery-cli
        schema: tgw-environment-recovery-cli/v1
    acceptance:
      - id: readonly-cli-boundary-valid
        verifier: tgw.environment.recovery-readonly-cli/v1
        assertion: only_structured_query_and_audit_operations_are_reachable
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert only the local CLI source and entry point
plan_acceptance:
  - S7-recovery-readonly-cli:readonly-cli-boundary-valid
rollback: Revert the local interface; no installed runtime or external state was changed.
```
