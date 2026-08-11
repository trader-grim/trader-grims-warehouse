# PP-WORKFLOW-001 — deployment, selector rollout, and rollback

**Owner:** shared

**Last verified:** 2026-08-10

**Applies to:** tgw-prod, NixOS, immutable TGW releases

**Last drill:** 2026-08-10, release `2c02eb2d`

## Preconditions

- Exact source commit passed its bounded tests, independent review, and diff check.
- The tgw-prod flake checkout is `/home/db/tgw-flake`, branch `master`.
- Schema additions are applied and verified before any consumer is enabled.
- The current release, config, queue inventory, and rollback generation are recorded.
- Provider identity comes from trusted TGW configuration, not an operator-entered
  eBay display name or Developer Portal guess.
- A canary SKU and explicit operator authority are available for any live effect.

## Deploy source without changing behavior

Registered procedure `app-release-install/v1` in
`config/environment/procedures.json`. Its structured inputs are the exact archive,
generation, commit, tree, archive digest, expected current generation, and unique
operation ID. Plan or runbook text does not authorize execution. The registered
procedure is currently **held** until `/opt/TGW/installer/current` is independently
installed and verified on tgw-prod. It must not be requested while held.

After a future completed procedure receipt, verify the selected generation through
that same independently installed wrapper. Do not fall back to importing the
installer from `/opt/TGW/current`.

Do not combine source selection, schema mutation, selector changes, and worker
restarts into one opaque step.

## Selector inventory

Read the effective worker configuration identified by the unit's
`EnvironmentFiles`; do not assume a stale path.

```bash
systemctl show 'tgw-worker@<queue>.service' \
  -p User -p ExecStart -p EnvironmentFiles -p WorkingDirectory
```

Current PP-WORKFLOW-001 seams are:

| Key under `workflow_migration` | Safe/default side | Governed side |
|---|---|---|
| `bundle_downstream` | `legacy` | `workflow` |
| `item_ai_identify_fanout` | `legacy` | `workflow` |
| `item_ebay_stage_fanout` | `legacy` | `workflow` |
| `ebay_upload_quota_timer` | `legacy` | `workflow` |
| `ebay_stage_provider_effect` | `legacy` | `workflow` |
| `ebay_publish_provider_effect` | `legacy` | `workflow` |
| `ebay_post_push_sync_producer` | `legacy` | `workflow` |
| `ebay_sync_targeted_consumer` | `off` | `workflow` |
| `ebay_legacy_stage_onboarding_consumer` | `off` | `workflow` |

`ebay_provider_identity` is an exact configured identity used in authority and
effect binding. Never change it merely to match a storefront label.

## Rollout order

For each seam, take a before/after queue inventory and use one canary. Do not
advance when a reconciliation gate, ambiguous effect, malformed payload, or
unexpected dead letter exists.

1. Deploy code with legacy/off defaults.
2. Apply and verify `operator_authorities`, `provider_effects`, and
   `provider_observations` schemas where required.
3. Verify exactly one intended worker unit/consumer per queue.
4. Migrate local fanout first: bundle intake, item identify, then local draft/price.
5. Enable a governed consumer before its producer.
6. For targeted sync: inventory mixed payloads, drain only exact legacy-targeted
   rows, enable `ebay_sync_targeted_consumer`, canary, then enable
   `ebay_post_push_sync_producer`.
7. Enable provider-effect stage/publish only after authority, reservation,
   ambiguity, and reconciliation checks pass.
8. Enable legacy-stage onboarding only for an explicit isolated request; it is
   not a generic workflow goal.

Targeted-sync inventory is read-only:

```bash
sudo -u tgw env PYTHONPATH=/opt/TGW/current/src \
  python3 -m tgw.workflow.sync_queue_inventory
```

Legacy-stage onboarding inventory must be called from the source helper or an
admitted wrapper; it returns only state/schema-shape counts.

## Rollback order

1. Stop the governed producer first.
2. Inventory governed queued, retry-wait, leased, and running rows.
3. Let claimed work finish or stop the exact worker and allow lease recovery.
4. Preserve ambiguous/provider-effect/observation rows; never delete them.
5. Cancel only exact unclaimed governed rows when rollback requires it, recording
   job IDs and counts. Never bulk-cancel a shared queue.
6. After governed work is drained, set its consumer to `off` or legacy.
7. Restore the prior config and restart only affected units.
8. If source rollback is required, request registered procedure
   `app-release-rollback/v1` with the completed selection receipt, expected current
   generation, and a unique rollback operation ID only after its independent
   installer hold is cleared. The procedure request requires explicit deployment
   approval and produces its own immutable receipt.

Additive schema stays in place. Do not drop tables during rollback.

## Verification

- Immutable release verification passes.
- No failed units or restart loops.
- Config contains only the intended selector delta.
- Queue inventory contains no ambiguous mixed-version payloads.
- One canary produces one graph-bound attempt and one truthful receipt.
- Same-generation duplicate dispatch is suppressed.
- No provider call occurs in observation-only/local canaries.
- Action Card converges or exposes the expected explicit gate.
