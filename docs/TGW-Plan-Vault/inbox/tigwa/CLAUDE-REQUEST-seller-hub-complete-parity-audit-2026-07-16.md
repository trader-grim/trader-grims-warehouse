# DAVE SCOPE CORRECTION — audit the Seller Hub, not only the listing editor

**For:** Claude
**From:** Tigwa, recording Dave's direction
**Date:** 2026-07-16
**Supersedes scope limitation in:** `CLAUDE-REQUEST-ebay-listing-form-parity-audit-2026-07-16.md`

## Correction

The listing-editor parity issue is one symptom. The requested audit is **Seller Hub parity across the TGW product**, because many Seller Hub pages, controls, and functions are absent entirely or represented by partial/guessed local substitutes.

Do not treat the existing listing-form request as a self-contained fix. It is the first workstream of a complete Seller Hub capability audit.

## Audit rule

For each actual Seller Hub navigation area and workflow available to Dave's seller account, compare:

```text
Seller Hub function / control / data source
    ↔ TGW equivalent (if any)
    ↔ authoritative eBay source and account-specific backing data
```

Do not pre-declare a fixed menu inventory from public documentation. Capture the seller account's actual current navigation, pages, feature flags, policy lists, and context-sensitive listing controls as observed from Seller Hub and/or authorized read-only APIs.

## Minimum coverage classes

Inventory and audit every available function in these classes, plus anything account-specific found during discovery:

1. Listing lifecycle: create, drafts, active, scheduled, ended/unsold, sold, bulk edit, relist/revise, item specifics/category requirements, images, variations/compatibility where available, listing errors and publishing state.
2. Account-backed listing choices: categories/store categories; business policies for payment, fulfillment, returns; handling/location; offer terms; promotion/marketing choices; templates/defaults; all dropdowns, suggestions, and autocomplete sources.
3. Commerce operations: orders, shipping/labels/tracking, cancellations, returns, disputes/claims, payments/payouts/fees, taxes where surfaced, buyer messages, offers, feedback.
4. Seller operations: performance/service metrics, research/market insights, reports/downloads, promotion/marketing hub, storefront controls, account/business-policy/settings surfaces, subscriptions/entitlements where relevant.
5. Automation and exception handling: bulk actions, rules/automations, warnings, errors, holds, policy violations, rate limits, and recovery paths.

## Required deliverable: Seller Hub parity register

Create a durable, reviewable register with one row per observed Seller Hub capability:

| Seller Hub area | Function/control | Account/category context | eBay backing source | TGW state | Gap type | Risk | Evidence | Proposed disposition |
|---|---|---|---|---|---|---|---|---|

`TGW state` must be one of:

```text
full parity | partial | guessed/local substitute | read-only only | absent | intentionally deferred
```

`Gap type` must distinguish missing UI, missing data synchronization, incomplete account list, missing workflow/action, wrong payload mapping, unhandled exception state, and unverified source.

## Evidence and safety

- Observe/download/query read-only data first. Do not create, revise, end, relist, ship, refund, message, accept an offer, alter a policy, or otherwise mutate eBay in the audit.
- Preserve page/form/API provenance, timestamp, account scope, category context, option IDs/names/counts, and screenshots or sanitized exports where permitted.
- Never call a guessed local default, a historical listing, or incomplete cache equivalent to live Seller Hub data.
- Where a capability cannot be safely audited through the API, request a Seller Hub view/screenshot or a controlled operator walkthrough; never request credentials in chat.

## Priorities

Rank by operational/revenue risk, beginning with absent or guessed data/actions that can produce a wrong listing, failed fulfillment/return handling, lost buyer communication/offer, missed payout/problem, or silently incorrect business-policy behavior.
