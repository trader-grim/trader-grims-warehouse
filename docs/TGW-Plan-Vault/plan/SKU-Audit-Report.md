---
title: SKU Normalization Audit Report
updated: 2026-06-03
status: audit complete — migration script not yet written
---

# SKU Normalization Audit Report

## Summary

55,351 items audited across 7 format classes.
**34,737 (62.8%) are already in canonical format.**
**20,614 (37.2%) require migration** — one-time operation.
11,351 non-canonical items have live eBay offers; those require eBay custom label updates as part of migration.

---

## Canonical Format (target)

```
tgw + YYYYMMDD + HHMMSS + mmm
```
- Prefix: `tgw` (3 chars, always lowercase)
- Date:   8-digit `YYYYMMDD`
- Time:   6-digit `HHMMSS`
- Ms:     3-digit milliseconds (zero-padded)
- Total:  **20 characters**
- Sortable as a plain string — lexicographic order = chronological order
- Example: `tgw20260603143022417`

---

## Format Classes

### Class A — Canonical ✅
| | |
|---|---|
| Count | **34,737** (62.8%) |
| Length | 20 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSS` + `mmm` |
| Years | 2014–2020 (bulk: 2015–2019) |
| Action | None |

These are correct and require no changes.

---

### Class B — Epoch-0 Date Corruption
| | |
|---|---|
| Count | **26** (0.05%) |
| Length | 20 |
| Pattern | `tgw1970MMDDHHMMSS` + `mmm` |
| Example | `tgw19700102105139553` |
| Root cause | Timestamp recorded as Unix epoch ≈ 0; original intake date lost |

All 26 share date `19700102` (Jan 2, 1970 UTC = a few hours past Unix epoch 0).
The real intake date is unknown. Items appear to be legitimate in-stock inventory.
Items checked: valid location, title, `#STATUS=In Stock`.

**Migration strategy:** Assign new canonical SKUs using current date + sequential suffix,
or use file `mtime` as a proxy for intake date. Requires manual review to confirm
none have eBay listings (check: all 26 have `ebay_item_id: null` ✓).

---

