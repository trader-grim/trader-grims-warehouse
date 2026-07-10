# INPROGRESS: a1131 client desktop setup

**Session**: 2026-06-27 (session 29, resumed after API timeout)
**Status**: COMPLETE — config pushed to a1131, activated successfully

## What was done

1. Added Step 4 to CLAUDE.md (mandatory todo + inbox note before any changes)
2. Created `.claude/skills/tgw-exit/SKILL.md` — exit skill for session cleanup
3. Created 5 new nix files for a1131 desktop setup:
   - `nix/os/plasma.nix` — KDE Plasma 6 session alongside Qtile
   - `nix/os/input-leap-server.nix` — KVM server on tgw-prod (a1131 is below)
   - `nix/os/input-leap-client.nix` — KVM client on a1131
   - `nix/os/power-server.nix` — screen off only, no suspend (generator power)
   - `nix/os/power-client.nix` — full power management on a1131
4. Modified 4 existing nix files:
   - `nix/os/desktop.nix` — added scrcpy; fixed kdeconnect package conflict with plasma6
   - `nix/home/db.nix` — added `solaar -w hide &` to autostart
   - `nix/hosts/tgw-test.nix` — imports plasma/input-leap-client/power-client; Syncthing 8385/22001
   - `nix/hosts/tgw-prod.nix` — imports input-leap-server/power-server
5. Fixed `scripts/tgw-push-config.sh` to use `~/tgw-flake` (real path) not the repo symlink
6. All changes staged in `~/tgw-flake` git repo and passed `nix flake check --no-build`
7. Pushed to a1131 (192.168.60.101) — activation succeeded

## State on a1131 after push

- Syncthing running on 8385 (GUI) / 22001 (sync) ✓
- Input Leap client loaded but inactive — needs graphical login to activate ✓ (expected)
- NixOS 25.05.20260102.ac62194 confirmed ✓
- Plasma 6 available in SDDM session picker (not yet verified visually)

## What still needs doing

- Log in to a1131 graphically; verify Plasma 6 + Qtile both appear in SDDM
- Verify Input Leap client autostart after login; pair with server (tgw-prod)
- Pair Syncthing devices: prod ↔ a1131 for ItemCatalog folder
- Process unprocessed SUGGESTIONS.md items (catio-0.1.0 rescue ISO, PP-AGENTIC-PRICE-001)
- Push input-leap-server + power-server to tgw-prod (prod push not done yet)
