# INPROGRESS: todo #1286 (PP-COHESION-001) — catalog check_only fresh-system crash

Fixed `build_all_catalogs(check_only=True)` raising `FileNotFoundError` on a
fresh system with no prior full_catalog file. Root cause:
`build_all_catalogs()` forced `source='full_catalog'` /
`source='search_catalog'` on the downstream `build_search_catalog()` /
`build_location_tree()` calls regardless of `check_only`, but check_only
mode never writes the upstream file, so a fresh-system preview had nothing
to load. Fixed by switching both calls to `source='auto'`, which already
implements the correct full_catalog -> search_catalog -> itemdata fallback
chain. Added `tests/test_catalog_check_only_fresh.py` (2 tests, confirmed
to reproduce the bug pre-fix via `git stash`). Full offline suite green:
2136 passed, 1 skipped. Committed on `todo/1286-catalog-check-only`. Result
manifest written to
`docs/TGW-Plan-Vault/plan/packets/results/1286-RESULT.md`. Done — not
merging/stitching, that's a separate step.
