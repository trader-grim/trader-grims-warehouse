# Result: 1324-1325 context-fixes
Status: done
Todo: #1324, #1325   PP: PP-EVENTD-001

Files touched:
- `src/tgw/context.py`
- `tests/test_context.py`
- `etc/interfaces/shell/tgw.source`

## #1325 — orphaned `tgw tgwset` call

Pre-flight: confirmed live via `python -m tgw.api tgwset` that `tgwset` is
not a valid subcommand (argparse choice list confirmed `set-context` is,
`tgwset` is not). `tgwset_selected()` in `etc/interfaces/shell/tgw.source`
shelled out to `tgw tgwset "..."` — repointed to `tgw set-context "..."`.

Live evidence:
```
$ python -m tgw.api tgwset
tgw: error: argument COMMAND: invalid choice: 'tgwset' (choose from ... set-context, get-context, clear-context ...)

$ python -m tgw.api set-context --help
usage: tgw set-context [-h] sku
positional arguments:
  sku         full SKU (tgwYYYYMMDDHHMMSSmmm)
```
`bash -n etc/interfaces/shell/tgw.source` — syntax OK after edit.

## #1324 — restore CurrentLocation symlink

Pre-flight: confirmed live that `src/tgw/context.py` (introduced fresh by
PP-CONTEXT-001, commit afa856e, 2026-06-11) had zero `CurrentLocation`
logic — only `CurrentItem`/`CurrentItem.json` are maintained. Found the
original behavior via `git log`/reading the still-present (dead) old
`tgwset()` shell function in `tgw.source`:
`ln -sf $catalogpath/$(tgw_location) $tgwpath/CurrentLocation`, where
`tgw_location` reads the item JSON's `.location` field. The modern
equivalent of `$catalogpath/<location>` is `location_dir(cfg, location)`
in `src/tgw/config.py` (`ItemCatalog/by-location/<location>`) — confirmed
live that this tree exists and is populated on disk
(`/opt/TGW/data/ItemCatalog/by-location/ALB1` etc.).

Implementation: extended `_update_compat_symlinks()` to also maintain
`/opt/TGW/CurrentLocation` via a new `_update_current_location_symlink()`
helper — reads `.location` from the item JSON, resolves it through
`location_dir()`, atomic-symlinks if the catalog dir exists. Silently
no-ops (no error) if the item has no location, the location's catalog dir
doesn't exist yet, or `location_tree_root` isn't in cfg — matches the old
shell function's silent behavior on an empty `tgw_location` result.
`clear_context()` now also removes `CurrentLocation`.

Live evidence (apply → confirm → revert → confirm reverted, real items,
real filesystem, via `sudo -u tgw` with worktree PYTHONPATH):
```
$ python -m tgw.api set-context tgw201411151951372   # location SAT013
{"ok": true, "sku": "tgw201411151951372", ...}
$ ls -la /opt/TGW/CurrentLocation
CurrentLocation -> /opt/TGW/data/ItemCatalog/by-location/SAT013

$ python -m tgw.api set-context tgw201412172047101   # location EA3035
{"ok": true, "sku": "tgw201412172047101", ...}
$ ls -la /opt/TGW/CurrentLocation
CurrentLocation -> /opt/TGW/data/ItemCatalog/by-location/EA3035

$ python -m tgw.api clear-context
{"ok": true, "changed": true}
$ ls /opt/TGW/CurrentLocation
ls: cannot access '/opt/TGW/CurrentLocation': No such file or directory
```
Original context (`tgw202607171235261`, no location) restored afterward —
confirmed `CurrentItem`/`CurrentItem.json` back in place and
`CurrentLocation` correctly absent (that item has no `.location` field).

New offline tests added in `tests/test_context.py`
(`TestCurrentLocationSymlink`, 6 cases): symlink created on set, updated on
SKU change, removed on clear, and three silent-no-op paths (no location
field, location dir missing, `location_tree_root` absent from cfg).

`PYTHONPATH`/`LD_LIBRARY_PATH` override confirmed pointing at the worktree
copy (`tgw.context.__file__` resolved under
`/opt/TGW/var/worktrees/1324-1325-context-fixes/src/tgw/context.py`)
before any test/live run.

```
$ pytest -q tests/test_context.py
26 passed

$ pytest -q   # full offline suite
2377 passed, 1 skipped, 2 failed (pre-existing, unrelated — see below)
```

Deviations from spec: none.

Pre-existing unrelated failure noted, not fixed (out of scope, confirmed
failing identically on the shared checkout / base branch before this
change): `tests/test_invariant_c12_field_set_accessors.py` — two tests
fail on stale line-number references into `http_server.py`'s C12
allowlist. Not touched by this packet. Filing a todo for it separately
per contract (see below).

Out-of-scope findings filed: #1507 — `tests/test_invariant_c12_field_set_accessors.py`
allowlist line numbers are stale against current `http_server.py`, causing
2 pre-existing test failures unrelated to this packet (confirmed failing
identically on base branch `catio-nix-0.0.1-alpha` before any change in
this branch) — filed with `--pp PP-FIELDCOMPLETE-001` (nearest existing
field-set-boundary PP; C12 invariant's own area).