### Class C — Modern Short (dominant non-canonical group)
| | |
|---|---|
| Count | **20,328** (36.7%) |
| Length | 18 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSSx` (7-digit time, 1-digit ms) |
| Example | `tgw202001291640269` → 2020-01-29 16:40:26.9 |
| Years | 2020–2026 |
| Live eBay offers | ~11,300 of these items |

This is the **largest migration target** and the highest-risk group due to live listings.
The format is almost canonical — it has the correct date and time, just uses 1 ms digit
instead of 3 and therefore lands at length 18 instead of 20.

**Canonical equivalent:** Pad ms digit to 3: `x` → `x00`
e.g. `tgw202001291640269` → `tgw20200129164026900`

**Collision risk:** Low — the 1-digit ms suffix already distinguishes rapid bursts;
padding to 3 digits creates a valid canonical SKU. A pre-migration uniqueness check
against all existing SKUs is required.

**eBay impact:** Any item with `ebay_offer.offer_id` or `ebay_listing.listing_id` needs
the eBay inventory item's `sku` custom label updated via Inventory API PUT before the
local rename, or eBay sync will break after migration.

Year breakdown:
```
2020: 3,127    2021: 5,557    2022: 3,611
2023: 1,897    2024:   471    2025: 4,879    2026: 786
```

---

### Class D — Underscore Separator
| | |
|---|---|
| Count | **33** (0.06%) |
| Length | 18 |
| Pattern | `tgw` + `YYYYMMDD` + `_` + `HHMMSS` (no ms) |
| Example | `tgw20200115_113609` → 2020-01-15 11:36:09 |
| Years | 2020 only |

A brief period where an underscore separator was used and milliseconds were dropped.
Almost certainly from the same intake tool as Class F (no-ms) but with the underscore added.

**Canonical equivalent:** Strip underscore, append `000` for ms.
e.g. `tgw20200115_113609` → `tgw20200115113609000`

---

### Class E — No Day (2005–2007 era)
| | |
|---|---|
| Count | **16** (0.03%) |
| Length | 18 |
| Pattern | `tgw` + `YYYYMM` + `HHMMSS` + `mmm` |
| Example | `tgw200503114925650` → 2005-03 (day unknown) |
| Years | 2005, 2006, 2007 |

The oldest surviving items. Date encoded without a day field; original day is unrecoverable
from the SKU alone. File `mtime` is not reliable this far back.

Note: `tgw200516...` has month `16` which is invalid as YYYYMM — these are likely
`YYMMDD` format (year 20, month 05, day 16) from a different encoding used briefly around
the same period. Both interpretations land in the same small group.

**Migration strategy:** Assign day = `01` as a conservative placeholder, or use file
`mtime`. Flag these in `sku_history` with `change_reason=epoch_no_day`.
Check for live eBay listings before renaming (current scan: none found).

---

### Class F — No Milliseconds
| | |
|---|---|
| Count | **210** (0.4%) |
| Length | 17 |
| Pattern | `tgw` + `YYYYMMDD` + `HHMMSS` |
| Example | `tgw20180108202128` → 2018-01-08 20:21:28 |
| Years | 2018 (32), 2020 (10), 2021 (168) |

Milliseconds were not recorded. The intake tool likely truncated or dropped them.

**Canonical equivalent:** Append `000`.
e.g. `tgw20180108202128` → `tgw20180108202128000`

**Collision risk:** Low — appending `000` should not collide with existing items since
no canonical item ends in exactly those digits at the same second. Verify with a
pre-migration uniqueness scan.

---

### Class G — Anomaly
| | |
|---|---|
| Count | **1** |
| Length | 19 |
| SKU | `tgw20210421C0939348` |
| Issue | Non-numeric character `C` at position 11 |
| Content | title=Encyclopedia of Combat Aircraft; location=disposed; status=unknown |

Single item with a non-digit character embedded in the timestamp. The `C` likely
originated from a typo or a copy-paste of a hex string. Item is in `disposed` location.

**Migration strategy:** Treat as Class C with `C` → `0`: `tgw20210421C0939348` → `tgw20210421009393480`?
Or simply assign a new canonical SKU. Low priority given `disposed` status.

---

## Length Histogram

```
Length  Count    Pct     Class(es)
------  -------  ------  ---------
17      210      0.38%   F (no-ms)
18      20,377   36.82%  C (modern-short) + D (underscore) + E (no-day)
19      1        0.00%   G (anomaly)
20      34,763   62.80%  A (canonical) + B (epoch-0)
```

---

## Migration Priority

| Priority | Class | Count | Complexity | eBay Impact |
|----------|-------|-------|------------|-------------|
| 1 (highest) | C — Modern Short | 20,328 | Medium | HIGH — ~11,300 live offers |
| 2 | F — No Milliseconds | 210 | Low | Low — check per item |
| 3 | D — Underscore | 33 | Low | Low — check per item |
| 4 | E — No Day | 16 | Medium | None found |
| 5 | B — Epoch-0 | 26 | High | None found |
| 6 | G — Anomaly | 1 | Low | None — disposed |

---

## eBay Migration Constraint

**11,351 non-canonical items have a live eBay offer_id or listing_id.**
These are almost entirely Class C (2020–2026 items actively in pipeline).

For each such item, migration must:
1. Update the eBay Inventory Item via `PUT /sell/inventory/v1/inventory_item/{new_sku}`
   with the full item payload, then
2. Update the Offer via `PUT /sell/inventory/v1/offer/{offer_id}` with `sku=new_sku`,
   or delete and re-create the offer — TBD based on eBay API behavior for SKU rename.
3. Rename the local `ItemData/<old_sku>/` directory and JSON file.
4. Write `sku_history` record.
5. Enqueue `catalog_rebuild`.

**Confirmed:** This is a one-time operation. After normalization, intake enforcement
prevents new non-canonical SKUs from entering the system.

---

## What Needs to Be Decided Before Writing Migration Code

1. **Class C padding rule confirmed:** `HHMMSSx` → `HHMMSSx00` (pad to 3-digit ms)
2. **Class B/E date proxy:** Use file `mtime`? Use `01` for missing day? Use a fixed
   "unknown" date range that won't collide? (Suggest: `tgw19991231235959` + counter)
3. **eBay rename path:** Can eBay Inventory API rename a SKU in-place, or must we
   delete + recreate? Test with one item before bulk run.
4. **Collision check scope:** Full ItemData scan + SQLite catalog + `sku_history` table
   (once created).
5. **Rollback window:** Migration script must write a rollback manifest (old→new map)
   before making any changes.

---

## Files Referenced

| Path | Purpose |
|------|---------|
| `/opt/TGW/data/ItemData/` | 55,351 item directories |
| `/opt/TGW/data/ItemCatalog/tgwcatalog.db` | SQLite catalog (sku, title, location, status, price) |
| `src/tgw/queue/state_machine.py` | `enqueue_job()` for post-rename catalog_rebuild |
| `src/tgw/items.py` | `atomic_write_json()` for safe file writes |
| `src/tgw/apis/ebay/client.py` | eBay API for offer/inventory updates |

---

## Next Steps (PP-ADD-005)

1. Review and confirm this audit (especially Class C padding rule and eBay rename path)
2. Write canonical SKU spec as a one-paragraph normative definition (for enforcement code)
3. Build `sku_history` table in `state_machine` DB
4. Build migration script: dry-run → review → live run with rollback manifest
5. Add SKU validation at intake points (bundle_intake, multi_intake) to reject non-canonical
