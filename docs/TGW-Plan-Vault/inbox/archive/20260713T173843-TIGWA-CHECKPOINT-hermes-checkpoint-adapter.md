# TIGWA CHECKPOINT — Hermes checkpoint adapter controlled live acceptance

**From:** Tigwa  
**Date/time:** 2026-07-13 09:43 PDT  
**PP:** PP-HERMES-EA-001  
**Related tracker:** #1356 (open; Claude owns reconciliation/closure)  
**Mode/provenance:** LIVE — Hermes `tgw-exit` controlled acceptance, authorized by Dave  
**Purpose:** Checkpoint and verification record; not a duplicate task

## What was being done

Dave authorized a Hermes-native implementation of TGW's agent-neutral checkpoint contract after requiring Tigwa to route the self-update through Claude's inbox/security ownership seam. Claude reconciled and approved the proposal without additional gates, wrote the canonical `reference/TGW-CHECKPOINT-CONTRACT.md`, and created todo #1356.

This session also established a recurring read-only canonical-plan review so Tigwa learns about plan changes without Dave having to point them out manually.

## Current todo state

- **#1356 — open, PP-HERMES-EA-001:** canonical checkpoint contract plus Hermes-native adapter. Local skill installation and dry run are complete. Controlled live recovery acceptance is recorded below. Claude retains tracker reconciliation/closure.
- **#1353 — open, PP-HERMES-EA-001:** Groq/Edge voice. Dave confirmed Telegram voice works. The CLI/shared voice interface is built and software-tested; physical microphone/speaker acceptance remains blocked until `tigwa` has audio-device permission.
- **#1346 — open, PP-HERMES-EA-001:** separate Telegram bot/channel topology remains unresolved.

No todo was marked done or otherwise mutated by this checkpoint.

## Verified outcomes and evidence

1. **Claude/security reconciliation**
   - Response: `inbox/archive/20260713T162700-RESPONSE-1356-hermes-checkpoint-skill.md`
   - Claude confirmed no security monitor/hook watches `/home/tigwa/.hermes/skills/` and approved the proposed scope.
2. **Canonical contract**
   - `reference/TGW-CHECKPOINT-CONTRACT.md`
   - Live SHA-256 before acceptance: `c2dd8a15dc7395e2e3864fcc34c50e6d4e9deb597aa4ee8ed966dfca44373bca`
3. **Hermes adapter installed**
   - `/home/tigwa/.hermes/skills/tgw-exit/SKILL.md`
   - SHA-256 at dry-run acceptance: `d3e748e7b812699d55ee1f1de6a3aef9c17efa80201f3a4f4a69708f92afd398`
4. **Dry run passed with zero checkpoint writes**
   - Remote plan-vault Git status, canonical contract/response hashes, Tigwa tracker listing, Hermes memory hashes, and adapter hash were identical before and after the dry-run inspection.
5. **Regular plan review established**
   - Hermes cron job: `tigwa-canonical-plan-review`, every four hours, read-only, local delivery.
   - First run status: OK.
   - Durable review: `/opt/TGW/tigwa/context/plan-watch/latest-review.md`
6. **Fresh-session recovery acceptance — PASSED**
   - A fresh isolated `hermes -z` session was launched from `/opt/TGW/tigwa` with only `terminal,file` tools and no current-chat handoff.
   - It read this live inbox note over SSH and returned:

```text
TRACKER: #1356 — open; Claude owns reconciliation/closure.
BLOCKER: Physical CLI voice acceptance remains blocked by a1131 audio-device permission for user `tigwa`.
RESULT: CHECKPOINT_RECOVERY_OK
```

   - It also reproduced the exact next action from this note. Exit status was 0.

## Files/artifacts changed by the broader work

Local a1131:

- `/home/tigwa/.hermes/skills/tgw-exit/SKILL.md` — created
- `/opt/TGW/tigwa/context/plan-watch/latest-review.md` — created by scheduled review
- `/opt/TGW/tigwa/context/plan-watch/state.json` — created by scheduled review
- Hermes cron state — one recurring job added
- Hermes durable memory — updated with plan-watch location and Dave's pain-point-first development method

Shared TGW plan vault:

- This checkpoint note only. Claude independently created/updated the archived request/response, canonical contract, taskboard, and PP material before this controlled live checkpoint.

## Durable decisions/memories

Saved in Hermes's own memory system, not Claude's memory directory:

- TGW improvement priority is the largest pain point; time sinks are a common subset, not the criterion.
- Canonical plan review now runs every four hours, with the latest local synthesis at `/opt/TGW/tigwa/context/plan-watch/latest-review.md`.
- The Hermes checkpoint adapter follows the canonical agent-neutral contract and remains distinct from context compression/new-session commands.

## Open risks and blockers

- Physical CLI voice acceptance remains blocked by a1131 audio-device permission for user `tigwa`; Tigwa did not inspect or modify Dave's flake.
- Todo #1356 should not be closed until Claude reviews this controlled acceptance evidence.
- The plan watch identified live contradictions around the now-deferred a1131 wake dependency and stale hostname/MCP language; these belong to canonical-owner reconciliation, not checkpoint mutation.
- The plan vault already contains unrelated uncommitted work; this checkpoint must not claim or alter it.

## Exact next action

Claude reviews this controlled acceptance note and reconciles/closes todo #1356 if satisfied. Dave may then use the adapter by asking Tigwa to run `tgw-exit` in dry-run or live mode before `/compress`, `/new <name>`, or `/clear`.

## Close-out summary

Hermes-native `tgw-exit` is installed. Its zero-write dry run passed, its controlled live checkpoint was read back and verified, and a fresh isolated Hermes session recovered the tracker, blocker, and exact next action from this note with `CHECKPOINT_RECOVERY_OK`.

**Checkpoint safety statement:** This checkpoint performed no commit, merge, push, live/production data mutation, TGW source change, service/worker/queue action, eBay/catalog action, production-config change, or flake access/change.
