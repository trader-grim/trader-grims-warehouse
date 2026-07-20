# Stage authorization: PP-EVIDENCE-001 Stage 0 — audit only

**From:** Dave, captured by Tigwa
**Date:** 2026-07-20
**Status:** authorized to start as a read-only staged audit; not implementation authorization
**Related:** proposed PP-EVIDENCE-001; PP-AGENTTRACE-001; PP-DATAINTEGRITY-001

## Dave’s direction

Start and stage the audit-only foundation first. The archive/history library is the rebuild substrate for TGW; integrity is paramount. Lockdown controls follow only after the audit establishes the current asset/trust/recovery posture and Dave reviews the staged proposal.

## Stage 0 scope

Produce a provenance-backed, live-verified asset/trust/recovery register for the initial critical classes:

1. Plan Vault and review/inbox history.
2. Agent trace raw archives and `agent_runs`/commitment design dependencies.
3. `state_machine` Postgres ledger and backup/restore path.
4. ItemData/raw catalog evidence and known redundancy/verification path.
5. Git repository/history protection and recovery posture.
6. Derived indexes/renders, explicitly labelled rebuildable rather than canonical.

For each: canonical/raw versus derived classification; current writer/verifier/recovery identities; storage/replication/backup/versioning evidence; retention; independently trusted boundary (or its absence); and open anomaly/recovery questions.

## Initial live observations — 2026-07-20

- Plan Vault exists at `/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault`, directory mode `0770`, owner/group IDs `900:900`.
- Agent traces exist at `/opt/TGW/var/agent-traces`, mode `0770`, owner/group IDs `900:900`; Phase 1 trace integrity hardening remains draft-only.
- ItemData exists at `/opt/TGW/data/ItemData`, mode `0750`, owner/group IDs `900:900`.
- Postgres backups exist under `/opt/TGW/var/backups`; `tgw-db-backup.timer` is enabled and active, with the latest observed dump `state_machine-20260720.dump` at 57,754,545 bytes. This confirms scheduled backup presence, not an independent trust domain or successful restore.
- The critical review has already identified unprotected GitHub `main`, GUI-managed Plan Vault Syncthing configuration, the same-identity writer/verifier gap, and a 35-day Postgres-backup prune versus permanent agent-trace retention as items to verify/classify rather than silently fix.

## Explicit boundaries

- Read-only inspection and report staging only.
- No storage, Syncthing, GitHub, database, flake, hook, credential, permission, retention, source, or production-data mutation.
- No canonical PP amendment, worker dispatch, or recovery drill until a staged report and Dave decision gate exist.
- A backup, hash, or versioned copy must be labelled only with the guarantee it actually establishes; no agent self-attestation is treated as sufficient proof.

## Next action

Complete the initial asset/trust register using live evidence and existing review artifacts, then return the staged `PP-EVIDENCE-001` proposal and a bounded recovery-drill decision for Dave review.
