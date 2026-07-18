# Consultation request: eBay token lifecycle for a read-only Tigwa Seller Hub connector

**From:** Dave via Tigwa
**Date:** 2026-07-17
**Requested reviewer:** Claude
**Purpose:** decide the correct access-management design before connecting Tigwa to eBay/Seller Hub authority.

## Context

The next planned connector is a narrow, read-only eBay Seller Hub authority path. Its purpose is to obtain account-backed categories, policies, field/capability information, and evidence for TGW parity work—never to guess local values.

The relevant eBay access token expires relatively quickly, but TGW already has a facility intended to keep that access alive. We need the actual existing lifecycle and the correct safe integration seam, not a new ad-hoc token-copying flow.

## Please inspect and report

1. Identify the existing TGW eBay token/refresh facility:
   - responsible service/module and runtime owner;
   - how access-token expiry is detected and refresh is performed;
   - durable credential/refresh-token ownership and protection boundary;
   - concurrency/refresh behavior and current health/alert path.

2. Recommend the least-privilege interface for a dedicated Tigwa process/profile to obtain **read-only, short-lived eBay access** without exposing durable credentials or creating a competing refresh loop.

3. State whether the correct seam is:
   - an existing TGW internal API/MCP capability;
   - a narrowly added broker/status endpoint/tool;
   - a controlled Vivaldi Seller Hub session for UI-only evidence;
   - or a combination, clearly divided by purpose.

4. Define the authorization boundary for the first connector iteration:
   - read-only operations and required scopes;
   - explicitly prohibited listing/offer/inventory/account mutations;
   - how token/authorization failure is surfaced to Dave;
   - whether the planned dedicated `tigwa` account changes any ownership or service-account decision.

5. Recommend an evidence/provenance contract for returned Seller Hub data: account/source identifier, retrieval time, stable IDs, freshness/refresh behavior, and what counts as authoritative versus partial or unavailable.

## Constraints

- Do not copy, print, send in chat, or move token/refresh-token secrets.
- Do not change eBay credentials, scopes, refresh state, services, MCP tools, or production code as part of this consultation.
- Do not attach Tigwa to `db`'s normal browser profile.
- Preserve TGW's existing recovery path and make any new broker/tool contract explicit and auditable.
- This is design/review work first; implementation follows only after Dave reviews the recommendation.

## Desired response artifact

A concise Plan Vault review naming the existing facility, recommended connector topology, exact read-only boundary, remaining credential/operator action, and any blocker or decision needed from Dave.
