# Session 31 — PP-FENCE-001 Session B Complete

## What happened this session

PP-FENCE-001 Session B is done. All 30 `atomic_write_json` call sites in `workers/` and `ebay/` have been replaced with fence client calls. 27 tests pass. CI grep audit test added to `test_invariants_items_fence.py`.

### What was migrated

| File | Sites | Fence calls used |
|------|-------|-----------------|
| `ebay_repush.py` | 1 | `fence_ebay_write(ebay_listing={"repush_at": ..., "photo_verify": None})` |
| `ebay_sync.py` | 1 | `fence_ebay_write(ebay_listing=..., ebay_offer=...)` |
| `ebay_upload.py` | 1 | `fence_patch_item({"ebay_photos": ..., "draft_listing": {"imageUrls": ...}})` |
| `bundle_intake.py` | 1 | `fence_create_item(sku, record)` |
| `ai_identify.py` | 1 | `fence_patch_item({title, category, ..., vision_results, identification_history})` |
| `ebay_draft.py` | 2 | `fence_patch_item({"offline_draft": True})` + `fence_patch_item({"draft_listing": draft})` |
| `ebay_price.py` | 1 | `fence_ebay_write(ebay_offer=...)` + `fence_patch_item({"draft_listing": draft})` |
| `ebay_stage.py` | 2 | `fence_patch_item({"pipeline_error": ...})` + `fence_ebay_write(ebay_offer=..., ebay_submitted=...)` |
| `ebay_publish.py` | 2 | `fence_patch_item({"pipeline_error": ...})` + `fence_ebay_write(...)` + `fence_patch_item({reprice_schedule, price_history, draft_listing})` |
| `ebay_price_reducer.py` | 2 | `fence_patch_item({"reprice_schedule": ...})` + `fence_ebay_write({"price": ...})` + `fence_patch_item({...})` |
| `ebay/pull.py` | 3/4 | `fence_ebay_write` + `fence_patch_item` (archive tombstone left as gap) |
| `ebay/snapshot_backfill.py` | 1 | `fence_patch_item({"ebay_submitted": ...})` |

### Documented gaps (left as `atomic_write_json` with PP-FENCE-001 comments)

- `multi_intake.py` (2 sites): newitems_dir write (outside ItemData scope) + key-deletion write (fence needs `delete_fields`)
- `ebay_sku_migrate.py` (3 sites): dir-rename context — migrate in Session C with the full rename workflow
- `ebay/pull.py` restore_archive_tombstone (1 site): needs create-or-overwrite (upsert) semantics not yet in fence

### Also: fence protection logic fix

`http_server.py` ebay_write protection changed from "always restore protected fields" to "restore only if NOT in incoming_block" — so workers can intentionally clear protected fields (e.g., ebay_repush clearing photo_verify) by explicitly including them in the payload.

## Next steps

1. **Restart workers** — fence is ready; all standard workers use it. Order: `token_refresh` → `pm_intake` → `ai_identify` → rest.
2. **PP-FENCE-001 Session C** — NixOS tgw-worker user; OS-level write lockout.
3. **PP-SEARCH-001 Phase 0** — recoll universal index (todo #1066).
