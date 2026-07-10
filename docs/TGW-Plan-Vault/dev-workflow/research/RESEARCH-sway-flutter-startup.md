# DONE: PP-WM-001 Sway TGW-ify + Flutter App Startup Fix

**Session:** 2026-06-29 (session 36, continuation of session 35)
**Todo:** #1074 (marked done)

## What was done

### Flutter app: replaced flutter_secure_storage with plain file storage
- Created `apps/tgw_app/lib/config/tgw_config.dart` — new helper that reads/writes
  `~/.config/tgw/` plain files on Linux (api-key, base-url, db-path, thumbnail-dir)
- Patched `api_client.dart`, `settings_screen.dart`, `offline_db.dart` to use TgwConfig
  (all FlutterSecureStorage references removed; package removed from pubspec.yaml)
- Root cause: flutter_secure_storage pulled in libsecret → tinysparql (GNOME tracker),
  which timed out trying to connect to the tracker D-Bus daemon (~1-2 min delay)

### Flutter app: rebuilt without libsecret dependency
- Build requires nix-shell with: cmake ninja pkg-config gtk3 clang libsecret sysprof libepoxy fontconfig
- cmake flags needed: `-DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++`
  and `-DCMAKE_EXE_LINKER_FLAGS="-L<epoxy-lib> -L<fontconfig-lib> -lepoxy -lfontconfig"`
- Bundle lib/ no longer contains libflutter_secure_storage_linux_plugin.so

### Flutter app wrapper /opt/TGW/bin/tgw-app
- Added LD_LIBRARY_PATH caching to `~/.cache/tgw/flutter-libpath` (rebuild on binary change)
- Added env vars to suppress all GTK D-Bus init that blocks startup:
  - `NO_AT_BRIDGE=1` — AT-SPI accessibility bridge
  - `GSETTINGS_BACKEND=memory` — dconf/GSettings
  - `GIO_USE_VFS=local` — GVfs
  - `GTK_MODULES=""` — colorreload/window-decorations modules
  - `GTK_USE_PORTAL=0` — xdg-desktop-portal settings (root cause of 3-min delay)

### Root cause of 3-minute startup delay
- `xdg-desktop-portal-gtk` was failing because `WAYLAND_DISPLAY` was not exported into
  the systemd user session. It tried to open `:0` (X11) and crashed, then the main
  portal timed out waiting for it (25s per query, repeated ~7 times = ~3 minutes).
- Fix 1 (immediate): `GTK_USE_PORTAL=0` in wrapper bypasses all portal queries
- Fix 2 (permanent): Added to sway config:
  `exec systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP DISPLAY`
  Also ran this live; xdg-desktop-portal-gtk is now active and running.

### Permissions script updates
- `/opt/TGW/bin/tgw-permissions-reset.sh` + repo copy updated:
  - Flutter SDK exception: `flutter/bin/` and `*.sh` get chmod 0750 (not 0640)
  - Flutter bundle .so*: removed `-type f` so symlinks are included

## What is still open
- **a1131 setup**: still needs sway + lan-mouse installed. See earlier session notes in
  `docs/TGW-Plan-Vault/dev-workflow/research/a1131-client-desktop-setup.md`.
  Next step: `sudo nixos-rebuild switch --flake .#a1131` on a1131, then write
  `~/.config/lan-mouse/config.toml` on both hosts.

## Build recipe (for reference if rebuilding Flutter app)
```bash
cd /opt/TGW/src/trader-grims-warehouse/apps/tgw_app
EPOXY=/nix/store/sknpzccsnkv0kjszbbyhls9cfx4z80r9-libepoxy-1.5.10/lib
FONTCFG=/nix/store/74z7naywq3fzikbsbb0248y7j6mgcmi6-fontconfig-2.16.0-lib/lib
sudo -u tgw nix-shell -p cmake ninja pkg-config gtk3 clang libsecret sysprof libepoxy fontconfig --run '
  export CC=clang CXX=clang++
  rm -rf build/linux
  cmake -G Ninja -B build/linux/x64/release \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
    -DFLUTTER_TARGET_PLATFORM=linux-x64 \
    "-DCMAKE_EXE_LINKER_FLAGS=-L$EPOXY -L$FONTCFG -lepoxy -lfontconfig" \
    "-DCMAKE_SHARED_LINKER_FLAGS=-L$EPOXY -L$FONTCFG -lepoxy -lfontconfig" \
    linux && \
  ninja -C build/linux/x64/release install
'
sudo chmod +x build/linux/x64/release/bundle/tgw_app
```
