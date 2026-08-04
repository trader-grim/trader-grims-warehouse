# DONE — todo #1256: per-item Best Offer control

## Pre-flight verification

Verified the actual eBay Sell Inventory API `BestOfferTerms` schema live
(WebSearch + official eBay dev docs) before building anything, rather than
guessing field names:
```
listingPolicies.bestOfferTerms: {
  bestOfferEnabled: bool,
  autoAcceptPrice:  {currency: str, value: str},
  autoDeclinePrice: {currency: str, value: str},
}
```

## Shipped (all 3 deliverables from the todo)

1. **`draft_listing` fields** — `best_offer_enabled` (bool),
   `best_offer_auto_accept_price`, `best_offer_auto_decline_price`. No
   allowlist change needed — confirmed `draft_listing.*` sub-fields are
   already freely PATCHable (unlike top-level `BULK_FIELD_KEYS`).
2. **Operator form control** — new "Best Offer" row in the item-detail
   editor (`_render_item_detail_html`), matching the existing store-category
   row styling: checkbox + two price inputs, wired into the existing
   `saveEbayDraft()` single-save-button flow (same pattern as
   shipping_profile/return_policy_id/store_category_id).
3. **`_build_offer_bodies` wiring** — `bestOfferTerms` only sent when
   `draft_listing.best_offer_enabled is not None` (distinguishes "operator
   made no choice, leave eBay's category default alone" from "operator
   explicitly turned it off" — `False` is a real, meaningful choice and
   still gets sent as `bestOfferEnabled: false`, not omitted).

## Live evidence

- `pytest -q tests/test_best_offer_terms.py` — 5 passed: omitted-when-unset,
  enabled=true, explicitly-disabled=false, with both auto-accept/decline
  prices, enabled-without-prices omits the price sub-fields.
- `pytest -q tests/test_http_server.py -k best_offer` — 2 passed: control
  reflects saved state (checked + prefilled prices), unchecked when unset.
- `pytest -q` (full suite) — 2059 passed, 1 skipped (was 2052 — 7 new tests).
- `ruff check` — clean.
