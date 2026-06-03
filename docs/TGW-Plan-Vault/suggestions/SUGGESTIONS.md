- [ ] 2026-06-03T02:07 :: test suggestion
- [ ] 2026-06-03T04:25 :: Add tgw requeue command: bulk-enqueue ai_identify for items matching a filter (e.g. has photos but no title, or ai_hint set but not identified) — for catalog maintenance without triggering eBay listing pipeline
- [ ] 2026-06-03T04:25 :: Get full item history into planning context: session history, hint trail, AI identification rounds per SKU — for audit and tuning visibility
- [ ] 2026-06-03T04:25 :: eBay Marketplace Insights (sold+trends) scope: apply for buy.marketplace_insights access in eBay developer portal; also investigate Finding API findCompletedItems as interim sold-price source (App ID only, no user token needed)
- [ ] 2026-06-03T07:19 :: Required fields defaults (fail-forward): price fallback to config ebay_default_price (visible placeholder, e.g. $9.99), description fallback to title, aspects fallback to empty dict. Remove RuntimeError gates in ebay_stage for price/description — stage with defaults, operator fixes in Seller Hub. See session notes.
- [ ] 2026-06-03T07:19 :: Fix ebay_stage / sync.py: add Content-Language: en-US header to all Inventory API PUT/POST calls (ebay_put, ebay_post). eBay errorId 25709 — currently blocking staging for any item.
- [ ] 2026-06-03T07:19 :: Sweep items with draft_listing but no ebay_photos into ebay_upload queue — items priced before ebay_upload was wired into pipeline. tgw requeue --no-ebay-photos or manual psql query. Needed before ebay_stage can proceed for those items.
- [ ] 2026-06-03T07:19 :: Sweep items with draft_listing but no ebay_photos into ebay_upload queue — items priced before ebay_upload was wired into pipeline. Needed before ebay_stage can proceed for those items.
- [ ] 2026-06-03T07:24 :: Create/update CLAUDE.md: point to TGW-Master-Plan.md, MEMORY.md index, and key settled architecture decisions. Goal: every fresh session starts with full project context without conversation history. This is more reliable than session continuation for long-running projects.
- [ ] 2026-06-03T17:28 :: up the priority of the data normalization and standardization in the project plan
- [ ] 2026-06-03T17:47 :: 
    Core problem
    - Sold reconciliation fails because it routes through the catalog as intermediary. Catalog is
    slow/batched and may not have ebay_listing.listing_id at sale time. Match sold orders directly
    against ItemData/*/\*.json on listing_id — never through the catalog.

    Three reconciliation tiers
    1. eBay API — GetMyeBaySelling (active + sold), match listing_id in item JSON. Sold history 90
    days default; use GetOrders with date ranges for older.
    2. Sold report CSV — match eBay item number directly against item JSON ebay_listing.listing_id,
    not catalog.
    3. Physical inventory sweep — generate a checklist of ambiguous-status SKUs (no ebay_listing,
    or unresolved active/sold) for human review. Item gone from shelf → sold/missing. Item present
    → available.

    What download current eBay data means
    - Pull all active offers/listings → write back into ebay_listing/ebay_offer per item JSON
    - Pull sold order history → match by SKU/listing_id, set status: Sold, record sale price and
    date
    - ebay_legacy_sync already writes ebay_listing from Trading API — extend this rather than
    rebuild it
    - ebay_sync exists but isn't writing enough back to item JSONs

    Local mirror principle (from today's session)
    - Every durable eBay-side ID and URL should be written back into item JSON immediately after
    the API call succeeds. This makes sold/active guards reliable without hitting the eBay API at
    pipeline time.

    Known data quality issues
    - Many items have Item number from legacy eBay CSV export fields — these are the parent
    bundle's item number, not the individual item's. Strip on encounter.
    - Items marked legacy_listing_resolved: True may still have active listings — the Active
    listing guard in ebay_stage now catches this, but the underlying data needs the sync pass to be
    authoritative.
    - Physical inventory has gaps from the old system — sold items not marked, available items with
    stale status.

- [ ] 2026-06-03T18:37 :: change initial listing pricing to 10% over 100th percentile. This sets list price on ebay page, and sometimes a higher priced sale. Then after a few days start job process lowering the price first 100th until 25th percentile or wherever
