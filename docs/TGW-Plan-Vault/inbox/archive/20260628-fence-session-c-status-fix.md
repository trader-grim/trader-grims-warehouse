# DONE: PP-FENCE-001 Session C + status resolution fix

Session 32 — 2026-06-28

## What was done

### PP-FENCE-001 Session C — http_server write consolidation
All 18 inline atomic_write_json call sites in http_server.py action handlers and photo
management replaced with three internal helpers:
- `_apply_patch(json_path, fields)` — core patch; None value = delete key
- `_apply_ebay_write(json_path, sku, *, ...)` — eBay block merge with field protection
- `_enqueue_catalog_rebuild(reason)` — coalesced 30s rebuild

atomic_write_json now only called inside these three functions. Smoke tested live (archive action).
The canonical fence endpoints delegate internally to the same helpers.

### sqlite_catalog.py status resolution fix
`_resolve_status()` function with terminal-state-wins logic. Fixes archive invisibility
(items with status=archived + #STATUS=new now correctly show as archived in catalog).
353 conflicted items resolved. 5,103 items have only #STATUS — normalization pending (todo #1053).

### Plan updated
Master plan updated with Session C record, Session D (OS lockdown) named, workers unblocked.
Inbox cleaned. Memory updated.

## Workers are NOW UNBLOCKED

Restart sequence:
1. catalog_rebuild — already running
2. thumbnail_gen
3. pm_intake
4. plan_render
5. ai_identify — verify one job completes cleanly
6. ebay_draft → ebay_upload → ebay_price → ebay_stage → ebay_publish

Start one at a time, verify each before proceeding.

## Still open

- ebay_sync broken: fetch_all_offers() returns 400/25707 silently — fix needed before ebay_sync is useful
- #STATUS normalization migration (todo #1053)
- PP-FENCE-001 Session D: NixOS tgw-worker OS user lockout (deferred)
- PP-BACKUP-001: backup health warnings still present
- End+relist gate test: verify ebay_end_listing → relist flow through UI
