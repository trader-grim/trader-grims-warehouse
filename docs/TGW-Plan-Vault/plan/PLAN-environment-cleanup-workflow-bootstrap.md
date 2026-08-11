---
schema: tgw-plan/v1
plan_id: PLAN-environment-cleanup-workflow-bootstrap
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T00:00:00-07:00
supersedes: null
registry_revision: sha256:8521e11a564129b1224156abd9496ccdf63770b765b864cf44f0d9f46ab96617
scope_hash: sha256:867c00840b5316fd1e3caa3affe3dc775503f35ec90655ba6afd74e657dc5e9b
tracks: [server-bootstrap]
dependencies: []
---
# Environment cleanup workflow bootstrap

This bounded plan creates and verifies only the local compiler and registry
scaffold needed to execute the separately approved server and quarantined-satellite
tracks. It cannot complete the parent environment recovery program.

## Workflow contract
```yaml
exclusions:
  - destructive cleanup or retirement
  - production or service changes
  - satellite access or recovered credential use
  - Hindsight or Hermes memory import
  - arbitrary prose or shell execution
  - parent program completion
work_units:
  - id: S0-authoritative-inventory
    title: Inventory repository-local authoritative inputs
    kind: discovery
    requires: []
    owns: [registry:tgw-environment-bootstrap]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-read-only-discovery
    treatment_version: "1"
    inputs: {source_commit: 82149b9061d8c1289659b9535d87524e2a237dd3}
    outputs: [{id: inventory-manifest, schema: tgw-environment-inventory/v1}]
    acceptance:
      - id: inventory-bound
        verifier: tgw.environment.artifact-bound/v1
        assertion: exact_plan_registry_repository_binding
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
  - id: S1-local-registry-scaffold
    title: Build the repository-local registry and clean workspace contract
    kind: configuration
    requires: [S0-authoritative-inventory]
    owns: [registry:tgw-environment-bootstrap]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-local-scaffold
    treatment_version: "1"
    inputs: {inventory_receipt: evidence:S0-authoritative-inventory}
    outputs: [{id: registry-artifact, schema: tgw-environment/v1}]
    acceptance:
      - id: registry-valid
        verifier: tgw.environment.registry-validate/v1
        assertion: schema_and_hash_valid
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
    on_conflict: reconciliation_required
    rollback: registry:previous-revision
  - id: S2-boundary-verification
    title: Verify clean context and non-Claude instruction boundaries offline
    kind: verification
    requires: [S1-local-registry-scaffold]
    owns: [verification:environment-boundaries]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-read-only-verify
    treatment_version: "1"
    inputs: {registry_receipt: evidence:S1-local-registry-scaffold}
    outputs: [{id: boundary-report, schema: tgw-boundary-verification/v1}]
    acceptance:
      - id: context-reproducible
        verifier: tgw.environment.boundary-test/v1
        assertion: clean_context_reproduces
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: claude-contract-isolated
        verifier: tgw.environment.boundary-test/v1
        assertion: non_claude_agents_reject_claude_authority
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - S0-authoritative-inventory:inventory-bound
  - S1-local-registry-scaffold:registry-valid
  - S2-boundary-verification:context-reproducible
  - S2-boundary-verification:claude-contract-isolated
rollback: Retain immutable receipts and remove only repository-local generated projections.
```
