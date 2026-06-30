# PP-PROMO-001 — Sale Event Automation (Design)

**Status**: P1 design complete. P2 (draft CLI) and P3 (apply CLI) planned.
**Scope requires**: `sell.marketing` ✅ already held. No new OAuth scope needed.
**Relates to**: PP-STRIKE-001 (strikethrough via `originalRetailPrice` — distinct), PP-REPRICE-001 (scheduled markdown stages — distinct)

---

## Problem

Dead-stock items — items that have completed all `reprice_schedule` stages without selling — sit at "move" price indefinitely with no additional buyer signal. The automated price reducer has already done its work; these items need a different lever.

An eBay **Promotions API markdown event** surfaces dead-stock with an "X% off" badge in search results and on the listing. This creates urgency and visibility without permanently changing the item price — when the promotion ends, eBay restores the display price automatically.

**Goal**: automate the dead-stock discovery → markdown draft → operator review → eBay apply cycle, using the already-held `sell.marketing` scope.

---

## Distinction from PP-STRIKE-001

| Feature | PP-STRIKE-001 (strikethrough) | PP-PROMO-001 (markdown promotion) |
|---------|------------------------------|-----------------------------------|
| Mechanism | `originalRetailPrice` field in offer body | Promotions API `ITEM_PRICE_MARKDOWN` |
| Shown as | "Was $X.XX" crossed out next to the current price | "X% off" or "Was $X / Now $Y" badge |
| Duration | Permanent until cleared | Time-bounded event (start/end date) |
| Source | Product MSRP from `product_lookup.msrp` | Any active live listing |
| Account prerequisite | Strikethrough Pricing program approval | Standard `sell.marketing` scope |
| Scope | `sell.inventory` offer body field | `sell.marketing` Promotions API |

Both can be active simultaneously. They are independent.

---

## eBay API Surface

| Concept | Detail |
|---------|--------|
| API | Promotions Management API |
| Base URL | `https://api.ebay.com/sell/marketing/v1/` |
| Auth scope | `sell.marketing` — ✅ already held |
| Promotion type | `ITEM_PRICE_MARKDOWN` |
| Inventory targeting | `INVENTORY_BY_VALUE` (list of `listingId`) or `INVENTORY_BY_CATEGORY` |
| Discount type | `percentageOffList` (percentage) or `amountOffList` (fixed amount) |
| Status flow | `DRAFT` → `SCHEDULED` → `RUNNING` → `PAUSED` / `ENDED` |

### Create endpoint

```
POST https://api.ebay.com/sell/marketing/v1/item_price_markdown
```

Returns a `Location` response header containing the new promotion URL; extract `promotion_id` from the trailing path segment.

### Canonical request body

```json
{
  "name": "TGW Dead Stock Clearance — 2026-06",
  "marketplaceId": "EBAY_US",
  "promotionStatus": "DRAFT",
  "startDate": "2026-06-20T00:00:00.000Z",
  "endDate":   "2026-07-20T00:00:00.000Z",
  "selectedInventoryDiscounts": [
    {
      "discountBenefit": {
        "percentageOffList": "20"
      },
      "inventoryCriterion": {
        "inventoryCriterionType": "INVENTORY_BY_VALUE",
        "inventoryItems": [
          { "listingId": "123456789012" },
          { "listingId": "234567890123" }
        ]
      }
    }
  ]
}
```

`promotionStatus: DRAFT` is safe — the promotion is not visible to buyers until promoted to `SCHEDULED` or `RUNNING`. Operators set the final status via Seller Hub.

### Read / list

```
GET /sell/marketing/v1/promotions?marketplace_id=EBAY_US&promotion_type=ITEM_PRICE_MARKDOWN
```

Use this as a **scope verification step** before any write: a 200 response confirms `sell.marketing` is live. A 403 means the token lacks the scope despite it appearing in the keyset metadata.

### Update / end

```
PUT    /sell/marketing/v1/item_price_markdown/{promotion_id}
POST   /sell/marketing/v1/promotion/{promotion_id}/pause
POST   /sell/marketing/v1/promotion/{promotion_id}/resume
DELETE /sell/marketing/v1/promotion/{promotion_id}
```

