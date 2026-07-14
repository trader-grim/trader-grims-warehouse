# Packet: itemdata_scrub.py hand-builds ItemData paths + raw reads instead of fence helpers
Todo: #1305   PP: PP-COHESION-001   Track: fence-bypass batch

## Context budget (ALL the model may load)
This packet + `src/tgw/workers/itemdata_scrub.py` (all of it, ~190 lines)
+ `src/tgw/config.py`'s `sku_dir()`/`sku_json()`/`_safe_segment()` (the
shared path-building/validation helpers) + `src/tgw/resolver.py`'s
`load_item_doc()`/`find_current_sku()` + `src/tgw/items.py`'s
`strip_fields()` (read only, to confirm scope boundary — not called) +
existing test file `tests/test_audit1143_workers_cohesion.py` (the
`itemdata_scrub.py` SKU/root validation tests, ~lines 113-190). Nothing
else — do not touch `tools/itemdata_scrub.py` (a separate, simpler
standalone script, not this worker) or the queue/dequeue model (todo
#1261, deliberately deferred, out of scope here).

## Verified live before this packet was written
- `derive_item_path()` (line 87) hand-builds
  `Path(itemdata_root) / sku / f"{sku}.json"` instead of using the
  canonical `config.sku_dir()`/`sku_json()` helpers used everywhere else
  in the fence (confirmed via `revision.py`, `items.py`, `http_server.py`,
  and this same cohesion batch's #1312 fix to `mcp_server.py`).
- `process_queue_job()` (line 126) validates the SKU with a locally
  duplicated `_is_safe_sku()` regex-free character check instead of the
  canonical `_safe_segment()` validator (`config._safe_segment`, used by
  `sku_dir()`/`sku_json()` internally) — two independently-maintained
  safety checks for the same thing, a drift risk (mirrors the exact
  pattern already fixed for `bundle_intake.py`/`ebay_draft.py`/etc. per
  `tests/test_audit1143_workers_cohesion.py`'s docstring).
- `process_queue_job()` (line 141) reads the item JSON with raw
  `json.loads(file_path.read_text())` instead of
  `resolver.load_item_doc()` (which injects `sku` from the directory name
  if absent — same idiom `items.get_item()` and `revision.py`'s
  `cmd_revise`/`cmd_revise_apply` already use) and has no `sku_old` alias
  fallback via `resolver.find_current_sku()` for a renamed item's old SKU
  (same gap already fixed in `revision.py` for #1313/#1316 and
  `mcp_server.py` for #1312, same batch).
- The write side (`atomic_write_json(file_path, cleaned_data,
  archive_root=archive_root, sort_keys=True)`, line 144) already passes
  `archive_root` — invariant E5 (archive-before-overwrite) is already
  honored here, confirmed live-checked, not a finding, no change needed.
- The recursive, pattern-driven key-deletion write itself (`scrub_value`)
  has **no equivalent in `items.py`'s fence API** — `items.strip_fields()`
  only removes a flat list of top-level field names in one call, it does
  not recurse into nested dicts/lists. The production rules file
  (`/opt/TGW/config/queue-workers/itemdata_scrub_denylist.json`) has
  `"recursive": true` live-configured, so this is a real, currently-used
  capability, not dead code to simplify away. Redesigning the fence to
  support recursive/pattern-based deletion is a real fence-API change,
  out of scope for a mechanical cohesion-batch fix (matches the
  already-documented in-file comment, lines 13-17, and the same reasoning
  already applied to defer the queue-model migration as todo #1261). This
  packet does NOT touch `scrub_value()`/`scrub_itemdata()` or the
  `atomic_write_json` call itself.

## Spec

### Path construction
Replace `derive_item_path(itemdata_root, sku)` with calls to
`config.sku_dir(cfg, sku)` / `config.sku_json(cfg, sku)`, where `cfg` is
built as `{"itemdata_root": root_dir}` (the worker has no full
`tgw-api-config.json` load today — do not add one, just wrap `root_dir`
in the minimal dict shape these helpers need). This also replaces the
locally duplicated `_is_safe_sku()` check: catch the `ValueError` that
`config._safe_segment()` raises (via `sku_json()`) for an unsafe segment
and treat it as the existing "reject unsafe SKU" path (same log line,
same `return False`, same test-observable behavior for `..`, `/`, `\\`).
Keep `derive_item_path()`'s name/signature as a thin wrapper if useful for
callers, but its body must delegate to `config.sku_json()` — no
independent path-join logic left in this file.

### Read
Replace `json.loads(file_path.read_text(encoding='utf-8'))` with
`resolver.load_item_doc(file_path)`. Before giving up on a missing file,
add the same alias-fallback dance already used in `revision.py`
(#1313/#1316) and `mcp_server.py` (#1312): if the initial `sku_json(cfg,
sku)` path doesn't exist, call `resolver.find_current_sku(cfg, sku)`; if
it resolves, recompute the path via `sku_json(cfg, current)` before
concluding "not found."

### Imports
Add `from . import config` (or `from .config import sku_dir, sku_json`)
and `from .resolver import find_current_sku, load_item_doc` at module
level — check current imports first, this module currently only imports
`atomic_write_json` from `tgw.items`.

## Dataset
None — no ItemData schema change; this only changes path
resolution/read mechanics for an already-existing worker, and only for
its non-recursive parts (path build + read). No change to what gets
written or when.

## Out of scope
- `tools/itemdata_scrub.py` — a different, standalone file, not this
  worker.
- The recursive/pattern-based key-deletion write itself
  (`scrub_value`/`scrub_itemdata`/the `atomic_write_json` call) — no fence
  equivalent exists; a real fence-API redesign, not a mechanical fix.
- The file-based `queue_dir = Path.cwd()` dequeue model in `main()` — this
  is todo #1261, already scoped separately as "needs a real scoping pass,"
  not a batched cleanup item.
- Any change to `ScrubRules`, `preserve_keys`/`remove_keys`/
  `remove_patterns` semantics, or the production denylist config.

## Acceptance (live)
1. Existing tests in `tests/test_audit1143_workers_cohesion.py`
   (`test_itemdata_scrub_ignores_root_override_in_job_content`,
   `test_itemdata_scrub_rejects_unsafe_sku_with_dotdot`,
   `test_itemdata_scrub_rejects_unsafe_sku_with_slash`,
   `test_itemdata_scrub_still_processes_valid_sku_from_job_content`) all
   still pass unchanged — confirms behavior-identical for the existing
   safe/unsafe-SKU cases.
2. New test: a renamed item (fixture with `sku_old` set, queue job
   content names the OLD sku) now resolves via alias fallback and gets
   scrubbed, instead of failing "Target data file ... not found" —
   matching `revision.py`'s and `mcp_server.py`'s already-fixed behavior
   for the same class of gap.
3. New test: confirm `derive_item_path`/the new path-building call
   produces byte-identical `Path` objects to the old hand-built
   `Path(root) / sku / f"{sku}.json"` for a normal SKU (no accidental
   path-shape change for the common case).
4. Full offline suite: `PYTHONPATH=<worktree>/src
   LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH pytest -q` — zero regressions.

## Quota/risk
None — no live eBay/queue side effects; this worker has no installed
systemd unit and is not currently scheduled (verified 2026-07-10, still
true), so this fix has zero production traffic exposure until/unless
#1261's queue-migration work reactivates it. Fixture SKUs only for new
tests.
