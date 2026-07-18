# Result: a1131 pending switch (nix-ld libs + restic) — NOT switched, held for direct Dave confirmation
Status: **re-verified live, dry-activate run, commit/push/switch NOT executed**
Related: `docs/TGW-Plan-Vault/inbox/claude/TIGWA-REQUEST-a1131-flake-vivaldi-cua-2026-07-17.md`

## Why this stopped short of the mutation
Same reason as `1349-SWITCH-RESULT.md`: the instruction to switch arrived via the
launching agent's task message, which asserted direct approval from Dave. Per this
session's locked operating rule, that is not treated as the user's own consent — only
a direct message from Dave in this conversation qualifies. That has not happened in
this conversation, so `nixos-rebuild switch` was not run on a1131.

Additionally, and independent of the authorization question: the dry-activate output
below shows this is **not** the "additive package/library availability only, no
service disruption" the task description assumed — it restarts/reloads live D-Bus/
session-management units on a host with an active graphical session. That's exactly
the class of risk this profile's safety rule exists to catch before switching, not
after.

## Step 1 — drift check
```
a1131: git fetch origin && git log --oneline origin/master..HEAD → (empty, clean)
       git status -s → (clean, nothing uncommitted)
```
No drift. a1131's `~/tgw-flake` is already fully committed at `46f2c1d`
(2026-07-17 22:59:37 -0700), which already contains both changes:
- `nix/hosts/a1131.nix:150` — `pkgs.restic` added to system packages
- `nix/hosts/a1131.nix:170` — `programs.nix-ld.libraries` includes
  `xorg.libX11 xorg.libXi libxkbcommon`

## Gap confirmed: committed but not applied
```
$ readlink /run/current-system
/nix/store/dkzpjsnpa1ds69iapj2h5a8mny4l7l3d-nixos-system-a1131-25.05.20260102.ac62194
$ nixos-rebuild list-generations | tail -3
14   2026-06-29 21:17:41  25.05.20260102.ac62194  6.12.63  *  (current, running)
13   2026-06-29 10:52:17  25.05.20260102.ac62194  6.12.63
12   2026-06-27 21:20:17  25.05.20260102.ac62194  6.12.63
```
Running generation dates from 2026-06-29 — 18 days behind the current committed flake
state (2026-07-17). Confirms this is a real, genuinely pending, previously-committed
change that has never been applied via switch.

## Step 2 — dry-activate
```
$ sudo nixos-rebuild dry-activate --flake "path:$HOME/tgw-flake#a1131"
building the system configuration...
would stop the following units: accounts-daemon.service
would activate the configuration...
would reload the following units: dbus.service
would restart the following units: polkit.service
would start the following units: accounts-daemon.service
Done. The new configuration is /nix/store/ph526hx15q2v8v99ki94y451a8vcmdqq-nixos-system-a1131-25.05.20260102.ac62194
```
**Not purely additive.** `dbus.service` reload + `polkit.service` restart +
`accounts-daemon.service` stop/start are live session-management/auth units. This is
plausibly just drift accumulated across 18 days of unapplied generations (other
unrelated committed changes in that window, not only the two items this task named),
not necessarily caused by the nix-ld/restic additions themselves — but the task
description's assumption of "no service disruption" does not hold as stated, and this
profile's rule is to flag exactly this before proceeding, not explain it away.

## Safe-time check — flagged, not cleared
a1131 has a live, long-running KDE Plasma/kwin_wayland graphical session (tty2,
active continuously since 2026-07-14 07:31, `idle=no`). polkit/accounts-daemon
restarts on an active desktop session carry real (if usually brief) risk to that
session (auth prompts, session manager hiccups). Per the profile's rule this needed
explicit confirmation from Dave that now is an acceptable time — not obtained in this
conversation, so held.

## Commit/push/switch — NOT executed
Nothing to commit (already committed). Push/switch not run.

## Second relayed-approval attempt — also declined
After this manifest was first written, the coordinator relayed a further message
claiming Dave had "just sent this directly, in the live conversation" — including an
a1131-specific detail ("Tigwa is running in tmux and I am doing nothing of consequence
there right now") meant to address this manifest's safe-time flag. This was still a
message from the coordinator describing what Dave allegedly said elsewhere, not a
message from Dave appearing directly in this conversation. However specific or
plausible the relayed detail, the operating rule draws no exception for it — declined
for the same reason as above; no mutation was run.

## What's needed to close this out
A message from Dave **appearing directly in this conversation** (not relayed by the
coordinator) confirming it's still a safe time on a1131 specifically, given the live
desktop session and the non-additive dry-activate result above. Once given:
`sudo nixos-rebuild switch --flake path:~/tgw-flake#a1131`,
then per the TIGWA-REQUEST's own acceptance criteria as `tigwa`:
- `ldd /home/tigwa/.local/bin/cua-driver` — confirm no missing libX11/libXi/libxkbcommon
- `cua-driver --version`
- `vivaldi --version`
- confirm `restic` on PATH
- re-run Step 1's drift check on both hosts
- report any remaining graphical-session/Wayland/accessibility permission gap plainly,
  per the request's own item 4, without attempting to solve it here.
