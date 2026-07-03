## PP-MIGRATE-001 ✅ COMPLETE (2026-06-20) — Migration Worker Self-Healing

**Opened and closed:** 2026-06-20 (session 35)
**Driver:** ebay_sku_migrate failures (aspect data constraints, policy violations) were surfacing
in dead-letter with no operator visibility and no recovery path. Dave observed this violates the
tgw.source archive-before/after pattern established for all item JSON manipulation.

### What was wrong

- Multi-value eBay aspects (e.g., `Artist: ["Sting", "Ruben Blades"]`) were being sent to the
  Inventory API verbatim; API rejected them (errorId 25002 data constraint)
- Aspect values > 65 chars caused similar rejections
- Permanently-failing items had no local record and no way to surface them to the operator
- `migrate-blocked` state was invisible — no CLI, no HTTP endpoint
- Old SKU searches returned 404 after successful migration (catalog/detail page broken)
- No pre-migration backup existed; data loss during a bad write had no recovery path

### What was built

**Auto-sanitize (no human needed):**
- `_sanitize_inv_aspects()` collapses multi-value aspects to first value, truncates to 65 chars
- Applied in `_migrate_inventory_live()` and `_recover_partial()` before every Inventory API PUT
- Sold item detection in `find_batch()` — auto-skips with `sku_migrate_skip=True`
- Extended `_PERMANENT_ERROR_SIGNALS` to include errorId 25019 (policy violation)

**Surface to operator:**
- `sku_migrate_blocked` field written to item JSON on permanent failure (error, timestamp, ebay_done)
- `/opt/TGW/var/migrate-blocked.json` registry — all blocked items in one place
- `GET /api/migrate/blocked` HTTP endpoint
- `tgw migrate-review` CLI — formatted table of blocked items

**Self-service resolution:**
- `tgw migrate-unblock <sku>` — clears skip/blocked flags; worker retries next batch
- `POST /api/items/{sku}/action migrate_unblock` — same from HTTP/UI
- Old-SKU redirect: `GET /api/items/<old_sku>` now follows `sku_history` → 301 to new SKU
- Search by old SKU: `sku_old` field indexed in SQLite catalog for `json_extract` queries

**Pre-migration archive (Dave's tgw.source pattern):**
- `rename_sku()` writes `var/migrate-archive/<old_sku>.json` before touching any file
- Non-fatal if write fails (logs warning; never blocks the migration)
- `tgw migrate-restore <old_sku>` — reads archive, looks up new SKU from `sku_history`,
  writes full snapshot back to the correct path with updated `sku` field; `--dry-run` available

### Architecture decision

Archive-before-write is a system invariant for any worker that reads-then-writes item JSON
during a destructive operation (rename, migrate, batch-edit). The pattern is: snapshot to
`var/migrate-archive/` before the first write; `ebay_live` provides an independent recovery
path via `tgw ebay-pull --sku <new_sku>` for eBay-side data.

---

