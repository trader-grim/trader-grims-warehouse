---
schema: tgw-plan/v1
plan_id: PLAN-app-installer-preflight-hold
version: 1
status: approved
owner: dave
authority_class: operator-approved
created_at: 2026-08-11T10:25:00-07:00
supersedes: null
registry_revision: sha256:45d9145a202806afed58eb28be3c141302d197b185b8624bfb8589cf326a2d56
scope_hash: sha256:3aa076149b6e50a29428cb96120fcbec5792f42db5a0df20d6dbc91a367519d4
tracks: [server]
dependencies: [PLAN-app-release-procedure]
---
# Server environment cleanup — production installer preflight and hold

This corrective unit records that tgw-prod lacks an independent application
installer and holds app install/rollback procedures until one is bootstrapped and
verified. It supersedes any inference that source registration made them executable.

## Workflow contract
```yaml
exclusions:
  - installer bootstrap release installation selection or rollback
  - fallback to an installer imported from the selected application release
  - executable procedure status without production path verification
work_units:
  - id: S9-hold-app-procedures
    title: Record missing independent installer and fail app procedures closed
    kind: configuration
    requires: []
    owns: [configuration:application-release-procedures]
    effect_class: local-reversible
    authority: plan-approved
    treatment_id: environment-app-installer-hold
    treatment_version: "1"
    inputs:
      production_host: tgw-prod
      expected_installer: /opt/TGW/installer/current/bin/tgw-release-install
      procedures: [app-release-install/v1, app-release-rollback/v1]
    outputs:
      - id: production-installer-preflight
        schema: tgw-procedure-registry-verification/v1
      - id: held-app-procedures
        schema: tgw-procedure-registry/v1
    acceptance:
      - id: production-installer-absence-recorded
        verifier: tgw.environment.app-installer-preflight/v1
        assertion: production_has_no_independent_installer_and_no_deploy_occurred
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
      - id: app-procedures-held
        verifier: tgw.environment.held-procedure/v1
        assertion: app_install_and_rollback_resolution_fail_closed
        evidence_schema: tgw-plan-evidence/v1
        freshness: same-plan-version
    on_conflict: reconciliation_required
    rollback: supersede only after independent installer verification evidence exists
plan_acceptance:
  - S9-hold-app-procedures:production-installer-absence-recorded
  - S9-hold-app-procedures:app-procedures-held
rollback: Keep procedures held; no production state was changed by this unit.
```
