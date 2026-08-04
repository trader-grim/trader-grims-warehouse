# PP-EBAY-MOTORS-001 — eBay Motors is not accounted for anywhere (URGENT)

**Framing locked 2026-07-18 (Dave):** "Motors needs to be available for every
eBay account. It is a barnacle marketplace. We just have to accommodate it."
Motors isn't an opt-in feature scoped to one account — any listing on any eBay
seller account can end up marketplace-tagged EBAY_MOTORS regardless of intent
(that's how it surfaced here in the first place, on the primary account).
The fix (`marketplace_id` field, `site_id` threaded through `trading_call()`)
is therefore **account-agnostic infrastructure**, not primary-account-specific
work — it must hold for the primary account today and for
[[PP-EBAY-ACCOUNT2-001]]'s future second account the same way, with no
per-account special-casing. This resolves PP-EBAY-ACCOUNT2-001's open
"does multi-marketplace mean Motors-on-existing-account or something
second-account-specific" question: Motors accommodation is shared plumbing
both accounts ride on, not a second-account feature.

**Opened:** 2026-07-04 (session 43, surfaced live during PP-PHOTOSYNC-001 P10).
**Status: URGENT PLANNING REQUEST — needs scoping before any fix work.** Dave: "we
do not have ebay motors accounted for anywhere... please add that as an urgent
planning request item."

## How this surfaced

Live-testing P10's repair path on `tgw20160122242616788`, eBay rejected the offer
update with: *"Best Offer is not permitted with a SKU selling on multiple eBay
marketplaces."* The item's Inventory API offer showed `marketplaceId: "EBAY_MOTORS"`
— a distinct eBay marketplace we have never explicitly designed for. Checking
turned up nothing: zero mentions of eBay Motors in any reference doc, zero
`marketplaceId` field anywhere in our item JSON schema, and one confirmed
structural gap in the code itself.

## Confirmed gaps (facts, not speculation)

1. **We do not store `marketplaceId` on any item, anywhere.** There is no field
   in the item JSON schema recording which eBay marketplace an offer/listing
   lives on. We cannot even answer "how many of our items are on eBay Motors"
   from local data alone — it would require a live per-SKU query across the
   fleet (no cheap bulk source found yet for this specific field; the Inventory
   API bulk `getInventoryItems` list used elsewhere in this session does not
   include marketplaceId, only the per-SKU offer GET does).
2. **`tgw.apis.ebay.trading.py` hardcodes `_SITE_ID = '0'` (EBAY_US) for every
   single Trading API call** — `GetMyeBaySelling`, `GetOrders`,
   `ReviseFixedPriceItem`, `UploadSiteHostedPictures`, `EndFixedPriceItem`,
   everything. eBay Motors is a distinct SiteID in the Trading API's site
   catalog. Any item whose ORIGINAL listing lives under a non-US-general
   SiteID may be invisible to, or behave unpredictably under, every Trading
   API call this codebase makes — `ebay_legacy_sync`'s "sync every active
   listing" claim and `ebay_sku_migrate`'s revise calls may both silently
   miss or mishandle Motors-site listings.
3. **Marketplace-specific business rules exist and we don't model them.** The
   Best-Offer/multi-marketplace conflict found live is one instance; eBay
   Motors also has its own category structure, fulfillment/shipping policy
   requirements (vehicle parts fitment data, freight-class shipping), and
   likely different condition/aspect requirements that our category-groups /
   fulfillment-policy config (keyed generically, no marketplace dimension)
   has no way to express.

## What is NOT yet known (the actual planning work)

- **How many items are genuinely on eBay Motors** — no cheap way to count
  today; needs either a live per-SKU offer sweep (expensive, ~19.5k calls) or
  a cheaper bulk source to be found (candidate: does the Trading API's
  `GetMyeBaySelling` response include a site/marketplace field per item when
  called with a non-default SiteID? Untested.)
- **Whether any Motors items are ALSO listed on a different marketplace
  simultaneously** — the literal duplicate-listing risk the rejection named.
  Not yet checked at scale; P10's duplicate-checker now surfaces per-item
  marketplace info going forward (see below) but has not been run at scale.
- **Whether our fulfillment policies, category mappings, and condition
  handling are even valid for Motors listings** — CATEGORY-QUIRKS.md and
  TGW-Config-Reference.md have no marketplace dimension today; unknown
  whether existing Motors listings are already misconfigured.
- **Whether the Trading API SiteID gap has caused silent failures already** —
  `ebay_legacy_sync` and `ebay_sku_migrate` have been running against SiteID=0
  for the project's whole history; unknown how many Motors-site actions
  silently no-op'd or errored without being flagged as Motors-specific.

## Immediate, scoped fix landed same session (not the full planning work)

`tgw.ebay.pull.check_legacy_duplicate_listing()` now also surfaces
`marketplace_id` and an `is_ebay_motors` flag in its result, and treats
**any SKU with more than one live offer across marketplaces as a duplicate
risk outright**, regardless of whether one of them matches the locally-recorded
listing_id — this is the closest thing to "checking eBay Motors" scopeable
without the fuller Motors project below. This does NOT close the gap: it
only makes the ONE check this session was already building marketplace-aware.
It does not give us a marketplaceId field on items, does not fix the SiteID
hardcoding, and does not tell us how many Motors items exist.

## Planning update — 2026-07-04, the scoping pass (todo #1129)

**Question 1 (item count) is now ANSWERED — no expensive sweep needed.**
Todo #1131's census (`reference/ebay-marketplace-census-2026-07-04.md`)
parsed the R1.8 snapshot's raw offer capture (`incoming/ebay/*.jsonl.gz` —
zero live eBay calls) and found: **202 EBAY_MOTORS SKUs out of 19,448
unique SKUs with a recorded marketplaceId** (48,885 offer records total).
That's ~1% of the marketplace-tagged fleet, ~0.36% of the full 55,419-item
catalog. **This is a small, bounded problem, not a fleet-wide one.**

