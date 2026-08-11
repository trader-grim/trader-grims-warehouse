---
schema: tgw-plan/v1
plan_id: PLAN-environment-recovery-acceptance-monitor
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T10:00:00-07:00
supersedes: null
registry_revision: sha256:8747c6bdcff7efe6534ff47e592d1c67c50787cb7fae048c0d9510e7c7002de5
scope_hash: sha256:3db81a5f4c1771be6e7ea46da497c2ed4d0a28b6816c951eb58b35b5f29ab968
tracks: [server, satellite]
dependencies: [PLAN-environment-cleanup-program]
---
# TGW environment recovery — evidence-backed acceptance monitor

This read-only unit reports proved, missing, and failed program requirements. Its
successful execution proves the audit is truthful; it does not prove the recovery
program complete or create final human acceptance.

## Workflow contract
```yaml
exclusions:
  - completion claim while any program requirement is missing or failed
  - synthetic satellite packages reviews dispositions or human acceptance
  - external access mutation memory lookup or execution
work_units:
  - id: A1-program-acceptance-monitor
    title: Audit server satellite and human acceptance evidence without inference
    kind: verification
    requires: []
    owns: [audit:environment-recovery-acceptance]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-recovery-acceptance-audit
    treatment_version: "1"
    inputs:
      program_plan_id: PLAN-environment-cleanup-program
      expected_satellites: [catnanny, helicrew]
      absence_is_success: false
    outputs:
      - id: program-acceptance-audit
        schema: tgw-environment-recovery-acceptance-audit/v1
    acceptance:
      - id: acceptance-audit-truthful
        verifier: tgw.environment.recovery-acceptance-audit/v1
        assertion: exact_requirements_classified_without_completion_overclaim
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - A1-program-acceptance-monitor:acceptance-audit-truthful
rollback: Retain the immutable audit; no program evidence or external state was changed.
```
