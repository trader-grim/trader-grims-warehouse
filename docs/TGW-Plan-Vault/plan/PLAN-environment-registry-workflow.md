---
schema: tgw-plan/v1
plan_id: PLAN-environment-registry
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T08:55:00-07:00
supersedes: null
registry_revision: sha256:0cfbdd4e24630207c5564ab793ac2f42bd498b39aed7497ed24cc89312ecc125
scope_hash: sha256:11172b29c672bdd84a11bf704cf3923f9006d8803cdcba862c80f15c8b894c4d
tracks: [server]
dependencies: [PLAN-environment-cleanup-servers@1]
---
# Authoritative TGW environment registry

This work unit creates a repository-local registry and validator. Installation into
`/opt/TGW/environment` and migration of consumers are separate work units.

## Workflow contract
```yaml
exclusions:
  - install or activate the registry outside this repository
  - change services deployments repositories or worktrees
  - infer current facts from Hindsight or agent memory
  - delete or rewrite historical references
work_units:
  - id: S1-environment-registry
    title: Build and verify the versioned environment registry
    kind: configuration
    requires: []
    owns: [registry:tgw-environment]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-registry-build
    treatment_version: "1"
    inputs:
      inventory_receipt: server-inventory-f16ad5146842bc84
      inventory_supplement: server-inventory-supplement-20260811
    outputs:
      - id: environment-registry
        schema: tgw-environment/v1
      - id: registry-validator
        schema: python-module/v1
    acceptance:
      - id: registry-valid
        verifier: tgw.environment.registry-validate/v1
        assertion: schema_revision_provenance_and_references_valid
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: retired-hosts-fail
        verifier: tgw.environment.retired-hosts/v1
        assertion: retired_and_unknown_hosts_fail_closed
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: remove repository-local registry files and retain receipts
plan_acceptance:
  - S1-environment-registry:registry-valid
  - S1-environment-registry:retired-hosts-fail
rollback: Remove only repository-local generated files; retain inventories and receipts.
```
