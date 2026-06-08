# Gemini Task 007 — Data / Archive History Consolidation

**Date prepared:** 2026-06-08
**Prepared by:** Claude (Opus 4.8), session 19 delegation pass
**Expected output:** `GEMINI-007-result.md` in `docs/TGW-Plan-Vault/inbox/`

> Why you (Gemini): this is a sprawling, low-structure corpus that needs a large-context inventory
> + organization plan. It mirrors the ItemArchive zip-indexing work already done — extend that
> pattern to the rest of `data/history/`.

---

## Context

`/opt/TGW/data/history/` is a **163 GB+** accreted dump of the business's past: legacy eBay/Magento
exports, error reports, GarageSale data, Foldio camera output, old ItemData/ItemImages trees,
campaign CSVs, the ItemArchive (zipped historical items), and more. Some of it has already been
indexed (the **ItemArchive zips → `archive-ebay-index.json`** work — 22,124 entries — see the
master plan PP-SOLD-001 Tier 2 archive tombstone pass). The rest is uncatalogued.

Top-level folders include (non-exhaustive — enumerate the real tree yourself):
`ItemArchive/` (163 GB of zips), `ItemData/`, `ItemImages/`, `eBay/`, `ebayimportjobs/`,
`GarageSale/`, `Foldio/`, `Errors/`, `Catalog_files/`, `catalog-backup/`, `gdrive-upload/`,
`ItemCreation/`, `Documents/`, `Applications/`, several `bin*` dirs, and many loose CSVs
(error reports, draft-import CSVs, campaign reports).

> **Scale warning:** This is huge. Do **not** read file *contents* wholesale. Inventory by
> walking the tree (names, sizes, counts, dates, extensions) and sampling a few representative
> files per folder type to infer its purpose. The deliverable is an **organization + index
> plan**, not a content dump.

## Your task
1. **Inventory the tree.** For each top-level folder: what is it, what's inside (file types,
   counts, total size, date range), and is it **live-referenced**, **archival**, or **junk/dupe**?
   Use `du -sh`, `find … -type f | wc -l`, extension histograms, and date sampling — not full reads.
2. **Classify** each folder/dataset into: KEEP-INDEX (worth indexing like the ItemArchive),
   KEEP-COLD (retain, no index needed), MIGRATE (belongs in current pipeline/ItemData),
   DUPLICATE (already represented elsewhere), or DISCARD-CANDIDATE (propose deletion — operator
   decides; never delete anything yourself).
3. **Map to current data.** Where legacy records correspond to current SKUs or eBay IDs, note the
   join key (SKU, eBay item number, filename pattern) so a future indexer can link them — same
   shape as `archive-ebay-index.json`.
4. **Propose an index schema** for the KEEP-INDEX sets (extend or parallel the existing
   `archive-ebay-index.json`): what columns, what the dedupe key is, how it links to live ItemData.
5. **Gap analysis.** What historical signal are we currently missing that this data could recover
   (e.g. pre-2-year-window sold prices for archive tombstones, old descriptions/photos for items
   that lost them, legacy categories)?
6. **Recommended cleanup order** — safest, highest-value first; flag anything large + clearly
   redundant for operator review.

## Output (`GEMINI-007-result.md`)
- **Folder inventory table**: path | purpose | files | size | date range | classification.
- **Migrate/index targets** with the proposed join key + index schema.
- **Gap analysis** — what we can recover and how it feeds current pipeline.
- **Cleanup recommendations** (operator-gated; nothing deleted by you).
- **A proposed `tgw`-side indexer design sketch** (so Claude can build the indexer worker, the way
  the ItemArchive index was built — through the tgw-api fence, not direct DB writes).

## Constraints
- **Read-only. Delete nothing. Move nothing.** This is analysis + a plan only.
- Don't read 163 GB — inventory by metadata and sampling.
- Don't commit to git.
- Output goes to `inbox/` for PM-intake to fold into the master plan.
