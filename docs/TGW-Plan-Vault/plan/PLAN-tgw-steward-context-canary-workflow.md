---
schema: tgw-plan/v1
plan_id: PLAN-tgw-steward-context-canary
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:36:00-07:00
supersedes: null
registry_revision: sha256:da3bc2f5d06d62271c60c3943693607c875c76107b92e9ba96884d09470bc5eb
scope_hash: sha256:7673fc76b8311431e657a6f7c91ff45656adc0a6f9c8a9b1928774fbcfa0000a
tracks: [server]
dependencies: [PLAN-instruction-authority-remediation, PLAN-environment-registry]
---
# TGW Steward — read-only current-context canary

This unit verifies the clean steward's structured current-context behavior. It
does not start an agent service, query Hindsight, import memory, contact a
satellite, or grant any production or infrastructure effect.

## Workflow contract
```yaml
exclusions:
  - Hindsight query memory import or recovered prompt execution
  - production infrastructure satellite or repository mutation
  - free-form tool invocation or authority inferred from returned text
work_units:
  - id: S6-steward-context-canary
    title: Verify cited current facts retired-name refusal and historical separation
    kind: verification
    requires: []
    owns: [agent:tgw-steward-context]
    effect_class: read-only
    authority: plan-approved
    treatment_id: environment-steward-context-canary
    treatment_version: "1"
    inputs:
      registry: config/environment/registry.yaml
      contract: config/environment/actors/tgw-steward.json
      query_schema: tgw-steward-query/v1
      history_lookup: disabled
    outputs:
      - id: steward-context-verification
        schema: tgw-steward-context-verification/v1
    acceptance:
      - id: steward-current-context-boundary
        verifier: tgw.environment.steward-context/v1
        assertion: current_facts_cited_retired_refused_history_non_authoritative
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: none-read-only
plan_acceptance:
  - S6-steward-context-canary:steward-current-context-boundary
rollback: Retain the canary evidence; no runtime or external state was changed.
```
