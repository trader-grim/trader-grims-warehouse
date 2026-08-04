# Packet: build_location_tree() routes through the hardened location_dir()
Todo: #1275   PP: PP-COHESION-001   Track: SECURITY batch, run 2 of new track (cadence rule)

## Context budget (ALL the model may load)
This packet + `src/tgw/catalog.py` (`build_location_tree()` only, lines
~256-315) + `src/tgw/config.py`'s `location_dir()` (read-only reference —
already fixed by todo #1274, do not modify further) + the todo brief
(`tgw todo brief 1275`). Nothing else.

## Verified live before this packet was written
`#1274` (merged same session, or in worktree `/opt/TGW/var/worktrees/1274-config-path-safety-validation`
if not yet stitched — check which) hardened `config.location_dir()` with
a strict allow-list + containment check. `build_location_tree()` does
NOT call it — it builds `link_dir = dest_root / location` directly at
line ~296, a separate raw join bypassing the fix entirely. This is a
genuinely distinct vulnerability, not resolved by #1274 alone (confirmed
by code inspection of the call path — no shared function in between).

## Spec
At `src/tgw/catalog.py`, inside `build_location_tree()`'s per-row loop
(~line 282-302):
```python
target = cfg['itemdata_root'] / sku
...
if not check_only:
    link_dir  = dest_root / location
    link_path = link_dir / sku
```
Replace the raw `dest_root / location` join with the hardened helper:
```python
from tgw.config import location_dir
...
if not check_only:
    try:
        link_dir = location_dir(cfg, location)
    except ValueError as exc:
        problems.append(f'unsafe location for sku {sku}: {exc}')
        continue
    link_path = link_dir / sku
```
This rejects a malicious/malformed `location` value the same way #1274
already rejects it elsewhere, and records it in the existing `problems`
list (which already feeds `{'ok': False, 'problems': [...]}` at the end
of the function) rather than crashing the whole catalog rebuild or
silently writing outside the tree.

`target = cfg['itemdata_root'] / sku` (line ~288) is a SEPARATE concern —
`sku` here comes from already-validated ItemData (not network input) and
is out of scope for this packet; do not change it unless todo #1275
explicitly calls it out (it doesn't — re-read the brief to confirm before
touching it).

## Dataset
None — this only rejects malformed input during catalog rebuild; valid
location values are unaffected (already proven safe by #1274's live
verification against the same real production location values).

## Out of scope
- `sku_migration.py`'s `rename_sku()` (todo #1284) — same bug shape,
  deliberately a SEPARATE task, not this one.
- `location_dir()` itself — already fixed by #1274, do not modify.
- The `target = cfg['itemdata_root'] / sku` line and anything else in
  this function beyond the one `link_dir` construction.

## Acceptance (live)
1. Run `build_location_tree()` (or a constructed equivalent) against real
   data with a normal, valid `location` value (e.g. `SAT013`) — confirm
   identical behavior to before (link built, no exception, no new
   `problems` entry).
2. Construct a row with `location = "../../../tmp/evil"` — confirm the
   function does NOT create a symlink outside `location_tree_root`, adds
   an entry to `problems`, and continues processing remaining rows rather
   than crashing.
3. Run the full offline suite — confirm zero regressions.

## Quota/risk
None.
