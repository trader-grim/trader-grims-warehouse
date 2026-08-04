# INPROGRESS — todo #1492 (PP-PORTABLE-CATALOG-001 basic launch/connect verification)

Working in worktree `/opt/TGW/var/worktrees/1492-flutter-launch-verify` on branch
`todo/1492-flutter-launch-verify`.

Verification/diagnostic packet only — not touching Phase A/B/C remediation code.

Findings so far (both confirmed live):
1. The Flutter Linux desktop app DOES build/launch/connect on tgw-prod itself, using
   the existing `/opt/TGW/bin/tgw-app` wrapper + a build already present at
   `apps/tgw_app/build/linux/x64/release/bundle/` (dated 2026-06-29, matches current
   `lib/` source — not stale). Launched live on the Sway desktop (workspace `2:tgw`),
   screenshotted, showed "ONLINE" and live queue-status tiles whose counts were
   cross-checked byte-for-byte against `psql state_machine` — genuine live connection,
   not cached/mock data.
2. a1131 has no Flutter SDK/toolchain at all — the app cannot be built or run there
   today. No Android SDK/NDK either (known gap, see memory
   `project-flutter-android-app-wanted`). So the only place this app currently runs is
   tgw-prod's own desktop — not actually "two devices on the LAN" as Dave's framing
   assumed.
3. Located the undocumented wrapper Dave had Tigwa build: on a1131, `tigwa`'s
   `~/.local/bin/tgw-prod` (Python) + `~/.config/fish/functions/tgw.fish` — SSHes into
   `db@tgw-prod` and execs the real `tgw` CLI there, piping argv/stdin through. Verified
   live: `tgw list --limit 2` returned real ItemData JSON from a1131's fish shell.
   Never documented in the plan vault before now.

Writing result manifest next: `docs/TGW-Plan-Vault/plan/packets/results/1492-RESULT.md`.
