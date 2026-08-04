# CLAUDE request — tracker-management boundary proposal for Tigwa

**From:** Dave via Tigwa
**To:** Claude
**Date:** 2026-07-18
**Related:** #1459 SSH credential scoping; Tigwa read-only MCP posture; `TIGWA-RESPONSE-dave-scope-and-process-discussion-2026-07-18.md`
**Status:** design/review request only — no code, account, credential, MCP-scope, tracker, flake, service, or production-data change authorized

## Decisions now settled

1. The dedicated remote read identity will be named **`tigwa-observe`**.
2. Its first cut remains entirely read-only; the existing local `tigwa` service identity is not reused.
3. Dave is the initial human break-glass owner. This is a documented manual recovery route only, never standing agent bypass authority.
4. No tracker-write capability is approved now.

## Request

Work out a reviewable tracker-management boundary that supports Tigwa’s genuine operational need without turning status reporting into generic taskboard mutation authority.

Please inspect the current relevant TGW tracker/taskboard mechanisms read-only and return a concise proposal that distinguishes these three lanes:

1. **Read-only tracker visibility:** exactly what task/status/assignment fields Tigwa needs, through what narrow interface, and how a “summary” cannot tunnel broad `tgw` CLI arguments.
2. **Routine operational receipts/status:** a dedicated append-only evidence/report path suitable for monitor/gateway/agent delivery results, separate from canonical task-state mutation. Specify ownership, schema minimums, retention/review path, and how it remains auditable without becoming another hidden queue.
3. **Future write request path:** if a later real need appears to create/change a todo or tracker state, define a review-first proposal capability (named target, explicit fields, provenance, human acceptance) rather than a direct generic write tool. It is not approved to implement now.

For each lane, state the least-privilege trust boundary, identity/transport assumptions (including `tigwa-observe`), failure behavior, and the evidence needed to validate it. Identify any existing implementation that can be reused safely and any remaining Dave decision. Keep the proposal separate from the eBay connector and from SSH dispatcher implementation details except where a capability boundary genuinely depends on them.

Deliver a review artifact to `inbox/tigwa/`; do not begin implementation.
