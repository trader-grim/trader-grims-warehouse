# B0 reconciliation observation — 2026-08-11

This is a working observation, not a `tgw-plan-evidence/v1` acceptance receipt.
`B0-reconcile:current-state-bound` remains `UNKNOWN` until runtime provider identity,
queue/effect/reconciliation rows, deployed release identity, and health are captured
by the registered verifier.

## Source evidence

- Bound source: `a25c734e853165a807abbf8aa434c8922027b12e` on
  `recovery/pp-workflow-marketplace`.
- Focused evaluator/scheduler/action-card/AI-identify suite: 65 passed in 1.52s.
  Expanded authority/provider-effect/stage/targeted-sync suite: 202 passed in
  2.07s.
- Current source contains governed identify, stage, publish, durable wait, targeted
  sync, and reconciliation migrations. Therefore
  `docs/ai-plans/pp-workflow-listing-topology-phase0.md` is migration provenance,
  not a current-state ledger.
- The operator CLI has no workflow subcommand, but the deployed authenticated
  read-only `GET /api/items/{sku}/workflow` route is present and responsive.
  Exact deployed release identity is still not exposed by the available read-only
  surfaces, so source/deployment parity remains unproved.
- Production health returned HTTP 503. The detailed health projection names two
  current failures: backup/rclone evidence is absent and the snapshot tree is stale;
  `ebay_sync_fallback` has 876 consecutive fallback runs.

## Fresh controlled-item observations

- `tgw202507261628068` is no longer merely an identify-path candidate. The current
  canonical record reports `ai_identified=true`, six local/uploaded/confirmed photos,
  price `27.99`, offer `267197491018`, listing `327304603193`, and an active published
  listing. It carries stage and publish provider-effect markers and a later read-only
  live sync. This is valuable historical/live-state evidence, but is not by itself a
  commissioning acceptance receipt because exact authority, queue attempts, effect
  rows, configured provider identity, and release binding have not yet been joined.
  Its fresh Action Card nevertheless reports `published` unmet, a
  `listing.stage` reconciliation gate, no legal action, and multiple governed dead
  letters (including stage, targeted sync, legacy-stage onboarding, draft, identify,
  and upload) alongside later succeeded attempts. This conflicts with the canonical
  active-listing projection and must be reconciled; later success does not erase the
  failed attempts.
- `tgw202604300922410` is not a valid staged-item candidate in its current state.
  It is identified and has uploaded-photo mappings, but price is null, its offer only
  records `insufficient_data`, and no staged offer/listing is present. It also retains
  an unrelated `archive_target_unmounted` pipeline error. Do not stage or publish it
  without resolving price/eligibility and obtaining the contract's explicit provider
  authority.
  Its Action Card truthfully reports `priced`, `staged`, `staged_content_current`,
  and `published` unmet; `ebay-price` is the sole eligible treatment. Historical
  upload is dead-lettered because an image exceeded the 15000-pixel dimension limit.

## B0 disposition

`HELD_CONTRADICTORY`. The backend is correctly refusing blind stage retry for the
published canary, but the canonical listing projection and workflow evidence graph
do not currently converge. Health and release/provider bindings are also incomplete.
No B0 receipt may be issued and B1 may not begin from these candidates.

## Bounded enabling work

The production API lacked a single privacy-safe surface that joins the exact rows
required by the admitted reconciliation runbook. Source now adds authenticated,
read-only `GET /api/items/{sku}/workflow-reconciliation`. Its response contains only
allowlisted effect, authority, and observation columns; configured provider identity;
and canonical stage/publish marker IDs. It deliberately excludes request JSON,
authority JSON, provider result bodies, tokens, and credentials, and performs no
provider call or mutation. Focused endpoint/ledger tests pass (65 passed in 1.19s;
Ruff and diff checks pass). This is source evidence only until independently reviewed,
released, and verified on production; it does not clear B0.

The exact candidate is frozen for independent review in
`plan/packets/PP-WORKFLOW-001-B0-reconciliation-surface.md`, bound to base commit
`a25c734e853165a807abbf8aa434c8922027b12e` and tracked implementation diff hash
`fb27f0643d8ae27605aa404480bdeee9f0ff46b93e14da41f9a257c75a943c2f`.

## Real workflow commissioning attempt

At Dave's direction, todo `#1746` was created for the bounded reconciliation
surface and submitted through the receipt-addressed coding workflow as request
`1a0ef5fe-4a69-4698-8253-70ccbc82c60d`, explicitly requesting source commit
`a25c734e853165a807abbf8aa434c8922027b12e` and Claude review.

The attempt failed safely and exposed two deployment/workflow defects before review:

1. the provisioned worktree was based on `2a40a2d7ae57d6d2e3b3108d308fda180aca5f7e`,
   not the requested source commit;
2. the clean stale branch was classified `implemented=true` merely because it
   differed from `origin/main`, although it contained none of todo #1746's work.

The subsequent ordering was correct for the admitted contracts:
`controller-verify` is allowed after `implemented=true` specifically to establish
`tested`, `linted`, and `controller_verified`; `claude-review` correctly requires
`implemented`, `tested`, and `linted` all true. Claude therefore did not run after
controller verification failed. Current source commit `f836726e` contains a later
source-commit binding fix, so production deployment lag is part of the contradiction
that must be resolved rather than papered over.

The durable failed receipt is
`e4889f15-6841-4c73-9b1d-1ea5cdfca9a1`. It records `controller-verify`, outcome
`failed`, with 16 failed / 3166 passed / 4 skipped tests. It establishes no
condition and is not retry authority. The governed worktree was not manually
modified. B0 remains held; deploying and proving the existing source-commit binding
fix, then rerunning from the requested source generation, is genuine enabling work
inside commissioning.

Release is not presently a legal next action. The approved
`PLAN-app-installer-preflight-hold` records that tgw-prod lacks the independent
installer and explicitly holds application install/rollback procedures until that
installer is bootstrapped and verified. Using an installer from the application
release being replaced is forbidden. Todo #1746 now carries this exact hold and the
failed receipt identity. Commissioning must resume at the installer prerequisite,
not by manually updating the receiver repository, repairing the governed worktree,
or resubmitting the unchanged request.

## Next legal action

Use the admitted provider-reconciliation procedure for the exact `listing.stage`
effect on `tgw202507261628068`, joining the succeeded stage/publish markers and live
provider observation without resending either write. Separately establish deployed
release and configured provider identity through a registered read-only surface.
Keep the unrelated platform-health failures visible. Only after the Action Card and
canonical listing agree may the registered B0 verifier issue an immutable receipt.
Do not enqueue, retry, stage, publish, or manually repair either item.
