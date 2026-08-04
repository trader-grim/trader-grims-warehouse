# TIGWA NOTE — Seller Hub UI authority findings for current-state triage

**From:** Tigwa, relaying Dave’s July 16 Seller Hub mapping/redesign discussion
**To:** Claude
**Date:** 2026-07-18
**Related:** Seller Hub parity audit; PP-SELLERHUB-001 / PP-LISTEDITOR-001; C14
**Mode:** Findings and triage request only — no code, service, eBay, or tracker mutation authorized.

## Purpose

This records the specific UI/data-authority failures Dave and Tigwa identified while deriving the Seller Hub mapping and redesign approach. These are not merely requests for visually nicer forms. They are cases where a control can exist while being operationally untrustworthy because its choices or behavior are locally inferred, stale, incomplete, or not linked to current eBay account authority.

Some findings may already be corrected or partially corrected. Do not treat this as a blanket reopening of old issues. Establish current state from current TGW implementation, tests, and live read-only eBay/Seller Hub evidence where available; classify each row `resolved`, `partial`, `open`, or `unknown` with citations.

## Anchor findings

### 1. Store Category dropdown

**Observed concern:** The Store Category control did not have demonstrated provenance to the actual Store-category hierarchy available to the seller account. A locally assembled or heuristic list is not an authoritative substitute.

**Required mapping:**
- account/store context and source endpoint/page;
- stable category ID and display path/label;
- completeness, pagination, refresh/cache state, and error state;
- TGW adapter/API that retrieves/normalizes the list;
- UI selection, draft/payload mapping, save/reload, and visible result verification.

### 2. Shipping Profile / fulfillment-policy dropdown

**Observed concern:** TGW had configuration-driven fulfillment-policy resolution and possible fallback behavior, but that does not prove the UI list is the current account-scoped policy list that Seller Hub presents. A displayed local/default choice must not be represented as a complete authoritative eBay choice set.

**Required mapping:**
- Seller Hub-visible policy choice semantics and account scope;
- current authoritative policy source, stable IDs, names, eligibility, and freshness;
- relevant dependencies (listing/category/marketplace/profile context);
- clear separation between an eBay-derived choice, a TGW convenience recommendation/default, and an explicit operator selection;
- exact write payload plus fresh externally meaningful result verification.

### 3. Category dropdown and dependent controls

**Observed concern:** Category selection cannot be treated as an isolated local selector. It must use the current marketplace/category-tree identity and drive truthful updates of dependent controls.

**Dependencies to map:** allowed conditions; required/recommended aspects; seller-defined aspects; Store-category applicability; business-policy/fulfillment availability; category-change effects; source failure and retry behavior.

### 4. Supporting data and API linkage

Dave’s requirement was explicit: map the control *and the existing eBay supporting data/API that builds it*. The comparison is not “Seller Hub has a dropdown and TGW has a dropdown.” The required chain is:

```text
Seller Hub control / observed behavior
→ eBay backing data/API or page evidence
→ account, category, listing, and marketplace context
→ stable IDs, values, pagination, eligibility, and dependencies
→ TGW adapter/service and cache/provenance behavior
→ TGW UI control and payload mapping
→ save/reload/result verification
```

## Related confirmed parity incidents

These are related examples of the same root pattern — TGW behavior was assumed/invented/partial rather than verified against actual eBay behavior:

1. **Condition granularity:** TGW represented three fake Used grades where many eBay categories expose one usable Used bucket.
2. **Best Offer:** no operator-visible enabled/disabled control existed.
3. **Custom aspects:** seller-defined custom aspect fields were invisible because TGW showed only official category-defined fields.
4. **Category change:** eBay discards non-category aspects when category changes; TGW retained incompatible/invisible aspects instead.
5. **C14 correction boundary:** an item-detail Material correction could silently fail. The standing invariant is: an operator correction either takes effect or visibly reports failure; it must never be silently lost.

## Requested Claude output

Produce a concise current-state triage artifact, not a speculative redesign:

| Finding | Current TGW behavior/evidence | Authoritative eBay/Seller Hub evidence | Classification | Gap and next bounded mapping action |
|---|---|---|---|---|

For every non-resolved item, identify the smallest missing evidence or mapping slice. Do not claim a local static/configured list is eBay-authoritative without source, scope, stable IDs, freshness, and completeness evidence. Do not make write-side changes or eBay mutations while doing this triage.

## Design direction retained

The eventual reviewed build packet remains:

```text
Seller Hub behavior map + Dave/operator intent
→ eBay supporting-data/API map
→ TGW adapter/API contract map
→ TGW UI-flow map
→ reviewed SHCS and acceptance suite
→ replaceable implementation executor(s)
```

The value is the reviewed specification and its evidence, not any particular UI contractor. Web and Flutter may consume the same domain/API/workflow contract while retaining surface-specific presentation.
