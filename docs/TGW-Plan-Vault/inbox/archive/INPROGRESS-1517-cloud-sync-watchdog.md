# INPROGRESS: todo #1517 — cloud-sync rclone hang watchdog

Working on the branch-per-task worktree at
`/opt/TGW/var/worktrees/1517-cloud-sync-watchdog` (branch
`todo/1517-cloud-sync-watchdog`), not the shared checkout.

Added a `timeout --kill-after` watchdog wrapper around the rclone
subprocess call in both `bin/tgw-cloud-sync` and `bin/tgw-itemdata-sync`
(the 2026-07-16 incident had `tgw-cloud-sync` hang after a Drive 403 and
hold the shared flock for ~1.5 days, starving `tgw-itemdata-sync`), plus a
`flock --timeout` on `tgw-cloud-sync`'s blocking lock acquisition as
defense in depth. Added env-var overrides (`RCLONE_TIMEOUT_SECS`,
`RCLONE_KILL_AFTER_SECS`, `FLOCK_TIMEOUT_SECS`, plus path overrides for
log/lock/stamp/status/local-dir) so the scripts stay testable without
touching production paths — same pattern already used in
`bin/tgw-offline-sync` (`TGW_OFFLINE_MOUNT_BASE`).

Wrote `tests/test_cloud_sync_watchdog.py` (6 tests) simulating a hung
rclone via a fake stub on PATH, confirming: watchdog fires and kills it,
script exits non-zero with a clear log line, the flock is released after
exit, the normal fast path is unaffected, real retry-exhaustion failures
stay distinguishable from watchdog timeouts, and the continuous
`tgw-itemdata-sync` loop survives a hung cycle and advances. All pass.
Live evidence: ran the real `rclone lsd` command through the identical
`timeout --kill-after` wrapper against the real `tgw-gdrive:TGW` remote as
`tgw` — completed in 1.3s, exit 0 (read-only, no prod write). Full
`pytest -q` suite: 2520 passed, 1 skipped, no regressions.

Filed #1519 (PP-BACKUP-001) as an out-of-scope adjacent finding:
`bin/dedupe-gdrive.sh` has the same hang-risk shape but isn't in this
packet's scope.

Next: write result manifest at
`docs/TGW-Plan-Vault/plan/packets/results/1517-RESULT.md` and commit.
