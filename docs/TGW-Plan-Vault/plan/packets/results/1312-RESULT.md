# Result: 1312 mcp-server-fence-paths
Status: done
Todo: #1312   PP: PP-COHESION-001

Files touched:
- src/tgw/mcp_server.py
- tests/test_mcp_server.py
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1312-mcp-server-fence-paths.md (breadcrumb, worktree-local)

Summary of change:
- `tgw_get_item()`: replaced the inline `cfg['itemdata_root']/sku/f'{sku}.json'`
  + `.exists()` + `json.loads(jf.read_text())` block with `items.get_item(cfg, sku)`,
  catching `FileNotFoundError` to produce the same `{'ok': False, 'error': f'item
  not found: {sku}'}` shape as before. `_images`/`_videos` keys that `get_item()`
  adds are left in the returned doc (additive, not stripped back to the old
  narrower shape), per spec.
- `tgw_enqueue()`: replaced the inline path-construction existence check with
  `items.sku_json(cfg, sku)`, and on miss falls back to
  `resolver.find_current_sku(cfg, sku)` (same idiom `items.get_item()` uses
  internally) before concluding "not found". Only an existence check is
  performed here (no full `get_item()` call), matching spec. `_VALID_ACTIONS`
  and enqueue-payload logic untouched.
- Both functions import `items`/`find_current_sku` locally inside the
  function body, matching this file's existing idiom (every other tool in
  mcp_server.py does its internal imports the same way, e.g.
  `from tgw.api import list_items` inside `tgw_search_items`).

Live evidence:
- `PYTHONPATH=.../1312-mcp-server-fence-paths/src LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
  python -c "import tgw.mcp_server as m; print(m.__file__)"` confirmed the
  worktree copy, not the shared checkout, before any test run.
- `pytest -q tests/test_mcp_server.py` → 21 passed (includes 2 new tests:
  `test_get_item_resolves_renamed_sku_via_alias_fallback` and
  `test_enqueue_resolves_renamed_sku_via_alias_fallback`, both using a
  fixture item with `sku_old` set, confirming resolution via
  `resolver.find_current_sku()` instead of returning "item not found").
- Full offline suite: `pytest -q` (worktree src on PYTHONPATH,
  LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH) → `2179 passed, 1 skipped, 1 warning
  in 155.51s` — zero regressions, zero new failures.

Deviations from spec: none. One implementation note not explicit in the
packet: `resolver.find_current_sku()` builds a process-level cache
(`resolver._sku_old_index`) on first call, so the new tests reset it via
`monkeypatch.setattr(resolver, "_sku_old_index", None)` in the shared `cfg`
fixture — otherwise a renamed-SKU fixture written under one test's
`tmp_path` wouldn't be picked up if an earlier test in the same process had
already built the cache against a different `tmp_path`. This is test
plumbing only, not a behavior change to either tool.

Out-of-scope findings filed: none — no new findings surfaced during this
packet; `tgw_search_items`, `tgw_queue_status`, and the `_VALID_ACTIONS`/
enqueue-payload logic were left untouched per the packet's Out of scope
list.
