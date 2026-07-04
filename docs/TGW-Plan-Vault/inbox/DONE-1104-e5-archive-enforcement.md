# DONE — todo #1104: enforce invariant E5 in code (archive-before-overwrite)

`items.atomic_write_json(..., archive_root=...)` now zips the current on-disk
item JSON into `archive_root/<sku>.zip` before any overwrite, fail-closed (an
archive error aborts the write). Wired into `_write_field` (covers
`update_item`/`update_items`/`locationupdate`/`catlocmvall`), `verifiedupdate`,
and http_server.py's three overwrite call sites (`_apply_patch`,
`_apply_ebay_write`, photo-order removal). `create_item` doesn't need it — A3
already refuses overwrites there.

`archive_root` added to `config.load_config()` (new key, defaults to
`/opt/TGW/data/ItemArchive`).

**Archive location repointed (Dave's direction):** the configured symlink was
stale (pointed at an unmounted `/media/TGW`). Real archive (54,688 zips, 163G)
currently lives at `/home/db/devices/porche/history/ItemArchive` — a
temporary consolidation point while Dave zipmerges several archive copies
together. Per his instruction, `/opt/TGW/data/ItemArchive` is now a plain
local directory (tgw:tgw, 750) on NVMe root, not a symlink — accumulates new
go-forward writes only, movable to another partition later without any code
change (archive_root is config-driven). Disk check: 57G free on that
partition at 80% used; fine for incremental go-forward writes, not a place
to let this grow to full-archive scale (163G) without periodic zipmerge
elsewhere — matches Dave's own stated pattern (regular small writes here,
occasional zipmerge to the real archive, so the merged set isn't constantly
written to).

Live-verified 2026-07-04: real item `tgw201412211145262` written via
`verifiedupdate()`; pre-write JSON correctly archived to
`ItemArchive/tgw201412211145262.zip` before the overwrite. 6 new unit tests
in `tests/test_invariants_items_fence.py`, including a fail-closed case.
Full suite: 1761 pass / 1 skipped / 0 fail / 0 errors, unchanged.

**Deferred (not in scope this pass):** `ebay_sku_migrate` archiving before
SKU-directory rename; a full grep-audit for any `rm -rf`-style deletion path
bypassing this; photo/media-file archiving (only item JSON is covered).
