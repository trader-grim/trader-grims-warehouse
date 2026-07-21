# Result: 1615 alt-text-history-staging
Status: done
Todo: #1615   PP: PP-DATALEARN-001
Files touched:
- src/tgw/alt_text.py
- tests/test_alt_text.py

Live evidence:
Ran `tgw.alt_text.cmd_alt_text()` for real, against the real
`/opt/TGW/config`-derived `itemdata_root` (`/opt/TGW/data/ItemData`), as
the `tgw` user, with only the network-touching vision-model call mocked
(no LLM quota spent, no eBay call). Used a throwaway test SKU
(`TEST1615HISTORYSTAGING`) that was deleted immediately after, so nothing
was left in the real dataset.

```
RESULT: {
  "ok": true,
  "sku": "TEST1615HISTORYSTAGING",
  "provider": "openrouter",
  "model": "qwen2.5vl:7b",
  "cache_hit": false,
  "alt_text": "Live-verify test photo for todo 1615",
  "seo_caption": "Live-verify test photo, safe throwaway SKU.",
  "image_copied_to": "TEST1615HISTORYSTAGING-alt.jpg",
  "archived_to_history": true,
  "history_path": "/opt/TGW/data/history-staging/TEST1615HISTORYSTAGING/TEST1615HISTORYSTAGING.jpg"
}
Expected staging path exists: True
Real history symlink target untouched (no ItemData subdir there): True
Cleanup done.
```

Confirms: the archive copy lands at
`/opt/TGW/data/history-staging/<sku>/<sku>.jpg` and the `history` symlink
onto the removable MasterArchive drive (`/opt/TGW/data/history` →
`/media/tgw/MasterArchive/history`) is never touched or resolved.

Test suite: `pytest -q` run with `PYTHONPATH`/`LD_LIBRARY_PATH` pointed at
this worktree (confirmed `tgw.alt_text.__file__` resolves under the
worktree, not the shared checkout, before trusting the result):
- `tests/test_alt_text.py`: 49 passed.
- Full suite: 2741 passed, 1 skipped, 2 failed — the 2 failures
  (`tests/test_invariant_c12_field_set_accessors.py`, both about stale
  line numbers in `ai_identify.py`) are pre-existing and reproduce
  identically on the base branch (`catio-nix-0.0.1-alpha`) before any
  change in this packet — unrelated file, out of this packet's scope, not
  caused by this change.

Deviations from spec: none. Followed the packet exactly:
- Added `_history_staging_sku_dir(cfg, sku)` (replacing `_history_sku_dir`)
  resolving `Path(cfg["itemdata_root"]).parent / "history-staging" / sku`
  — same derivation pattern as before, new leaf name, no new required
  config key.
- Both `cmd_alt_text()` and `_apply_alt_text_result()` now copy the
  original photo to the staging dir instead of the `history` symlink path
  (same `shutil.copy2` behavior, same "skip if already archived" shape).
- Removed `_history_root_reachable()` entirely (only caller was these two
  archive blocks; confirmed no other references remain except an
  explanatory comment) along with both `archive_target_unmounted` /
  `fence_patch_item(...pipeline_error...)` finding branches — nothing left
  that can be "unreachable" since staging is always local disk.
- Removed now-unused imports (`os`, `tgw.logging as tgw_logging`,
  `tgw.apis.fence.patch_item as fence_patch_item`) — all three were only
  used by the removed guard/finding code.
- Updated the module docstring's workflow step 3 and both archive-block
  comments to describe the new always-local behavior instead of the old
  drive-mount-conditional one.
- Updated `tests/test_alt_text.py`: two tests' expected paths moved from
  `history/ItemData/<sku>/` to `history-staging/<sku>/`;
  `test_broken_history_symlink_does_not_crash` (cmd_alt_text) and
  `test_apply_alt_text_result_broken_history_symlink_does_not_crash`
  (batch-apply path) rewritten to prove a dangling `history` symlink no
  longer affects anything (archive now succeeds normally, writing to
  staging, and the symlink itself is left untouched — old behavior of
  deferring with a C11 finding is gone); `test_history_root_reachable_helper`
  removed (function no longer exists); the "history reachable" control-case
  test kept but simplified since there's no guard branch left to control
  for.
- Did not touch the `history` symlink, `/media/tgw/MasterArchive`, the
  future sweep/merge job, the `-alt.jpg` companion-derivative logic, or the
  `store_hash`/`lookup_hash` dedup cache — all explicitly out of scope.
- Did not touch `ai_identify.py` or anything outside `alt_text.py` (and its
  test file).

Out-of-scope findings filed: none. (The two pre-existing C12 allowlist
test failures are a known, unrelated pre-existing issue in `ai_identify.py`
— not something newly discovered by this packet's work; leaving it for
whoever owns that file's line-number drift rather than filing a redundant
todo, since it reproduces identically on the base branch untouched.)
