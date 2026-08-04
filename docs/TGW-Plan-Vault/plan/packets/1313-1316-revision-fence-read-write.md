# Packet: revision.py's cmd_revise/cmd_revise_apply bypass the fence on both read and write
Todo: #1313, #1316   PP: PP-COHESION-001   Track: fence-bypass batch

**Combined deliberately**: both todos are the same anti-pattern (raw
read + non-archived write) repeated identically in `cmd_revise` (line 170)
and `cmd_revise_apply` (line 425) in `src/tgw/revision.py`. One packet,
one branch, fixes both functions consistently — fixing only one would
leave the other with divergent SKU-resolution behavior.

## Context budget (ALL the model may load)
This packet + `src/tgw/revision.py` lines 1-260 (`cmd_revise`) and
425-530 (`cmd_revise_apply`) + `src/tgw/items.py`'s `get_item()` (lines
~167-190, the fence's own established fallback idiom to mirror) and
`atomic_write_json()`'s docstring/signature (~line 94) + `src/tgw/resolver.py`'s
`load_item_doc()` (line 65) and `find_current_sku()` (line 101) + this
todo's existing test file if one exists for revision.py. Nothing else —
do not read or touch `_apply_live_revision`, drift-detection, or any
other revision.py logic unrelated to the read/write of the item JSON
itself.

## Verified live before this packet was written
- Both `cmd_revise` (line 197) and `cmd_revise_apply` (line 448) compute
  `json_path = sku_json(cfg, sku)`, check `.exists()`, and on success call
  raw `json.loads(json_path.read_text(encoding="utf-8"))` — bypassing
  `resolver.load_item_doc()`/`load_item_doc_by_sku()` entirely. This means:
  (a) no SKU-alias fallback via `find_current_sku()` after a rename — a
  request for an old SKU 404s here even though `items.get_item()` and
  `resolver.load_item_doc_by_sku()` would resolve it; (b) no `sku` key
  injection if the doc is missing one (`load_item_doc()`'s other job).
- Both functions' write path — `atomic_write_json(json_path, item,
  pretty=cfg.get("pretty", True))` at line 230 and line 527 — omits
  `archive_root`, so invariant E5 (archive-before-overwrite) never fires
  for a revision-draft write or a live-apply write, unlike every other
  fence-compliant write path in this codebase.
- `items.py::get_item()` (line 167) already implements the correct
  fallback idiom this packet should mirror: compute `sku_json(cfg, sku)`,
  if missing resolve via `resolver.find_current_sku(cfg, sku)`, recompute
  the path from the resolved SKU, else `FileNotFoundError`. No existing
  helper returns both the resolved path AND the parsed doc together, so
  the fix replicates this same idiom locally (matching the established
  convention) rather than inventing a new shared helper.
- `atomic_write_json()`'s `archive_root` param (item.py line 94-108) is
  exactly `cfg.get('archive_root')` in every other correct caller in this
  codebase (confirmed pattern from #1274/#1298-1300's fixes this same
  cohesion batch) — same value to pass here.

## Spec

In **both** `cmd_revise` and `cmd_revise_apply`, replace:

```python
json_path = sku_json(cfg, sku)
if not json_path.exists():
    return {"ok": False, "error": f"item JSON not found: {json_path}"}

try:
    item = json.loads(json_path.read_text(encoding="utf-8"))
except Exception as exc:
    return {"ok": False, "error": f"failed to read item JSON: {exc}"}
```

with:

```python
json_path = sku_json(cfg, sku)
if not json_path.exists():
    from tgw.resolver import find_current_sku
    current = find_current_sku(cfg, sku)
    if current:
        json_path = sku_json(cfg, current)
    else:
        return {"ok": False, "error": f"item JSON not found: {json_path}"}

try:
    from tgw.resolver import load_item_doc
    item = load_item_doc(json_path)
except Exception as exc:
    return {"ok": False, "error": f"failed to read item JSON: {exc}"}
```

(Local imports match this file's existing style — check whether
`revision.py` already imports `resolver` at module level; if so, use that
import instead of a local one, for both functions consistently.)

Then, in both functions' `atomic_write_json(json_path, item,
pretty=cfg.get("pretty", True))` calls, add
`archive_root=cfg.get("archive_root")` as a keyword argument. Both write
call sites (line 230 in `cmd_revise`, line 527 in `cmd_revise_apply`) get
this change identically.

## Dataset
None — no ItemData content changes; this only changes how existing item
JSONs are located/read/archived-before-overwrite by these two entry
points. No schema change.

## Out of scope
- `_apply_live_revision`, drift detection (`detect_drift`,
  `_overlapping_drift`), diff formatting, or any eBay API call logic in
  `cmd_revise_apply` — untouched.
- Any change to `resolver.py`, `items.py`, or `atomic_write_json()`
  itself — this packet only changes revision.py's two call sites.
- Do not add a new shared helper that returns both path+doc together —
  out of scope for this batched fix; file a todo if you find the
  duplication (repeating the fallback dance in two functions) worth a
  future refactor, don't build it here.

## Acceptance (live)
1. Existing revision.py tests (find via `tests/test_revision*.py` or
   similar) still pass with `PYTHONPATH=<worktree>/src pytest
   tests/test_revision*.py -q`.
2. New test: call `cmd_revise` with a SKU that has been renamed (has a
   `sku_old` pointing to it, matching `find_current_sku`'s contract) using
   the OLD sku value — confirm it now resolves and succeeds instead of
   404ing with "item JSON not found". Use a temp item dir fixture, not a
   real ItemData SKU.
3. New test: call `cmd_revise` (or `cmd_revise_apply` with `dry_run=False`
   against a fixture) on an item JSON that already exists at the target
   path, with `cfg['archive_root']` pointed at a temp dir — confirm a
   zip archive of the pre-overwrite content appears in that archive_root
   after the write (matching `atomic_write_json`'s documented E5
   behavior). This is the actual regression check for the missing
   `archive_root` — do not accept "call succeeded" alone as evidence.
4. Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` — zero
   regressions.

## Quota/risk
None — no live eBay calls in scope; `cmd_revise`/`cmd_revise_apply` with
`dry_run=True` (the default) never calls `_apply_live_revision`, and this
packet doesn't touch that path at all.
