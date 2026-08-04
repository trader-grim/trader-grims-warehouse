# IN PROGRESS — #1630 Portable Catalog basic launch/connect on a1131

**Started:** 2026-07-21, continuing the same-day remote-ops planning thread
(PP-REMOTEOPS-001, born from Dave's satellite-warehouse shipping pain).

## What's happening

PP-PORTABLE-CATALOG-001's Flutter app (`apps/tgw_app/`) has never once
successfully launched/connected on a1131 — the only known real blocker
before PP-REMOTEOPS-001's rest of the phased plan (Tailscale-join, task
worksurfaces, offline resilience) can be attempted. Dave: "you have a1131
sitting right here. make it work."

## Where I am

Diagnosing on a1131 (`ssh claude@192.168.60.101`, key-only, no sudo) —
checking whether the app is even installed/built there, what the actual
failure looks like, whether it's a build issue (per the documented
precedent in `pp/PP-PORTABLE-CATALOG-001.md` — todo #151 self-marked-done
while the build was actively failing on missing libsecret-1-dev/pubspec
deps) or a connectivity/config issue (API base URL pointing at the wrong
host, tgw-http not reachable from a1131, etc.).

Per CLAUDE.md invariant E12: diagnosing/root-causing here in the main
session is fine; once a concrete fix is scoped, the actual code change
routes to `tgw-coder` in an isolated worktree+branch, not a direct edit
here.

## Progress, same session

- a1131 checkout was genuinely stale (#1082 confirmed live): `99fd1fb`
  (2026-06-24) -> fast-forwarded to `812f691` (2026-07-19), clean.
- Root cause #1: app never built on a1131 at all (no bundle, no launcher,
  no Flutter SDK on PATH). Root cause #2: `api_client.dart` defaults
  `base_url` to `127.0.0.1:7373`, dead on a1131 (tgw-http only runs on
  tgw-prod, confirmed via curl: LAN IP 192.168.60.100:7373 -> 303,
  localhost -> connection refused).
- Flutter 3.32.0 installed via `nix-shell -p flutter` (no system install,
  matches Nix discipline). `flutter pub get` succeeded.
- Dave authorized copying the live `tgw-api-key.json` bearer token to
  `~/.config/tgw/api-key` on a1131 (auto-mode classifier correctly blocked
  this until explicit go-ahead — credential deployment, not implied by
  "make it work"). `~/.config/tgw/base-url` set to
  `http://192.168.60.100:7373`.
- Build attempt hit a real app-code bug, not infra: Flutter 3.32.0's
  `DropdownButtonFormField` no longer accepts `initialValue:` (renamed to
  `value:`), 4 call sites across `browse_screen.dart` /
  `edit_item_screen.dart`. Filed as todo #1631 (depends on #1630), packet
  written (`packets/1631-tgw-app-dropdown-initialvalue.md`), dispatched to
  `tgw-coder` per invariant E12 (app code fix, not a direct edit from the
  main session).

## Progress, continued

- #1631 (tgw-coder's 4-site rename) reviewed, build-verified live on
  a1131 (`flutter build linux --release` -> zero errors,
  `packets/results/1631-REVIEW.md`), stitched into
  `catio-nix-0.0.1-alpha` (commit `15d7210`, merge `<see git log>`),
  todo #1631 marked done.
- `tgw_app` now **builds** successfully on a1131 for the first time ever.
  Not yet **launched** — a1131 has a live shared Sway session (seat0,
  user db) that Claude/Dave both use; per the live-desktop-notice
  feedback rule, launching a GUI window there needs a heads-up first, not
  a silent pop. Dave asked to hold this specific step and queue it here
  instead, to be done fresh alongside the currently-held inbox batch
  (Tigwa's pending research/response docs) rather than mid-thread.

## Next step (queued, held per Dave's request 2026-07-21)

**Flutter GUI-launch verification on a1131** — when inbox processing
resumes: launch `/opt/TGW/bin/tgw-app`-equivalent (bundle at
`apps/tgw_app/build/linux/x64/release/bundle/tgw_app`, no launcher
script installed on a1131 yet, run directly or copy tgw-prod's
`/opt/TGW/bin/tgw-app` wrapper over first) against the live Wayland
session there, confirm it actually opens and can browse/reach the
catalog over `http://192.168.60.100:7373` (config already in place:
`~/.config/tgw/base-url`, `~/.config/tgw/api-key`). Get real visual
confirmation for Dave — screenshot or his own eyes — not just "binary
built," matching this PP's own standing "compiles != verified working"
lesson. This is PP-REMOTEOPS-001 Phase 1's actual completion condition.
