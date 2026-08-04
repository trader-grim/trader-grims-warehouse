# Review: 1283 clip-db-permissions-ttl
Status: cleared — concurrent batch (post 2-in-a-row graduation), stitching independently.
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec — `_connect()` chmods parent dir to `0700` and db file to
`0600` unconditionally on every call (self-healing, exactly as specced);
`_RETENTION_DAYS = 14` constant added; TTL prune added alongside the
existing row-count prune in `record_clip()`, same commit, no new
scheduling mechanism. Out-of-scope — sensitivity filtering, capture
daemon/widget correctly NOT touched, exactly as the packet excluded.
Invariants — n/a (local filesystem/SQLite permissions + prune only, no
ItemData/queue impact). Live evidence — re-verified independently: 4 new
tests cover fresh-creation permissions (0600/0700), self-heal from a
simulated pre-fix 0644 file, TTL prune removing a 20-day-backdated row
while keeping a same-day row, and the pre-existing row-count retention
still enforced (proves no regression, not just assumed). Confirmed
`tgw.clip.__file__` resolves under the worktree path, full offline suite
2124 passed/1 skipped/0 failed — matches executor's reported numbers. No
deviations from spec, no out-of-control triggers fired.

Stitched.
