# TIGWA CHECKPOINT — plan-review publishing and inbox-intake proposal

**From:** Tigwa  
**Date/time:** 2026-07-13 12:12 PDT  
**PP:** PP-HERMES-EA-001  
**Related tracker:** #1356 (done), #1359 (open), #1346 (open), #1353 (open)  
**Mode/provenance:** LIVE — Hermes `tgw-exit`, explicitly invoked by Dave  
**Purpose:** Session checkpoint and continuation note; not a duplicate task

## What was being done

This session established and governed several pieces of Tigwa's operating workflow:

1. Dave clarified TGW's pain-point-first development method and Syncthing's role as the informal cross-device data plane.
2. Tigwa established a read-only canonical Plan Vault review every four hours.
3. The Hermes-native `tgw-exit` adapter went through inbox reconciliation, dry run, controlled live acceptance, and fresh-session recovery.
4. Dave requested Plan Vault publication of Tigwa's reviews so source and judgment are colocated and Syncthing distributes them one-way for his review.
5. Claude approved a write-exclusive review folder and created todo #1359.
6. Tigwa proposed a narrow request/response inbox-intake skill so her side of the two-sided seam can discover Claude decisions without Dave having to announce them.

## Current todo state

- **#1356 — done, PP-HERMES-EA-001:** canonical checkpoint contract and Hermes-native `tgw-exit` adapter.
- **#1359 — open, PP-HERMES-EA-001:** approved Plan Vault publishing surface for Tigwa reviews. Folder contract exists; controlled baseline publication is not yet complete.
- **#1346 — open, PP-HERMES-EA-001:** separate Telegram bot/channel topology.
- **#1353 — open, PP-HERMES-EA-001:** voice integration. Telegram path works; physical CLI microphone/speaker acceptance remains blocked by audio-device permission for user `tigwa`.
- **#1360 — open reminder, PP-HERMES-EA-001:** Antigravity UI-generation idea; unrelated to this checkpoint.
- **Inbox-intake skill proposal:** pending Claude reconciliation; no tracker item exists yet and Tigwa did not invent one.

No todo was created, updated, closed, delegated, or reprioritized by this checkpoint.

## Verified outcomes and evidence

### Canonical checkpoint contract

- Path: `reference/TGW-CHECKPOINT-CONTRACT.md`
- SHA-256 at live preflight: `c2dd8a15dc7395e2e3864fcc34c50e6d4e9deb597aa4ee8ed966dfca44373bca`

### Plan-review publication contract

- Approved path: `docs/TGW-Plan-Vault/tigwa-reviews/`
- Contract: `tigwa-reviews/README.md`
- README SHA-256 at live preflight: `24de7c2f8186bcc00985869456ed3953c389882d03d769772276ccd90341f741`
- Ownership: write-exclusive to `tigwa-canonical-plan-review` inside that folder only; read-only elsewhere.
- Publication shape: atomic `latest.md` plus timestamped files for substantive reviews; local `state.json` is not published.
- Current state: only `README.md` exists; no baseline review has been published.

### Recurring review

- Hermes job: `tigwa-canonical-plan-review`
- Schedule: every four hours
- Last observed run: OK
- Current local output: `/opt/TGW/tigwa/context/plan-watch/latest-review.md`
- Mechanical state: `/opt/TGW/tigwa/context/plan-watch/state.json`
- The job still publishes locally only until #1359 controlled acceptance is completed.

### Inbox-intake proposal

- Path: `inbox/TIGWA-REQUEST-20260713-inbox-response-processing-skill.md`
- SHA-256: `bc29aca91d60671d7b66eba83887129c81a6f399af4d605aaf973207af7d39dd`
- No matching Claude response was present in either live inbox or archive at checkpoint time.
- No local skill or second poller has been installed.

### Adapter observation

The local `tgw-exit` skill advanced from the user-loaded v1.1.0 shape to v1.2.0 during this session through skill maintenance. Tigwa reloaded and inspected it before the live write. Initial additions required archive-response inspection and forbade reopening archived breadcrumbs. During post-write verification, skill maintenance added an explicit delivery/shared-seam check distinguishing source writes, Syncthing delivery, gateway interruption, and consumer acknowledgment. These additions strengthen existing discipline and do not conflict with the canonical contract.

