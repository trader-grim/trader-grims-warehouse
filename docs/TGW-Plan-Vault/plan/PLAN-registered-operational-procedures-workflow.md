---
schema: tgw-plan/v1
plan_id: PLAN-registered-operational-procedures
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:30:00-07:00
supersedes: null
registry_revision: sha256:eb34185b3ec08b2e028f6b8786fb9ca29a3057b21fc663b25716c963034dcc5d
scope_hash: sha256:aaa730f2aaf7d337fb79186e58f131669a6446e7a96f9f7971798cab8a043b49
tracks: [server]
dependencies: [PLAN-instruction-authority-remediation]
---
# Server environment cleanup — registered operational procedures

This work unit replaces direct effectful deployment instructions with validated
procedure identities. It creates no procedure runner and performs no deployment,
switch, rollback, service change, or remote mutation.

## Workflow contract
```yaml
exclusions:
  - execution of a registered procedure
  - deployment switch rollback service restart or remote mutation
  - authority inferred from plan text runbook prose memory or history
  - secret credential or approval material in the procedure registry
work_units:
  - id: S5-register-operational-procedures
    title: Register NixOS switch and rollback identities and migrate runbooks
    kind: configuration
    requires: []
    owns: [configuration:operational-procedure-registry]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-procedure-register
    treatment_version: "1"
    inputs:
      registry: config/environment/procedures.json
      mutable_procedures: [nixos-prod-switch/v1, nixos-prod-rollback/v1]
      direct_invocation_allowed: false
    outputs:
      - id: procedure-registry
        schema: tgw-procedure-registry/v1
      - id: post-procedure-instruction-audit
        schema: tgw-instruction-audit/v1
    acceptance:
      - id: procedure-registry-valid
        verifier: tgw.environment.procedure-registry/v1
        assertion: exact_revision_authority_and_rollback_bindings_validate
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: runbooks-use-procedure-identities
        verifier: tgw.environment.runbook-procedure-routing/v1
        assertion: no_direct_mutable_deployment_commands_remain
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert only this local registry and runbook migration commit
plan_acceptance:
  - S5-register-operational-procedures:procedure-registry-valid
  - S5-register-operational-procedures:runbooks-use-procedure-identities
rollback: Revert the local migration; no registered procedure was executed.
```
