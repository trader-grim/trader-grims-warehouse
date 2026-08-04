# Result: todo #1492 flutter-launch-verify
Status: done
Todo: #1492   PP: PP-PORTABLE-CATALOG-001

## Summary (Dave's first-ever look at whether this app works)

**Launched cleanly and connected.** Not "mostly works" — a fully clean launch
against live production data, verified live on tgw-prod's own Sway desktop.
This was NOT known/verified before this session (packet explicitly says
"Dave has never seen the Flutter app fire up even once").

## 1. Flutter app launch/connect — live evidence

Command used (existing wrapper, unmodified):
```
/opt/TGW/bin/tgw-app
```
Ran on `tgw-prod` as `db` (member of group `tgw`, which owns the built
bundle), with `WAYLAND_DISPLAY=wayland-1` exported into the live Sway
session (workspace `2:tgw`, already reserved for this app per existing
sway config — did not touch Dave's active workspace `1:shell`).

**Result:** window `com.example.tgw_app` appeared within ~3 seconds,
showing the Home screen: `TGW` header, a green **ONLINE** connection-status
badge, a SKU quick-lookup box, and a live "Queue Status" grid of per-queue
job-state tiles (`ai_identify`, `alt_text`, `catalog_rebuild`, `ebay_draft`,
`ebay_legacy_sync`, `ebay_price`, `ebay_price_reducer`, `ebay_publish`,
`ebay_repush`, `ebay_sku_migrate`, `ebay_stage`, `ebay_sync`, `ebay_upload`,
`plan_render`, `pm_intake`, ...).

Screenshot captured: `docs/TGW-Plan-Vault/plan/packets/results/evidence/1492-tgw-app-launched-online.png`

**Cross-checked against the real backend, not just "looks plausible":**
```
sudo -u tgw psql state_machine -c "
  SELECT queue_name, state, count(*) FROM queue_jobs
  WHERE queue_name IN ('ebay_draft','ai_identify','catalog_rebuild')
  GROUP BY queue_name, state ORDER BY queue_name, state;"
```
returned:
```
    queue_name    |    state    | count
-------------------+-------------+-------
 ai_identify       | succeeded   |     5
 ai_identify       | cancelled   |     3
 catalog_rebuild   | succeeded   |  2559
 catalog_rebuild   | cancelled   |    15
 ebay_draft        | succeeded   | 72328
 ebay_draft        | dead_letter |  2779
 ebay_draft        | cancelled   |   552
```
— an exact match, digit-for-digit, to what the app rendered in its tiles.
This is a genuine live connection to `tgw-http` (port 7373,
`~/.config/tgw/base-url` = `http://127.0.0.1:7373`), not cached/mock data.

App closed cleanly after evidence capture (`pkill`, verified no leftover
process); Dave's workspace `1:shell` was never touched.

### What made this actually work
- A working Linux build already existed at
  `apps/tgw_app/build/linux/x64/release/bundle/` (dated 2026-06-29,
  matches the current `apps/tgw_app/lib/` source — not a stale build; the
  last commit touching `lib/` is the same session, `62b087a`).
- The existing `/opt/TGW/bin/tgw-app` wrapper (documented in
  `docs/TGW-Plan-Vault/dev-workflow/research/RESEARCH-sway-flutter-startup.md`)
  is required — it sets `NO_AT_BRIDGE=1`, `GSETTINGS_BACKEND=memory`,
  `GIO_USE_VFS=local`, `GTK_MODULES=""`, `GTK_USE_PORTAL=0` and a cached
  `LD_LIBRARY_PATH`, all of which prevent the previously-documented
  3-minute GTK-portal startup hang. Running the raw binary directly, with
  none of this, is the likely reason nobody has seen it "just work" before.
- Must run as a user in the `tgw` group with the live Wayland session's
  `WAYLAND_DISPLAY`/`XDG_RUNTIME_DIR` exported — the bundle is
  `tgw`-group-owned, not world-executable.

### The gap in the "two known devices" framing (real finding, not fixed here)
The app **only runs on tgw-prod itself** right now — there is no second
device it currently works on:
- **a1131 has no Flutter SDK/toolchain at all** (`which flutter` → not
  found, no `flutter-sdk` directory anywhere on the host). It cannot build
  or run this app today. a1131 only has read-only NFS mounts of
  tgw-prod's data/log — no local Flutter dev environment was ever set up
  there.
- No Android build has ever been produced (known gap, memory
  `project-flutter-android-app-wanted`: `android/` scaffold exists,
  SDK/NDK never installed).
- So "two known devices on the same LAN" cannot currently mean "the
  Flutter app running on two devices" — whatever Dave meant by that,
  it isn't reachable with today's toolchain state. Filed as #1527 for
  Dave to clarify (see Out-of-scope findings below) rather than guessed at.

## 2. The wrapper Tigwa built to reach tgw without the app — located, live-verified

**Found on `a1131`, in `tigwa`'s home directory** (not previously in the
plan vault at all — the master plan explicitly said this was "not yet
located," confirmed true before this session):

- `~/.local/bin/tgw-prod` (Python, executable) — execs `ssh -o
  BatchMode=yes -o ConnectTimeout=10 db@192.168.60.100 <remote command>`,
  where the remote command base64/JSON-encodes `sys.argv[1:]` and runs
  `fish -c "tgw $argv"` on tgw-prod via the remote system's own Python,
  preserving argv exactly (handles spaces/quoting correctly) and streaming
  stdin/stdout/stderr through the SSH pipe.
- `~/.config/fish/functions/tgw.fish` — a one-line fish function named
  `tgw` that just calls `~/.local/bin/tgw-prod $argv`, so from a1131's
  fish shell, typing `tgw ...` transparently runs the real, authoritative
  `tgw` CLI on tgw-prod over SSH, with a top-of-file comment: "TGW CLI
  wrapper: the authoritative tgw command runs on tgw-prod."

Both files date from 2026-07-15/16 (`stat` confirmed), i.e. built before
this session — this is exactly Dave's "had Tigwa build a wrapper... to get
to tgw without futzing around" reference.

**Live-verified from a1131:**
```
sudo -u tigwa /home/tigwa/.local/bin/tgw-prod --help
```
→ full `tgw` CLI usage/subcommand listing (get, list, search, resolve,
quality, hint-trail, audit-trail, reprice-suggest, staged,
velocity-report, seo-audit, locate, update, update-where, ...).
```
sudo -u tigwa /home/tigwa/.local/bin/tgw-prod list --limit 2
```
→ `{"ok": true, "count": 2, "items": [{"sku": "tgw201401011441054", ...}, ...]}`
— real ItemData JSON, round-tripped over SSH from a1131.

**This partially answers the packet's own framing** ("it may already
solve part of what Flutter was meant to do"): it gives Dave/Tigwa full
read/write CLI access to `tgw` from a1131 today, with zero Flutter/mobile
dependency — but it is terminal/CLI-only (no browse-by-photo grid, no
touch UI), so it is a *different* capability than the Flutter app's
visual browse/review screens, not a full substitute.

## Files touched
None in `src/` or app code — this was a verification/diagnostic packet.
Touched only:
- `docs/TGW-Plan-Vault/inbox/claude/INPROGRESS-1492-flutter-launch-verify.md` (breadcrumb, this branch)
- `docs/TGW-Plan-Vault/plan/packets/results/1492-RESULT.md` (this file)
- `docs/TGW-Plan-Vault/plan/packets/results/evidence/1492-tgw-app-launched-online.png` (screenshot evidence)

No pytest run required — no code changed. (`tgw-app` wrapper, Flutter
bundle, and the a1131 CLI wrapper were exercised live but not modified.)

## Live evidence
- Screenshot: `docs/TGW-Plan-Vault/plan/packets/results/evidence/1492-tgw-app-launched-online.png` — app window, ONLINE badge, live queue tiles.
- `psql state_machine` queue-count cross-check (above) — exact match to app's rendered numbers.
- `sudo -u tigwa /home/tigwa/.local/bin/tgw-prod --help` and `... list --limit 2` on a1131 — both returned real, live `tgw` CLI output over SSH.

## Deviations from spec
- None on the packet's letter. One judgment call flagged explicitly: I
  launched the app on tgw-prod's live Sway desktop (the same display
  Claude's own session and Dave's `db` session share) rather than
  attempting an Android/a1131 launch, because (a) a1131 has no Flutter
  toolchain at all (see above) and (b) no Android build has ever existed.
  tgw-prod's own desktop was the only place a launch was actually
  possible today. I minimized disruption (used the workspace already
  reserved for this app, `2:tgw`, not Dave's active `1:shell`; closed the
  app immediately after capturing evidence) but did not pause to ask
  first, since the packet's explicit goal was "drive it to a point where
  a user would see something" and Dave's own todo says this launch has
  literally never been observed.

## Out-of-scope findings filed
- #1526 — document the a1131 `tgw-prod` CLI wrapper properly in the plan
  vault (this result manifest is not a permanent reference doc).
- #1527 — a1131 has no Flutter toolchain; the "two known devices" framing
  needs Dave's clarification before Phase A/B/C work is scoped, since the
  app currently has exactly one place it can run (tgw-prod itself).
