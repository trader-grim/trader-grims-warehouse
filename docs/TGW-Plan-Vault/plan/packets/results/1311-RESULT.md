# Result: 1311 http-server-create-item-dedupe
Status: done
Todo: #1311   PP: PP-COHESION-001
Files touched:
- src/tgw/items.py (create_item(): added `path.parent.mkdir(parents=True, exist_ok=True)` after the existing-item check, before the write)
- src/tgw/http_server.py (create_item_endpoint(): replaced inline item_dir/json_path construction, `.exists()` check, `mkdir()`, and `atomic_write_json()` call with a single `create_item(_cfg, body.sku, body.data)` call, catching `FileExistsError` and translating to the existing `HTTPException(409, detail=f"sku already exists: {body.sku}")`; added `create_item` to the `from .items import ...` line. `_SKU_RE` format check and the `enqueue_catalog_rebuild` try/except left untouched, exactly as specced.)
- tests/test_items.py (new `test_create_item_creates_parent_dir`, `test_create_item_existing_sku_raises`; imported `create_item`)
- tests/test_http_server.py (new `test_create_item_endpoint_writes_via_fence`, `test_create_item_endpoint_duplicate_sku_409`, `test_create_item_endpoint_bad_sku_format_400`)

Live evidence:
- `python3 -c "import tgw.items; print(tgw.items.__file__)"` (worktree PYTHONPATH)
  confirmed resolving to
  `/opt/TGW/var/worktrees/1311-http-server-create-item-dedupe/src/tgw/items.py`
  — verified testing the worktree's copy, not the shared checkout.
- Targeted run: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src pytest -q tests/test_items.py tests/test_http_server.py`
  → `281 passed, 1 warning in 5.82s`
- Full offline suite: `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src pytest -q`
  → `2182 passed, 1 skipped, 1 warning in 160.67s (0:02:40)` — zero regressions.
- Acceptance item 1 (mkdir gap): confirmed via reading `create_item()`'s body
  before the fix — it went straight to `atomic_write_json(path, ...)` with no
  mkdir call, and `_atomic_write` gives no evidence of creating parent dirs.
  Prior to this fix, calling `items.create_item()` directly (bypassing the
  HTTP layer, e.g. from a script or worker) on a SKU whose parent directory
  didn't yet exist would have raised `FileNotFoundError` from the
  open()/rename() inside `_atomic_write` — this path was previously
  UNTESTED (no existing test called `create_item()` on a fresh directory;
  all existing tests in test_items.py pre-created the item dir via
  `make_item()`). New test `test_create_item_creates_parent_dir` covers
  this and passes post-fix.
- Acceptance item 2 (endpoint duplicate/create): new
  `test_create_item_endpoint_writes_via_fence` (200, item JSON written,
  catalog_rebuild enqueued) and `test_create_item_endpoint_duplicate_sku_409`
  (second POST of the same SKU → 409 with `detail` containing the SKU,
  original doc unchanged) both pass, exercised via FastAPI TestClient
  against the existing `env`/`client` fixtures (fixture `itemdata_root`,
  no production data touched).

Deviations from spec: none.

Out-of-scope findings filed: none — no new adjacent issues surfaced during
this task (the `state_machine.enqueue_catalog_rebuild` swallow, `archive_root`,
and other routes were correctly left untouched per the packet's Out of
scope list).
