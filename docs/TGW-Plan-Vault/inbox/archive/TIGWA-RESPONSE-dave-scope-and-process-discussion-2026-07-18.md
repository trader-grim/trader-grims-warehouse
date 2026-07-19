# TIGWA response — Dave discussion of SSH scope, eBay connector, and process pattern

**From:** Tigwa, relaying Dave
**To:** Claude / Dave
**Date:** 2026-07-18
**Re:** `RESPONSE-ssh-credential-scoping-review-2026-07-18.md`, `RESPONSE-ebay-token-lifecycle-consult-2026-07-18.md`, and `RESPONSE-issue-resolution-pattern-2026-07-18.md`
**Status:** decision/clarification record; no account, key, sudoers, flake, service, token, or tracker mutation authorized by this note

## 1. SSH credential scoping (#1459)

- Dave agrees with the **separate, read-only identity** direction until a concrete write requirement exists. Reuse of the existing local `tigwa` service identity remains out of scope.
- Dave raised **“t-lite?”** as a possible name. This needs a naming decision before build: `tigwa-observe` describes the limited remote-dispatch role more precisely, while “Tigwa-lite” already describes the monitor/gateway role and could be confused with a broader service identity. Keep the proposed identity read-only regardless of its final name.
- **Break-glass means Dave, not an agent.** It is a named human recovery owner: someone who may use a documented manual path if the new fail-closed read boundary incorrectly blocks a necessary safe operation during migration. It must not become a standing broad agent credential or silent re-grant. Recommend Dave as the initial/sole break-glass owner unless he delegates it explicitly.
- **Tracker writes:** no write requirement is approved now; keep them out of the first cut. The review’s concern is that a seemingly read-only tracker summary implemented via a broad `tgw` CLI path could accidentally tunnel arbitrary arguments or evolve into task writes. When a real write use case appears, define one named capability and its review/acceptance gate. For routine agent receipts/status, prefer a dedicated append-only report/evidence seam over changing taskboard state.

## 2. eBay read-only connector (#1513)

- Dave approves proceeding **independently** of SSH credential scoping. The connector remains a narrow API/MCP surface and does not receive token-file, credential-file, refresh, or marketplace mutation access.
- It should expose the health-diagnostic data actually needed for safe operator use. At minimum, return a non-secret token availability/expiry or age indication and a clear `ebay_token_unavailable` failure result; add read-only refresh-worker health/last-success-or-failure evidence if that is necessary to diagnose the same availability condition. Do not expose access/refresh token material or broaden the business-operation surface under the label of diagnostics.

## 3. Issue-resolution loop

Dave agrees it is **not a contract**, but it is an observed pattern worth consulting when deriving or reviewing processes. Retain it as comparative evidence; do not promote it into a shared normative vocabulary or policy without a concrete process use and Dave review.

## Remaining named decision

Before SSH Stage 0 begins, settle the dedicated identity name (`tigwa-observe` recommended for clarity versus a `t-lite`-derived name). No other SSH-scope expansion is requested.
