# Result: 1523 revision-apply-empty-aspect-fix
Status: done
Todo: #1523   PP: PP-LISTEDITOR-001

Files touched:
- src/tgw/revision.py
- tests/test_revision.py

Live evidence:
- Pre-flight read confirmed `src/tgw/ebay/sync.py`'s `_build_offer_bodies()`
  (lines ~488-491) already applies the `if v not in (None, '')` omission
  rule for cleared aspects, with a comment tying it to invariant C14 /
  todo #1462 (the eBay errorId 25002 garbled-error incident).
- `src/tgw/revision.py`'s `_place_delta_in_bodies` (item_specifics/aspects
  branch, ~line 287) did NOT have this filter — confirmed by reading the
  code before editing (was building `product["aspects"]` from all
  `val.items()` unconditionally).
- Fix applied: added `if v not in (None, "")` to the dict comprehension in
  `_place_delta_in_bodies`, mirroring `_build_offer_bodies` exactly, plus
  an explanatory comment referencing C14/#1462/#1523/#1468.
- Removed the `@pytest.mark.xfail(strict=True, ...)` marker from
  `TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value`
  in tests/test_revision.py (test body itself was unchanged — it was
  already the correct regression test, just marked xfail pending this fix).
- Ran the specific regression test standalone (worktree-isolated
  PYTHONPATH/LD_LIBRARY_PATH, confirmed `tgw.revision.__file__` resolved
  under the worktree, not the shared checkout):
  ```
  tests/test_revision.py::TestLiveApply::test_c14_aspects_delta_clear_omits_key_not_blank_value PASSED
  1 passed, 77 deselected in 0.36s
  ```
- Ran the full pytest suite (same worktree-isolated env, 2546 collected):
  ```
  2545 passed, 1 skipped, 1 xfailed, 2 warnings in 241.96s (0:04:01)
  ```
  The 1 remaining xfail is unrelated pre-existing (not the C14 test, which
  now passes and is unmarked). No regressions.

Deviations from spec: none. Fix is a one-line filter addition mirroring
`_build_offer_bodies`'s existing pattern exactly, as specified, plus
removal of the now-satisfied `xfail` marker as the packet's Acceptance
section explicitly called for.

Out-of-scope findings filed: none — no new friction encountered beyond
the already-documented worktree PYTHONPATH/LD_LIBRARY_PATH override,
which is pre-existing standing guidance (tgw-coder profile contract), not
a new finding.
