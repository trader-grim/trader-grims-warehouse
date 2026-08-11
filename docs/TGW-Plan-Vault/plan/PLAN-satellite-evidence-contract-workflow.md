---
schema: tgw-plan/v1
plan_id: PLAN-satellite-evidence-contract
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:42:00-07:00
supersedes: null
registry_revision: sha256:fae3f04f133c4ec41b0c73808e48ceed8a5fb37772bcb2225868741b978f547b
scope_hash: sha256:99e256605e6907fdff47fb488219ddda3fd02a8e8531a76dbe4b0d630c1d0e32
tracks: [satellite]
dependencies: [PLAN-satellite-access-discovery]
---
# Quarantined satellite recovery — neutral evidence package contract

This unit establishes the format that future Catnanny and Helicrew acquisitions
must satisfy. It creates no evidence package for either laptop and performs no
access, imaging, export, memory import, secret handling, or quarantine release.

## Workflow contract
```yaml
exclusions:
  - satellite access imaging export or network contact
  - recovered content execution or import
  - secret material in normalized review data
  - authority inferred from any recovered record
work_units:
  - id: L2-satellite-evidence-contract
    title: Validate append-only neutral evidence and classification boundaries
    kind: configuration
    requires: []
    owns: [schema:satellite-evidence-package]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-satellite-evidence-contract
    treatment_version: "1"
    inputs:
      source_hosts: [catnanny, helicrew]
      quarantine: required
      current_authority: forbidden
      executable_content: forbidden
    outputs:
      - id: satellite-evidence-package-contract
        schema: tgw-satellite-evidence-package/v1
    acceptance:
      - id: satellite-evidence-boundary-valid
        verifier: tgw.environment.satellite-evidence-contract/v1
        assertion: provenance_quarantine_secret_and_authority_rules_fail_closed
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert only this local validator contract
plan_acceptance:
  - L2-satellite-evidence-contract:satellite-evidence-boundary-valid
rollback: Revert the local contract; no satellite or recovered evidence was changed.
```
