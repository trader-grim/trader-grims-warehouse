# Research — Library/archive as an independent state-machine authority

**Status:** Staged research / design hypothesis. Not implementation authorization.
**Requested by:** Dave Buko, 2026-07-22.
**Scope:** Separate the authority and lifecycle of TGW’s retained knowledge/evidence library from tgw-prod’s operational runtime state.

## Hypothesis

The library/archive is its own system, not another TGW worker queue. It should have an independently operated state machine, ideally on separate infrastructure and with separate credentials, so tgw-prod can submit evidence but cannot silently promote, rewrite, or erase canonical library history.

## Proposed authority split

| Domain | Authority |
|---|---|
| Live items, jobs, workers, service operations | TGW production state / existing operational state machine |
| Artifact admission, provenance, review acceptance, canonical placement, supersession, retention | Library/archive state machine |
| Event delivery/replay between domains | JetStream or another durable transport; not the source of truth |
| Raw and large retained content | Content-addressed archive storage; history/provenance retained separately from derived indexes |

The library state machine would not replace TGW’s operational state machine and must not become a second work queue.

## Candidate artifact lifecycle

```text
observed / received
  -> staged
  -> provenance-verified
  -> review-pending
  -> accepted into canonical library
  -> superseded (earlier version retained)
  -> archived / retention-expired only by explicit policy and authority

At any point:
  -> integrity exception
  -> source unavailable / replica stale
  -> rejected / deferred
```

Each transition should retain artifact/version ID, content hash, source/run identity, actor, timestamp, decision/evidence reference, and predecessor/supersession relationship.

## TGW-to-library contract

TGW may publish an append-only submission containing an artifact reference/content hash, origin/run identity, and declared artifact type. The library independently verifies and records it.

TGW can read only a clearly labelled library result: accepted canonical reference, staged/deferred status, or integrity/degraded exception. A TGW worker’s successful write or self-attestation is not promotion to canonical library truth.

## Why host separation is useful

1. A production compromise, accidental cleanup, or runtime database failure cannot by itself rewrite library admission/acceptance history.
2. The library can independently witness production artifacts and flag missing, changed, stale, or uncommitted evidence.
3. Long-lived provenance, rebuild manifests, and retention policy do not impose pressure on operational state.
4. A separately recoverable host/copy supports a real rebuild/restore drill.

Separate boxes alone do not establish authenticity. The design must avoid shared broad write credentials and prove: append-only submission receipt, content identity, library-side acceptance receipt, independent verification, anomaly reporting, and recovery/restore behavior.

## Relationship to current TGW direction

PP-AIOPS-001 / PP-RUNNERCOMMS-001 establish JetStream as the intended durable transport for audit, handoff, and mailbox semantics. That is compatible with this idea: JetStream would carry library submission/receipt events, while the library’s state machine remains an independent authority. `queue_jobs` and tgw-prod’s operational database should not become the library ledger.

## Initial boundary proposal

Potential library-owned classes:
- canonical plans and plan evidence;
- research/source captures and review/acceptance records;
- runbooks and resource job descriptions/resumes;
- integrity manifests, archive records, and rebuild evidence.

Remain TGW-production-owned:
- live inventory and operational workflow state;
- worker/job scheduling and retries;
- runtime service health.

## Open design questions for later

1. Is the library only an independently witnessed archive, or also the canonical home for plans, runbooks, resource resumes, and accepted research?
2. What artifact types may be submitted automatically versus requiring human staging?
3. What is the minimal independently operated authority substrate: append-only database/ledger plus content-addressed storage, Git/git-annex plus signed receipts, or another shape?
4. What identity/credential separation survives compromise of tgw-prod?
5. What acceptance, supersession, retention, integrity-exception, and rebuild-drill evidence is mandatory?
6. Which small read-only API/MCP queries let TGW use accepted library material without granting operational systems broad library writes?

## Non-authorizations

This note does not authorize a new database, service, host, broker subject, credential, flake modification, archive move, data ingestion, or production cutover. It is retained research pending Dave acceptance and a bounded future design/review packet.
