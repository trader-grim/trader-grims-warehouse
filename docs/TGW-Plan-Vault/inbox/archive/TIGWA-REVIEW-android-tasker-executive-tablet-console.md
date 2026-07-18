# TIGWA REVIEW REQUEST — Android/Tasker executive tablet console recommendation

**Reviewer:** Claude and Dave  
**Requested by:** Tigwa  
**Date:** 2026-07-15  
**Related tracker:** todo #1425  
**Plans:** `PP-TASKER-001`, `PP-HERMES-EA-001`, `PP-HARDWARE-001`

## Artifacts for review

1. `docs/TGW-Plan-Vault/inbox/TIGWA-REPORT-android-tasker-executive-tablet-console.md`
2. `docs/TGW-Plan-Vault/inbox/TIGWA-REPORT-android-tasker-executive-tablet-console.yaml`

The Markdown is the human-readable research/recommendation. The YAML is its lightweight machine-readable companion for coding-agent retrieval.

## Requested review

1. Confirm the proposed boundary: Tasker/Termux edge devices expose named, audited capabilities; they do not accept arbitrary agent shell, intent, or Tasker execution.
2. Confirm the local-LAN-first alarm/ACK vertical slice is the correct first build target for the tablet in front of a1131.
3. Confirm that source import must preserve and inventory the actual 2014→present Tasker/CameraData estate before any modernization or refactor.
4. Identify conflicts with the real source tree, device policies, camera/scanner integrations, or existing PP-TASKER-001 direction after Dave supplies the tree.
5. Decide whether the first visual layer should be a Tasker Scene or a locally served web/PWA surface launched by Tasker.
6. Confirm that review/approval presentation is route/display-only in v1 and does not create an unattended production-control path.

## Evidence / scope

- Current official Tasker, TaskerNet, AutoApps, Join, Termux:API/Termux:Tasker, Android dedicated-device, KDE Connect, Syncthing, and kiosk documentation was reviewed; source URLs are included in the report.
- The report was independently augmented with focused research on Tasker ingress/security, AutoApps maintenance/permission risk, and the Termux edge boundary.
- Existing TGW `PP-TASKER-001`, earlier Android annunciator proposal, and Camera App design proposal were reviewed.
- No source tree, repository, Tasker profile, Android device, service, network, Nix flake, or production data was changed.
- The report explicitly marks source/device details that remain unknown until the actual tree and exports are available.

## Requested outcome

A review decision that either:

- approves Phase 0 source preservation/inventory after tree arrival;
- requests a bounded correction; or
- records an alternative first vertical slice.
