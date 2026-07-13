# Result: 1284 sku-migration-location-safety
Status: done
Todo: #1284   PP: PP-COHESION-001
Files touched:
- src/tgw/sku_migration.py
- tests/test_sku_migration_location_safety.py (new)
- docs/TGW-Plan-Vault/inbox/INPROGRESS-1284-sku-migration-location-safety.md (new, breadcrumb)

Live evidence:
- New test `tests/test_sku_migration_location_safety.py` exercises the two
  acceptance scenarios directly against `rename_sku()`:
  - `test_valid_location_symlink_updated_no_warning` — normal `location`
    value ("SAT013"): symlink created under `location_tree_root`, resolves
    to the new item dir, no "unsafe location" warning logged. PASSED.
  - `test_malicious_location_rejected_not_escaped` — `location =
    "../../../tmp/evil"`: no symlink/file created outside
    `location_tree_root` (`tmp_path/tmp/evil` confirmed absent), a warning
    containing "unsafe location" and "rename_sku" is logged, and
    `rename_sku()` still returns `ok: True` with the SKU/JSON rewrite
    completed (new dir + new JSON exist) — i.e. the bad location value
    does not roll back or abort the already-completed rename. PASSED.
  - Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` →
    `2110 passed, 1 skipped, 1 warning in 38.04s`. Confirmed via
    `tgw.sku_migration.__file__` resolving under the worktree path before
    running, so this ran against the worktree's own edits, not the shared
    checkout.

Deviations from spec:
- **Branch base.** The packet/invocation instructed `git worktree add -b
  todo/1284-sku-migration-location-safety ... main`, and stated todo #1274
  "has already been merged into main." Pre-flight check found this false
  for the literal git branch `main` (HEAD `6f2d7ef`) — `config.location_dir()`
  there is still the unhardened raw-join version; #1274/#1275 only exist
  on `catio-nix-0.0.1-alpha` (merge commit `dbac723`), which is also the
  actual active development trunk (matches the shared checkout's current
  branch). Building on literal `main` would have defeated the packet's
  entire premise (there'd be no hardened `location_dir()` to route
  through). Rebuilt the worktree/branch from `catio-nix-0.0.1-alpha`
  instead of `main`. This is a deviation from the literal invocation
  instruction, flagged here rather than silently substituted, per Prime
  Directive 3.
- Everything else implemented exactly per the packet spec (import added,
  try/except ValueError wrapping `location_dir()` call, warning log
  message text as specified, symlink-update block otherwise unchanged,
  no changes to `location_dir()` itself or the rest of `rename_sku()`).

Out-of-scope findings filed: none.
