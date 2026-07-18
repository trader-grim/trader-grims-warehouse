# Result: 1264 cloud-sync-live-confirm
Status: blocked
Todo: #1264   PP: PP-BACKUP-001

Files touched: none (investigation/verification only, no code changes)

## Pre-flight (live, invariant C11)

1. `--tpslimit 2` confirmed present in both `bin/tgw-cloud-sync` and
   `bin/tgw-itemdata-sync` on current `catio-nix-0.0.1-alpha` HEAD (landed
   2026-07-10, commit `18d4406 fix(#1264): add --tpslimit 2 to rclone
   cloud-sync + itemdata-sync`). No drift since the todo was written.
2. `journalctl -u tgw-cloud-sync.service` / `/opt/TGW/var/log/rclone-sync.log`
   history reviewed back to 2026-07-03: **no successful completed run of
   `tgw-cloud-sync.service` exists at any point**, before or after the
   `--tpslimit 2` fix. Every invocation since the fix landed
   (2026-07-10 09:43, 2026-07-13 16:10, 2026-07-15 15:09, 2026-07-16 13:10,
   2026-07-17 23:41) shows `INFO: Starting transaction limiter: max 2
   transactions/s with burst 1` (confirming the flag is actually being
   applied at runtime, not just present in the script) followed by a
   `403 Quota exceeded ... RATE_LIMIT_EXCEEDED` on the very first "list
   directory" call, retried 3x (rclone's `--retries` default), and failing
   every single attempt in every run.
3. A prior interrupted attempt at this same packet (branch history/breadcrumb
   inherited from before this session) found the 2026-07-16 13:10 run hung
   indefinitely after its first 403 rather than exhausting retries and
   exiting — a separate hang/deadlock bug, filed as an out-of-scope finding
   below, not fixed here per the packet's explicit scope limit. That prior
   session's `systemctl restart` unstuck the hang and let
   `tgw-itemdata-sync` resume; no further action was needed on that thread
   this session.

## Live run triggered/observed this session

The regular daily timer (`tgw-cloud-sync.timer`) had already started a run at
**2026-07-17 23:41:18 PDT** before this session began polling (no manual
trigger was needed — the run was already in progress and live, so it was
observed to completion rather than started fresh, matching packet step 2's
"don't re-run unnecessarily" — except in this case the existing run was
still active, so this **is** the live confirmation run for this session).

- Started: 2026-07-17 23:41:18 PDT (`tgw-cloud-sync.service`, PID 483377)
- Ended: 2026-07-18 02:52:19 PDT — `Main process exited, code=exited,
  status=1/FAILURE`
- Elapsed: **3h 10m 50.6s** (`rclone-sync.log`: `Elapsed time: 3h10m50.6s`)
- Verified single-instance: `rclone-itemdata-sync.log` shows 1699+ (now
  further extended) consecutive `cycle N skipped — lock held by another
  rclone process` entries for the full duration of this run, confirming
  only ONE rclone process was active against the API during the failure —
  ruling out "two concurrent instances" as a contributing cause this time.
- 3 full retry attempts, each doing a fresh `--fast-list` root-directory
  read, all three failed identically:
  - Attempt 1/3 @ 01:23:43 — `403 ... RATE_LIMIT_EXCEEDED`
    (`quota_metric: drive.googleapis.com/default`, `quota_limit:
    defaultPerMinutePerProject`)
  - Attempt 2/3 @ 02:20:04 — same error
  - Attempt 3/3 @ 02:52:19 — same error, retries exhausted, rclone reports
    `Errors: 1 (retrying may help)` and exits 1
  - Net transfer across the whole 3h11m run: `Transferred: 0 B / 0 B` —
    the sync never got past the initial directory listing on any attempt.
- `tgw-itemdata-sync.service` resumed normally the moment the lock released
  (02:52:41, "Starting transaction limiter" logged, files copying again
  within seconds) — confirms this run did NOT hang like the 07-16 one;
  it correctly exhausted retries and exited.
