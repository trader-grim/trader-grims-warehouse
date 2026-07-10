# PP-AMAZON-001 — Amazon FBM for books/media (exploration, not yet scoped for build)

**Opened:** 2026-07-04. Dave: "let's also start looking into branching out
to Amazon fulfilled by merchant for books and media. That would give us
another source of data as well as another marketplace with higher velocity."

**Status: research pass only.** No account exists, nothing built, no
commitment made. This is the same kind of "surface the real tradeoffs before
committing" pass as PP-EBAY-MOTORS-001's scoping — web-researched
2026-07-04, current as of that date.

## Why this fits TGW

- TGW already has real inventory in exactly the categories Amazon's media
  business is built around: DVD (1,245 items), Magazines (954), CD, Video
  Game (per the master-catalog category counts checked the same day).
  These are liquid, standardized, ASIN-matchable categories — the easiest
  possible fit for a new marketplace, not a stretch.
- A second marketplace is also a second **pricing-signal source** — Amazon's
  own active/used listing prices for the same ASIN are an independent comp,
  useful even for items that stay eBay-only (feeds the same comp-engine work
  in `pp/PP-PRICING-001.md` Phase -1).
- FBM (not FBA) matches TGW's existing model — no inventory gets shipped
  to a third-party warehouse; it stays here and gets picked/packed
  in-house, same as the current eBay flow. Fulfillment operationally
  resembles what's already built, just with a second "publish" target.

## What's genuinely different / harder than it used to be

**Books ungating has tightened significantly.** As of 2026, Amazon
frequently requires "Needs Approval" for used book listings and commonly
asks for **invoices for 10+ units from an approved supplier** to ungate —
something a thrift/estate-sale/one-off sourcing model (TGW's actual sourcing
pattern) usually cannot produce. This is a real, structural blocker for the
*Books* category specifically, distinct from DVD/CD/Video Games, which are
generally less gated. **Recommend scoping Books out of an initial push and
starting with DVD/CD/Video Games/Magazines instead**, where TGW already has
meaningful inventory and gating is historically lighter.

**Fee load on low-price media items is real and needs modeling before
committing.** Professional seller plan: **$39.99/month flat** (no per-item
fee, required for Buy Box eligibility and bulk/API access — Individual plan
charges $0.99/item instead and has no API access, a non-starter for an
automated pipeline). On top of that, Media category items (Books, Music,
DVD, Video Games) carry **~15% referral fee + a flat "closing fee" of
$1.35–$1.80/item** on top of the percentage. Combined with Media Mail
shipping (~$3.50–4.50/item for a typical DVD/media item) and TGW's own
observed sold prices for this class of item (median often $10–20 per the
sold-item data checked the same day, see PP-PRICING-001 Phase -1), the
margin math needs an actual model before committing — a $12 DVD sale could
plausibly lose $1.80 + ~$1.80 referral + ~$4 shipping ≈ **$7.60 in fees/
shipping against a $12 sale**, before COGS. This is not a reason to avoid
Amazon, but it is a reason to price-model per-category before listing
anything, not after.

## What's needed to actually start (none of this exists yet)

1. **Amazon Seller Central account** — Professional plan ($39.99/mo).
   Dave's decision/signup; not something to automate.
2. **SP-API (Selling Partner API) access** — the programmatic interface
   TGW would need for anything beyond manual listing (bulk create, price
   sync, order pull). Requires a registered developer application through
   Seller Central; distinct approval flow from eBay's, own OAuth model.
3. **Category ungating** — per-category, likely needed for at least some
   of DVD/CD/Video Games even if lighter than Books; unknown until an
   account exists and the actual current gating status is checked (gating
   policy changes over time, and general web research isn't a substitute
   for checking the real account).
4. **UPC/ASIN matching layer** — Amazon lists against existing product
   catalog entries (ASINs) via UPC/EAN, not free-text titles like eBay.
   TGW's existing `PP-LOOKUP-001` barcode/UPC lookup infrastructure is
   directly reusable here — this is a real synergy, not new work from
   scratch.
5. **A second "publish" pipeline stage** — mirrors the existing
   `ebay_draft`/`ebay_upload`/`ebay_publish` worker chain conceptually, but
   is genuinely new code: different auth, different API shape, different
   condition/aspect vocabulary (Amazon's condition grading differs from
   eBay's), different fulfillment/shipping-template model.

## Recommendation

**Worth pursuing, scoped narrowly first.** Suggested order:
1. Dave signs up for Seller Central (Professional), checks real
   current gating status for DVD/CD/Video Games/Magazines with TGW's
   actual account (gating is account-specific and time-sensitive — web
   research above is directional, not a substitute for checking live).
2. Build a price model per category (the fee math above) against TGW's
   real historical sold prices for that category — confirm margin is
   real before any listing work starts.
3. If margin holds: SP-API developer registration, then a minimal
   read-only integration first (pull comps/pricing data for ASINs
   matching TGW's UPC-identified inventory) — this alone delivers the
   "another source of data" half of Dave's ask without touching
   fulfillment/listing risk at all.
4. Listing/fulfillment pipeline (the harder half) only after (3) proves
   the data signal is worth it and margin math checks out.

## Open questions (Dave's call)

- Which category to pilot first — DVD (largest clean-category count at
  1,245) or a smaller but higher-margin category?
- Read-only comp-data integration only (low risk, immediate value) vs.
  committing to the full listing pipeline in the same push?
- Does this change PP-PRICING-001's priority — should Amazon comp data
  be a third `MarketDataProvider` alongside Browse comps and the
  own-sales engine, evaluated with the same eval-before-wiring discipline
  as the Gemini/SerpApi options?
