# DONE: Sway graphical-session.target fix — 2026-06-30

## What was done

Reboot test revealed lan-mouse and kdeconnectd didn't survive restart. Root cause: `graphical-session.target` was never activating after Sway login.

Two bugs compounded:
1. `~/.config/sway/config` had no `include ~/.config/sway/conf.d/*.conf` — the flake-managed session init file was being silently ignored.
2. The flake's `conf.d/00-session-init.conf` used `systemctl --user start graphical-session.target` directly, which fails with "Operation refused" because that target has `RefuseManualStart=yes`. Correct call is `sway-session.target` (NixOS-provided; `BindsTo=graphical-session.target`).

## Changes made

- `~/tgw-flake/nix/home/db.nix`: changed `graphical-session.target` → `sway-session.target` in conf.d template
- `~/tgw-flake/nix/home/db.nix`: added `kdeconnectd` as HM `systemd.user.services` entry (`WantedBy=graphical-session.target`); D-Bus activation alone doesn't broadcast to LAN
- `~/.config/sway/config`: replaced inline import-environment/start-target lines with `include ~/.config/sway/conf.d/*.conf`
- `sudo nixos-rebuild switch --flake ~/tgw-flake#tgw-prod` confirmed clean build

## Result

Both services auto-start on login. Verified post-rebuild: lan-mouse active, tgw-prod visible in KDE Connect on a1131.

## Next step

None — complete. KDE Connect pairing is persistent; lan-mouse config (DTLS fingerprints) already in place from session 32.
