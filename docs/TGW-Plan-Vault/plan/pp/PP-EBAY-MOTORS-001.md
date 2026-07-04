# PP-EBAY-MOTORS-001 — eBay Motors is not accounted for anywhere (URGENT)

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

## Next steps (planning session, not a single packet)

1. Decide: is a live per-SKU sweep for marketplaceId worth ~19.5k calls
   (Inventory pool, cheap relative to the 2M/day budget) to get a real count,
   or is there a cheaper bulk source to find first?
2. Decide whether `tgw.apis.ebay.trading.py`'s SiteID needs to become
   per-call/per-item configurable, and what that touches (every Trading API
   call site, `ebay_legacy_sync`, `ebay_sku_migrate`, `ebay_upload`'s
   `UploadSiteHostedPictures`).
3. Decide whether item JSON needs a `marketplace_id` field added to the
   schema (`TGW-Item-JSON-Schema.md` update) and where it gets populated from
   (offer sync, ebay_stage, or a dedicated backfill).
4. Decide whether CATEGORY-QUIRKS.md / fulfillment policy config need a
   marketplace dimension, and audit existing Motors listings for correctness
   once they can be identified.

This is filed as a planning request, not a build packet — no todos filed yet
beyond the immediate duplicate-checker scoping (done). Dave to prioritize
against the rest of the plan.
