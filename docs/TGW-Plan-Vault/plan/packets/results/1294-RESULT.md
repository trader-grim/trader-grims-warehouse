# Result: 1294 sku-migration-collision-report
Status: done
Todo: #1294   PP: PP-COHESION-001
Files touched: src/tgw/sku_migration.py, tests/test_sku_migration_collision_report.py
Live evidence:
- Confirmed live (before fix) that `collision_report()` had zero callers anywhere in src/ or tests/ (`grep -rn "collision_report" src/ tests/` → only its own def), matching the packet's pre-flight claim.
- Rewrote `collision_report()` to consume `check_collisions()`'s actual dict shape (`ok`, `raw_a_collisions`, `auto_resolved`, `unresolvable`, `safe_to_migrate`, `resolved_pairs`) exactly per the packet's specified implementation, preserving the existing output contract (`ok`, `total`, `by_type`, `collisions`, `safe_to_migrate`).
- Ran against constructed cfg/monkeypatched `check_collisions` (module loaded from worktree, confirmed via `sm.__file__` resolving under `/opt/TGW/var/worktrees/1294-sku-migration-collision-report/...`):
  - Empty-collision case: `{'ok': True, 'total': 0, 'by_type': {'auto_resolved': 0, 'unresolvable': 0}, 'collisions': [], 'safe_to_migrate': True}` — no TypeError.
  - A-to-A collision-pair case: `{'ok': False, 'total': 1, 'by_type': {'auto_resolved': 1, 'unresolvable': 0}, 'collisions': [{'winner': ..., 'loser': ..., 'natural_target': ..., 'resolved_target': ...}], 'safe_to_migrate': True}` — `collisions[0]` confirmed to be an actual pair dict with `winner`/`loser`/`natural_target`/`resolved_target` keys, not a string.
  - Arithmetic invariant `by_type['auto_resolved'] + by_type['unresolvable'] == total` verified in both cases plus a third unresolvable-mixed case.
- Added `tests/test_sku_migration_collision_report.py` (3 tests) locking in the fix; ran with `PYTHONPATH=/opt/TGW/var/worktrees/1294-sku-migration-collision-report/src:$PYTHONPATH pytest -q tests/test_sku_migration_collision_report.py` → `3 passed`.
- Full offline suite re-run with the same PYTHONPATH override to confirm the worktree's own copy was under test (not the shared checkout): `2049 passed, 1 skipped` — no regressions.
Deviations from spec: none — implementation matches the packet's specified code verbatim.
Out-of-scope findings filed: none
