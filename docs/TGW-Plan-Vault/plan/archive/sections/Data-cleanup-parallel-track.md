## Data cleanup (parallel track)
### SKU normalization (PP-ADD-005) — Critical; unblocks PP-ADD-003, 006, 008
#### Audit ✅ COMPLETE + decisions confirmed (2026-06-03) — see `SKU-Audit-Report.md`
- Canonical format is **18 chars**: `tgwYYYYMMDDHHMMSSs` (tenths, not ms) — chosen for barcode labels
- 55,351 items; 20,328 already canonical (Class C); 35,023 to migrate
- **7 format classes, all migration rules confirmed:**
  - C: len-18 `tgwYYYYMMDDHHMMSSs` — **20,328 ✅ already canonical**
  - A: len-20 `tgwYYYYMMDDHHMMSSmmm` — 34,737; truncate last 2 digits; collision check first; live listings via slow eBay batch (delist→relist ~50/batch)
  - B: Epoch-0 `tgw1970...` — 26; new format `tgw201501021970xxx` (last 3 of original); no eBay listings
  - D: Underscore len-18 — 33; strip `_`, append `0`
  - E: YYMMDD 2020-era — 16; prepend `20` to expand year, truncate ms to 1 digit
  - F: No-tenths len-17 — 210; append `0`
  - G: Anomaly len-19 — 1 item (disposed); manual
#### Migration script ✅ READY TO RUN (2026-06-03) — see `src/tgw/sku_migration.py`
- `tgw sku-migrate` CLI with --check-collisions, --class, --dry-run/--run,
  --include-live-ebay, --limit, --manifest
- `sku_history` table created in `state_machine` DB
- 7 Class A collision pairs auto-resolved (hundredths digit fallback)
- 1 Class B collision auto-resolved (alternate suffix window)
- Single live-item test verified end-to-end (Class B epoch-0)
- Rollback manifest written to `/opt/TGW/var/log/sku-migrate-<ts>.json` on every run
- **Bug fixed (2026-06-04)**: `rename_sku` now wraps all OS operations in try/except; per-item errors are counted and logged without killing the batch. Also guards against stale `new_link` symlink from a partial prior run.

#### ✅ MIGRATION COMPLETE for non-eBay items (2026-06-04)
- Steps 1–3, 5, 6 done; ~8,370 eBay live listings remain (step 4 — gradual batch)
- 228 non-eBay items (classes F,D,E,B) migrated; 26,423 Class A non-live migrated
- All catalogs rebuilt (`tgw build-all`) — 55,351 items
- `bundle_intake.SKU_RE` updated to `r'^tgw\d{15}$'` (enforces exact 18-char format)

