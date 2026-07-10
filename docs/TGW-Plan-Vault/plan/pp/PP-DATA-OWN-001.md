## PP-DATA-OWN-001 — eBay Data Sovereignty / Full Inventory Mirror

**Opened:** 2026-06-17 (session 34)
**Status:** Phase 1 RUNNING — initial pull in progress against 19,446 items
**Core principle:** We own our data. eBay stores nothing we don't also have locally.
eBay has lost seller data before. All listing data, item specifics, images, prices,
sold history, policies — everything eBay has about our listings must be mirrored locally
and kept current. We never rely on eBay as the authoritative source for our own inventory.

### What was wrong

As of 2026-06-17, 0 of 55,351 local items had `ebay_live`, `ebay_submitted`, or `draft_listing`
populated. The data fields present were legacy flat fields (`title`, `description`, `price`,
`Condition`) from an old eBay CSV export — not connected to the new pipeline. 19,446 active
eBay listings held the authoritative data; we couldn't see or edit it through our own UI.
This blocked listing new items, re-listing sold items, and auditing existing listings.

### Architecture

```
eBay Inventory API ──► ebay_live.inventory_item  (title, aspects, imageUrls, condition, UPC)
eBay Inventory API ──► ebay_live.offer            (price, description HTML, category, policies,
                                                   listing_id, offer_id, status)
ebay_live ──────────► draft_listing              (editable staging area for the UI editor)
draft_listing ──────► ebay_submitted             (audit trail — what we last pushed to eBay)
```

`ebay_live` = raw eBay response, never hand-edited. Refreshed by `ebay-pull`.
`draft_listing` = what the editor works on. Created from `ebay_live` if source='ebay_live';
  not overwritten once manually edited (source changes to something else).
`ebay_submitted` = written by `ebay_stage` when we push. Snapshots what we sent.

### Phases

- **Phase 1 — Initial bulk pull** (2026-06-17, IN PROGRESS):
  - `tgw ebay-pull --no-active --no-sold` running against all 19,446 Inventory API items
  - Writes `ebay_live.{inventory_item, offer}` to each item JSON
  - Backfills `draft_listing` from live data (title, aspects, price, description, images, policies)
  - Also syncs `ebay_listing.{listing_id, offer_id, status}` from offer response
  - Runtime: ~30 minutes at 50ms/offer call; catalog_rebuild fires on completion

- **Phase 2 — Ongoing sync** (TODO):
  - `ebay_sync` worker (Inventory API) already runs daily — extend to write `ebay_live`
  - `ebay_legacy_sync` worker (Trading API) runs daily for non-Inventory items
  - Add `--inventory` flag to scheduled sync so `ebay_live` stays current

- **Phase 3 — Sold/transaction history** (TODO):
  - `GetOrders` already pulls completed orders → `ebay_sale` block
  - Verify all 976 sold items have `ebay_sale` data; backfill missing
  - Sold items list UI shows only 2 — fix the status filter in http_server.py

- **Phase 4 — Account policies mirror** (TODO):
  - Pull fulfillment, payment, return policies via `sell.account` API
  - Store in `data/ebay-policies.json` + make available in editor dropdowns
  - Pull store categories; verify against `tgw-api-config.json` mappings

- **Phase 5 — Forward sync** (TODO):
  - After editing `draft_listing`, pushing to eBay writes `ebay_submitted`
  - On each push confirm `ebay_live` refreshes from the response
  - Alert if eBay-side data diverges from `ebay_live` (someone edited on eBay directly)

### Files changed

- `src/tgw/ebay/pull.py` — added `sync_inventory_api()`, `iter_inventory_api_items()`,
  `fetch_offer_for_sku()`, `apply_ebay_live()`, `backfill_draft_from_live()`
- `src/tgw/api.py` — `ebay-pull` CLI now runs Inventory API mirror as Phase 1;
  new flags `--no-inventory`, `--skip-offers`, `--no-draft`

### Next steps (unblock listing)

1. Wait for Phase 1 pull to complete (~30 min from 08:51 PDT 2026-06-17)
2. Verify editor shows data: `tgw serve` → `/form/items/{sku}` for any active SKU
3. Fix sold items display (Phase 3 — status filter bug in http_server.py)
4. Confirm full list → stage → publish flow works on one test item
5. Schedule `ebay-pull` as a nightly cron to keep `ebay_live` current

