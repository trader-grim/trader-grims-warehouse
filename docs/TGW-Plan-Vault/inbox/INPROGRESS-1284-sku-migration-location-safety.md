# In progress: todo #1284 — rename_sku() location-symlink safety

Working in worktree `/opt/TGW/var/worktrees/1284-sku-migration-location-safety`
on branch `todo/1284-sku-migration-location-safety`, branched from
`catio-nix-0.0.1-alpha` (not literal `main` — see deviation note in result
manifest: actual git `main` ref does not yet contain #1274/#1275, so
`location_dir()` wasn't hardened there; `catio-nix-0.0.1-alpha` is the
branch that has it and is the current active trunk). Applied the packet's
spec exactly: `rename_sku()`'s location-symlink block in
`src/tgw/sku_migration.py` now routes through `config.location_dir()`
inside a try/except ValueError, logging a warning and skipping the symlink
update on unsafe input rather than raising into the outer exception
handler (which would have incorrectly reported the whole rename as
failed). Added `location_dir` to the `.config` import. Next: run acceptance
tests (normal location, path-traversal location, full offline suite), then
write the result manifest and commit.
