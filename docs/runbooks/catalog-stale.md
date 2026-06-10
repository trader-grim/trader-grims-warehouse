# Runbook: stale or broken derived catalogs

**Failure mode:** the derived read models diverge from ItemData truth — SQLite catalog
(`ItemCatalog/tgwcatalog.db`), `search-catalog.json`, CSVs, the `by-location/` symlink
tree, or thumbnails. Every list/search surface (CLI list/search/resolve, Flutter app,
tablet web forms, MC) reads these; **full item GET reads the JSON and is never stale**.

Key design fact: all derived stores are regenerable from ItemData. Rebuilding is always
safe; the worst a rebuild crash leaves behind is *stale*, not corrupt (the next run
overwrites). No data may live only in a catalog.

## Symptoms

- `tgw search` / app search doesn't show a recently created or edited item, but
  `tgw get <SKU>` shows the change. (≤ ~30 s + rebuild-time lag is NORMAL — the rebuild
  job coalesces with a 30 s delay.)
- Items missing or duplicated in the Flutter app list views.
- `by-location/` browse shows items in their old bin after a location change.
- Thumbnails missing/blank in UIs.
- `tgw health` flags the SQLite catalog or thumbnails section.
- SQLite errors in catalog_rebuild journal (`database is locked`, `malformed`).

## Likely root causes

1. **Rebuild signal lost**: a write happened while Postgres was down, so the
   `catalog_rebuild` enqueue failed (the writer's `except UniqueViolation: pass` only
   covers dedupe; a dead DB raises out of `handle` → retried for workers, but CLI/HTTP
   writes can silently skip the signal).
2. **catalog_rebuild worker down/dead-lettered** — writes accumulate, catalog freezes.
3. **Rebuild churn**: a steady trickle of writes keeps the O(55K-item) rebuild cycling;
   the catalog is perpetually minutes behind during bulk operations. Expected during
   bulk edits / sku-migrate bursts.
4. **SQLite file damaged** (disk full mid-write, manual fiddling).
5. **thumbnail_gen backlog** or Pillow missing in the venv.
6. **ISS-003 trap**: live config sets `full_catalog_path: master-catalog.json` but
   `load_config()` defaults to `tgwcatalog.json` — the code default silently wins. If
   anything is "reading the wrong catalog file", this is why.
7. **Crash between JSON write and symlink update** for `location` — stale link until the
   next full rebuild (accepted; rebuild is the reconciler).

## Diagnosis

```bash
# 1. Is a rebuild pending/running/stuck?
psql -U tgw state_machine -c "
  SELECT state, not_before, attempt_count, error_detail, updated_at
  FROM queue_jobs WHERE queue_name='catalog_rebuild'
  ORDER BY created_at DESC LIMIT 5;"
systemctl status tgw-worker@catalog_rebuild.service
journalctl -u tgw-worker@catalog_rebuild.service --since "-2 hours"

# 2. How stale is the catalog actually?
ls -l --time-style=full-iso /opt/TGW/data/ItemCatalog/tgwcatalog.db \
                            /opt/TGW/data/ItemCatalog/search-catalog.json

# 3. Truth vs derived for one item
sudo -u tgw tgw get <SKU>                         # JSON truth
sudo -u tgw tgw list --search "<title fragment>"  # derived read — do they agree?

# 4. SQLite integrity
sudo -u tgw sqlite3 /opt/TGW/data/ItemCatalog/tgwcatalog.db 'PRAGMA integrity_check;'

# 5. Thumbnails
psql -U tgw state_machine -c "
  SELECT state, count(*) FROM queue_jobs
  WHERE queue_name='thumbnail_gen' GROUP BY 1;"
ls /opt/TGW/data/ItemCatalog/thumbnails/ | wc -l

# 6. Disk space (rebuild writes whole files)
df -h /opt/TGW

# 7. Health's own view
sudo -u tgw tgw health
```

## Recovery

```bash
# Standard fix for everything derived — force a rebuild:
sudo -u tgw tgw build-all
#   (operator CLI may rebuild inline; workers must never — they enqueue instead)

# Worker down → restart, the pending job will be claimed:
sudo systemctl restart tgw-worker@catalog_rebuild.service

# Damaged SQLite file → delete it; the rebuild recreates from scratch:
sudo -u tgw rm /opt/TGW/data/ItemCatalog/tgwcatalog.db
sudo -u tgw tgw build-all

# Stale location tree only → the full rebuild regenerates by-location/ symlinks
# (covered by build-all; no separate command needed).

# Thumbnails → full sweep:
sudo -u tgw tgw build-thumbnails

# Rebuild dead-lettered → read the error first (usually disk/permissions), fix, then:
sudo -u tgw tgw dead-letter --requeue <JOB_ID>
```

**Note on churn (cause 3):** not an incident. If bulk operations make staleness
operationally painful, that's the satellite/dirty-flag design in the master plan — a
project, not a recovery.

## Rollback

- None needed for rebuilds — derived stores have no authoritative content. Any rebuild,
  re-rebuild, or deletion of a derived file is recoverable by another rebuild.
- **Never "fix" a discrepancy by editing the catalog directly** (SQLite or JSON catalog):
  the next rebuild erases your edit. If the catalog is "right" and the JSON is "wrong",
  the JSON is still canonical — fix the item through the fence (`tgw set`, HTTP PATCH).
- If you deleted the wrong file: only ItemData and secrets are non-regenerable. Anything
  under `ItemCatalog/` rebuilds. If ItemData itself was touched, stop and go to the
  trader-grims-backup snapshots.

## Verification

```bash
# 1. Rebuild completed
psql -U tgw state_machine -c "
  SELECT state, finished_at FROM queue_jobs
  WHERE queue_name='catalog_rebuild'
  ORDER BY created_at DESC LIMIT 3;"
ls -l --time-style=full-iso /opt/TGW/data/ItemCatalog/tgwcatalog.db   # fresh mtime

# 2. Truth and derived agree for the items that triggered the incident
sudo -u tgw tgw get <SKU>
sudo -u tgw tgw list --search "<title fragment>"   # item present, fields current
sudo -u tgw tgw resolve --location "<location>"    # location tree current

# 3. Write-path round trip: edit a field, wait ~60 s, confirm it appears in search
sudo -u tgw tgw update <SKU> notes "catalog-verify $(date +%s)"
sleep 60 && sudo -u tgw tgw list --search "catalog-verify"

# 4. SQLite clean
sudo -u tgw sqlite3 /opt/TGW/data/ItemCatalog/tgwcatalog.db 'PRAGMA integrity_check;'

# 5. Health clean (catalog + thumbnails sections)
sudo -u tgw tgw health
```
