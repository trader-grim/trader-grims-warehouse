---
title: SKU Normalization Audit Report
updated: 2026-06-03
status: decisions confirmed — migration script ready to build
---

# SKU Normalization Audit Report

## Summary

55,351 items audited across 7 format classes.
**20,328 (36.7%) are already in canonical format (Class C).**
**35,023 (63.3%) require migration** — one-time operation.
Live eBay listings follow a slower batch path: delist → update SKU → relist.

---

## Canonical Format (confirmed)

```
tgw + YYYYMMDD + HHMMSS + s
```
- Prefix: `tgw` (3 chars, always lowercase)
- Date:   8-digit `YYYYMMDD`
- Time:   6-digit `HHMMSS`
- Tenths: 1-digit tenths-of-second (0–9)
- Total:  **18 characters**
- Sortable as a plain string — lexicographic order = chronological order
- Example: `tgw202001291640269`

Length chosen to fit barcode labels. Class C items are already in this format.

---

## Format Classes

### Class C — Already Canonical ✅
| | |
|---|---|
| Count | **20,328** (36.7%) |
| Length | 18 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSSs` |
| Example | `tgw202001291640269` → 2020-01-29 16:40:26.9 |
| Years | 2020–2026 |
| Action | None — this IS the canonical format |

---

### Class A — Too Long (truncate ms)
| | |
|---|---|
| Count | **34,737** (62.8%) |
| Length | 20 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSS` + `mmm` (3-digit ms) |
| Example | `tgw20170607143022417` → 2017-06-07 14:30:22.417 |
| Years | 2014–2020 (bulk: 2015–2019) |
| Live eBay offers | Significant — check per item |

**Migration rule:** Drop last 2 ms digits (keep tenths, discard hundredths + thousandths).
`tgw20170607143022417` → `tgw201706071430224`

**⚠ Collision check required first.** Two items created within the same 100ms window
(same `HHMMSSs` = same tenths digit) would produce the same 18-char SKU.
Pre-migration: scan all Class A items, find any pairs where `sku[:18]` collides, resolve
manually before running bulk migration. Expected to be rare but must be confirmed zero.

**eBay path:** Slow batch — delist, update SKU, relist. Do not attempt to bulk-rename
live listings inline. Batch in groups of ~50, confirm each batch before continuing.

Year breakdown:
```
2014: 326    2015: 3,418    2016: 4,193    2017: 16,023
2018: 3,712  2019: 5,785    2020: 1,280
```

---

### Class B — Epoch-0 Date Corruption
| | |
|---|---|
| Count | **26** (0.05%) |
| Length | 20 |
| Pattern | `tgw1970MMDDHHMMSS` + `mmm` |
| Example | `tgw19700102105139553` |
| Root cause | Unix epoch ≈ 0 timestamp; original intake date lost |
| Live eBay offers | None (all confirmed `ebay_item_id: null`) |

All 26 share date `19700102`. Items are legitimate in-stock inventory.

**Migration rule:** New SKU encodes the source and best-guess year.
Format: `tgw` + `20150102` + `1970` + last 3 digits of original SKU

`tgw19700102105139553` → last 3 = `553` → `tgw201501021970553`

- `20150102` — 2015 is the best-guess actual intake year
- `1970` — embedded in the time field to acknowledge the corrupt source date
- Last 3 digits — preserves uniqueness within the group (all have different tails)
- Total: 3 + 8 + 7 = **18 chars** ✓

**eBay path:** No live listings; local rename only.

---

### Class D — Underscore Separator
| | |
|---|---|
| Count | **33** (0.06%) |
| Length | 18 |
| Pattern | `tgw` + `YYYYMMDD` + `_` + `HHMMSS` (no tenths) |
| Example | `tgw20200115_113609` → 2020-01-15 11:36:09 |
| Years | 2020 only |

**Migration rule:** Strip underscore, append `0` for tenths.
`tgw20200115_113609` → `tgw202001151136090`

Total: 3 + 8 + 6 + 1 = **18 chars** ✓

**Collision check:** Confirm `tgw202001151136090` does not already exist.

---

### Class E — YYMMDD Format (actually 2020)
| | |
|---|---|
| Count | **16** (0.03%) |
| Length | 18 |
| Pattern | `tgw` + `YYMMDD` + `HHMMSS` + `mmm` |
| Example | `tgw200503114925650` → 20-05-03 = 2020-05-03 11:49:25.650 |
| Actual year | 2020 (confirmed) |
| Live eBay offers | None found |

The 6-digit date is `YYMMDD` with 2-digit year (20 = 2020), not `YYYYMM`.
`200503` = year 20 (2020), month 05, day 03.

**Migration rule:** Prepend `20` to expand 2-digit year to 4-digit; keep tenths only.
`tgw200503114925650`:
- Date: `20` + `200503` = `20200503`
- Time: `114925` (HHMMSS)
- Tenths: `6` (first digit of `650`)
- Result: `tgw202005031149256`

Total: 3 + 8 + 6 + 1 = **18 chars** ✓

