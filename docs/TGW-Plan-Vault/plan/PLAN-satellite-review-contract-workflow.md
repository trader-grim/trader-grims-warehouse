---
schema: tgw-plan/v1
plan_id: PLAN-satellite-review-contract
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:50:00-07:00
supersedes: null
registry_revision: sha256:7b651f38086413d3f94b42c457ab15e372c5574e16525ca426ba08125a8784c5
scope_hash: sha256:ea1ece3f17e0f40504515b1e17f2366e8f7867b9b606dba6aec38b04ec7f53c4
tracks: [satellite]
dependencies: [PLAN-satellite-evidence-contract]
---
# Quarantined satellite recovery — reviewed import decision contract

This unit defines human-reviewed, class-specific decisions for future evidence
packages. It imports no record, creates no steward store, and does not claim that
Catnanny or Helicrew data has been acquired or reviewed.

## Workflow contract
```yaml
exclusions:
  - recovered record import or execution
  - agent-only approval of initial memory batches
  - machine path permission host or procedure data in personal memory
  - current authority granted by a review decision
work_units:
  - id: L3-satellite-review-contract
    title: Enforce complete human review and separate recovery destinations
    kind: configuration
    requires: []
    owns: [schema:satellite-review-ledger]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-satellite-review-contract
    treatment_version: "1"
    inputs:
      package_schema: tgw-satellite-evidence-package/v1
      review_schema: tgw-satellite-review/v1
      complete_record_coverage: required
      human_reviewer: required
    outputs:
      - id: satellite-review-contract
        schema: tgw-satellite-review/v1
    acceptance:
      - id: satellite-review-boundary-valid
        verifier: tgw.environment.satellite-review-contract/v1
        assertion: complete_human_review_destinations_and_non_authority_fail_closed
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert only this local review validator
plan_acceptance:
  - L3-satellite-review-contract:satellite-review-boundary-valid
rollback: Revert the local contract; no recovered record or destination store was changed.
```