### eBay constraints

| Constraint | Limit | Enforcement |
|------------|-------|-------------|
| Minimum discount | 5% | API error on create |
| Maximum discount | 80% | API error on create |
| Listings per promotion | ~500 (empirical) | TGW default cap: 50 |
| Active markdown promotions per listing | 1 | Checked by apply command |
| Lead time for SCHEDULED | ≥ 1 hour before start | Start date validation |
| Maximum promotion duration | No hard limit; 30 days recommended | Soft limit in config |

---

## Data Flow

```
dead_stock list (reports._scan_items)
  │  fields: sku, title, location, group, days_stale, last_stage, price
  │  needs augmentation: listing_id from ebay_listing.listing_id
  │
  ▼
tgw promo draft [--discount N] [--min-days N] [--min-price X]   ← P2 (read-only)
  │  apply filters: min_days_stale, min_price, has_listing_id, not promo_skip
  │  compute discount %, event name, date range
  │  write markdown draft → vault inbox or promo-drafts/
  │
  ▼
Operator reviews markdown draft file
  │  removes SKUs, adjusts discount %, dates
  │  completes operator checklist (see below)
  │
  ▼
tgw promo apply <DRAFT_FILE>                                      ← P3 (eBay writes)
  │  parse approved SKU list from markdown
  │  verify listing IDs still active (GET /inventory_item)
  │  verify no existing active markdown promotion per listing
  │  POST /sell/marketing/v1/item_price_markdown → get promo_id
  │  write ebay_promo block to each item JSON via tgw-api PUT
  │
  ▼
Operator reviews event in Seller Hub → sets status → RUNNING
  │
  ▼
Promotion expires → eBay restores display price automatically
  │
  ▼
tgw promo end <promo_id>  (or let it expire)                      ← P4 (lifecycle)
  │  DELETE or POST .../pause
  │  clear ebay_promo block from item JSONs
```

---

## Draft Generation (`tgw promo draft`) — P2

### Input

Reuses `reports._scan_items()` from `tgw/reports.py`, augmented with `listing_id`:

```python
# augmentation needed in _scan_items (or a promo-specific variant):
item.get("ebay_listing", {}).get("listing_id")
```

The dead_stock row dict gains a `listing_id` field. Items without one are silently excluded (legacy items not yet through `ebay_sku_migrate`).

### Filters

Applied in order; all configurable via CLI flags with config key fallbacks:

| Filter | Default | CLI flag | Config key |
|--------|---------|----------|------------|
| Minimum days stale | 30 | `--min-days` | `promo.min_days_stale` |
| Minimum current price | 2.00 | `--min-price` | `promo.min_price` |
| Maximum items in event | 50 | `--max-items` | `promo.max_items` |
| Discount percentage | 20 | `--discount` | `promo.discount_pct` |
| Event duration (days) | 30 | `--duration` | `promo.duration_days` |
| Start offset from today | 2 | `--start-offset` | `promo.start_offset_days` |
| Marketplace | EBAY_US | — | `promo.marketplace_id` |

### Hardcoded exclusions (always applied, not configurable)

- Items with no `ebay_listing.listing_id` — never live or still migrating
- Items with `promo_skip: true` in item JSON (operator opt-out flag)
- Items with `ebay_promo.promo_id` already set (already in an active promo)
- Items with `status` in: `sold`, `archived`, `disposed`, `discard`, `vero`, `draft`, `merged`

### Output — draft markdown format

Written to `docs/TGW-Plan-Vault/inbox/promo-YYYYMMDD.md` (picked up by `pm_intake` or applied manually):