### Data Track C — Reference and Relationship Data (adopted 2026-06-19)

Captures reference data that powers the decision engine without touching per-item JSON.
Safe to run in parallel with Stages 1–5 — all writes go to `data/` reference files or
documentation, not to `ItemData/<SKU>/<SKU>.json`.

**Guiding principle:** Much of what Dave has learned about eBay quirks, category constraints,
and listing behavior has come from investigating metadata. This track captures that knowledge
systematically. Unknown fields are where the operational tidbits live — store everything eBay
returns, not just what we currently consume.

#### C1 — Shipping Policies
Pull all fulfillment/payment/return policies via `sell.account` API.
Store in `data/ebay-policies.json` with full policy detail.
Fixes ISS-002 (wrong shipping profile on 9 items); powers shipping cost calculation and
repricer accuracy. **Can start immediately after Stage 0.** Read-only pull, no risk.

#### C2 — eBay Category Data (full pull for all categories in use)

**C2a — Category hierarchy (main + secondary + store categories)**
- Pull full category tree for all primary categories across our listings
- Pull secondary categories where assigned (sparse but must be captured)
- Pull eBay Store category structure — main + all secondaries (mostly empty today,
  but the structure must be captured so the decision engine can assign as inventory grows)
- Sources: Taxonomy API `getCategoryTree`; store categories via `sell.stores` or Trading API `GetStore`
- Store as `data/ebay-categories.json`

**C2b — Item aspects: full attribute set per category**
- For every eBay category ID in use, pull the complete aspect/attribute list:
  required, recommended, optional; value constraints (free-text vs enum, allowed values)
- Required aspects are a hard gate — eBay rejects listings missing them; knowing them
  in advance powers pre-flight validation in `ebay_draft`
- Source: Taxonomy API `getItemAspectsForCategory` or Trading API `GetCategorySpecifics`
  (available under existing scopes)
- Store as `data/ebay-aspects-by-category.json`, keyed by category ID
- Cross-reference against `category-groups.json` (25 groups → full aspect requirement list)

**C2c — EPS image URLs: map eBay-hosted images to local photos**
- EPS URLs live in `ebay_live.product.imageUrls[]` from Track A Phase 1 pull
- Correlate each EPS URL to the local photo it came from (by filename/order)
- Validates ISS-013 repair: if an item's EPS primary doesn't match repaired local primary,
  that's a re-upload candidate
- Store as `data/ebay-image-map.json` (SKU → [{eps_url, local_file, position}])
- No new API call — derivation pass over existing Track A data after Phase 2 (ongoing sync)

**C2d — Full raw metadata capture**
The investigation-driven metadata principle applies everywhere, but *especially* to per-item
eBay data. Store complete eBay API responses, not just consumed fields.

For Track A item data: store the full Inventory API + offer response as `ebay_raw` sub-object
in `ebay_live` — condition description text, item specifics as-listed, custom label, subtitle,
secondary category, store category assignment, promoted listing status, compatibility data,
regional availability, and any fields eBay returns that we haven't mapped yet.

For category-level metadata: store the full Taxonomy/Trading API response per category, not
just the fields we consume.

Correlation pass: for each item, diff `ebay_live` (what eBay has) against `ItemData/<SKU>.json`
(what we sent/know locally) — divergences are candidates for `CATEGORY-QUIRKS.md` entries and
decision-engine rules. Document surprises as they surface; each quirk discovered this way is
operational knowledge that was previously locked in operator memory.

#### C3 — Category Group Enrichment
Review `category-groups.json` (25 groups); fill missing `size_class`, floor prices, typical
prices, eBay category ID mappings. Drive from C2a/C2b findings — aspect requirements and
category hierarchy inform which group assignments are correct. Powers template intake, pricing
floors, the decision engine.

#### C4 — Location Types
Define storage location types and their properties (size class capacity, access method).
Powers semi-chaotic storage assignment and pick-path optimization.

#### C5 — eBay Error Code Index
Extend `reference/eBay-Error-Codes.md` with gaps found during dead-letter triage.
Powers litterbox auto-classification rules.

---

