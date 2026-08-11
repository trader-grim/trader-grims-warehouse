---
schema: tgw-plan/v1
plan_id: PLAN-deterministic-task-context
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T09:00:00-07:00
supersedes: null
registry_revision: sha256:36bab243bc74515bdcf5e6a75a21604c7fe292a0c1badec444afa2c9b207b7d4
scope_hash: sha256:d8b20e93faa5c9e6893fbe029c85b21d1e8f37c0b61253be8000b2e3c0dca82e
tracks: [server]
dependencies: [PLAN-environment-registry@1]
---
# Deterministic task context

This unit defines and verifies a task/context manifest. It does not create, move,
clean, or delete a Git worktree.

## Workflow contract
```yaml
exclusions:
  - worktree creation deletion or mutation
  - service deployment or configuration activation
  - authority from persona history or memory
  - Claude contract delivery to non-Claude actors
work_units:
  - id: S2-task-context
    title: Build deterministic actor-scoped task context
    kind: configuration
    requires: []
    owns: [workspace-contract:environment-recovery]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-task-context-build
    treatment_version: "1"
    inputs:
      environment_registry_revision: sha256:486e0686a1541c0eec904b288ce7f72074eac9e655940bdba285f32a10b19185
      actor: codex
    outputs:
      - id: task-manifest
        schema: tgw-task/v1
      - id: resolved-context
        schema: tgw-resolved-context/v1
    acceptance:
      - id: context-reproducible
        verifier: tgw.environment.task-context/v1
        assertion: identical_manifest_produces_identical_context
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
      - id: actor-boundary
        verifier: tgw.environment.actor-boundary/v1
        assertion: actor_receives_only_registered_authority_files
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-registry-revision
    on_conflict: reconciliation_required
    rollback: remove repository-local manifest and context projection
plan_acceptance:
  - S2-task-context:context-reproducible
  - S2-task-context:actor-boundary
rollback: Remove only generated repository-local projections; retain receipts.
```
