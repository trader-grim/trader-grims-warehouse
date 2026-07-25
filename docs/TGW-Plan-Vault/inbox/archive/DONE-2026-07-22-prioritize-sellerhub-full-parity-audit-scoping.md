# TIGWA REQUEST — Prioritize Seller Hub full-parity audit scoping

**From:** Tigwa, at Dave’s direction
**To:** Claude
**Programs:** PP-SELLERHUB-001; related PP-EBAY-ACCOUNT2-001
**Status:** Request for a bounded audit/scoping packet and review. No eBay mutation, credential handling, model-spend commitment, or UI implementation is authorized by this request.

## Why this needs to move up

Dave identifies the Seller Hub audit as a key trove of hidden gaps. The recent dropdown fixes are valuable, but they demonstrate the larger issue: local approximations can look complete until compared with the seller account’s actual authoritative surface.

The governing PP already says TGW should ultimately do everything Seller Hub does, better, and records that the full account-specific feature audit was proposed but not run. Please treat this as a high-value discovery/prioritization lane, not as another narrow listing-editor ticket.

## Requested first artifact: SHCS audit/scoping packet

Produce a reviewable **Seller Hub Capability Specification (SHCS) audit plan** that defines how we will build an account-specific gap register. It must cover the full Seller Hub surface, not just listing creation: inventory/listings, orders/fulfillment/returns, messages, marketing/promotions, finances/reports, store/categories, business policies/settings, bulk and exception workflows, and context-dependent controls/feature flags.

For each capability/control, the eventual register must distinguish:

- real Seller Hub account evidence (UI and/or authorized read-only API), scoped and timestamped;
- TGW’s actual web/API/worker/mobile behavior with code/runtime provenance;
- backing source, stable IDs, count/pagination, retrieval/freshness/cache behavior for every list/dropdown/autocomplete/default;
- `full-parity`, `partial`, `guessed-local-substitute`, `read-only-only`, `absent`, `intentionally-deferred`, or `blocked-unverified`;
- gap type, operational/revenue/compliance/data-integrity risk, and disposition.

Explicit required invariant: no guessed local value can be presented as a complete seller-account-authoritative option set. Best Offer remains an explicit operator-visible checkbox, not an inferred default.

## Safe discovery boundary

- Start read-only against the established seller account; no listing creation/revision/end/relist, policy/settings change, messaging, order action, or credential copying.
- Do not begin an API connector from token-health telemetry. First identify/review the existing token-facility owner, least-privilege read-only seam, scopes, expiry failure behavior, and provenance envelope.
- The prospective second account remains a later sandbox only; no account registration/credential work is requested here.
- Do not spend Gemini or other model quota merely because the old PP mentioned it. The packet must compare deterministic/UI/API collection versus model-assisted synthesis, name estimated scope/cost, preserve raw evidence, and require Dave approval before a spend-bearing run.

## Requested output and sequencing advice

Please return:

1. the smallest safe Phase 0 evidence-collection plan;
2. a proposed SHCS register schema and sample rows for existing known gaps/fixes;
3. required account/UI/API evidence and explicit blockers;
4. a risk-ranked initial audit order, beginning with wrong-listing/fulfillment/policy/compliance risks;
5. proposed acceptance criteria for declaring a control truly authoritative rather than merely rendered;
6. how this audit should be scheduled as a capacity-funded discovery lane without displacing current production reliability work.

This is a design/review request. Do not update Seller Hub code or the canonical Master Plan until Dave reviews the resulting packet.
