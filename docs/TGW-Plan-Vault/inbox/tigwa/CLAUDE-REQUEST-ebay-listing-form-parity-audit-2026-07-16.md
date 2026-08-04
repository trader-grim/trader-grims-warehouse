# DAVE REQUEST — eBay listing-form parity audit (no guessed backing lists)

**For:** Claude
**From:** Tigwa, recording Dave's direction
**Date:** 2026-07-16
**Scope:** read-only audit and comparison first. Do not mutate listings, account policies, canonical configuration, or source data without a reviewed follow-up.

## Problem observed

The TGW listing form's Store category, shipping-profile, and return-profile lists appear incomplete and potentially guessed rather than retrieved from the seller's authoritative eBay account data. That is unacceptable for a listing editor.

## Required outcome

Treat the current eBay listing flow and the seller account's actual backing values as the source of truth. Produce a field-parity matrix showing every eBay listing field / section relevant to TGW, whether TGW has it, its UI control, its backing source, and its status:

```text
present-and-authoritative | present-but-partial | present-but-guessed |
missing | intentionally-not-supported
```

For every dropdown/suggestion/autocomplete, record its exact source, identity key, refresh/cache policy, and whether TGW is showing the complete current eBay account list.

## Explicit initial findings to verify

1. **Best Offer** must be an operator-visible checkbox/toggle in the editor — not an implicit or hidden behavior. Its enabled state and auto-accept/auto-decline terms must map truthfully to eBay's listing policy.
2. **Store categories** must come from the seller's actual eBay Store category hierarchy, including primary/secondary choices and IDs — not a short guessed category-group list.
3. **Shipping/fulfillment policies** must come from the seller's actual eBay policy list, with IDs and current names — not size-class guesses or a hand-maintained partial list.
4. **Return policies** must come from the seller's actual eBay return-policy list, with IDs and current names — not assumed defaults.
5. Compare all remaining visible listing-form controls and account-backed choices likewise: category selection/leaf constraints, condition/aspects, pricing, quantity, duration/format where applicable, payment/fulfillment/return policy, item location, handling time, images, title/description, SKU, promotions, and any eBay-required conditional fields.

## Evidence standard

- Use account-authorized, read-only eBay sources or a controlled Seller Hub listing-form inspection. Do not ask Dave to expose credentials in chat.
- Preserve endpoint/form source, retrieval timestamp, seller-account scope, IDs, names, and result counts.
- Distinguish eBay API capability from what the Seller Hub form actually exposes for this account/category.
- Do not call a static local mapping, old listing, or model inference “authoritative.”
- If an account/API scope cannot retrieve a list, report the exact blocker and a safe read-only method; do not fabricate values.

## Deliverable

Submit a concise review artifact to the normal Plan Vault seam containing:

1. field-parity matrix;
2. authoritative-list inventory with counts and provenance;
3. missing/partial/incorrect items ranked by listing risk;
4. proposed data-source/refresh architecture;
5. test plan that proves list completeness and correct eBay payload mapping before any implementation.
