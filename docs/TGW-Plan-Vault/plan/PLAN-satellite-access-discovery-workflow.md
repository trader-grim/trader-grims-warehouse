---
schema: tgw-plan/v1
plan_id: PLAN-satellite-access-discovery
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:22:25-07:00
supersedes: null
registry_revision: sha256:bf93cacc22da1165bda05f0dfe2e42d3fa4425bf6100313e58428633ac3b5667
scope_hash: sha256:5e1c678d025fd38e8ac83a2998c9f1c75e921e8d80046880187e56c058f3f84f
tracks: [satellite]
dependencies: [PLAN-environment-cleanup-satellites]
---
# Quarantined satellite recovery — local access and custody discovery

This work unit determines whether the current Codex environment contains a
trusted, non-secret access definition for either quarantined laptop. Historical
names, fingerprints, memories, and reports are evidence only and grant no access
or execution authority.

## Workflow contract
```yaml
exclusions:
  - network contact DNS probing SSH connection authentication or wake action
  - recovered credential key token or session use
  - execution of recovered agent code prompts hooks plugins or memories
  - quarantine release imaging mutation deletion or sanitization
work_units:
  - id: L1-satellite-access-discovery
    title: Classify local access definitions and preserve the acquisition hold
    kind: discovery
    requires: []
    owns: [inventory:satellite-access-custody]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-satellite-access-discovery
    treatment_version: "1"
    inputs:
      hosts: [catnanny, helicrew]
      discovery_scope: local-configuration-and-historical-references
      quarantine: true
    outputs:
      - id: satellite-access-inventory
        schema: tgw-satellite-access-inventory/v1
    acceptance:
      - id: satellite-access-held
        verifier: tgw.environment.satellite-access-hold/v1
        assertion: each_host_classified_and_no_access_authority_inferred
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - L1-satellite-access-discovery:satellite-access-held
rollback: Retain the immutable discovery receipt; no satellite state was contacted or changed.
```
