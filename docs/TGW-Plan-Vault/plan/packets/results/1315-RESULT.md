# Result: 1315 scrub-archive-root
Status: done
Todo: #1315   PP: PP-COHESION-001
Files touched: src/tgw/scrub.py, tests/test_scrub.py
Live evidence:
- Pre-flight: confirmed `scrub.py` already imports `atomic_write_json` from
  `.items` (the archive-root-aware implementation), not the archive-less
  duplicate in `catalog.py` — so this was a missing-argument bug at the 3
  call sites, not a wrong-import bug.
- Fix: `data_scrub_pass1`, `data_scrub_qty_repair`,
  `data_scrub_size_class_backfill` now call
  `atomic_write_json(path, doc, archive_root=cfg.get('archive_root'))`,
  matching the convention used at every other call site in the codebase
  (`api.py`, `alt_text.py`, `revision.py`, `items.py`).
- Added one regression test per pass in `tests/test_scrub.py`
  (`test_overwrite_is_archived`) asserting `archive_root/<sku>.zip` exists
  after a real (non-dry-run) overwrite.
- `PYTHONPATH`/`LD_LIBRARY_PATH` override confirmed pointing at this
  worktree (`tgw.scrub.__file__` resolved under
  `/opt/TGW/var/worktrees/1315-scrub-archive-root/src/tgw/scrub.py`
  before running tests).
- `pytest -q tests/test_scrub.py`: 22 passed (19 pre-existing + 3 new).
- Full offline suite `pytest -q`: 2192 passed, 1 skipped, 208.82s — no
  regressions.
Deviations from spec: none. Passed `archive_root=cfg.get('archive_root')`
(matching existing codebase convention) rather than requiring callers to
always populate `cfg['archive_root']`; when a caller's cfg lacks
`archive_root`, `atomic_write_json` treats it as None and skips archiving
(same behavior as every other `.get('archive_root')` call site in the
codebase — this fix makes these 3 passes consistent with that convention,
not a new/different one).
Out-of-scope findings filed: none — no new adjacent issues found beyond
what the packet described. (Noted but out of scope, already covered by
CLAUDE.md/plan: `catalog.py` still has its own `atomic_write_json` without
archive_root support, used for non-item catalog/digest writes per its own
docstring contract — not an E5 violation since it's documented as
non-item-covered, left untouched per packet scope.)
