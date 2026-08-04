# Packet: http_server.py create_item_endpoint duplicates items.create_item's logic inline
Todo: #1311   PP: PP-COHESION-001   Track: fence-bypass batch

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py`'s `create_item_endpoint()` (~line
1034, the whole function, plus the `_SKU_RE` constant immediately above
it) + `src/tgw/items.py`'s `create_item()` (line 150) and `sku_json()`
(the path-building helper it uses internally) + this route's existing
test file if one exists. Nothing else.

## Verified live before this packet was written
- `create_item_endpoint()` (line 1034) does its own SKU-format validation
  (`_SKU_RE.match`), builds `item_dir`/`json_path` inline from
  `_cfg["itemdata_root"]`, checks `json_path.exists()` itself, calls
  `item_dir.mkdir(parents=True, exist_ok=True)` itself, and calls
  `atomic_write_json(json_path, record, pretty=...)` itself — duplicating
  every step `items.create_item(cfg, sku, data)` (line 150) already does,
  except the mkdir (which `create_item()` doesn't currently do — see
  Spec).
- `items.create_item()` raises `FileExistsError` on an existing SKU;
  `create_item_endpoint()` currently raises `HTTPException(409, ...)` for
  the same case — the endpoint needs to keep translating to an
  HTTPException, just via a caught exception from the fence function
  instead of its own inline `.exists()` check.
- `items.create_item()` does NOT currently create the parent directory
  (`item_dir.mkdir(...)`) before writing — only the endpoint's inline
  version does this. Confirmed by reading `create_item()`'s body: it goes
  straight to `atomic_write_json(path, record, ...)` with no mkdir.
  `atomic_write_json` → `_atomic_write` writes to `path`, which requires
  the parent directory to already exist (no evidence in `_atomic_write`
  of it creating parents). This is a real behavior gap in the fence
  function itself, not just a duplication — see Spec.

## Spec
1. In `items.py::create_item()`, add `path.parent.mkdir(parents=True,
   exist_ok=True)` before the `atomic_write_json` call (after the
   `path.exists()` check, so an existing item still raises
   `FileExistsError` before any directory operation) — this closes the
   real behavior gap so the fence function is a complete drop-in
   replacement for what the endpoint currently does.
2. In `http_server.py::create_item_endpoint()`, replace the inline
   `item_dir = ...`, `json_path = ...`, `.exists()` check,
   `item_dir.mkdir(...)`, and `atomic_write_json(...)` calls with a single
   call to `items.create_item(_cfg, body.sku, body.data)`, wrapped to
   translate `FileExistsError` into the existing `HTTPException(409,
   detail=f"sku already exists: {body.sku}")`. Keep the `_SKU_RE` format
   validation in the endpoint (that's HTTP-layer input validation, not
   fence logic — out of scope to move). The function should end up
   needing `from .items import create_item` added to the existing
   `from .items import atomic_write_json, locationupdate` import line.
3. Keep the endpoint's `state_machine.enqueue_catalog_rebuild(...)` call
   and its `try/except: pass` swallow exactly as-is — untouched,
   unrelated to this fix.

## Dataset
None — no schema change; this only changes which function performs an
already-existing write path.

## Out of scope
- The `state_machine.enqueue_catalog_rebuild` call or its exception
  handling.
- `archive_root` — not applicable here (item creation, not overwrite);
  do not add it to `create_item()`'s write call.
- Any other route in http_server.py.

## Acceptance (live)
1. New test in `items.py`'s test file: `create_item()` on a SKU whose
   parent directory doesn't yet exist (fresh temp `itemdata_root`) —
   confirm it succeeds and creates the directory (previously this would
   have raised `FileNotFoundError` from the underlying write, or been
   untested — check which and note in the result manifest).
2. New/updated test for `create_item_endpoint`: POST a new SKU — 200/ok,
   item JSON written correctly. POST the same SKU again — 409 with the
   existing error detail message. Both via the FastAPI test client
   against a fixture `itemdata_root`, not production data.
3. Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` — zero
   regressions.

## Quota/risk
None — no live eBay calls; fixture SKUs only, never touching real
ItemData.
