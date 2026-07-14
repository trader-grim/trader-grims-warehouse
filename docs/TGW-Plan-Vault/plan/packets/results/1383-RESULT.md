# Result: 1383 photo-history-recovery-tools-atomic-copy
Status: done
Todo: #1383   PP: PP-COHESION-001
Files touched: tools/photo_history_recovery.py, tests/test_photo_history_recovery_tools_atomic_copy.py (added by reviewer — see note below)

Reviewer addition (2026-07-14): the packet's own acceptance criteria were
satisfied via manual verification, but no automated regression test existed
for `recover_item()`'s atomic-copy behavior specifically — unlike the
sibling worker-side #1307 fix, which has 3 dedicated tests
(`test_ensure_copy_leaves_no_tmp_file_behind_on_success` /
`test_ensure_copy_uses_temp_file_then_atomic_replace` /
`test_ensure_copy_cleans_up_temp_file_on_copy_failure`). Reviewer added the
mirrored 3 tests for `recover_item()` in
`tests/test_photo_history_recovery_tools_atomic_copy.py` before stitching,
closing that gap. All 3 pass; full suite re-run at 2200 passed (2197 + 3
new), 1 skipped, zero regressions.

Live evidence:
- `recover_item()` now writes via a temp file (`dest.with_name(dest.name + f'.tmp{os.getpid()}')`)
  + `shutil.copy2(src, tmp_dest)` + `os.replace(tmp_dest, dest)`, matching the #1307 pattern in
  `src/tgw/workers/photo_history_recovery.py::ensure_copy()` exactly, adapted to `recover_item()`'s
  existing try/except-around-copy + `rows.append({'action': 'error', ...})` shape.
- Manual verification (acceptance step 2, real tmp dir + fixture photo):
  ```
  STEP2 PASS: bytes match, no tmp file left: /tmp/.../ItemData/tgwTEST1/photo.jpg
  {'sku': 'tgwTEST1', 'ref': 'photo.jpg', 'action': 'copied',
   'source': '/tmp/.../history/photo.jpg', 'dest': '/tmp/.../ItemData/tgwTEST1/photo.jpg',
   'all_matches': ['/tmp/.../history/photo.jpg']}
  ```
  dest bytes matched src bytes exactly; `item_dir.iterdir()` after the run contained only the
  final `photo.jpg`, no `.tmp<pid>` file.
- Manual verification (acceptance step 3, `shutil.copy2` monkeypatched to raise `OSError('disk
  full (simulated)')`):
  ```
  SKU tgwTEST2: copy failed /tmp/.../history/photo2.jpg -> /tmp/.../ItemData/tgwTEST2/photo2.jpg: disk full (simulated)
  STEP3 PASS: no partial file, no tmp file, error row appended:
  {'sku': 'tgwTEST2', 'ref': 'photo2.jpg', 'action': 'error',
   'source': '/tmp/.../history/photo2.jpg', 'dest': '/tmp/.../ItemData/tgwTEST2/photo2.jpg',
   'error': 'disk full (simulated)'}
  ```
  `dest.exists()` was False, `item_dir` was fully empty (no stray `.tmp<pid>` file), and exactly
  one `'error'` row was appended — same contract as before the fix.
- Existing sibling test file `tests/test_photo_history_recovery_dry_run.py` (covers the already-
  merged #1307 worker-side fix, not this tools/ file directly — no existing tests target
  `tools/photo_history_recovery.py::recover_item()` specifically): `9 passed in 0.72s`.
- Full offline suite (`PYTHONPATH=<worktree>/src`, `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH`,
  confirmed `phr.__file__` resolved under the worktree path before running):
  `2197 passed, 1 skipped, 1 warning in 235.19s (0:03:55)` — zero regressions, zero failures.

Deviations from spec: none — copy-block replacement matches the packet's exact spec text
verbatim (temp file named `dest.name + f'.tmp{os.getpid()}'`, `shutil.copy2` to tmp,
`os.replace` onto `dest`, cleanup + same `'error'` row shape on failure, `continue` preserved).

Out-of-scope findings filed: none — no new findings surfaced; `src/tgw/workers/photo_history_recovery.py`
was read-only per the packet's context budget and not touched; no other copy/write site in
`tools/photo_history_recovery.py` was touched.
