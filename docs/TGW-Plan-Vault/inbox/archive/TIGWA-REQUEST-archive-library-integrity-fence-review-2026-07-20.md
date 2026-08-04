# Request: independent sinkhole review — archive/library integrity and rebuild fence

**From:** Tigwa, for Dave
**To:** Claude
**Date:** 2026-07-20
**Status:** design review only; no implementation authorization
**Programs touched:** PP-AGENTTRACE-001; PP-DATAINTEGRITY-001; any later Plan Vault/archive retention work

## Dave’s direction

TGW’s history and archives are not merely backups. They are the evidence and recovery substrate from which the system must be reconstructable after host loss, service failure, bad automation, or compromise. Integrity is paramount. We need the strongest practical anti-tamper fence, while accurately stating what each layer proves and preserving recovery paths.

This follows the agent-trace anti-cover-up review, but is deliberately broader than trace logging. It includes raw traces, ItemData and associated evidence, Plan Vault/source history, retained research/support artifacts, derived indexes, and their recovery/version history.

## Initial proposal for review

Build a cross-cutting integrity/rebuild contract, phased and review-gated, rather than declaring any one filesystem, database row, or Syncthing share “immutable.” The contract would classify every durable asset and its evidence state:

- **raw canonical evidence** — retained source bytes plus origin, custody, and content commitment;
- **derived/recomputable views** — indexes, rendered documents, catalogs, search records, and UI summaries that may be rebuilt from canonical inputs;
- **independent recovery evidence** — separately administered/versioned copy or witness whose compromise path is not identical to the primary writer;
- **integrity status** — self-claimed, hash-verified, independently witnessed, degraded, anomalous, or unrecoverable/unknown.

For each class, document: writer identity, read/delete/replace powers, commitment/verification method, independent copy/witness, retention/versioning semantics, restore procedure, and expected operator-visible anomaly state.

## Threat model to test, not assume away

Please review against at least:

1. An ordinary agent or automation process that can rewrite its own outputs/logs.
2. A compromised primary host or service identity.
3. Accidental partial write, bad deployment, deletion, or corrupted replication.
4. A silent divergence between raw evidence and a derived index/render/UI.
5. A recovery event where only a subset of hosts/copies survives.
6. An attacker or faulty process that changes the first copy before its first hash/replication commitment.
7. A false sense of security from application-level "append-only" logic that is bypassable by the same Unix/DB identity.

## Proposed architectural principles

- Preserve raw evidence and make derived layers explicitly recomputable.
- Commit and independently verify bytes; do not accept agent self-attestation as sole proof.
- Separate writer, verifier, and recovery/witness authority as far as practical; name residual shared-trust assumptions where separation is not yet feasible.
- Prefer append-only evidence records and explicit, logged correction/supersession paths over silent overwrite.
- Versioned/off-host copies improve recovery, but must be labelled as recovery evidence rather than proof the first capture was truthful.
- Reconciliation must detect and surface missing, orphaned, changed, uncommitted, stale, or sync-degraded evidence; it must not auto-repair, delete, or close anomalous records.
- A real acceptance test includes a bounded restore/rebuild drill and evidence of what was recovered, verified, regenerated, and still uncertain.

## Boundaries and non-goals

- This is not approval for blanket immutability, a new PKI scheme, ubiquitous root access, broad backups, credentials, flake changes, data migration, or automatic remediation.
- Security controls must preserve Dave’s recovery paths; a fence that cannot be operated, audited, or restored from is not sufficient.
- Agent trace hardening packet #1586 remains separately DRAFT and not authorized.
- A hash detects post-commit byte changes; it does not independently prove initial capture authenticity unless a genuinely separate witness/capture boundary exists.

## Requested independent review

Please return a concise, evidence-backed sinkhole review—not generic concurrence. Specifically:

1. Identify missing asset classes, trust boundaries, adversary paths, and recovery failures.
2. Challenge any claim that is not actually provided by hashes, application constraints, Syncthing versioning, database permissions, or host separation.
3. Recommend the smallest staged foundation that materially improves authenticity and rebuildability without over-broad authority or infrastructure changes.
4. State whether this should extend PP-DATAINTEGRITY-001, receive a linked governing PP, or use another existing canonical home; cite source anchors.
5. Propose acceptance evidence for a future bounded restore drill.
6. Recommend a durable cross-agent protocol: critical change proposals are independently reviewed through the Plan Vault, responses are semantic-read and reconciled, and the active agents proactively surface the resulting Dave decision gate rather than waiting for repeated manual inbox prompts.

Return via the Plan Vault inbox. Do not implement, dispatch, alter storage/retention/configuration, or amend canonical plans based on this request.
