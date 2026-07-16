# TGW operational runbook gap report

**Reporter:** Tigwa / Leotha  
**Date:** 2026-07-13  
**Purpose:** Report observed gaps for Dave and Claude review. This is a reporting mirror, not a second task and not a canonical plan edit.

## Evidence reviewed

- `reference/TGW-Quickstart.md`
- `OPERATIONS-vault-sync.md`
- `reference/runbooks/nixos-prod-cutover-runbook.md`
- `reference/PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md`
- `reference/TGW-VAULT-RESTORE.md`
- `reference/TGW-VAULT-RESTORE-FIXES.md`
- `plan/pp/PP-RECOVERY-001.md`
- Live `tgw-snapshot` script, service, and timer
- Live NVMe SMART data and the 2026-07-13 thermal event
- Live eBay picklist/sync behavior observed earlier on 2026-07-13

## Immediate operational gaps

### 1. No canonical thermal incident and drill runbook

The known-hot tgw-prod NVMe has a local shutdown service at 88°C, but the reviewed runbooks do not define:

- who monitors which temperature,
- which SMART sensor is authoritative,
- warning/escalation thresholds,
- the operator-presence contract,
- who may stop workloads,
- who may shut down the host,
- drill envelope and stop conditions,
- evidence to preserve,
- or recovery verification.

The generic ACPI thermal zone is not sufficient. The incident showed the critical value is the hottest NVMe SMART sensor, not the composite temperature.

### 2. Same-host monitoring failure domain

The local shutdown mitigation worked, but no independent observer alerted full Tigwa during the first event. A host-local monitor cannot reliably report after the host loses power, networking, or its agent process.

The runbook should distinguish:

- local mitigation,
- external detection,
- delivery to Dave,
- handoff to full Tigwa,
- and confirmation that Dave actually saw/heard the alert.

### 3. Intervention authority is unspecified

Monitoring authority was incorrectly treated as shutdown authority. The current operator contract established by Dave is:

- observe and report,
- get Dave immediately,
- propose or perform only authorized workload mitigation,
- do not remotely shut down tgw-prod without asking Dave,
- leave the tested on-host 88°C service as the automatic safeguard.

This needs to be explicit in the thermal runbook rather than inferred during an incident.

### 4. Cool-boot snapshot window is undocumented

The working recovery sequence observed today was:

1. Boot while the SSD is relatively cool.
2. Immediately run the approved incremental Btrfs snapshot.
3. Verify the local read-only snapshot and received read-only snapshot.
4. Quiet additional I/O.
5. Observe the delayed thermal rise.

Evidence from the 16:32 snapshot:

- snapshot: `20260713T1632`
- completed in 27 seconds
- about 496.8 MB read and 112.4 MB written
- verified local and received read-only subvolumes
- SSD Sensor 1 rose approximately 76 → 80 → 84 → 85 → 87°C afterward
- no SMART media, integrity, or error-log entries

The procedure should include a prerequisite temperature gate, exact verification, and a rule against stacking other heavy I/O behind the snapshot.

### 5. Workload mitigation ladder is missing

The runbooks do not define a least-destructive response before total shutdown. A candidate ladder for review is:

1. Alert Dave and report temperature/trend.
2. Identify the active I/O producer.
3. Stop launching new heavy work.
4. Ask before pausing or terminating an active agent/job.
5. Preserve completed output and task state.
6. Observe recovery.
7. Allow the on-host shutdown service to act at its configured threshold.

No automatic process-killing policy should be inferred without Dave/Claude approval.

### 6. Physical operations section is explicitly a stub

`TGW-Quickstart.md` §9 still has TODOs for the intake station, scale, camera/Foldio360, and label printer. Physical procedures are business-critical and cannot be reconstructed reliably during failure or staff handoff.

### 7. Runbook review/freshness control is missing

The runbooks have mixed dates, migration states, and generations. A task-start gate should identify:

- document owner,
- last verified date,
- host/OS generation to which it applies,
- superseding plan or runbook,
- destructive-operation authority,
- and last successful drill.

Operational agents should read the current relevant runbook before acting, not only after an incident.

## Stale or ambiguous documentation requiring reconciliation

### 8. Restore command syntax inconsistency

`TGW-Quickstart.md` documents queue-first syntax:

```text
tgw enqueue-sku QUEUE SKU...
```