```markdown
---
pp: PP-PROMO-001
generated: 2026-06-12T14:30:00Z
discount_pct: 20
start_date: 2026-06-14
end_date: 2026-07-14
marketplace: EBAY_US
status: DRAFT
---

# PP-PROMO-001 Sale Event Draft — 2026-06-12

**Event name**: TGW Dead Stock Clearance — 2026-06
**Discount**: 20% off list price
**Start**: 2026-06-14 00:00 UTC
**End**:   2026-07-14 00:00 UTC
**Marketplace**: EBAY_US
**Items**: 23

## SKU List

| SKU | Title | Group | Days Stale | Price | Discounted | listing_id |
|-----|-------|-------|------------|-------|------------|------------|
| tgw20260115120000123 | Garmin GPS 60 | electronics | 82 | $24.99 | $19.99 | 123456789012 |
| tgw20260210090000456 | Logitech MX Keys | peripherals | 61 | $34.99 | $27.99 | 234567890123 |
...

## Operator Instructions

1. Review the SKU list — delete any rows you don't want in this event
2. Adjust `discount_pct` in the YAML header if needed (must be 5–80)
3. Adjust `start_date` / `end_date` if needed (start must be ≥ today + 1h)
4. Complete the operator checklist in the PP-PROMO-001 design doc
5. Apply: `tgw promo apply docs/TGW-Plan-Vault/inbox/promo-2026XXXX.md`
   (or move this file to inbox/ — pm_intake will not auto-apply; P3 is manual)
```

The `Discounted` column = `price × (1 − discount_pct / 100)` rounded to .99 — lets the operator spot-check no item falls below floor.

---

## Item JSON Writeback (Phase 3)

After `tgw promo apply` successfully creates the eBay promotion, write back to each item JSON via the tgw-api PUT fence (never direct file mutation):

```json
"ebay_promo": {
  "promo_id":    "5xxxxxxxxxxxxxxxx",
  "event_name":  "TGW Dead Stock Clearance — 2026-06",
  "discount_pct": 20,
  "start_date":  "2026-06-14",
  "end_date":    "2026-07-14",
  "applied_at":  "2026-06-12T14:35:00Z"
}
```

This block is cleared by `tgw promo end <promo_id>` or when `ebay_sync` detects the promotion has ended.

---

## Config Keys

Add to `tgw-api-config.json` under a top-level `"promo"` key:

```json
"promo": {
  "enabled":            false,
  "min_days_stale":     30,
  "min_price":          2.00,
  "max_items":          50,
  "discount_pct":       20,
  "duration_days":      30,
  "start_offset_days":  2,
  "marketplace_id":     "EBAY_US"
}
```

`"enabled": false` is the safe default. The `tgw promo draft` command requires `promo.enabled: true` to run (prevents accidental use before scope verification). `tgw promo apply` also checks this flag.

---

## Operator Verification Checklist

Complete this checklist before running `tgw promo apply`. Each item is a hard gate — do not proceed past any unchecked item.

### 1. Scope verification (run once, before first apply)

- [ ] Run `tgw promo list` (P2 read-only command) — confirm it returns 200 and a (possibly empty) list of promotions. A 403 means `sell.marketing` is not active on this token; do not proceed.
- [ ] In Seller Hub: go to **Marketing → Promotions** — confirm the Promotions manager UI loads. If it says "not eligible" or redirects, scope may not be provisioned.

### 2. Draft file review

- [ ] All `listing_id` values in the draft are 12-digit numbers (spot-check format; the apply command validates existence)
- [ ] Item count is ≥ 3 (single-item events have negligible buyer signal)
- [ ] Item count is ≤ 50 (TGW conservative cap; eBay empirical limit ~500 but untested)
- [ ] No SKU in the list has `status: sold`, `archived`, or `disposed` — the draft generator filters these, but verify the generation date is today (stale draft = stale status)
- [ ] No SKU in the list has `promo_skip: true` set in its item JSON (auto-filtered; spot-check 1–2 high-value SKUs)

### 3. Pricing sanity

- [ ] `discount_pct` in the YAML header is in the range **5–80** (eBay API rejects outside this range)
- [ ] Every row in the `Discounted` column is **≥ $0.99** (eBay rejects sub-$0.99 listings)
- [ ] Every discounted price is above the **category floor** for its group:
  - Run `tgw category-groups` and compare discounted prices against `pricing.floor` for each group
  - Floor = `price × (1 − discount_pct/100)` must be ≥ `group.pricing.floor`

### 4. Event parameters

