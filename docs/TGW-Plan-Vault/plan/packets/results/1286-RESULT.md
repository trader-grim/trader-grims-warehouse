# Result: 1286 catalog-check-only
Status: done
Todo: #1286   PP: PP-COHESION-001
Files touched:
- src/tgw/catalog.py (`build_all_catalogs()` — the two hardcoded
  `source='full_catalog'` / `source='search_catalog'` arguments to
  `build_search_catalog()` / `build_location_tree()` changed to
  `source='auto'`, plus an explanatory comment)
- tests/test_catalog_check_only_fresh.py (new — 2 tests covering the
  fresh-system check_only dry-run acceptance case)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1286-catalog-check-only.md
  (breadcrumb)
- docs/TGW-Plan-Vault/plan/packets/results/1286-RESULT.md (this file)

What was wrong: `build_all_catalogs(cfg, check_only=True)` unconditionally
called `build_search_catalog(cfg, source='full_catalog', check_only=True)`
and `build_location_tree(cfg, source='search_catalog', check_only=True)`.
Both `build_full_catalog()` and `build_search_catalog()` only write their
output file `if not check_only`, so on a fresh system (or any check_only
dry-run before a real build has ever run) `cfg['full_catalog_path']` and
`cfg['search_catalog_path']` never exist on disk. Forcing `source=
'full_catalog'`/`'search_catalog'` bypassed the `source='auto'` fallback
logic that both `build_search_catalog()` and `build_location_tree()`
already implement (check `*_path.exists()`, fall back down the chain to
`itemdata`), so `load_full_catalog()` / `load_search_catalog()` raised
`FileNotFoundError` instead of the preview cleanly falling back to reading
ItemData directly.

The exact fix: in `build_all_catalogs()` (src/tgw/catalog.py), changed
`build_search_catalog(cfg, source='full_catalog', check_only=check_only)`
-> `build_search_catalog(cfg, source='auto', check_only=check_only)`, and
`build_location_tree(cfg, source='search_catalog', check_only=check_only)`
-> `build_location_tree(cfg, source='auto', check_only=check_only)`. No
other function touched — `load_full_catalog()`'s own `FileNotFoundError`
guard is left in place unchanged for callers that explicitly pass a
concrete `source=` and expect the file to exist (not blanket-swallowed).
`'auto'` behaves identically to the old hardcoded values once a real
(non-check_only) build has run and written the intermediate files, so
normal (non-check_only) `build_all_catalogs()` behavior is unchanged.

Live evidence:
- `PYTHONPATH=/opt/TGW/var/worktrees/1286-catalog-check-only/src pytest -q
  tests/test_catalog_check_only_fresh.py` -> `2 passed`. Confirmed via
  `python3 -c "import tgw.catalog as c; print(c.__file__)"` that the
  worktree's own copy was under test (resolved to
  `/opt/TGW/var/worktrees/1286-catalog-check-only/src/tgw/catalog.py`, not
  the shared checkout's editable install).
- Reproduced the original bug: `git stash` on `src/tgw/catalog.py` (the fix
  only) then re-ran the same two tests -> both failed with
  `FileNotFoundError: missing full catalog: .../full_catalog.json` raised
  from `build_all_catalogs -> build_search_catalog -> load_full_catalog`,
  exactly the reported crash. `git stash pop` restored the fix.
- Full offline suite: `PYTHONPATH=/opt/TGW/var/worktrees/1286-catalog-check-only/src
  pytest -q` -> `2136 passed, 1 skipped, 1 warning in 50.25s` (2134
  pre-existing + 2 new tests, zero regressions; the 1 skip is pre-existing
  and unrelated).

Deviations from spec: none. Used `source='auto'` (the existing fallback
mechanism already coded in both downstream functions) rather than adding a
new bespoke check_only branch, per the packet's "don't over-engineer" /
"fix exactly this bug" constraint — this is the minimal change that makes
the forced-source calls behave the same as their own already-correct
`'auto'` logic.

Out-of-scope findings filed: none. No new/removed metered API calls; no
config/secrets/OAuth scope touched; catalog rebuild is still always
invoked as a job, calling convention unchanged.