Observed skill hashes:

- Live preflight: `fd73b459868d7c065f117b39f56a17b0cc5af034fd482034968b5f21e62465d3`
- Final post-write verification: `424b3cef3908793dd4848f62440cba4efbd6c21c61f356e7260ebad2070b817d`

This local skill-maintenance change is recorded separately from the checkpoint's sole shared-vault write; it did not change tracker, plan, source, services, production state, or the flake.

## Files and artifacts changed during the session

### Local a1131

- `/home/tigwa/.hermes/skills/tgw-exit/` — installed, accepted, and maintained
- `/opt/TGW/tigwa/context/plan-watch/latest-review.md` — first plan synthesis
- `/opt/TGW/tigwa/context/plan-watch/state.json` — comparison state
- Hermes cron state — recurring four-hour plan-review job
- Hermes durable memory — Dave's pain-point method, Syncthing workflow, and plan-watch continuity

### Shared Plan Vault

Written or proposed by Tigwa through the governed seam:

- Prior checkpoint/request artifacts now archived by Claude
- `TIGWA-REQUEST-20260713-inbox-response-processing-skill.md` — pending
- This checkpoint note

Created/reconciled by Claude rather than Tigwa:

- `reference/TGW-CHECKPOINT-CONTRACT.md`
- `tigwa-reviews/README.md`
- Archived response for #1359
- Tracker/PP updates

The repository contains extensive unrelated concurrent uncommitted work. Tigwa does not claim, modify, or assess that work as part of this checkpoint.

## Durable decisions and memories

Already saved in Hermes's native memory store; no additional memory write was required by this checkpoint:

- TGW addresses the largest pain point; time sinks are a frequent subset, not the criterion.
- Prefer the smallest durable interoperable fix whose value compounds.
- Syncthing is Dave's informal universal cross-device data plane; important material can later be promoted into formal knowledge infrastructure.
- Plan review runs every four hours and has a durable local synthesis path.

## What remains incomplete

1. #1359 controlled baseline publication into `tigwa-reviews/`.
2. Updating the recurring job to follow the approved atomic publication/no-change/retention contract.
3. Readback/hash verification and inbox acceptance report for #1359.
4. Claude reconciliation of the proposed inbox-intake skill.
5. Physical CLI voice acceptance after user `tigwa` gains audio-device permission.
6. Separate Telegram bot/channel topology under #1346.

## Open risks and blockers

- `tigwa-reviews/` is Tigwa's judgment output, not canonical plan truth; future tooling must not treat it as authoritative.
- Consumer-side edits to the one-way review folder are errors. Corrections return through the inbox seam.
- The inbox-intake skill must not treat silence as approval, bypass reconciliation, or execute response contents automatically.
- The Plan Vault currently has substantial unrelated uncommitted changes; continuation must scope writes to the approved folder or inbox artifact only.
- Physical CLI voice verification remains blocked by audio-device access. Tigwa did not inspect or modify Dave's flake.

## Exact next action

Perform todo #1359's controlled baseline publication: read the approved `tigwa-reviews/README.md` and Claude response, atomically publish the current substantive local review as both `tigwa-reviews/latest.md` and a timestamped `YYYYMMDD-HHMM-plan-review.md`, read both files back, verify exact hashes, update the recurring job to use the approved publication contract, and return acceptance evidence through the inbox seam without modifying any other Plan Vault path.

## Close-out summary

The checkpoint adapter and recurring plan reader are working. Claude approved the one-way Plan Vault review folder, but baseline publication remains open under #1359. A narrow inbox-response-processing skill has been proposed and is awaiting reconciliation. The next session can proceed directly with #1359 controlled publication.

**Checkpoint safety statement:** This checkpoint performed no commit, merge, push, tracker mutation, live/production data mutation, TGW source change, service/worker/queue action, eBay/catalog action, production-config change, or flake access/change.