- [ ] `start_date` is at least **1 hour from now** (eBay requires lead time for SCHEDULED status)
- [ ] `end_date` is no more than **90 days** from start_date (longer events lose urgency; 30 days recommended)
- [ ] `event_name` is buyer-appropriate — it appears in eBay search results under the listing
- [ ] No existing active `ITEM_PRICE_MARKDOWN` promotion overlaps any `listing_id` in the draft (one active markdown promo per listing is the eBay limit; the apply command checks this, but pre-verify in Seller Hub for peace of mind)

### 5. Post-apply verification (after `tgw promo apply` completes)

- [ ] `tgw promo list` shows the new promotion with `DRAFT` status and the correct `promotion_id`
- [ ] Open Seller Hub → **Marketing → Promotions** — confirm the event appears with the correct name, dates, and discount
- [ ] Spot-check 2–3 item JSONs: confirm `ebay_promo.promo_id` is written and matches the Seller Hub promo ID
- [ ] Set the promotion status to **SCHEDULED** (if start date is in the future) or **RUNNING** (if starting now) in Seller Hub — the TGW apply command creates it as `DRAFT` intentionally so the operator has final control
- [ ] Record the `promo_id` in a plan inbox note for tracking

---

## Risks and Open Questions

### R1 — Listing ID availability during SKU migration

`ebay_sku_migrate` is still running (~8,350 legacy listings remaining as of 2026-06-12). Legacy items may not have `ebay_listing.listing_id` populated yet. The draft generator filters these out silently. As migration completes, more items become eligible.

**Mitigation**: log a count of "skipped: no listing_id" during draft generation so the operator knows the coverage gap.

### R2 — ebay_price_reducer interaction during active promo

If `ebay_price_reducer` fires a reprice event while an item is in an active markdown promotion, the interaction is undefined (the markdown applies as a percentage of the then-current offer price, which just changed). Possible outcomes: correct (eBay recomputes the % discount off the new base), confusing (buyer sees 20% off a lower price than expected), or API error.

**Mitigation short-term**: add `promo_skip: true` check to `ebay_price_reducer` — skip repricing any item with an active `ebay_promo` block (until end_date passes). This is a P2 dependency to wire before first production use.

**Mitigation long-term**: P4 lifecycle command clears `ebay_promo` when promo ends; `ebay_price_reducer` then resumes normal operation.

### R3 — Scope never exercised on current token

`sell.marketing` is declared in the keyset but has never been called. The first real call is a live test. The P2 read-only `tgw promo list` command is the verification gate — run it before any apply.

### R4 — Promotion type restrictions by category

Some eBay categories (motors, real estate, luxury) may not support `ITEM_PRICE_MARKDOWN`. The API will return an error on the specific `listingId`. The apply command should handle per-item errors gracefully (log + skip the failing listing, continue with the rest) rather than aborting the entire promotion create.

### R5 — Price floor interaction

The discounted price (offer price × (1 − pct/100)) must remain above the category floor configured in `category-groups.json`. The draft generator can compute this and annotate or exclude items that would breach the floor. This is a quality gate, not an eBay constraint.

---

## Phase Plan

| Phase | Scope | Status |
|-------|-------|--------|
| **P1** | Design doc + operator checklist (this file) + master plan section | ✅ DONE |
| **P2** | `tgw promo draft` + `tgw promo list` read-only scope check | ✅ DONE (2026-06-29) |
| **P3** | `tgw promo apply <DRAFT_FILE>` + `tgw promo start/end/sync`; `ebay_price_reducer` promo-skip gate (R2 fix) | ✅ DONE (2026-06-29) |
| **P4** | Web UI and Flutter surfaces; `tgw promo status` summary with impressions from analytics | Future |

**P2 prerequisite**: none.
**P3 prerequisite**: P2 `tgw promo list` returns 200 (scope confirmed active) — run before first `apply`.
**P4 prerequisite**: P3 shipped and at least one full promo cycle completed.

---

## Seller Hub Conflict Risk — Why to Stop Using Seller Hub for Operations

### Safe to do in Seller Hub (read-only or isolated APIs)
- **Viewing anything** — always safe
- **Messages / Buyer messages** — separate messaging API; no impact on listing data
- **Offer responses** — safe, but TGW's `/form/offers` + `tgw offers` should be used instead
- **Performance metrics / analytics** — read-only views

