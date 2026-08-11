---
schema: tgw-plan/v1
plan_id: PLAN-environment-cleanup-satellites
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T00:00:00-07:00
supersedes: null
registry_revision: sha256:0f829f7de2d7a90de00d7c5fc3cb2442add9a069b7459b8cfc9d273382914f6d
scope_hash: sha256:58a9ce433b315fdee492cd3e1293a5278bfc70e34593c9eb08f16fd74c14c7fb
tracks: [satellite]
dependencies: []
---
# Quarantined satellite recovery — governed reference inventory

This first work unit inventories repository-local claims and records access as
unknown. It does not contact a quarantined host or consume recovered credentials.

## Workflow contract
```yaml
exclusions:
  - satellite login or network contact
  - recovered credential use
  - execution of recovered code prompts hooks or plugins
  - Hindsight or Hermes memory import
  - deletion sanitization or quarantine release
work_units:
  - id: L0-satellite-reference-inventory
    title: Inventory repository-local catnanny and helicrew recovery references
    kind: discovery
    requires: []
    owns: [inventory:quarantined-satellites]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-satellite-reference-inventory
    treatment_version: "1"
    inputs:
      hosts: [catnanny, helicrew]
      quarantine: true
    outputs:
      - id: satellite-reference-inventory
        schema: tgw-environment-satellite-reference-inventory/v1
    acceptance:
      - id: quarantine-boundary-recorded
        verifier: tgw.environment.satellite-quarantine/v1
        assertion: references_hashed_and_access_remains_unverified
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - L0-satellite-reference-inventory:quarantine-boundary-recorded
rollback: Retain the immutable reference inventory; no satellite is contacted.
```