---

### Class F — No Tenths
| | |
|---|---|
| Count | **210** (0.4%) |
| Length | 17 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSS` |
| Example | `tgw20180108202128` → 2018-01-08 20:21:28 |
| Years | 2018 (32), 2020 (10), 2021 (168) |

**Migration rule:** Append `0` for tenths.
`tgw20180108202128` → `tgw201801082021280`

Total: 3 + 8 + 6 + 1 = **18 chars** ✓

**Collision check:** Confirm appended `0` doesn't collide with an existing Class C SKU
at the same second.

---

### Class G — Anomaly
| | |
|---|---|
| Count | **1** |
| Length | 19 |
| SKU | `tgw20210421C0939348` |
| Issue | Non-numeric `C` at position 11 |
| Status | `disposed` |

**Migration rule:** Strip `C`, keep first 15 digits after `tgw`, truncate to 18.
`tgw20210421C0939348` → `tgw202104210093934` (treat `C` as `0`, take tenths = `3`... 
or simply assign `tgw202104210939348`[:18] = `tgw20210421093934`)

Low priority — item is disposed. Handle manually.

---

## Length Histogram (pre-migration)

```
Length  Count    Pct     Class(es)
------  -------  ------  ---------
17      210      0.38%   F — append 0
18      20,377   36.82%  C (canonical) + D (strip _) + E (prepend 20)
19      1        0.00%   G — manual
20      34,763   62.80%  A (truncate) + B (new format)
```

Post-migration: all 55,351 items at length 18.

---

## Migration Priority & eBay Path

| Priority | Class | Count | eBay live | Path |
|----------|-------|-------|-----------|------|
| — | C | 20,328 | ~11,300 | **Already canonical — no change** |
| 1 | F — No tenths | 210 | check per item | Fast — append `0`, verify collision |
| 2 | D — Underscore | 33 | check per item | Fast — strip `_`, append `0` |
| 3 | E — YYMMDD | 16 | none | Fast — prepend `20`, truncate ms |
| 4 | B — Epoch-0 | 26 | none | Fast — new `tgw201501021970xxx` format |
| 5 | G — Anomaly | 1 | none | Manual |
| 6 | A — Too long | 34,737 | significant | **Slow batch** — collision check first, then delist→rename→relist in groups |

Class A is done last and slowest: collision check must be clean before any live
listings are touched. eBay batching in groups of ~50 with confirmation between batches.

---

## eBay Batch Migration Path (Class A live listings)

For each item in Class A with `ebay_offer.offer_id` or `ebay_listing.listing_id`:

1. **Delist** — end the eBay listing (`POST /sell/inventory/v1/offer/{offer_id}/withdraw`)
2. **Rename locally** — move `ItemData/<old>/` to `ItemData/<new>/`, rewrite JSON `sku` field, write `sku_history` record
3. **Re-create eBay inventory item** — `PUT /sell/inventory/v1/inventory_item/{new_sku}`
4. **Re-create offer** — `POST /sell/inventory/v1/offer` with new SKU, same price/condition/policies
5. **Re-publish** — `POST /sell/inventory/v1/offer/{new_offer_id}/publish`
6. **Enqueue** `catalog_rebuild`

Process in batches of ~50. Confirm each batch (check Seller Hub) before continuing.
Items without live listings (no `offer_id`) can be renamed offline in bulk.

---

## Collision Check Plan (Class A)

Before running any Class A migration:

```python
# Find all Class A SKUs whose 18-char truncation matches an existing SKU
canonical = set(all_18_char_skus)   # current Class C + D + E + F after their migrations
collisions = []
for sku in class_a_skus:
    candidate = sku[:18]
    if candidate in canonical or candidate in [s[:18] for s in class_a_skus if s != sku]:
        collisions.append((sku, candidate))
```

Expected: zero or near-zero collisions. Any found must be resolved manually
(keep one, assign a new SKU to the other) before the bulk run proceeds.

---

## Files Referenced

| Path | Purpose |
|------|---------|
| `/opt/TGW/data/ItemData/` | 55,351 item directories |
| `/opt/TGW/data/ItemCatalog/tgwcatalog.db` | SQLite catalog |
| `src/tgw/queue/state_machine.py` | `enqueue_job()` for post-rename catalog_rebuild |
| `src/tgw/items.py` | `atomic_write_json()` for safe file writes |
| `src/tgw/apis/ebay/client.py` | eBay API for delist/relist |

---

## Next Steps (PP-ADD-005)

1. ✅ Audit complete
2. ✅ Migration rules confirmed
3. Build `sku_history` table in `state_machine` DB
4. Run Class A collision check — report results before any migration code runs
5. Build migration script: dry-run → review → live run with rollback manifest
6. Execute fast classes first (F, D, E, B, G) — no eBay impact
7. Execute Class A offline items (no live listing) — bulk
8. Execute Class A live listings — slow batch via eBay delist/relist
9. Add SKU validation at intake (bundle_intake, multi_intake) — reject non-18-char on input
