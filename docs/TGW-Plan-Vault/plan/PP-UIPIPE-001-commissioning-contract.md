---
schema: tgw-plan/v1
plan_id: PP-UIPIPE-001-COMMISSIONING
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T14:16:00-07:00
supersedes: null
registry_revision: sha256:9489cd2c9b82aaeb59f812ea2adc96257bb80ad568d54dab7c58991186003e4c
scope_hash: sha256:6de9f18dc4b47fefa8268d4bb72836b14e33d426c9c603ca94f7ac96c9bf29c3
tracks: [listing-ui]
dependencies: [PP-WORKFLOW-001-COMMISSIONING@1]
---
# PP-UIPIPE-001 commissioning contract

Outcome: the operator sees authoritative listing state, its evidence, and the next
legal action; can invoke a governed `ai_identify` path; and can recover held or
failed work without database repair or knowledge of hidden retry order.

Canonical reconciliation: `PP-UIPIPE-001` was absorbed into `PP-EDITOR-001` on
2026-07-11. This identifier is retained only as the bounded commissioning lineage;
it does not revive an independent umbrella or supersede `PP-EDITOR-001`.

## Workflow contract
```yaml
exclusions:
  - redesign of unrelated editor field surfaces
  - hidden direct queue ordering or manual database repair
  - provider stage or publish authority granted by a UI control
  - UI success inferred from backend queue completion alone
work_units:
  - id: U0-state-and-identify
    title: Expose authoritative state next legal action and governed AI identify
    kind: migration
    requires: []
    owns: [interface:listing-action-card]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: listing-ui-contract
    treatment_version: "1"
    inputs: {backend_contract: PP-WORKFLOW-001-COMMISSIONING@1, controlled_sku: tgw202507261628068}
    outputs: [{id: listing-ui-state-contract, schema: tgw-listing-ui-state/v1}]
    acceptance:
      - id: state-and-identify-truthful
        verifier: tgw.listing.ui-state-contract/v1
        assertion: authoritative_state_evidence_next_action_and_ai_identify_are_usable
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert UI projection while retaining backend workflow state
  - id: U1-recovery
    title: Expose truthful holds errors reconciliation and legal recovery
    kind: migration
    requires: [U0-state-and-identify]
    owns: [interface:listing-action-card]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: listing-ui-recovery
    treatment_version: "1"
    inputs: {manual_database_repair: forbidden, hidden_retry_order: forbidden}
    outputs: [{id: listing-ui-recovery, schema: tgw-listing-ui-recovery/v1}]
    acceptance:
      - id: recovery-truthful
        verifier: tgw.listing.ui-recovery/v1
        assertion: stuck_item_can_be_recovered_through_evidence_bound_legal_actions
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: revert UI recovery controls without altering durable attempts or receipts
  - id: U2-operator-acceptance
    title: Obtain Dave's controlled-item operator acceptance
    kind: operator-acceptance
    requires: [U1-recovery]
    owns: [acceptance:listing-ui]
    effect_class: read-only
    authority: operator-explicit
    treatment_id: listing-ui-acceptance
    treatment_version: "1"
    inputs: {backend_acceptance: evidence:PP-WORKFLOW-001-COMMISSIONING}
    outputs: [{id: listing-ui-operator-acceptance, schema: tgw-listing-ui-operator-acceptance/v1}]
    acceptance:
      - id: operator-accepted
        verifier: tgw.listing.ui-operator-acceptance/v1
        assertion: controlled_identify_hold_error_and_recovery_cases_are_explicitly_accepted
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: retain rejection evidence and revert only the UI candidate
plan_acceptance:
  - U0-state-and-identify:state-and-identify-truthful
  - U1-recovery:recovery-truthful
  - U2-operator-acceptance:operator-accepted
rollback: Revert only the UI candidate; retain backend authority state, attempts, receipts, and operator findings.
```
