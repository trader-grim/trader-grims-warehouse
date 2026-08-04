# Result: 1517 cloud-sync-watchdog
Status: done
Todo: #1517   PP: PP-BACKUP-001

## Files touched
- `bin/tgw-cloud-sync` — added `timeout --kill-after` watchdog around the
  rclone subprocess call, `flock --timeout` on the blocking lock
  acquisition, a `log()` helper, and env-var overrides for testability
  (`RCLONE_TIMEOUT_SECS`, `RCLONE_KILL_AFTER_SECS`, `FLOCK_TIMEOUT_SECS`,
  `TGW_RCLONE_CONF`, `TGW_RCLONE_SYNC_LOG`, `TGW_RCLONE_SYNC_STAMP`,
  `TGW_RCLONE_LOCK`). Dropped `set -e` (kept `-u -o pipefail`) since both
  failure points (flock timeout, rclone timeout/failure) are now handled
  explicitly with `if`/exit rather than relying on implicit early exit.
- `bin/tgw-itemdata-sync` — same `timeout --kill-after` watchdog around
  its per-cycle rclone call inside the continuous loop, plus matching env
  overrides (`RCLONE_TIMEOUT_SECS`, `RCLONE_KILL_AFTER_SECS`,
  `TGW_RCLONE_CONF`, `TGW_ITEMDATA_LOCAL`, `TGW_RCLONE_ITEMDATA_LOG`,
  `TGW_RCLONE_ITEMDATA_STATUS`, `TGW_RCLONE_LOCK`,
  `TGW_ITEMDATA_SYNC_INTERVAL`).
- `tests/test_cloud_sync_watchdog.py` (new) — 6 tests covering both
  scripts.

## Live evidence
- New test file: `pytest -q tests/test_cloud_sync_watchdog.py` → `6
  passed in 14.31s` (run with `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH
  PYTHONPATH=<worktree>/src:$PYTHONPATH`, confirmed importing the
  worktree's own `tgw` module first via `python3 -c "import tgw;
  print(tgw.__file__)"` → resolved under the worktree path, not the
  shared checkout).
- Full suite regression check: `pytest -q` → `2520 passed, 1 skipped, 1
  warning in 206.26s` — no regressions.
- Real live network call through the exact same wrapper pattern (as
  `tgw` user, real `rclone.conf`, real Drive remote, read-only):
  ```
  $ sudo -u tgw bash -c 'time timeout --kill-after=10 60s rclone lsd
    tgw-gdrive:TGW --config /opt/TGW/config/rclone.conf --tpslimit 2'
             0 2026-05-14 20:17:18        -1 bin
             0 2026-05-15 09:01:32        -1 config
             0 2026-05-14 20:17:40        -1 data
             0 2026-05-14 20:18:03        -1 docs
             0 2026-05-15 09:02:16        -1 logs
             0 2026-05-15 09:00:45        -1 src
  real  0m1.294s
  EXIT: 0
  ```
  Confirms the `timeout --kill-after` wrapper + real rclone binary + real
  network round trip work together cleanly and fast on the normal path,
  with no prod write (read-only `lsd`, not the full destructive sync — a
  full off-cycle production sync was judged out of scope for this
  packet's acceptance, which only calls for "a real short rclone call or
  a fast no-op").
- Test-simulated hang path (in `test_cloud_sync_watchdog.py`): a fake
  `rclone` stub that ignores SIGTERM and sleeps 300s is correctly bounded
  by the watchdog (SIGKILLed via `--kill-after`), the script exits
  non-zero, logs `"watchdog fired"`, does NOT write the success stamp,
  and — critically — the shared flock is provably released immediately
  after (a fresh non-blocking `flock` acquire from the test succeeds).
  The continuous `tgw-itemdata-sync` loop is shown to survive a hung
  cycle and advance to cycle 2 rather than wedging.

## Deviations from spec
- **Exact timeout ceiling is a judgment call, not specified in the
  todo** — chose `RCLONE_TIMEOUT_SECS=14400` (4h) default: well above the
  normal ~43min full-sync duration (~5.5x headroom) but well below the
  observed ~1.5-day hang, so a real hang is caught same-day rather than
  running into the next scheduled 02:30 run. `RCLONE_KILL_AFTER_SECS=30`
  default (grace period after SIGTERM before SIGKILL) — chosen to give
  rclone a chance to unwind cleanly (flush its own log/state) before a
  hard kill, without adding meaningful delay to the timeout's total
  effect. `FLOCK_TIMEOUT_SECS=14400` (same as the rclone ceiling) as
  defense-in-depth on `tgw-cloud-sync`'s blocking lock wait — this case
  should no longer be reachable now that `tgw-itemdata-sync`'s own rclone
  call is also bounded, but the todo explicitly names "a hung flock hold"
  as an in-scope failure mode, so it's covered directly rather than only
  transitively. Flagging all three per CLAUDE.md Prime Directive 3 rather
  than silently picking values.
- **Applied the same watchdog to `tgw-itemdata-sync`, not just
  `tgw-cloud-sync`** — the todo's narrative names `tgw-cloud-sync` as the
  process that hung and starved `tgw-itemdata-sync` via the shared flock,
  but the Fix section's own wording ("a hung rclone subprocess (or a hung
  flock hold) cannot block indefinitely") is symmetric, and
  `tgw-itemdata-sync`'s rclone call could equally hang and hold the same
  lock, which would then block `tgw-cloud-sync`'s scheduled run in the
  opposite direction. Both scripts were in the packet's declared context
  ("Read both scripts fully first ... before adding anything"), so this
  is read as in-scope rather than scope creep — flagging explicitly in
  case that reading is wrong.
- **Dropped `set -e` from `tgw-cloud-sync`** (kept `-u`/`pipefail`) —
  needed so `rc=$?` after the `timeout` call captures the real rclone
  exit code rather than bash exiting immediately on non-zero before the
  watchdog-vs-real-failure branch can run and log. Both failure paths
  (flock timeout, rclone timeout/failure) now have explicit `exit 1`
  calls, so behavior on failure is unchanged (still exits non-zero) — only
  the mechanism for getting there changed from implicit to explicit.
- Left rclone's own internal retry/backoff completely untouched, per the
  todo's explicit instruction not to change the already-working
  3-retry-exhaust-and-exit normal path.

## Out-of-scope findings filed
- #1519 (PP-BACKUP-001): `bin/dedupe-gdrive.sh`'s rclone call has no
  timeout/watchdog either (same hang-risk shape) — doesn't share the
  `tgw-rclone-gdrive.lock` flock so it can't starve the other two syncs,
  but a hung manual invocation would block whoever ran it. Not fixed
  inline; out of this packet's declared scope (todo names only
  `tgw-cloud-sync`/`tgw-itemdata-sync`).
