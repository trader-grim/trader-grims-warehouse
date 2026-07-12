# DONE — todo #1239 (audit#1143 MERGED #1179+#1180)

Two eBay API disk caches did unlocked, non-atomic read-modify-write:

- `specifics.py:190` — `get_aspects()`'s per-category aspects cache
  (`ebay-aspects-cache.json`, shared across `ebay_draft`/`ebay_sync`/
  `tgw-http`): read the whole dict, added one entry, wrote the whole dict
  back with a plain `write_text` — two concurrent cache-miss writers could
  race and silently drop each other's new entries, and a crash mid-write
  could corrupt the entire cache (every category, not just the one being
  written).
- `taxonomy.py:86` (+ two more sites at ~199 and ~215) — the category tree
  ID cache and the full category-tree caches (shared by both the EBAY_US
  tree and the Motors tree via `_load_or_fetch_tree`/`_fetch_tree_live`):
  same plain `write_text`, no atomic tmp+rename. Corruption would silently
  re-trigger live Taxonomy API calls on every subsequent read — the exact
  quota-exhaustion failure mode these caches exist to prevent.

## Fix
New shared module `src/tgw/apis/ebay/_cache_io.py` (stdlib-only, mirroring
the existing `_token_io.py` naming convention for shared eBay-API-adjacent
IO helpers), with two entry points matching each cache's actual risk
profile:

- `atomic_write_cache_json(path, data)` — tmp+rename atomic write for
  single-value caches (tree ID, tree data). No lock needed for correctness
  here (each write fully overwrites with a freshly fetched value — no
  merge, so last-write-wins is safe); the fix is purely about eliminating
  the partial-write corruption risk. Used at all 3 `taxonomy.py` write
  sites (`get_category_tree_id`, `_load_or_fetch_tree`, `_fetch_tree_live`)
  — since the latter two are the shared core for BOTH the EBAY_US and
  Motors tree caches, this one change covers all 4 tree-cache write paths
  in the file.
- `locked_merge_cache_json(path, merge)` — holds an exclusive `flock` on a
  `<path>.lock` sidecar across a **fresh** read+merge+atomic-write cycle, so
  concurrent writers merge instead of racing to overwrite each other. The
  live API fetch itself happens *before* acquiring the lock (outside it),
  so concurrent live calls for different categories aren't serialized —
  only the disk merge is. Used in `specifics.py`'s `get_aspects()`.

`bulk_refresh_aspects()`'s per-category shard writes were left untouched —
each shard is a single-value write to its own separate file (no shared dict
to race), and it's only ever run single-process via the
`tgw warm-ebay-aspects` CLI, so it's outside this bug's actual blast radius.

## Tests
- New `tests/test_cache_io.py` (9 tests): atomic write correctness (valid
  JSON, full overwrite, no leftover tmp files, parent dir creation), and
  the locked merge helper (creates from empty, merges into existing
  entries without dropping them, sequential writers each preserved,
  recovers from a corrupt existing file instead of crashing, lock file
  created alongside the cache).
- New `tests/test_specifics_aspects_cache.py` (3 tests): `get_aspects()`
  persists to disk; two different categories across separate "process"
  calls both persist (the regression case for #1239 — proves the merge
  path, not last-write-wins); a disk-cache hit avoids a live call.
- Existing `tests/test_tree_id_resilience.py`, `test_category_tree.py`,
  `test_motors_category_tree.py`, and 4 other taxonomy/specifics-adjacent
  test files (79 tests total) all still pass unchanged — confirms the
  atomic-write swap didn't change any observable cache behavior.

`pytest -q tests/test_cache_io.py tests/test_specifics_aspects_cache.py
tests/test_tree_id_resilience.py tests/test_category_tree.py
tests/test_motors_category_tree.py`: all pass. Full suite: 1991 passed, 1
skipped, 2 failed (both pre-existing/unrelated in
`test_invariants_pricing.py`).

## Live verification (read-only against the real cache; a copy for the write test)
Copied the real production `/opt/TGW/data/ItemCatalog/ebay-aspects-cache.json`
(13.6 MB, 350 real category entries) to a scratch tmp path — the original
production file was never touched — and ran `locked_merge_cache_json`
against the copy: all 350 pre-existing entries were confirmed still present
after the merge, plus the new test entry, proving the fix works correctly
at real production scale and data shape.

No deviations from the todo brief. No config/secrets/OAuth scopes touched;
no live eBay Taxonomy API calls made; production cache files untouched.