#### Execution sequence (when ready)
1. ✅ `tgw sku-migrate --check-collisions`  — confirm still clean
2. ✅ `tgw sku-migrate --class F,D,E,B --run`  — 228 items, no eBay listings, done
3. ✅ `tgw sku-migrate --class A --run`  — 26,423 Class A without live listings, done
4. **eBay live listings (~8,370 Class A) — spread delist/relist over time, not bulk**
   - Bulk delist+relist in one shot resets listing age, loses watchers, tanks placement
   - Spreading ~50 relists/day through the day is fine and may actually help the algorithm
     (fresh listings boosted by eBay's new-listing window, spread across the day)
   - Physical inventory sweep (PP-SOLD-001) first — sold items don't need renaming at all,
     reducing the batch by however many have sold
   - Worker approach: `tgw-worker@ebay_sku_migrate.service` — daily batch of N items,
     scheduled across the day (e.g. 5 items/hour), tracks progress in `sku_history` table
   - Rate: configurable; start conservative (10/day), increase if no issues
5. ✅ `tgw build-all`  — all catalogs rebuilt (55,351 items)
6. ✅ Add SKU validation at intake — `bundle_intake.SKU_RE` now enforces exactly 18 chars

#### Remaining work
- Intake enforcement: validate 18-char format at bundle_intake / multi_intake ingestion points
- Post-migration verification report (`tgw sku-migrate --verify` or manual audit)
- Catalog/search SKU matching: tolerate any variant by matching on first 18 characters — covers residual format drift without requiring full normalization first

INPROGRESS-sku-migration-blast.md — eBay SKU migration blast running (3,203 items, ~7-8 hours, 83 skip-flagged)
### Data scrub passes (priority elevated 2026-06-03)
- Pass 1: itemdata_scrub dry-run → review → --write (merge history keys, drop junk)
- Pass 2: photo_history_recovery dry-run → review → --write
- Pass 3: import eBay listings to fill gaps; then freeze the field schema
- Epoch-zero SKU purge (tgw1970*) subsumed by PP-ADD-005 normalization
- Recovery source: historical-tgw-catalog.json
- Field rename: `#VERIFIED` → `verified` (legacy field from eBay CSV export; hash prefix was never intentional — fold into Pass 1 or run as a standalone scrub step)

### ItemArchive — legacy sold/ended listing history
- Path: `/opt/TGW/data/history/ItemArchive/` — zipped legacy item packages from the old system
- Archive eBay index: `/opt/TGW/var/archive-ebay-index.json` — 6,824 entries built by `tgw import-sold-csv`
- Content: older eBay listings predating the current ItemData structure; useful for sold reconciliation
- Integrate: re-run `tgw import-sold-csv` after `ebay_sku_migrate` progresses to build up more archive matches
- Archive index grows as migrated SKUs accumulate in `sku_history` table

### PP-SOLD-001 — Sold reconciliation and inventory status sync (design ready)

#### Problem
Sold reconciliation fails when routed through the catalog as intermediary — catalog is
batched and may not have `ebay_listing.listing_id` at sale time. Status fields across the
55K+ item catalog are stale for many legacy items. Physical inventory has gaps.

#### Three reconciliation tiers
1. **eBay API** — `GetMyeBaySelling` (active + sold) and `GetOrders` (date ranges); match
   `listing_id` directly against `ItemData/*/\*.json`. `ebay_legacy_sync` already does the
   active side — extend it to pull sold orders and mark items status=Sold.
2. **Sold report CSV** — match eBay item number against `ebay_listing.listing_id` in item
   JSON directly, never through catalog. CSV download from Seller Hub.
3. **Physical inventory sweep** — generate checklist of ambiguous-status SKUs (no
   `ebay_listing`, or active/sold unresolved) for human review. Item gone → sold/missing;
   item present → available.

#### Local mirror principle (settled 2026-06-03)
Every durable eBay-side ID and URL written back into item JSON immediately after API call
succeeds. Makes sold/active guards reliable without hitting eBay API at pipeline time.

#### Known data quality issues to resolve
- Many items have `Item number` from legacy eBay CSV export — this is the parent bundle's
  listing ID, not the individual item's. `multi_intake` now strips it on encounter.
- Items with `legacy_listing_resolved: True` may still have active listings — Active listing
  guard in `ebay_stage` catches new cases; sync pass needed to make data authoritative.
- Physical inventory gaps from old system — sold items not marked, available items stale.

#### Status (2026-06-04)
- **Tier 1 DONE**: `ebay_legacy_sync` extended with `_sync_sold()` — GetOrders polling,
  365-day initial lookback in 90-day windows, state file at `runtime/state/ebay-sold-sync-state.json`.
  Items matched by `listing_id`, written `status=sold` + `ebay_sale` block, catalog_rebuild enqueued.
- **Tier 2 CSV import done (run 2)** — 2-year CSV (`2024-06-05 → 2026-06-04`): 208 listing-ID
  matches + 909 fuzzy-title matches (total ~3,083 sold items now recorded).
  **Archive tombstone pass added** (`pull.restore_archive_tombstone`): when archive index matches
  a listing ID but ItemData JSON is absent, the item is extracted from the archive ZIP and restored
  as a tombstone (`_archive_tombstone: True`, `ebay_listing.listing_id` set from `Item number`),
  then marked sold normally. Idempotent; dry-run peeks without writing.
  Archive index is 22,124 entries (223–326xxx range, ~2018–2023 era). The 2-year CSV does not
  overlap this range — **0 archive matches until a full all-time eBay sold export is used**.
  To get archive hits: download complete sold history from Seller Hub (all years) and re-run
  `tgw import-sold-csv <full-history.csv> --fuzzy`.
  Flags: `--fuzzy` / `--fuzzy-threshold`; archive pass auto-activates when cache exists.
- Tier 3 (physical sweep checklist) pending.

#### Tier 4 (future) — eBay push notification webhook
eBay Trading API supports `SetNotificationPreferences` + `FixedPriceTransaction` event for real-time
sold notification (reduces latency from daily poll → seconds). Requires:
- A stable **public HTTPS URL** (eBay does TLS validation; bare IP:port not accepted)
- New endpoint in tgw-api: `POST /webhooks/ebay/notification` — parse XML, verify delivery token,
  call the same sold-mark logic as `_sync_sold()`
- Exposure options: nginx reverse proxy + static public IP + DNS, or **Cloudflare Tunnel** (free,
  zero port-opening, works behind NAT — preferred if no static IP)
- Deferred until public endpoint question is resolved (see PP-REMOTE-001)
- Daily GetOrders polling already provides coverage in the meantime

#### Sequencing
- Depends on PP-ADD-005 (SKU normalization) for reliable listing_id → SKU matching
- `ebay_legacy_sync` extension (add sold order pull) is first deliverable ✓ DONE
- Physical sweep tool after sync is authoritative
- `status` field freeze after Pass 3 data scrub

