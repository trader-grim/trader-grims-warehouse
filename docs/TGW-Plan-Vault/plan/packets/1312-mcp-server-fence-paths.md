# Packet: mcp_server.py tgw_get_item and tgw_enqueue construct ItemData paths inline
Todo: #1312   PP: PP-COHESION-001   Track: fence-bypass batch

## Context budget (ALL the model may load)
This packet + `src/tgw/mcp_server.py`'s `tgw_get_item()` (~line 78) and
`tgw_enqueue()` (~line 211), including the `_get_cfg()` helper they both
call + `src/tgw/items.py`'s `get_item()` (line 167) and `sku_json()`
(the shared path-building helper) + this file's existing test file if
one exists. Nothing else — do not touch `tgw_search_items`,
`tgw_queue_status`, or any other MCP tool.

## Verified live before this packet was written
- `tgw_get_item()` (line 87) builds `jf = cfg['itemdata_root'] / sku /
  f'{sku}.json'` inline, checks `.exists()`, and does its own
  `json.loads(jf.read_text())` — bypassing `items.get_item()` entirely,
  which means: no SKU-alias fallback (`find_current_sku`) after a
  rename, and no `_images`/`_videos` media discovery that `get_item()`
  normally provides (this tool's docstring doesn't promise media lists,
  so that's an acceptable behavior difference — see Out of scope).
- `tgw_enqueue()` (line 241) builds the same inline path
  (`cfg['itemdata_root'] / sku / f'{sku}.json'`) purely to check
  existence before enqueueing — same duplication, same missing
  alias-fallback.
- `items.sku_json(cfg, sku)` is the single shared path-building helper
  used everywhere else in the fence (confirmed via `revision.py`,
  `http_server.py` usage) — both functions here should use it instead of
  reconstructing the path inline, even where they don't need the full
  `get_item()` doc.

## Spec

### tgw_get_item
Replace the inline `jf = cfg['itemdata_root'] / sku / f'{sku}.json'` +
`.exists()` + `json.loads(jf.read_text())` block with a call to
`items.get_item(cfg, sku)`, catching `FileNotFoundError` to produce the
same `{'ok': False, 'error': f'item not found: {sku}'}` shape this tool
already returns. `get_item()` returns `_images`/`_videos` keys the
current inline version doesn't — leave them in the returned doc (additive,
not a behavior removal); do not strip them back out to match the old
narrower shape.

### tgw_enqueue
Replace the inline `jf = cfg['itemdata_root'] / sku / f'{sku}.json'` +
`.exists()` existence check with `items.sku_json(cfg, sku)` for the path,
plus the same alias-fallback dance `items.get_item()` uses internally
(`from .resolver import find_current_sku` — if the initial path doesn't
exist, resolve via `find_current_sku(cfg, sku)` and recompute before
concluding "not found"). This tool only needs an existence check, not the
full doc, so don't call `get_item()` here — just replicate the path
resolution, matching the idiom already established in `items.get_item()`
and this same cohesion batch's fix to `revision.py` (#1313/#1316).

Both functions need `from . import items` (or equivalent) added if not
already imported at module level — check current imports first.

## Dataset
None — no ItemData schema change; this only changes path
resolution/read mechanics for two existing read paths.

## Out of scope
- `tgw_search_items`, `tgw_queue_status`, `tgw_health`, or any other MCP
  tool in this file.
- Adding SKU-alias fallback awareness to the tool's docstring/behavior
  description beyond what's needed for the fix itself.
- The `_VALID_ACTIONS` set or any enqueue-payload logic in `tgw_enqueue`
  beyond the existence check.

## Acceptance (live)
1. New test: `tgw_get_item` called with an OLD sku value for an item
   that has since been renamed (fixture with `sku_old` set) — confirm it
   now resolves via alias fallback instead of returning "item not
   found", matching `items.get_item()`'s documented behavior.
2. New test: `tgw_enqueue` with the same renamed-SKU fixture — confirm
   the existence check now passes via alias fallback (enqueue succeeds)
   instead of failing "item not found".
3. Existing mcp_server.py tests, if any, still pass unchanged (a
   straightforward existing-SKU get/enqueue should behave identically to
   before).
4. Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` — zero
   regressions.

## Quota/risk
None — no live eBay/queue side effects beyond what these tools already
do (enqueue already goes through `state_machine.enqueue_job`, unchanged
by this packet); fixture SKUs only for new tests.
