# Result: 1309 ready-fence-bypass
Status: done
Todo: #1309   PP: PP-COHESION-001

Files touched:
- src/tgw/ready.py
- tests/test_ready.py

What was wrong: `ready_pool()` in `src/tgw/ready.py` constructed ItemData
paths inline (`child / f'{child.name}.json'` from a raw `root.iterdir()`
scan) and read each item's JSON directly with `json.loads(jf.read_text(...))`,
bypassing the tgw-api fence (invariant A4 — "tgw-api is the fence... never
construct paths directly"). This skipped the fence's benefits: consistent
duplicate-key JSON validation, sku-from-doc resolution, and alignment with
every other reader in the codebase (`catalog.py` etc., which all use
`find_item_jsons()` + `load_item_doc()`).

Exact fix: `ready_pool()` now calls `find_item_jsons(cfg)` (from
`tgw.resolver`) to enumerate item JSON paths and `load_item_doc(json_path)`
(already imported in `ready.py` from `tgw.items`, itself re-exported from
`tgw.resolver`) to load each doc — the same pattern `catalog.py` uses.
`json_path.parent.name` is kept only as the doc-injection fallback (already
built into `load_item_doc`), and the pool's `sku` field is now taken from
`doc.get('sku')` (falling back to the directory name only if absent) rather
than always trusting the directory name. Removed the now-unused `import json`.

Behavior change (beneficial side effect, not suppressed): items whose JSON
`sku` field diverges from their (stale) directory name — the state a
renamed-but-not-yet-relocated item can be in — are now reported in the pool
under their canonical sku instead of the stale directory name, matching
`load_item_doc`'s standard sku-resolution behavior used everywhere else.
Function signature/return shape and all other behavior (filter conditions,
sort order, `dole_batch_size`, `set_ready`/`unset_ready`/`cmd_ready`) are
unchanged — those already went through the fence and weren't touched.

Test added: `test_ready_pool_uses_canonical_sku_from_doc_not_dir_name` in
`tests/test_ready.py` — writes an item whose JSON `sku` field diverges from
its directory name (simulating a rename recorded in-doc) and confirms
`ready_pool()` reports the canonical (doc) sku, not the stale directory
name — a case the old raw-read implementation would have gotten wrong.
Existing `test_ready_pool_filters_and_sorts_oldest_first` and the rest of
`tests/test_ready.py` (15 tests total) continue to pass unmodified,
confirming no behavior regression on the basic case.

Live evidence (pytest, PYTHONPATH pinned to worktree, confirmed
`tgw.ready.__file__` resolves under the worktree path before running):
- `tests/test_ready.py`: 15 passed
- Full suite (`pytest -q`): 2151 passed, 1 skipped, 1 failed — the single
  failure is `tests/test_llm_google_direct.py::TestCallModelGoogleDirectDispatch::test_success_does_not_touch_openrouter`,
  the pre-existing known flake tracked as todo #1370 (shared quota-state
  pollution across the full-suite run; unrelated to ready.py/items.py/
  resolver.py). No other failures.

Deviations from spec: none. `find_item_jsons()`/`load_item_doc()` (from
`tgw.resolver`, re-exported via `tgw.items`) were used as the fenced
equivalent, exactly as the packet anticipated ("likely `find_item_jsons(cfg)`
... combined with `load_item_doc()`").

Out-of-scope findings filed: none — no new friction or adjacent bugs
surfaced during this fix.
