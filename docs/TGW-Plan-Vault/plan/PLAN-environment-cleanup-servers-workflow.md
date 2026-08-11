---
schema: tgw-plan/v1
plan_id: PLAN-environment-cleanup-servers
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T00:00:00-07:00
supersedes: null
registry_revision: sha256:0f829f7de2d7a90de00d7c5fc3cb2442add9a069b7459b8cfc9d273382914f6d
scope_hash: sha256:88d03f1ba5f116b23b1c2aa77bdf73f222747096bbe3cea45e87d06878abe998
tracks: [server]
dependencies: []
---
# Server environment cleanup — governed inventory

This first work unit freezes read-only facts. It performs no cleanup, deployment,
service change, repository mutation, or retirement.

## Workflow contract
```yaml
exclusions:
  - destructive cleanup or retirement
  - deployment or service changes
  - mutable repository or flake operations
  - authority inferred from memory or historical plans
work_units:
  - id: S0-server-inventory
    title: Inventory current server environment and conflicting references
    kind: discovery
    requires: []
    owns: [inventory:server-environment]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-server-inventory
    treatment_version: "1"
    inputs:
      production_host: tgw-prod
      development_host: tgw-lib
      retired_alias: a1131
    outputs:
      - id: server-inventory
        schema: tgw-environment-server-inventory/v1
    acceptance:
      - id: server-inventory-bound
        verifier: tgw.environment.server-inventory/v1
        assertion: exact_sources_hashed_and_conflicts_classified
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - S0-server-inventory:server-inventory-bound
rollback: Retain the immutable inventory receipt; no operational state is changed.
```
