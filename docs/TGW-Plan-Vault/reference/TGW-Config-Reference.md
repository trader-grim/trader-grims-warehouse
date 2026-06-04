---
title: TGW Config Reference
markmap:
  colorFreezeLevel: 2
  initialExpandLevel: 3
updated: 2026-06-04
---

# TGW Config Reference

## Overview
- File: `/opt/TGW/config/tgw-api-config.json`
- Loaded by: `src/tgw/config.py → load_config()`
- All path values resolved to absolute `Path` objects by `load_config`
- Keys not present in JSON fall back to coded defaults (marked below)
- Workers access config as `cfg['key']` — never read the JSON directly

---

## Paths (in JSON, overridable)

### Core roots
- `secrets_root` — `/opt/TGW/secrets` — secret files (chmod 700, files 600)
- `itemdata_root` — `/opt/TGW/data/ItemData` — one folder per SKU
- `catalog_root` — `/opt/TGW/data/ItemCatalog` — all catalog outputs
- `incoming_path` — `/opt/TGW/incoming` — intake staging area
- `plan_vault_path` — `docs/TGW-Plan-Vault` — Obsidian vault

### Catalog outputs (derived from catalog_root if not in JSON)
- `sqlite_catalog_path` — `catalog_root/tgwcatalog.db` — primary SQLite catalog
- `thumbnail_root` — `catalog_root/thumbnails/` — `{sku}.jpg` per item
- `search_catalog_path` — `catalog_root/search-catalog.json`
- `location_tree_root` — `catalog_root/by-location/` — symlink tree
- `full_catalog_path` — `catalog_root/tgwcatalog.json` (code default) ⚠ JSON says master-catalog.json — mismatch; code wins
- `full_catalog_csv_path` — `catalog_root/tgwcatalog.csv`
- `search_catalog_csv_path` — `catalog_root/searchcatalog.csv`
- `ebay_draft_csv_path` — `catalog_root/ebay-draft-offline.csv` — offline fallback drafts

### Derived (not in JSON — constructed by load_config)
- `ebay_token_path` — `secrets_root/ebay-token.json`
- `ebay_credentials_path` — `secrets_root/ebay-credentials.json`
- `newitems_path` — `incoming_path/newitems/`
- `plan_inbox_path` — `plan_vault_path/inbox/`
- `plan_master_path` — `plan_vault_path/plan/TGW-Master-Plan.md`

---

## Database

- `postgres_dsn` — `'dbname=state_machine user=tgw'` (default; not in JSON currently)
  - Change only if running Postgres on non-default socket/port or with a password
  - All workers call `state_machine.init(cfg['postgres_dsn'])` on startup

---

## eBay Policies (account-level IDs — change rarely)

- `fulfillment_policy_id` — `199931446015` — **FC4** default for most categories
- `payment_policy_id` — `246544838015`
- `return_policy_id` — `246544837015`
- `fulfillment_policy_by_category` — per-category fulfillment overrides
  - `88758` (Stamps) → `223550459015`
  - `280` (Postcards) → `213431337015`
  - `31740` (Barware) → `252109696015`
  - `60115` (Kitchenware) → `252109696015`
  - `2036` (Pottery & Glass) → `252109696015`
  - `52365` (Figurines) → `213957220015`
  - `261672` (Decorative collectibles) → `186871591015`
  - All others: fall back to `fulfillment_policy_id` (FC4)

---

## Pricing

- `reprice_stages` — markdown schedule (array, each has `days`, `percentile`, `label`)
  - Current: launch (day 0, max→.99) → retail (day 3, p75) → move (day 17, p25)
  - All fields configurable; change here to affect all future reprice schedules
  - Already-published items use the schedule stored in their item JSON at publish time
- `category_price_defaults` — fallback price when Browse API comps are thin
  - `280` (Postcards) → $9.99
  - `61312` → $8.99
  - ⚠ Very thin coverage — expand as more categories are priced

---

## Catalog Build

- `search_catalog_fields` — fields included in search-catalog.json (legacy list; includes stale field names)
- `search_catalog_required` — fields required for catalog inclusion (default: `["sku"]`)
- `pretty_json` — `true` — item JSON written with indentation
- `skip_missing_files` — `true` — catalog build skips items with missing JSON
- `thumbnail_size` — `[256, 256]` (default, not in JSON) — thumbnail dimensions in pixels

---

## Workers

### ebay_sku_migrate (read from `raw` dict — not in load_config return)
- `ebay_sku_migrate.enabled` — `true`
- `ebay_sku_migrate.batch_size` — `5` items per hourly run
- `ebay_sku_migrate.interval_hours` — `1`
- To pause migration: set `enabled: false`
- To increase rate: increase `batch_size` (watch eBay for listing-age complaints first)

---

## Legacy / Stale Keys (in JSON, ignored by load_config)
These are read into `cfg['raw']` but have no effect. Safe to remove when cleaning up the config file.

- `itemcreation_root` — old intake staging path, superseded by `incoming_path`
- `archive_root` — referenced by old tgw.source scripts; not used by any Python worker
- `api_root` — old monolith path; fully retired
- `config_root` — unused; path derivation happens in load_config
- `source_root` — old monolith source path; retired
- `log_root` — superseded by journalctl / systemd logging
- `ebay.redirect_uri` — OAuth redirect URI used during initial token grant only; not needed at runtime
- `ebay.scopes` — scopes string used during initial token grant only; not needed at runtime
- `json_editor` / `image_viewer` — shell tool preferences from old tgw.source; not used by workers

---

## Secrets Reference (files in secrets_root)

| File | Contents | Used by |
|------|----------|---------|
| `ebay-credentials.json` | `{app_id, cert_id, dev_id*}` — eBay app credentials | token_refresh, all eBay API callers |
| `ebay-token.json` | `{access_token, refresh_token, expires_at}` | All eBay API callers |
| `tgw-api-key.json` | `{api_key}` — tgw-http Bearer token | tgw-http server + Flutter app |

*`dev_id` optional; enables full SOAP notification signature verification (PP-SOLD-001 Tier 4)

**Needed for PP-LOOKUP-001 (not yet created):**
- `upcitemdb-credentials.json` — `{api_key}`
- `go-upc-credentials.json` — `{api_key}`
- `discogs-credentials.json` — `{token}`
- `igdb-credentials.json` — `{client_id, client_secret}`

---

## Adding a New Config Key

1. Add to `tgw-api-config.json` with the value
2. Add a `p(key, default)` call (for paths) or `raw.get(key, default)` in `load_config()`
3. Add to the return dict
4. Workers access via `cfg['key']`
5. Document it here