### Risky in Seller Hub (can break TGW's Inventory API control)
- **Editing listing price in Seller Hub** — Seller Hub may use Trading API `ReviseFixedPriceItem`
  instead of the Inventory API. This detaches the listing from TGW's `offer_id`. TGW's next
  offer PUT may then reject (offer mismatch) or create a NEW listing — a duplicate.
- **Editing title / description / aspects / category in Seller Hub** — same detachment risk.
  TGW's next `ebay_draft`+`ebay_upload` cycle may create a duplicate or get a 404 on the offer.
- **Ending a listing via Seller Hub** — listing ends; `ebay_sync` eventually catches it but
  there's a window where item JSON shows `status: listed` on a dead listing.
- **Bulk edit in Seller Hub** — batch Trading API calls; same detachment risk at scale.
- **Relisting in Seller Hub** — creates a new listing independent of TGW's offer_id.

### Promotions — safe to move to TGW immediately
The Marketing API (`sell.marketing`) is entirely separate from the Inventory API. Creating,
editing, pausing, or ending a promotion does NOT modify listing data and carries no duplicate
risk. The one internal risk (R2) — price reducer repricing an item mid-promo — is now gated
by the `has_active_promo()` check added to `ebay_price_reducer.py`.

### Migration plan to stop using Seller Hub
1. **Promotions** (immediate): run `tgw promo sync` to import existing Seller Hub promos →
   TGW now has `ebay_promo` blocks for all active promos → price reducer promo-skip is live →
   all future promos created via `tgw promo draft → apply → start`. Stop using Seller Hub
   Marketing page.
2. **Offers / Buyer offers** (already done): use `/form/offers` + `tgw offers` exclusively.
3. **Pricing** (already done): `ebay_price_reducer` is the only repricer; never change price in Seller Hub.
4. **Listing details** (already done): all edits go through `tgw edit` / `/form/items/{sku}` → `ebay_draft` → operator review → `ebay_stage` → `ebay_publish`.
5. **Ending listings** (already done): `tgw action archive` or `tgw action ebay_end_listing` — never end from Seller Hub.
6. Orders / fulfillment: still requires Seller Hub until `sell.fulfillment` scope is approved (PP-EDITOR-001 F15).

---

## Implementation Notes

### Module placement

| Component | Location |
|-----------|----------|
| `cmd_promo_draft()` | `src/tgw/promo.py` (new module) or `src/tgw/reports.py` extension |
| `cmd_promo_list()` | `src/tgw/promo.py` |
| `cmd_promo_apply()` | `src/tgw/promo.py` (P3) |
| eBay Promotions API client | `src/tgw/apis/ebay/promotions.py` (new, P3) |
| CLI wiring | `src/tgw/api.py` `tgw promo <sub>` dispatch |
| Config | `tgw-api-config.json` `promo.*` keys |
| Tests | `tests/test_promo.py` |

### `promo.py` structure sketch

```python
# src/tgw/promo.py
# dead_stock scan + filter → draft markdown writer (P2)
# eBay Promotions API calls (P3)

def cmd_promo_draft(cfg, output_dir, **filters) -> dict:
    """Read-only: generate markdown draft from dead_stock scan."""
    ...

def cmd_promo_list(cfg) -> dict:
    """Read-only scope check: list ITEM_PRICE_MARKDOWN promotions."""
    ...

def cmd_promo_apply(cfg, draft_path) -> dict:
    """P3: parse draft, validate, POST to Promotions API, write ebay_promo."""
    ...
```

### Test coverage (P2)

- `test_promo_draft_filters`: verify min_days_stale, min_price, no-listing-id exclusions
- `test_promo_draft_output`: markdown YAML header + table structure
- `test_promo_draft_floor_annotation`: items below category floor are flagged in output
- `test_promo_draft_disabled`: `promo.enabled: false` raises ConfigError before running
- `test_promo_list_mocked`: mock GET returns 200, check return shape

All tests offline; no live eBay calls.
