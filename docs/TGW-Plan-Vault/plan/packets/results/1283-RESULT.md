# Result: 1283 clip-db-permissions-ttl
Status: done
Todo: #1283   PP: PP-COHESION-001
Files touched:
- src/tgw/clip.py
- tests/test_clip.py

Live evidence:
Manual verification script run against the worktree copy (confirmed via
`tgw.clip.__file__` resolving under the worktree path before testing):
```
mode file: 0o600
mode dir: 0o700
before heal: 0o644
after heal: 0o600
rows after TTL prune: ['fresh', 'heal']
```
- Fresh db creation: file mode 0o600, parent dir mode 0o700.
- Pre-existing 0o644 file self-healed to 0o600 on next `record_clip()` call.
- Row backdated 20 days via direct SQL was pruned by the TTL delete on the
  next `record_clip()` call; a same-day row and the triggering row both
  survived.
- Row-count retention (2000-row cap) still enforced — added regression
  test `test_row_count_retention_still_enforced`.
- Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` →
  2124 passed, 1 skipped, 0 failed (zero regressions).
- New tests added: `test_db_created_with_restrictive_permissions`,
  `test_permissive_existing_db_self_heals`,
  `test_ttl_prune_removes_old_rows_keeps_recent`,
  `test_row_count_retention_still_enforced` — all pass in
  `tests/test_clip.py`.

Deviations from spec: none. Implemented exactly as specified —
`_connect()` chmods parent dir 0700 and db file 0600 unconditionally on
every call (self-healing), `_RETENTION_DAYS = 14` TTL prune added
alongside the existing row-count prune in `record_clip()`, both run on
every insert with no new scheduling mechanism. Sensitivity filtering and
the X11/daemon/widget layer were confirmed out of scope and untouched.

Out-of-scope findings filed: none.
