# DONE — SKU Migration Complete (Session 35, 2026-06-29)

## What was done

Session 35 completed the ebay_sku_migrate run that had been blocked/looping:

**Root cause of infinite retry loop**: Items with `ebay_done=True` (old offer deleted, new
offer created but unpublished) that hit errorId 25021/25002/25004/25005/25604 were not in
`_PERMANENT_ERROR_SIGNALS`, so the worker retried them forever. Fix: added all these signals.

**Fix applied**: `_PERMANENT_ERROR_SIGNALS` in `src/tgw/workers/ebay_sku_migrate.py` expanded
to include: 25021 (condition invalid after USED_EXCELLENT retry), 25002 (missing/invalid item
specific), 25004 (qty=0 / sold), 25005 (invalid category), 25604 (availability not found),
"already have on eBay" (duplicate), "not allowed to revise ended listings" (ended).

**Migration outcome** (02:39 UTC):
- "no live non-canonical items remain — done" reached
- ~29 items permanently blocked with `sku_migrate_skip=True`
- Worker restart unblocked all 14 workers

**Photo push**: `scripts/ebay_photo_push.py --include-no-eps` ran live.
- 539 items: photos pushed to eBay successfully
- 66 items: eBay 400 error (same permanently-blocked migration items; can't update via API)

**Workers**: All 14 now active. `ebay_legacy_sync` running 365-day sold-order lookback.

## What is still open

- ~29 migration-blocked items need data fixes before normal pipeline can handle them:
  - 25021 items: need category changed to one that accepts used condition
  - 25002 items: missing required item specifics (Type, etc.) — ebay_draft should fix on re-run
  - 25004 items: qty=0 on eBay — likely sold; `ebay_legacy_sync` lookback will detect
  - Use `tgw migrate-unblock <sku>` to clear `sku_migrate_skip` after fixing data
- PP-BACKUP-001 timers still not installed
- PP-FENCE-001 Session D — 6 unfenced write sites (deferred)
- `nixos-rebuild switch` for Sway + lan-mouse (modules ready since session 34)

## Next step

Pipeline is running normally. No immediate action required. Monitor:
```
sudo journalctl -u tgw-worker@ebay_legacy_sync.service --no-pager | grep -E "sold|mark_item"
```