`TGW-VAULT-RESTORE.md` uses:

```text
tgw enqueue-sku --queue echo <any-sku>
```

The restore verification command should be checked against the live CLI and corrected through review.

### 9. Snapshot/vault naming ambiguity

`TGW-VAULT-RESTORE.md` says an older `TGW-SNAPSHOT-0` naming scheme was replaced by `TGW-VAULT`, while the live Btrfs snapshot service currently sends `/opt/TGW` to `/home/snapshot/TGW-SNAPSHOT-0`.

These may represent different devices and purposes, but the distinction is not obvious enough for emergency use. The runbooks should explicitly distinguish:

- TGW-VAULT secrets/database/flake recovery media,
- TGW-SNAPSHOT-0 Btrfs history target,
- and any ItemData/archive snapshot disks.

### 10. Old MX/pre-NixOS material remains operational-looking

`PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` and portions of the cutover runbook contain pre-NixOS assumptions, apt commands, MX Snapshot procedures, and old storage paths. They may remain valid as historical disaster recovery, but should be labeled by applicability so an agent does not treat them as routine current-host operations.

### 11. Remote-backup instructions conflict with current local safety boundary

Older restore material references `dbukove:/TGW/...`. Tigwa’s current boundary is never to touch or index `TGW/` or `TGW-SECRETS/` through the `dbukove` rclone remote. The owner and current approved restoration path need reconciliation before any agent follows those lines.

### 12. USB restore path remains incompletely drilled

`TGW-VAULT-RESTORE-FIXES.md` explicitly says the physical USB source path was not live-tested. The reviewed boot also showed `tgw-usb-stamp.service` failed. The runbook should say whether absence of the USB makes that failure expected, how to distinguish expected absence from a real stamp failure, and the accepted drill procedure.

### 13. Recovery documentation proves the danger of weak evidence searches

`PP-RECOVERY-001` records a false code-loss conclusion caused by incomplete commit searching and branch confusion. Operational diagnosis should require direct state verification across branches, editable installs, services, and live behavior before declaring code or work missing.

## eBay operations/API gaps to study next

Dave identified the eBay API as the next required study area. The following gaps are already visible:

### 14. Sold-order source-of-truth and picklist recovery

Earlier today:

- `tgw picklist --status Sold` did not match Dave’s eight sales,
- both eBay sync workers were inactive,
- a direct read-only completed-orders feed produced only a partial emergency list.

A runbook needs to define:

- authoritative sold-order source,
- expected synchronization latency,
- worker/timer health checks,
- completed-order time windows and pagination,
- cancellation/refund/combined-order handling,
- SKU/listing-to-ItemData reconciliation,
- deduplication and sync-state files,
- dry-run recovery,
- and acceptance against Seller Hub order count.

### 15. eBay API surface and responsibility map

The operator reference mentions Trading/Inventory behavior but does not present a concise responsibility map for:

- Trading API completed orders,
- Inventory API items/offers/listings,
- OAuth refresh and approved locked scopes,
- notification/webhook delivery,
- rate limits and pagination,
- legacy versus platform-native listings,
- and which worker owns each state transition.

### 16. Token and scope incident procedure

The Quickstart says scopes are locked and speculative additions have broken OAuth, and identifies `token_refresh` plus `tgw restart-ebay-token`. A dedicated API incident runbook should specify safe checks, re-consent authority, token-storage evidence, rollback, and confirmation that live synchronization resumed.

### 17. API correctness acceptance

A successful HTTP response or worker exit is not enough. For sold/picklist operations, acceptance should compare:

- eBay/Seller Hub order count,
- TGW synchronized sold records,
- generated picklist lines,
- physical SKU/location resolution,
- and unresolved exceptions.

## Recommended review order

1. Thermal/drill and external-alert runbook.
2. eBay sold-order/picklist runbook and API responsibility map.
3. Current-OS restore/snapshot index with explicit media names.
4. Quickstart command validation against the live CLI.
5. Physical station procedures.
6. Apply owner/date/applicability/drill metadata to every active runbook.

## Authority statement

This report does not edit canonical runbooks, change services, modify the flake, alter production data, or assign tracker work. Dave indicated that Tigwa may later check and update runbooks when confidence and authority are established. Until then, these are reported gaps for Dave and Claude to reconcile.
