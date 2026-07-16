Working on todo #1294 (PP-COHESION-001) in worktree
/opt/TGW/var/worktrees/1294-sku-migration-collision-report, branch
todo/1294-sku-migration-collision-report. Task: fix `collision_report()` in
`src/tgw/sku_migration.py`, which currently iterates `check_collisions()`'s
dict return value as if it were a list of collision dicts (TypeError on
`c['conflict_type']`). Fix per packet: rewrite to consume the dict's actual
keys (`ok`, `raw_a_collisions`, `auto_resolved`, `unresolvable`,
`safe_to_migrate`, `resolved_pairs`) while preserving collision_report()'s
existing output contract (`ok`, `total`, `by_type`, `collisions`,
`safe_to_migrate`). No caller wiring, no changes to check_collisions()
itself. Next: read sku_migration.py, apply fix, run pytest with PYTHONPATH
override, write result manifest.
