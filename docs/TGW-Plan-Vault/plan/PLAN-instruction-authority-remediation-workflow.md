---
schema: tgw-plan/v1
plan_id: PLAN-instruction-authority-remediation
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:15:00-07:00
supersedes: null
registry_revision: sha256:9cbfdc909f17a7c662e7995a86f62dc4655636ff229bae052d2d6dac6c5453ca
scope_hash: sha256:36cd68b2606863bf4011807899c1f566812b5802f47d83c1e365215462cd8971
tracks: [server]
dependencies: [PLAN-instruction-conflict-audit@1]
---
# Current instruction authority remediation

This unit replaces current operational routing while retaining historical source in
Git and in the Plan Vault. It does not import Hermes or Hindsight memory.

## Workflow contract
```yaml
exclusions:
  - delete historical plans memories or audit evidence
  - import recovered persona memory or executable instructions
  - change services hosts repositories or deployment state
  - grant infrastructure authority to TGW Steward
work_units:
  - id: S4-authority-remediation
    title: Install clean TGW Steward contract and retire obsolete routes
    kind: configuration
    requires: []
    owns: [authority:agent-instructions]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-instruction-remediate
    treatment_version: "1"
    inputs:
      instruction_audit: instruction-audit-1befcb2a10d7d232-classified
      previous_registry_revision: sha256:486e0686a1541c0eec904b288ce7f72074eac9e655940bdba285f32a10b19185
    outputs:
      - id: steward-contract
        schema: tgw-agent-contract/v1
      - id: environment-registry-revision
        schema: tgw-environment/v1
      - id: obsolete-profile-tombstone
        schema: tgw-instruction-tombstone/v1
    acceptance:
      - id: steward-boundary-valid
        verifier: tgw.environment.steward-contract/v1
        assertion: clean_contract_has_no_memory_or_infrastructure_authority
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
      - id: retired-routing-fails
        verifier: tgw.environment.retired-routing/v1
        assertion: current_agent_routes_do_not_resolve_a1131_or_obsolete_maintainer
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
    on_conflict: reconciliation_required
    rollback: revert exact source commit while retaining audit and receipts
plan_acceptance:
  - S4-authority-remediation:steward-boundary-valid
  - S4-authority-remediation:retired-routing-fails
rollback: Revert exact source changes; never delete historical plans or receipts.
```
