---
schema: tgw-plan/v1
plan_id: PP-WORKFLOW-001
version: 2
status: active
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T15:00:00-07:00
supersedes: PP-WORKFLOW-001@1
registry_revision: sha256:54ef67ebf562b33270a4c0ddcbd0dad058fa82d42070c19786608ce752f69451
scope_hash: sha256:cb002fcf060d2d15e319bdc16ff0f64f6431ec563d015f23a047ddc56cfcad13
tracks: [listing-backend]
dependencies: []
---
# PP-WORKFLOW-001 — governed listing backend

## Exact outcome

The authoritative listing workflow durably advances a controlled item through
identify → draft → price → upload → stage → publish. Every transition is selected
from current evidence; failed attempts, waits, ambiguous provider effects, retries,
and reconciliation remain durable and visible. Provider writes require exact current
operator authority. No stage is complete until its declared evidence is verified.

## Workflow contract
```yaml
exclusions:
  - general listing-editor redesign beyond workflow approval and current-status surfaces
  - bulk or fleet-wide listing migration
  - provider write authority inferred from this plan
  - manual database repair hidden retry ordering or blind dead-letter replay
  - completion based only on tests queue success provider response or historical prose
work_units:
  - id: W0-operator-surface
    title: Restore real workflow approvals and current execution status
    kind: migration
    requires: []
    owns: [surface:workflow-approvals, surface:workflow-runs]
    effect_class: local-reversible
    authority: plan-approved
    operator_surface: null
    treatment_id: codex-implement
    treatment_version: "1"
    inputs:
      approval_design: PP-APPROVAL-001
      approval_route: /form/approvals
      status_route: /form/runs
      status_source: canonical_plan_graphs_coding_requests_receipts_authorities_and_provider_effects
      required_projection:
        - exact_plan_version_scope_graph_unit_target_and_requested_effects
        - conditions_receipts_freshness_attempts_waits_holds_and_reconciliation
        - authority_scopes_expiry_and_legal_next_actions
      forbidden_substitutes: [draft_approval, ordinary_stage_or_publish_button, legacy_agent_runs_only]
      decomposition:
        required: true
        coordinator: codex
        subagents:
          - {role: approval-design-and-history-auditor, effect: read-only}
          - {role: canonical-status-source-auditor, effect: read-only}
          - {role: operator-surface-test-reviewer, effect: read-only}
        rule: coordinator_owns_edits_and_integrates_subagent_findings
    outputs:
      - {id: operator-surface-artifact, schema: tgw-workflow-operator-surface/v1}
      - {id: deployment-receipt, schema: tgw-surface-deployment/v1}
    acceptance:
      - {id: surface-deployed, verifier: tgw.workflow.operator-surface-deployed/v1, assertion: approvals_and_current_runs_routes_are_reachable_in_the_deployed_release, evidence_schema: tgw-plan-evidence/v1, freshness: same-plan-version}
      - {id: surface-discoverable, verifier: tgw.workflow.operator-surface-discoverable/v1, assertion: operator_reaches_pending_approvals_through_normal_navigation, evidence_schema: tgw-plan-evidence/v1, freshness: same-plan-version}
      - {id: status-current, verifier: tgw.workflow.operator-status-current/v1, assertion: routes_project_current_canonical_graph_attempt_receipt_authority_and_effect_state, evidence_schema: tgw-plan-evidence/v1, freshness: same-plan-version}
      - {id: decision-roundtrip, verifier: tgw.workflow.operator-decision-roundtrip/v1, assertion: approve_and_hold_create_immutable_exactly_bound_decisions_without_implicit_execution, evidence_schema: tgw-plan-evidence/v1, freshness: same-plan-version}
    on_conflict: reconciliation_required
    rollback: disable approval actions retain decisions and receipts and restore prior read-only navigation
  - id: W1-codex-implementation
    title: Codex closes the highest-value backend workflow gaps using bounded subagents
    kind: migration
    requires: [W0-operator-surface]
    owns: [source:listing-workflow-backend]
    effect_class: local-reversible
    authority: plan-approved
    operator_surface: null
    treatment_id: codex-implement
    treatment_version: "1"
    inputs:
      required_outcome: identify_draft_price_upload_stage_publish_converges_from_verified_evidence
      source_priority: live_source_then_tests_then_current_docs
      decomposition:
        required: true
        coordinator: codex
        subagents:
          - role: topology-and-evidence-auditor
            effect: read-only
          - role: retry-and-reconciliation-auditor
            effect: read-only
          - role: focused-test-reviewer
            effect: read-only
        rule: coordinator_owns_edits_and_integrates_subagent_findings
      mandatory_boundaries:
        - one_shot_evaluation_after_durable_evidence_change
        - unchanged_failed_attempt_does_not_redispatch
        - waits_use_durable_not_before_without_worker_sleep
        - ambiguous_provider_effect_never_blind_retries
        - stage_and_publish_require_exact_operator_authority
        - receipts_bind_sku_generation_graph_condition_treatment_and_provider_effect
      admitted_first_gaps:
        - ebay_upload_requires_priced_before_it_is_eligible
        - every_EPS_write_has_a_durable_per_photo_content_bound_provider_effect
        - post_dispatch_uncertainty_gates_reconciliation_and_forbids_resend
        - partial_progress_survives_crash_without_repeating_accepted_uploads
      controlled_skus: [tgw202507261628068, tgw202604300922410]
    outputs:
      - id: implementation-receipt
        schema: receipt/tgw-workflow/v1
      - id: implementation-diff
        schema: tgw-source-diff/v1
    acceptance:
      - id: implementation-bound
        verifier: tgw.coding.implementation-receipt/v1
        assertion: exact_plan_scope_source_generation_subagent_findings_tests_and_diff_are_bound
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: independently-reviewed
        verifier: tgw.coding.claude-review/v1
        assertion: exact_implementation_is_independently_reviewed_against_this_frozen_plan
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: controller-verified
        verifier: tgw.coding.controller-verification/v1
        assertion: focused_and_regression_checks_pass_against_the_reviewed_generation
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert only the candidate source diff and retain all receipts
  - id: W2-controlled-production-acceptance
    title: Reconcile and accept one controlled end-to-end production sequence
    kind: operator-acceptance
    requires: [W1-codex-implementation]
    owns: [provider:ebay-listing]
    effect_class: provider-write
    authority: operator-explicit
    operator_surface: workflow-approvals
    treatment_id: listing-provider-acceptance
    treatment_version: "1"
    inputs:
      identify_candidate: tgw202507261628068
      stage_candidate: tgw202604300922410
      provider_identity: runtime-authoritative
      checklist: reference/runbooks/pp-workflow-001-acceptance.md
    outputs: [{id: production-acceptance, schema: tgw-listing-provider-acceptance/v1}]
    acceptance:
      - id: production-sequence-accepted
        verifier: tgw.listing.provider-acceptance/v1
        assertion: exact_current_authority_effect_reconciliation_receipts_and_dave_acceptance_are_present
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: stop producer preserve effects reconcile ambiguity and select prior accepted release under operator authority
plan_acceptance:
  - W0-operator-surface:surface-deployed
  - W0-operator-surface:surface-discoverable
  - W0-operator-surface:status-current
  - W0-operator-surface:decision-roundtrip
  - W1-codex-implementation:implementation-bound
  - W1-codex-implementation:independently-reviewed
  - W1-codex-implementation:controller-verified
  - W2-controlled-production-acceptance:production-sequence-accepted
operator_surfaces:
  - id: workflow-approvals
    route: /form/approvals
    audience: authenticated-operator
    actions: [approve, hold, reconcile]
    status_source: canonical-plan-graphs-coding-requests-receipts-authorities-and-provider-effects
    required_for: [W2-controlled-production-acceptance]
    deployment_condition: W0-operator-surface:surface-deployed
    discoverability_condition: W0-operator-surface:surface-discoverable
    freshness_condition: W0-operator-surface:status-current
rollback: Stop new dispatch first; preserve attempts receipts authorities effects and observations; reconcile ambiguity; revert source or select the prior accepted release only through its authority gate.
```
