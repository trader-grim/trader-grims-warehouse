# DONE — code-review follow-ups on todo #1239

`/code-review` (medium effort, 8 finder angles + verification) on the
uncommitted #1239 work (the new `_cache_io.py` module and its wiring into
`specifics.py`/`taxonomy.py`) surfaced 3 confirmed findings, all traced to
the same root cause: the module rolled its own tmp+rename atomic write
instead of reusing `tgw.catalog.atomic_write_json()`, which already exists
for exactly this class of file (non-item catalog/cache JSON).

## Fix 1 — permission-mode regression (reproduced live)
The hand-rolled `atomic_write_cache_json()` didn't preserve the target
file's existing mode — `NamedTemporaryFile` always creates at 0600, and a
plain rename carries the temp file's mode, not the destination's. Confirmed
live: the real production caches are 0644; a reproduction (chmod 0644 →
call the old helper → result 0600) showed the very next cache write would
have silently narrowed them to owner-only, breaking any non-tgw reader
(health checks, recoll indexer, a future confined worker per todo #1253).

Fixed at the root: `_cache_io.py` no longer implements its own atomic
write. `locked_merge_cache_json()` now delegates to
`tgw.catalog.atomic_write_json()` (which already handles mode preservation
via `_existing_mode_or_default()`), and `taxonomy.py`/`specifics.py`'s
single-value cache writes now call that same helper directly
(`from tgw.catalog import atomic_write_json as _atomic_write_cache_json`,
`pretty=False` to keep the large aspects cache compact). This removes a
fourth independent tmp+rename implementation from the tree rather than
adding one.

## Fix 2 — bulk_refresh_aspects()'s shard writes were the one unconverted site
`bulk_refresh_aspects()` writes ~15,000 per-category shard files
(`ebay-aspects-bulk/<cid>.json`) and was left as a plain `write_text()` —
the one write site in the same file #1239 didn't cover. A crash mid-loop
corrupts a shard; `_load_bulk_shard()`'s silent catch-and-fall-through then
burns a live per-category Taxonomy call (5,000/day pool) instead of using
the bulk shard (100/day pool) — defeating the point of the bulk download.
Now routed through the same `atomic_write_json(..., pretty=False)` call.

## Fix 3 — orphaned tmp files on write failure
`tgw.catalog.atomic_write_json()` itself (the helper now being reused
everywhere) had no cleanup on a failed write — `NamedTemporaryFile(delete=
False)` never auto-removes on an exception (ENOSPC, non-serializable
value), so failed writes leaked tmp files into the target directory
indefinitely. Wrapped the write in try/except to unlink the tmp file on any
failure before re-raising. Fixing it once here benefits every caller of
this helper (catalogs, both eBay caches), not just the new code.

## Tests
- `tests/test_catalog_atomic_write_perms.py`: added
  `test_json_write_failure_does_not_leak_tmp_file` (fix 3's regression case).
- `tests/test_cache_io.py`: rewritten — removed the obsolete
  `atomic_write_cache_json` tests (function moved), added
  `test_preserves_existing_file_mode_through_merge` (fix 1's regression
  case via the merge path) and `test_no_leftover_tmp_files_after_write`.
- `tests/test_specifics_aspects_cache.py`: added
  `TestBulkRefreshAspectsAtomicity` (fix 2's regression case — shards are
  valid JSON, no leftover tmp files).

`pytest -q tests/test_catalog_atomic_write_perms.py tests/test_cache_io.py
tests/test_specifics_aspects_cache.py tests/test_tree_id_resilience.py`: all
pass. Full suite: 1991 passed, 1 skipped, 2 failed (both
pre-existing/unrelated in `test_invariants_pricing.py`).

## Live verification (against copies of real production caches; originals untouched)
- Copied the real 13.6 MB / 350-entry `ebay-aspects-cache.json`, chmod'd to
  0644, ran `locked_merge_cache_json` on the copy: mode preserved at 0644
  (previously would have become 0600), all 350 entries preserved plus the
  new one.
- Same test against a copy of the real `ebay-category-tree-id.json` via the
  now-shared `atomic_write_json(..., pretty=False)` call: mode preserved at
  0644.

No deviations. No config/secrets/OAuth scopes touched; production cache
files were copied for testing, never written to directly.
