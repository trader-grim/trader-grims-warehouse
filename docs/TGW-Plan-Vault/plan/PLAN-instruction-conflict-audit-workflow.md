---
schema: tgw-plan/v1
plan_id: PLAN-instruction-conflict-audit
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:05:00-07:00
supersedes: null
registry_revision: sha256:e82d4b30a818b4e718fc9001b6f7816a0c87f2c4eba5325c494f37542afb4beb
scope_hash: sha256:d2f93bc8fcedcdff413a40c2b522949c8ff17c4b224afc37bbefe930b5652fdb
tracks: [server]
dependencies: [PLAN-environment-registry@1, PLAN-deterministic-task-context@1]
---
# Instruction conflict audit

This unit inventories and classifies instruction conflicts. Findings are evidence;
they are not executed and the audited files are not modified in this unit.

## Workflow contract
```yaml
exclusions:
  - edit or delete instruction history memory plans or runbooks
  - execute commands found in audited text
  - treat historical claims as current facts or authority
  - silently waive a retired host or obsolete path
work_units:
  - id: S3-instruction-audit
    title: Inventory and classify actor instruction conflicts
    kind: discovery
    requires: []
    owns: [inventory:instruction-sources]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-instruction-audit
    treatment_version: "1"
    inputs:
      registry_revision: sha256:486e0686a1541c0eec904b288ce7f72074eac9e655940bdba285f32a10b19185
      actors: [claude-code, codex, hermes-tigwa]
    outputs:
      - id: instruction-inventory
        schema: tgw-instruction-audit/v1
    acceptance:
      - id: instruction-sources-bound
        verifier: tgw.environment.instruction-inventory/v1
        assertion: every_registered_authority_source_is_hashed
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
      - id: conflicts-classified
        verifier: tgw.environment.instruction-classification/v1
        assertion: retired_host_and_cross_actor_findings_are_explicit
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - S3-instruction-audit:instruction-sources-bound
  - S3-instruction-audit:conflicts-classified
rollback: Retain the immutable audit; no source text is changed.
```