**Question 2 (cross-marketplace duplicates) — ANSWERED for this snapshot:
zero found.** The census's cross-marketplace check (same SKU live on two
marketplaces at once — the exact risk the original rejection named) came
back empty against this data. Good news, but it's one point-in-time
snapshot, not a live guarantee — worth re-running the census after any
bulk requeue/resync activity, not just once.

**Question 3 (SiteID hardcoding fix) — scoped, smaller than feared.**
Checked `trading.py` directly: `_SITE_ID = '0'` is referenced in exactly
**one place** — inside the single central `trading_call()` function that
every other function in the module (`get_orders`, `get_my_ebay_selling`,
`end_item`, `revise_item_sku`, `revise_item_pictures`, `get_best_offers`,
`respond_to_best_offer`, etc.) delegates to for its HTTP headers. This is
NOT a sprawling per-call-site rewrite — it's one function signature change
(`trading_call(cfg, call_name, ..., site_id=_SITE_ID)`, threaded through to
the ~9 public wrappers as an optional param, EBAY_MOTORS's real Trading API
SiteID is `100`). Recommend: build this once `marketplace_id` exists on
items (question 4) so callers have something to pass.

**Question 4 (schema field) — recommend adding it now, cheaply.** Add
`marketplace_id` to the item JSON schema (`TGW-Item-JSON-Schema.md`),
populated two ways: (a) **immediate backfill** for the 202 known SKUs
using the census's already-captured data (no live calls needed — the
offer records already have it), (b) **going forward**, populate from
`ebay_stage`/offer-sync whenever a fresh offer GET happens (the field is
already present in the raw capture, just never promoted into the item
JSON).

**Question 5 (category/fulfillment marketplace dimension) — defer.**
Given the tiny confirmed footprint (202 items, zero live duplicate risk
found), this doesn't need to block anything else. Worth a follow-up
audit of just those 202 SKUs' category/fulfillment config once
`marketplace_id` is backfilled and queryable, but not urgent at fleet
scale.

### Recommended priority order (Dave's call)
1. Backfill `marketplace_id` onto the 202 known SKUs from existing census
   data (cheap, no API calls, todo-sized).
2. Add `marketplace_id` to the schema + wire population into `ebay_stage`
   going forward (small, contained).
3. Thread `site_id` through `trading_call()` (small, one function + ~9
   thin wrapper signature updates) — do this once (1) exists so there's a
   real value to pass.
4. Audit the 202 SKUs' category/fulfillment config for Motors-correctness
   (manual/scripted review, not urgent).
5. Re-run the census periodically (e.g. alongside future R1.8-style
   snapshots) rather than treating today's zero-duplicates result as
   permanent.

This is no longer "urgent, unscoped" — it's scoped, small, and ready to be
sliced into ordinary todos whenever Dave prioritizes it against the rest
of the plan. No code changed by this planning pass.