- Current state (post-run, still live): `tgw-cloud-sync.timer` active,
  next trigger 2026-07-19 02:30 PDT (~23h out); no immediate manual
  re-trigger performed, per the "do not attempt further fixes" instruction
  below.

## Live evidence (verbatim, tail of `/opt/TGW/var/log/rclone-sync.log` for this run)

```
2026/07/18 02:52:19 ERROR : Google drive root 'TGW': error reading destination root directory: couldn't list directory: googleapi: Error 403: Quota exceeded for quota metric 'Queries' and limit 'Queries per minute' of service 'drive.googleapis.com' for consumer 'project_number:202264815644'.
...
"quota_limit": "defaultPerMinutePerProject",
"quota_metric": "drive.googleapis.com/default",
"reason": "RATE_LIMIT_EXCEEDED"
...
2026/07/18 02:52:19 ERROR : Attempt 3/3 failed with 1 errors and: couldn't list directory: googleapi: Error 403: ... RATE_LIMIT_EXCEEDED
2026/07/18 02:52:19 INFO  :
Transferred:            0 B / 0 B, -, 0 B/s, ETA -
Errors:                 1 (retrying may help)
Elapsed time:   3h10m50.6s
2026/07/18 02:52:19 NOTICE: Failed to sync: couldn't list directory: googleapi: Error 403: ... RATE_LIMIT_EXCEEDED
```

`systemctl status tgw-cloud-sync.service`:
```
Active: failed (Result: exit-code) since Sat 2026-07-18 02:52:19 PDT
Main PID: 483377 (code=exited, status=1/FAILURE)
IP: 42.3M in, 13.7M out
CPU: 51.102s
```

## Conclusion

**The `--tpslimit 2` fix does not resolve the 403 RATE_LIMIT_EXCEEDED
failure.** Every run since the fix landed on 2026-07-10 (5 separate timer
firings spanning 8 days, plus this session's fresh confirmation) has failed
with the identical `RATE_LIMIT_EXCEEDED` error on the very first directory
listing, verified with only one rclone process active. `tgw-cloud-sync.service`
has **never** completed a full sync, before or after the fix. Per the
packet's explicit instruction ("If a real 403 recurs even at tpslimit 2, do
not attempt further fixes — report the failure and stop; that needs Dave's
input on further tuning"), no further tuning was attempted this session.

Root-cause note for Dave's next tuning pass (observation, not a fix
attempted): the failure happens on the very first `--fast-list` directory
read, before any transfers/checkers even start, and 3 consecutive attempts
in the same run all fail identically — suggesting the per-minute quota may
already be exhausted from the *previous* failed run's retries (each attempt
does a full recursive listing of a large tree) rather than from
transfer-time throughput; `--tpslimit` caps steady-state rate but does not
prevent a single `--fast-list` recursive walk from firing a burst of
list-page requests. This is an observation for Dave to weigh, not a change
made here.

## Deviations from spec

None. Followed the packet's pre-flight → trigger/observe → stop-on-403
sequence exactly.

## Out-of-scope findings filed

- **Hang/deadlock-after-403 in `tgw-cloud-sync.service`** — observed once
  (2026-07-16 13:10 run hung ~1.5 days with zero throughput/CPU after its
  first 403 instead of exhausting retries and exiting, starving
  `tgw-itemdata-sync` via the shared flock for the same duration). NOT
  reproduced in this session's run (which correctly failed after 3
  attempts and released the lock normally), so it may be intermittent.
  Filed as **#1517** (`pp_ref=PP-BACKUP-001`): "tgw-cloud-sync.service
  observed to hang (not fail) after a Google Drive 403 RATE_LIMIT_EXCEEDED
  — starved tgw-itemdata-sync via shared flock for ~1.5 days on 2026-07-16
  (13:10 run); not reproduced 2026-07-18 (that run correctly exhausted 3
  retries and exited). Intermittent hang/deadlock, needs a timeout/watchdog
  on the flock hold or rclone subprocess."
- `bin/dedupe-gdrive.sh` — left untouched per packet's explicit
  out-of-scope instruction.
