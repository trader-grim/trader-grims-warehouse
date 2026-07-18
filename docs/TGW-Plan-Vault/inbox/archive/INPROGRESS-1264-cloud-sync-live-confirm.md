# INPROGRESS — todo #1264 (PP-BACKUP-001) — tgw-coder live-confirm run

**Branch:** `todo/1264-cloud-sync-live-confirm` (worktree `/opt/TGW/var/worktrees/1264-cloud-sync-live-confirm`)

## What I found (pre-flight, live)

- `--tpslimit 2` confirmed present in `bin/tgw-cloud-sync` and `bin/tgw-itemdata-sync` on current HEAD — no drift since 2026-07-10.
- `systemctl status tgw-cloud-sync.service` showed the *current* run (started
  2026-07-16 13:10:02) still "activating" 1 day 10h later. Journal + rclone log
  (`/opt/TGW/var/log/rclone-sync.log`) show it hit the same
  `403 RATE_LIMIT_EXCEEDED` (`Queries per minute` quota, "Attempt 1/3 failed") at
  2026-07-16 14:47:43, then went completely idle: 0 B transferred, ~0 CPU consumed,
  no network sockets, no further log lines besides periodic 60s stats heartbeats
  showing 0 B/0 B, for the following ~1.5 days. rclone never logged "Attempt 2/3" —
  this is a hang/deadlock after the first 403, not an active retry loop.
- Side effect: `tgw-cloud-sync`'s blocking `flock` on `/run/lock/tgw-rclone-gdrive.lock`
  meant `tgw-itemdata-sync.service` (continuous loop, non-blocking flock) skipped
  every single cycle since the hang began — 1699 consecutive "skipped — lock held"
  entries in `rclone-itemdata-sync.log`, i.e. ItemData→GDrive sync has been starved
  for ~1.5 days as a knock-on effect.
- No successful completed run of `tgw-cloud-sync.service` exists anywhere in the
  journal history (`journalctl -u tgw-cloud-sync.service` back to 2026-07-03): every
  invocation ends in `killed/signal` (superseded by next day's timer) or
  `exit-code=1/FAILURE` (403 after --retries exhausted), until this run which just hangs.

## Action taken

This is within packet step 3 ("if no successful completed run exists yet, trigger it").
Restarting the hung `tgw-cloud-sync.service` (`systemctl restart`) to: (a) unstick the
lock so `tgw-itemdata-sync` can resume, (b) get a clean, actually-progressing run to
observe to completion. Will monitor via the Bash background-poll pattern (no tight loop),
expected ~43min+ for first full pass, though the rate-limit-throttled real-world timing
so far has run much longer (up to 15h "Attempt N" spacing seen historically) — will let it
run and report actual observed duration, not assume the original 43min figure still holds.

Filing the hang/deadlock-after-403 behavior as a NEW out-of-scope finding (not fixing it
here per packet: "if a real 403 recurs even at tpslimit 2, do not attempt further fixes
yourself — report... and stop"). Restarting a hung process is incident response, not a
retry-logic code fix, so it stays in scope for step 3's "trigger a real run."
