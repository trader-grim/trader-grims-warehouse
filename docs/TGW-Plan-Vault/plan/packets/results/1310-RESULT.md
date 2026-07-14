# Result: 1310 http-server-delete-asset-archive
Status: done
Todo: #1310   PP: PP-COHESION-001
Files touched: src/tgw/http_server.py, tests/test_http_server.py
Live evidence:
- New tests `test_delete_asset_archives_before_unlink` and
  `test_delete_asset_no_archive_root_still_deletes` in
  tests/test_http_server.py, run with
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src pytest -q
  tests/test_http_server.py -k delete_asset` (confirmed `tgw.http_server.__file__`
  resolved to the worktree copy before running): `2 passed, 265 deselected
  in 1.46s`.
- First test confirms: (a) `front.jpg` removed from the SKU dir after
  `DELETE /api/items/{sku}/assets/front.jpg` with `archive_root` set, (b)
  `archive_root/front.zip` exists and contains one entry named
  `front.jpg.<timestamp>` whose bytes exactly match the original photo's
  bytes (byte-for-byte zip round-trip check).
- Second test confirms: with `archive_root` unset from `_cfg`, the same
  DELETE route still returns 200 and removes `back.PNG` with no exception,
  and no `archive` directory is created anywhere under the test's tmp_path
  (no archive attempted when the null-safe guard is not satisfied).
- Full offline suite, same PYTHONPATH/LD_LIBRARY_PATH override:
  `2179 passed, 1 skipped, 1 warning in 158.01s` — zero regressions.
Deviations from spec: none — implemented exactly as specified (import
moved into the existing `from .items import atomic_write_json,
locationupdate` line, `archive_root = _cfg.get("archive_root")` guard
before `target.unlink()`, `photo_order` update logic and all other routes
left untouched).
Out-of-scope findings filed: none
