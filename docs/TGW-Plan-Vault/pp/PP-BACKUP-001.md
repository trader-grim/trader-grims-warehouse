# PP-BACKUP-001 — backup + DR (full incident detail; see also plan/PLAN-backup-dr.md)

## PP-BACKUP-001 — backup + DR
**Top operator risk: nothing running; work ledger not re-derivable.** Scripts+timers
exist in `etc/systemd/`. Operator todos #61/#146/#147; restore script #1052; DR
drills #1050/#1051. Plan: `PLAN-backup-dr.md`.

**2026-07-10 ALARM + fix (todo #1258):** `tgw health` reported db dump stale
124h (limit 26h) and rclone cloud-sync had never completed. Root cause:
`tgw-db-backup`'s script was moved 2026-07-04 to also write onto a dedicated
physical drive (`/dev/sdc1`, LABEL=`tgw-db-backup`, btrfs) mounted at
`/opt/TGW/mnt/tgw-db-backup` — but that mount was done by hand, never
declared in the NixOS flake, and the 2026-07-06 reboot silently dropped it.
Every nightly dump since failed with a bare `mkdir: Permission denied`
against the empty, root-owned mountpoint; `tgw-cloud-sync` failed
independently and separately (unrelated to the mount — its first-ever full
run had just never completed).

Immediate fix (done live, with Dave's sign-off): remounted `/dev/sdc1`,
ran `tgw-db-backup.service` to catch up the dump (confirmed via `tgw
health` — staleness cleared), kicked off `tgw-cloud-sync.service` (first
full run, long-running, left running in background).

**Durable fix — APPLIED and reboot-verified 2026-07-12** (corrected from
"NOT yet applied," Fable independent review #1338: the master plan's own
warning had gone stale — the fix was live-verified before this correction
landed). Changes shipped in `~/tgw-flake`:
- `nix/hosts/tgw-prod.nix` — new `fileSystems` entries (by-label, `nofail`)
  for all three `sdc` partitions: `tgw-db-backup`, `tgw-itemdata-snap`,
  `tgw-itemarchive` — the latter two were equally undeclared and at the
  same silent-unmount risk, just not yet symptomatic.
- `nix/tgw/backup.nix` — `tgw-db-backup.service` gets
  `unitConfig.RequiresMountsFor = "/opt/TGW/mnt/tgw-db-backup"` (same
  pattern `tgw-snapshot` already uses for its own mount) so a missing mount
  is a loud, correctly-attributed service failure instead of a confusing
  `mkdir` error.
- Validated: `nix flake check` clean for all 3 hosts; `/etc/fstab` confirmed
  containing all three `LABEL=...  nofail,x-systemd.device-timeout=5s`
  entries; **the 2026-07-11 11:11 reboot proved the fix live** — `/dev/sdc1`
  came back mounted at `/opt/TGW/mnt/tgw-db-backup` without manual
  intervention.
- Remaining open item: only the rclone rate-limit issue below (#1264) —
  the mount-durability risk itself is closed.

**Also fixed:** `tgw-restore.sh` bug; `TGW-VAULT-RESTORE.md` written
covering both restore paths, live-verified dry-run.

**A3 redesign — promoted from FUTURE-IDEAS 2026-07-18.** Automate the
encrypted secrets bundle's *distribution* (new Syncthing leg to a1131,
alongside the existing GDrive leg), demoting the manual USB-fob swap from
load-bearing to a supplementary true-air-gap copy. Passphrase/identity
custody is Dave's personal, undisclosed, out of scope. Honest 3-2-1 note:
a1131 shares tgw-prod's site, so only GDrive is genuinely off-site today —
a tablet that actually leaves the premises would be the missing leg, open
question for Dave on which device qualifies. A7 (bulk-data physical-drive
rotation) stays a separate tier. Full design: `PLAN-backup-dr.md` §5.5.

**Separate, newly-discovered issue (todo #1264):** the `tgw-cloud-sync.service`
run kicked off above did NOT succeed — it failed after 43 minutes with a
Google Drive API 403 `RATE_LIMIT_EXCEEDED` (`defaultPerMinutePerProject`,
840000/min), from listing the entire `/opt/TGW` tree in one burst on its
first-ever completed run. `tgw health`'s "backups" check is still WARN on
this. Distinct root cause from the mount issue above (which is genuinely
fixed) — needs rclone rate-limiting (`--tpslimit`/`--drive-pacer-min-sleep`)
or a chunked first sync, not a bare retry (the underlying cause is
unchanged, a retry now would likely hit the same wall).

