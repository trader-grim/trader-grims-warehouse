Working on todo #1275 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1275-catalog-location-tree-safety` on branch
`todo/1275-catalog-location-tree-safety`. Task: route
`catalog.py`'s `build_location_tree()` link_dir construction through the
hardened `config.location_dir()` (from todo #1274, not yet merged to main)
instead of a raw `dest_root / location` join, closing a path-traversal gap
on catalog rebuild. Since #1274 isn't merged to main yet, fast-forward
merged branch `todo/1274-config-path-safety-validation` into this worktree
first so the fix uses the real hardened function, not a duplicate. Edit
applied at the `if not check_only:` block inside the per-row loop; malformed
locations now append to `problems` and `continue` instead of raising/
escaping. Next: run offline pytest with PYTHONPATH override, run live
acceptance against real + malicious location values, write result manifest.
