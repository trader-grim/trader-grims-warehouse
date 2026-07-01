# DONE: CatioNIX dual-desktop wiring — session 32 (2026-06-30)

## What was done

Full a1131 + tgw-prod desktop integration, committed to tgw-flake as `4c5b014`.

### Clipboard (a1131)
- Root cause: Google's JS clipboard event interception truncated at paragraph breaks
- Fix: `dom.events.clipboardevents.enabled = false` in Firefox about:config (per-user, not Nix)
- `firefox-wayland` package bakes in Wayland-native operation regardless of env var sourcing
- CopyQ installed, klipper suppressed via autostart override

### Wayland toolset
- Committed: ydotool, wl-clipboard, firefox-wayland, scrcpy; xterm/xclip/xdotool removed
- `environment.variables` (not sessionVariables) for Wayland env vars — SDDM reads /etc/environment

### lan-mouse DTLS — bidirectional cursor crossing
- **Root cause of weeks of failure**: `[authorized_fingerprints]` TOML format had key/value
  reversed. Correct format: fingerprint hash is the TOML key, label is the value.
  `contains_key()` in listen.rs checks for the fingerprint as map key.
- Both hosts now have `activate_on_startup = true` for bidirectional crossing
- Fingerprints (SHA256 of DER cert via openssl x509 -noout -fingerprint -sha256):
  - tgw-prod: `41:0c:67:38:75:56:af:8c:de:6a:59:9b:30:62:b5:bc:52:21:48:d6:34:19:93:b5:4d:11:06:1b:a1:3b:b7:8a`
  - a1131:    `f9:c4:cd:b8:aa:3d:f3:af:fa:c9:ce:1c:ef:de:e2:14:8c:e5:48:47:e4:a9:a2:d1:7c:5a:ce:de:dd:be:d1:0b`
- PEM files regenerated on both hosts; stored at ~/.config/lan-mouse/lan-mouse.pem (not Nix-managed)

### Syncthing dual-instance
- **Port assignment** (was backwards, now correct):
  - db user (NixOS services.syncthing): 8384/22000/21027 — tgw-install-bundle
  - tgw user (syncthing-tgw system svc): 8385/22001/21028 — plan vault docs/
- **Key lesson**: use `services.syncthing.guiAddress` NixOS option, NOT `settings.gui.address`.
  The latter only sets config.xml via API; the systemd unit's `-gui-address` CLI flag overrides it.
- Both GUIs bound to 0.0.0.0 (LAN-accessible): http://192.168.60.100:8384/8385, http://192.168.60.101:8384/8385
- syncthing-tgw started with `--gui-address=0.0.0.0:8385` CLI flag in ExecStart

### KDE Connect
- kdeconnectd runs as systemd user service in sway.nix
- Needs hardcoded env vars (QT_QPA_PLATFORM=wayland, WAYLAND_DISPLAY=wayland-1,
  XDG_RUNTIME_DIR=/run/user/1000, DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus)
  because service starts before Sway calls systemctl --user import-environment
- tgw-prod visible on a1131 KDE Connect after reboot

## What is still open

- KDE Connect device pairing (manual step in KDE Connect GUI — accept on both sides)
- KDE Connect clipboard sharing (will work once paired — built-in plugin)
- Syncthing tgw instances (8385 on both) need to be paired to each other via web GUI
- a1131 Plasma comes up on tty7 (cosmetic; both Sway + Plasma sessions registered by SDDM)

## Next steps

1. Pair KDE Connect devices (accept request on tgw-prod + a1131)
2. Pair tgw Syncthing instances: open http://192.168.60.100:8385 and http://192.168.60.101:8385,
   add each as a device in the other's GUI
3. Continue PP-* work — see handoff.md
