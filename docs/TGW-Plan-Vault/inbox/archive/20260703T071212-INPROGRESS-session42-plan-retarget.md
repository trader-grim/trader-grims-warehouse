
## Addendum 3 (evening): incoming/ root, backfills, THE loop root cause

- **/opt/TGW/incoming/** is now the root of ALL inbound data per Dave's directive
  (newitems/ + ebay/ + lookups/, 2770 tgw:tgw + default ACLs); eBay capture moved
  there; Data Charter updated ("inbound root" section).
- **784-item draft-price backfill** — stale pre-s41 draft prices above live markdown
  healed from mirror via fence; before/after in var/backups/s42-price-backfill/.
- **Never-raise clamp** added to ebay_stage (C5-extended; allow_price_raise override;
  4 tests) — force re-stage can never raise a live price again.
- **ROOT CAUSE of the day's churn found via capture**: http PATCH auto-redraft fired
  on WORKER fence patches → infinite draft→patch→redraft loop (287 jobs/SKU; 2 live
  listings PUT every ~90s). Fixed: fence sends X-TGW-Caller; auto-redraft now
  operator-edits-only. Loop verified dead (0 new jobs/PUTs over full cycle windows).
  Price-revert incident DOWNGRADED: capture ground truth shows the 5 flagged legacy
  items were never actually pushed to eBay. Todo #1107 closed.
- **R1.8 full snapshot RUNNING** (transient unit tgw-r18-snapshot): 19,486 SKUs
  inventoried, per-SKU offers in flight (~90 min), all raw responses landing in
  incoming/ebay/ via the fence.
- **Ramp resumed**: +500 dead-letters requeued (0 failures at enqueue); remaining
  ~2,640 after batch review. Sync one-way change on Dave's admin todo (#1106).

## Addendum 4 (night): pricing system defused — Dave's operator test found it

Dave inspected tgw202605060201087 and unraveled the pricing subsystem:
- price_history showed $309.99 "Published" while eBay live price was $29.99
  (publish recorded the schedule's launch figure, not reality) — FIXED, + manual/UI
  price edits now append price_history (they never did; his $82.99 was invisible).
- reprice schedules: computed at publish from Browse asking-price "comps" — 6 of the
  8 pipeline-published items had fire-sale schedules (one floor literally $0.00 due
  07-05; $309.99→$4.79; $379.99→$75). Reducer would have executed them (C5 only
  prevents raises). 73 more unpublished items carry junk comps that would mint the
  same at publish.
- Dave ENDED ALL 6 LISTINGS (withdraw verified UNPUBLISHED on eBay) and ordered the
  fix: schedule minting now DISABLED by default (`reprice_schedule_enabled` config
  flag, off) — the pipeline cannot change prices unsupervised; reducer got a cliff
  guard (stage <50% of predecessor stage or <$2.99 hard floor → refused + stamped +
  surfaced, never silently retried). Re-enable minting ONLY after PP-REPRICER-001
  rebuilds pricing on real sold data (marketplace_insights scope, in the eBay
  application review).
- alt_text queue has NO worker unit installed (jobs sit "queued" forever on item
  pages) — surfaced, not yet resolved (batch-path design decision pending).
- Tests: +4 new (no-schedule default, actual-price history, cliff refusals); 39/40
  invariant-suite green (1 pre-existing failure).

## Addendum 5 (late night): the working queue exists — Dave resumes where the site broke

- Inventory page "Eligible" filter built + live-verified (#1112): status new/In Stock,
  nothing live on eBay — 2,100 items. THIS IS THE WORKING QUEUE. Dave: start at the
  top, one item at a time until it works — resuming exactly what he was doing when
  the site broke 2 days ago.
- His first test immediately found: ending a listing only wrote ebay_listing.status,
  leaving ebay_offer.status='PUBLISHED' locally (eBay says UNPUBLISHED) → items
  showed as Listed everywhere. Fixed in both end paths + 6 items' mirrors corrected +
  "Listed" badge now reads listing STATUS (Ended badge + relist actions for ended
  items). All live-verified.
- Operating mode + operator-gate-is-the-design + PP-BULKLIST-001 (gated) encoded in
  plan. PP-PRICING-001 (SerpApi) recovered + 27 dropped PPs restored to index.
- Reprice schedules: minting DISABLED, reducer cliff-guarded, 6 fire-sale listings
  ENDED by Dave, publish history records actual price, UI price edits append history.
- NEXT: snapshot completes (~19.5k offers) → full mirror-vs-eBay reconciliation
  report → Dave works the Eligible list top-down. EPS pool resets 00:10 PST
  (photo uploads for new listings gated until then).
