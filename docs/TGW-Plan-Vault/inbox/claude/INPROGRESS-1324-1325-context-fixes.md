# In progress: #1324 + #1325 — context.py fixes (PP-EVENTD-001)

Working in isolated worktree `/opt/TGW/var/worktrees/1324-1325-context-fixes`
on branch `todo/1324-1325-context-fixes`.

- **#1325**: `tgwset_selected()` in `etc/interfaces/shell/tgw.source` shells
  out to `tgw tgwset "..."` — confirmed live that `tgw tgwset` is not a real
  subcommand (`tgw set-context` is, added by PP-CONTEXT-001/afa856e). Fix:
  repoint the shell-out to `tgw set-context`.
- **#1324**: confirmed `src/tgw/context.py` has zero `CurrentLocation`
  logic — only maintains `/opt/TGW/CurrentItem` + `CurrentItem.json`. Old
  `tgwset()` shell function (still present, dead, in tgw.source) did
  `ln -sf $catalogpath/$(tgw_location) $tgwpath/CurrentLocation` where
  `tgw_location` reads the item's `.location` JSON field. Modern equivalent
  of `$catalogpath/<location>` is `location_dir(cfg, location)` in
  `src/tgw/config.py` (catalog_root/by-location/<location>, live tree
  confirmed populated on disk). Plan: extend `_update_compat_symlinks` in
  context.py to also maintain `/opt/TGW/CurrentLocation` -> that dir when
  the item has a `location` field, and have `clear_context` remove it too.

Status at breadcrumb time: investigation done, about to write the fix.
