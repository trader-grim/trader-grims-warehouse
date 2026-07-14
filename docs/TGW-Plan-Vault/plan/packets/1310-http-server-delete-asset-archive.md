# Packet: http_server.py DELETE /api/items/{sku}/assets/{filename} deletes a photo with no archive step
Todo: #1310   PP: PP-COHESION-001   Track: fence-bypass batch

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py`'s `delete_asset()` (~line 2282,
the whole function) and its one caller context (the `@app.delete` route
decorator immediately above it) + `src/tgw/items.py`'s
`_archive_before_overwrite()` (line 48) and `atomic_write_json()`'s
`archive_root` docstring (line 94) for the established pattern + this
route's existing test file if one exists. Nothing else — do not read or
touch other routes in http_server.py.

## Verified live before this packet was written
- `delete_asset()` (line 2282) resolves the target photo path, path-
  traversal-checks it, then calls `target.unlink()` directly (line 2295)
  — the file is gone with zero archive step, unlike every JSON write path
  in this codebase which archives via `archive_root` before a destructive
  overwrite (invariant E5).
- `items.py::_archive_before_overwrite(archive_root, path)` (line 48) is
  generic — it zips whatever file is at `path` into
  `archive_root/<path.stem>.zip`, not JSON-specific (confirmed by reading
  its body: `zf.write(path, arcname=...)` on the raw file). It is safe to
  reuse for a photo file, not just item JSONs.
- `http_server.py` already imports from `items.py` at module level (line
  37: `from .items import atomic_write_json, locationupdate`) — add
  `_archive_before_overwrite` to that same import line rather than a new
  import statement.
- `_cfg['archive_root']` is the established config key used by every other
  archive-before-overwrite call site in this file (confirmed via
  `bulk_apply`'s `atomic_write_json(..., archive_root=_cfg.get("archive_root"))`
  a few functions away).

## Spec
In `delete_asset()`, immediately before `target.unlink()` (line 2295),
add:

```python
from .items import _archive_before_overwrite
archive_root = _cfg.get("archive_root")
if archive_root:
    _archive_before_overwrite(archive_root, target)
target.unlink()
```

(Move the import to the existing module-level `from .items import
atomic_write_json, locationupdate` line instead of a local import, to
match the file's existing style — add `_archive_before_overwrite` to that
tuple.)

Guard on `if archive_root:` (not unconditional) because `_archive_before_overwrite`
itself has no such guard and would raise on a `None` archive_root — match
`atomic_write_json`'s own null-safe pattern (it only archives `if
archive_root is not None and path.exists()`), don't introduce a new
failure mode for configs without `archive_root` set.

## Dataset
None beyond photos already covered by invariant E5's existing archive
mechanism — this only adds an archive step before an existing delete
path, no new data is collected.

## Out of scope
- `photo_order` update logic (the `_apply_patch` call after `unlink()`) —
  untouched.
- Any other route in http_server.py.
- Promoting `_archive_before_overwrite` to a public (non-underscore) name
  — out of scope; import it as-is across the module boundary.

## Acceptance (live)
1. New test: call `delete_asset` (via the FastAPI test client or direct
   function call) against a fixture SKU dir with `archive_root` set to a
   temp dir and a real photo file present — confirm after the call: (a)
   the photo file is gone from the SKU dir, (b) a zip matching the
   photo's stem exists in the temp archive_root and contains the
   original photo bytes (open the zip, compare content).
2. New test: same setup but with `archive_root` unset/None on the config
   — confirm the delete still succeeds (no exception) and no archive
   step is attempted, matching the null-safe guard.
3. Existing http_server.py asset-route tests, if any, still pass.
4. Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` — zero
   regressions.

## Quota/risk
None — no live eBay calls, no production data touched; test-fixture SKUs
only.
