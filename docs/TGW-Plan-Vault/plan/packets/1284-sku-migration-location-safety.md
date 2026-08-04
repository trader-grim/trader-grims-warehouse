# Packet: rename_sku() routes through the hardened location_dir()
Todo: #1284   PP: PP-COHESION-001   Track: SECURITY batch, concurrent (graduated after #1274/#1275)

## Context budget (ALL the model may load)
This packet + `src/tgw/sku_migration.py` (`rename_sku()`'s location-symlink
block only, lines ~394-404) + `src/tgw/config.py`'s `location_dir()`
(read-only reference — already fixed by todo #1274/#1275, merged to
main) + the todo brief (`tgw todo brief 1284`). Nothing else.

## Verified live before this packet was written
`config.location_dir()` is now hardened (todo #1274, merged to
`catio-nix-0.0.1-alpha`) — same fix shape already successfully applied to
`catalog.build_location_tree()` (todo #1275). `rename_sku()` builds
`link_dir = cfg['location_tree_root'] / location` directly (line ~396),
bypassing the hardened helper the same way `#1275`'s bug did before its
fix — this is the third and final independent bypass of the same
underlying vulnerability class.

## Spec
At `src/tgw/sku_migration.py`, in `rename_sku()`'s location-symlink block:
```python
# 3. Update location symlink
if location:
    link_dir  = cfg['location_tree_root'] / location
    old_link  = link_dir / old_sku
    new_link  = link_dir / new_sku
    if old_link.exists() or old_link.is_symlink():
        old_link.unlink()
    if link_dir.exists():
        if new_link.exists() or new_link.is_symlink():
            new_link.unlink()
        os.symlink(new_dir, new_link)
```
Replace the raw join with the hardened helper:
```python
# 3. Update location symlink
if location:
    try:
        link_dir = location_dir(cfg, location)
    except ValueError as exc:
        log.warning("rename_sku: unsafe location %r for %s: %s", location, new_sku, exc)
    else:
        old_link  = link_dir / old_sku
        new_link  = link_dir / new_sku
        if old_link.exists() or old_link.is_symlink():
            old_link.unlink()
        if link_dir.exists():
            if new_link.exists() or new_link.is_symlink():
                new_link.unlink()
            os.symlink(new_dir, new_link)
```
Import `location_dir` from `.config` at the top of the file if not
already imported. A rejected location value logs a warning and skips the
symlink update — it must NOT abort the rest of `rename_sku()` (the SKU
rename/JSON rewrite above this block already completed by this point;
don't roll that back over a bad location value).

## Dataset
None — this only rejects malformed/malicious location input during a
rename; valid values are unaffected (already proven safe against real
production data by #1274's own live verification).

## Out of scope
- `location_dir()` itself — already fixed, do not modify.
- Any other part of `rename_sku()` (SKU/JSON rewrite logic above this
  block).
- `catalog.py`/`http_server.py` — already handled (todos #1275/#1273).

## Acceptance (live)
1. Call `rename_sku()` (or a constructed equivalent) with a normal, valid
   `location` value — confirm identical behavior to before (symlink
   updated, no exception, no warning logged).
2. Call it with `location = "../../../tmp/evil"` — confirm no symlink is
   created outside `location_tree_root`, a warning is logged, and the
   function completes (SKU rename itself still succeeds) rather than
   raising an unhandled exception.
3. Run the full offline suite — confirm zero regressions.

## Quota/risk
None.
