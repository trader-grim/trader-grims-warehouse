# CLAUDE triage — Seller Hub UI authority findings (current-state)

**From:** Claude
**To:** Tigwa / Dave
**Date:** 2026-07-18
**Related:** `TIGWA-NOTE-seller-hub-ui-authority-findings-2026-07-18.md`, PP-SELLERHUB-001,
PP-LISTEDITOR-001, C14
**Todo:** #1543
**Status:** current-state triage artifact only — no code, eBay mutation, or tracker change made.

## Method

Read-only code investigation (`http_server.py`, `ebay/sync.py`, `apis/ebay/{trading,specifics,
conditions}.py`, `category_aspect_migration.py`) plus `reference/invariants.md` (C14) and
PP-LISTEDITOR-001 for already-recorded findings. Citations are file:line into
`src/tgw/` unless noted.

## Triage table

| # | Finding | Current TGW behavior/evidence | Authoritative eBay evidence | Classification | Gap / next bounded mapping action |
|---|---|---|---|---|---|
| 1 | **Store Category dropdown** | Option list built by scanning `category-groups.json` for `store_category`/`store_category_id` (`http_server.py:5730-5744`) — a **local/config-assembled list**. Separately, TGW *does* call live `get_store_categories` (Trading API GetStore, `apis/ebay/trading.py:308`) but only at **push time**, to translate an already-chosen ID into a name for `storeCategoryNames` (`ebay/sync.py:373-403`) — never to populate the dropdown itself. Push-time cache is process-lifetime, no TTL (`sync.py:373-383`). | Live GetStore call exists in-repo and is proven to work (used at push) — just not wired to the picker. | **Open** | Wire `dl-store-cat-select`'s option source to the same `get_store_categories()` call (or a periodically-refreshed cache of it) instead of `category-groups.json`, so the dropdown can never offer a category the live account doesn't have. Smallest slice: confirm whether `category-groups.json`'s `store_category` entries were ever verified against a live GetStore pull, or are hand-authored/inferred. |
| 2 | **Shipping Profile / fulfillment-policy dropdown** | Config-driven, not live at selection time. Resolution chain (`ebay/sync.py:181-306`) reads `tgw-api-config.json` keys (`fulfillment_policy_by_profile`, `_by_category`, `_by_size_class`, `_free_shipping`, `_envelope`, global default `fulfillment_policy_id`="FC4"). Dropdown options come from a static cache file `ebay-fulfillment-policies.json` (`http_server.py:2424-2431`), not a live `/sell/account/v1/fulfillment_policy` call. There **is** a live post-push reconciliation net (`ebay_sync.py:420-424`) that overwrites the local mirror if the pushed policy drifted from eBay's live `listingPolicies.fulfillmentPolicyId` — but that only catches drift *after* a push, not before the operator picks from the dropdown. | No live pre-selection call to the Account API found; live-authoritative check only exists post-push. | **Partial** | This is exactly the finding's own language: "TGW had configuration-driven fulfillment-policy resolution... that does not prove the UI list is the current account-scoped policy list." Confirmed true. Next slice: determine whether `ebay-fulfillment-policies.json` is refreshed by a live Account API pull anywhere (worker or manual command) and how stale it can get before the dropdown shows a policy the account no longer has, or one it has that isn't listed. |
| 3 | **Category dropdown + dependent controls** | Category picker is live (`/api/ebay/category-search`, type-ahead against eBay categories). On selection, `loadCatCtx()` calls `/api/ebay/category-context/{id}` (`http_server.py:2352-2452`), which does 3 sub-calls: (i) conditions — live eBay condition-policy metadata, cached (`apis/ebay/conditions.py`); (ii) aspects — live Taxonomy API `get_item_aspects_for_category` (`apis/ebay/specifics.py:112,209-247`), two-tier cache (per-category live pull, 5,000/day quota; bulk shard, 100/day quota); (iii) store-category/fulfillment/pricing suggestions — from local `category-groups.json` (same source as #1/#2 above). On category change, conditions are re-fetched and remapped same-or-worse via `best_condition_for_enum` (`http_server.py:2372-2386`). Non-official (custom/orphaned) aspects are **not auto-discarded** — `category_aspect_migration.py:43-90` computes orphaned Set B keys live but requires an explicit operator action to migrate, a deliberate divergence from eBay's own auto-discard, to preserve data (Prime Directive 1) rather than silently drop it. | Conditions + aspects legs are live-authoritative and cached correctly; store-category/fulfillment legs of the *same* context call inherit gaps #1/#2. | **Partial** | Conditions and aspects legs classify **resolved**. Store-category and fulfillment-policy legs of this same combined call inherit findings #1/#2 above — same fix, not a separate one. No new mapping slice beyond #1/#2. |
| 4 | **Supporting data/API linkage (the full chain requirement)** | Chain is fully present and correctly live-sourced for conditions and aspects (Metadata + Taxonomy APIs → adapter → cache → UI → payload → verification). Chain is **config/local-sourced, not live**, for store-category and fulfillment-policy at the selection step (live-verified only at push or post-push reconciliation, not before the operator picks). | See #1/#2. | **Partial** (same underlying gap as #1/#2) | Same fix as #1/#2 — move the pre-selection data source for store-category and fulfillment-policy dropdowns from static/config to a live-refreshed pull, matching the pattern already proven for conditions and aspects in the same `category-context` endpoint. |

## Related confirmed parity incidents — current status

| Incident | Current state | Evidence |
|---|---|---|
| Condition granularity (fake USED_EXCELLENT/GOOD/ACCEPTABLE fan-out) | **Mostly resolved, one residual gap.** `allowed_conditions_for_category` now returns the real per-category eBay set (`http_server.py:2356-2359`), not a fabricated superset. But outbound `_CONDITION_MAP` still collapses many free-text synonyms to a fixed `USED_GOOD` default (`ebay/sync.py:136-168`), and a runtime fallback still force-rewrites a rejected condition to `USED_EXCELLENT` (`sync.py:700-704`) — narrowed, not eliminated end-to-end. |
| Best Offer control | **Resolved.** Operator-visible tri-state select (unset/enabled/disabled) — `http_server.py:6165-6169,5697-5715` — pushed via `bestOfferTerms` only when explicitly set (`sync.py:554-565`), PP-OFFER-001/#1256. |
| Custom/seller-defined aspects invisible | **Resolved.** Aspects form renders official category aspects plus any stored keys not in the official list, tagged "CUSTOM ASPECT" with an explicit keep/discard control (`http_server.py:3144`, `_CATEGORY_CONTEXT_IIFE`). |
| Category change discards non-category aspects (or fails to) | **Resolved by design divergence, not parity.** TGW does not auto-discard on push — the operator gets a live orphaned-aspect detector (`category_aspect_migration.py:43-90`) and an explicit migrate/discard choice, deliberately differing from eBay's silent auto-discard to protect data per Prime Directive 1. This is intentional, not a bug — flagging only so it isn't mistaken for an unfixed parity gap. |
| C14 (Material field silent-save-failure) | **Partially resolved, two new same-class bugs open.** `invariants.md:760-854`: originally "⚠️ open, no detector." A fleet-wide round-trip detector was built 2026-07-18 (todo #1468, `test_c14_*` suite) and is green for item-detail edits, aspects form, bulk edit, `accept_proposals`. Two new C14-class findings surfaced *while building the detector* remain open: **todo #1522** — unlocked `title`/`description` silently reverted by the padlock auto-sync on the next unrelated `draft_listing` save (`invariants.md:846-854`); and a second instance noted but not fully re-read in this pass (starts around `invariants.md:855` — worth a direct follow-up read before treating C14 as closed). |

## Summary classification

- **Resolved:** conditions metadata, aspects metadata (Taxonomy API leg), Best Offer control,
  custom aspect visibility, category-change data-preservation design.
- **Partial:** condition-enum collapse/fallback (residual); C14 (detector built, 2 new findings open).
- **Open:** store-category dropdown authority (config list vs. live GetStore); fulfillment-policy
  dropdown authority (static cache vs. live Account API, pre-selection).

## Smallest next bounded mapping actions (no implementation authorized yet)

1. Confirm `category-groups.json`'s store-category entries' provenance — hand-authored/inferred,
   or ever verified against a live `get_store_categories()` pull. (Answers finding #1's open gap.)
2. Confirm whether/how `ebay-fulfillment-policies.json` gets refreshed from the live Account API,
   and how stale it's allowed to get before the dropdown can show a wrong or missing policy.
   (Answers finding #2/#4's open gap.)
3. Direct follow-up read of `invariants.md` past line ~855 for the second unresolved C14-class
   bug's full description (this pass didn't capture it completely).

None of these three require an eBay mutation or code change to answer — all are read-only
evidence-gathering, consistent with this triage's scope.
