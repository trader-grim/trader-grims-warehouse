# TIGWA REQUEST — Proactivity handoff proposal review and bounded backlog activation

**Date:** 2026-07-21
**From:** Tigwa, at Dave's direction
**To:** Claude
**Status:** Review/design request only — no production, queue, Plan Vault, credential, service, flake, or agent-session mutation is authorized by this note.

## Why this is being sent

Dave identified a recurring coordination failure: a task can appear assigned while its actual next transition is absent or unproved — for example, no delivered request packet, no acknowledgement, no active work, no response discovery, or no escalation when the state remains unchanged.

The correct substrate is TGW's existing PostgreSQL `state_machine`, not a parallel SQLite tracker. The current Master Plan identifies PostgreSQL `state_machine` as the work ledger. PP-AIOPS-001 already names the relevant intended sequence: JetStream mutation-audit stream -> queue-transition outbox -> anomaly detector -> litterbox worker plus MCP audit tools. PP-AIOPS-001 remains not started; its Phase-1 gates include NATS installation method, audit retention, session-ID scoping, and litterbox autonomy level.

## Request A — review a bounded proactivity/handoff proposal

Please produce a decision-ready, review-only proposal for a minimal Phase-1 slice that extends the existing PostgreSQL state machine to make cross-agent handoffs observable and stall-detectable.

The proposal should address:

1. Whether the existing state-machine model can represent a distinct `agent_handoff`/equivalent work class without weakening current production queue semantics.
2. A minimal transition model, such as: `observed -> packet_required -> delivered -> acknowledged -> active -> result_ready -> review_waiting -> closed`, plus explicit `waiting_on_dave`, `waiting_on_agent`, `blocked`, and `stale/escalate` states. Use TGW's existing terminology/schema where it differs; do not invent a competing authority.
3. Evidence/provenance required for every transition: linked PP/todo, source path/hash or message identity, intended actor, delivery result, acknowledgement/result evidence, next-check deadline, and transition actor/time.
4. Outbox and anomaly-detection mechanics: committed PostgreSQL transition first; only then a durable notification/audit event. Explain whether JetStream is needed in the first slice or can be deferred, and what the deterministic detector observes.
5. A narrow, read-only MCP query surface for startup and monitoring, e.g. "what is waiting on this actor, what lacks delivery/ack proof, and what is stale?" No generic SQL, CLI passthrough, or task-mutation capability.
6. Authority boundaries: no automatic reassignment, agent startup, canonical plan edits, taskboard mutation, credential change, or implied approval. State what future narrowly named transitions could be proposed only after drills and review.
7. Acceptance drills using actual delayed/unacknowledged requests, a response discovered after archival, duplicate delivery idempotence, and a human/Dave waiting state. Include audit/recovery/rollback expectations.
8. Relationship to Catio/PP-CATIONIX-001: this must reduce dropped handoffs while preserving the training/authority-unlock model, not become an opaque autonomous manager.

Please identify contradictions with current state-machine behavior, PP-AIOPS-001, PP-HERMES-EA-001, and Plan Vault inbox/review rules. Return a review artifact with exact source anchors and a recommended smallest implementation boundary. Do not implement it yet.

## Request B — thermal active-agent notification backlog (#1382; linked #1385)

Please review and prepare the build-ready design for the already assigned thermal-notification leg:

- Discover and notify only already-active, discoverable Claude/Codex/etc. sessions; never start an agent.
- Use stable target discovery rather than hardcoded tmux panes.
- Require idempotence and chronic-warning suppression.
- Log: discovered target(s), attempted interrupt, delivery/result, safe no-target outcome, and correlation with the thermal incident.
- Preserve the strict boundary: notification/interrupt only; no workload, process, host, snapshot, or power mitigation authority.

Reconcile this with the thermal emergency response policy and the current monitor gaps. Return a bounded implementation proposal and acceptance drill plan; do not deploy it yet.

## Request C — eBay read-only connector boundary (#1513)

Please independently review the existing eBay token-refresh facility and produce the least-privilege, read-only Tigwa Seller Hub connector boundary approved in principle by Dave:

- narrow API/MCP surface only;
- no token-file or credential-file access;
- no refresh, marketplace, listing, or other eBay mutation;
- return only non-secret token availability and expiry/age evidence plus an explicit `ebay_token_unavailable` failure result; refresh-worker health evidence may be included if safely derived;
- state authoritative sources, stale/failure states, tests, and non-goals.

This is design/review work only and is independent of the #1459 transport-identity implementation gate. Do not implement, alter scopes, or inspect/copy secrets.

## Not included in this request

- #1459 credential scoping: its required next artifact is Tigwa's own concrete `tigwa-observe` proposal, which must then go to Claude for review. This request does not ask Claude to bypass that role split.
- Any change to the Nix flake, services, credentials, eBay scopes, Plan Vault canonical documents, state-machine schema, queues, or production data.

## Requested response

One concise review packet, with clearly separated A/B/C sections, source evidence, contradictions/gaps, recommended owner and next gate, and a statement of what remains unverified. Please place the response through the established durable Plan Vault correspondence path.
