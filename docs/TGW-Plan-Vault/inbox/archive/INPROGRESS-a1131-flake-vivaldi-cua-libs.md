# DONE: a1131 flake — add libX11/libXi/libxkbcommon for cua-driver-rs

Todo #1512, PP-HERMES-EA-001. Dave authorized directly (via Tigwa request,
`TIGWA-REQUEST-a1131-flake-vivaldi-cua-2026-07-17.md`), executed 2026-07-17.

## What changed
- `nix/hosts/a1131.nix`: added `programs.nix-ld.libraries` entry
  `[ xorg.libX11 xorg.libXi libxkbcommon ]`, scoped to a1131 only (merges
  with base.nix's `[ zlib openssl libpq ]` list, not a replacement).
- Commit `17f9e75` on tgw-flake, pushed to origin/master.
- a1131 generation 56 (2026-07-17 22:38:34), `nixos-rebuild switch`
  confirmed live, `/run/current-system` matches dry-activate store path.

## Acceptance evidence (all 4 gathered, see final report to Dave)
1. Plain `ldd` on NixOS is a known false-negative here — its script
   hardcodes the real glibc dynamic linker, bypassing the binary's actual
   recorded interpreter (`/lib64/ld-linux-x86-64.so.2`, nix-ld's shim), so
   it can never see nix-ld-resolved libs regardless of config. Verified via
   the binary's real interpreter directly (`/lib64/ld-linux-x86-64.so.2
   --list`, tigwa's normal env, no manual overrides) — full resolution,
   including libX11/libXi/libxkbcommon.
2. `cua-driver --version` -> `cua-driver 0.8.3` (as tigwa).
3. `vivaldi --version` -> `Vivaldi 7.6.3797.58 stable` (as tigwa).
4. tigwa has no active graphical/Wayland session (no DISPLAY,
   WAYLAND_DISPLAY, or XDG_SESSION_TYPE) — cua-driver's actual desktop
   control still needs a real session to attach to; not built here, and not
   solved by routing Tigwa into db's session (explicitly out of scope).
   Foundation-only, per the source request.

## Side note
Found a pre-existing uncommitted local change on a1131
(`pkgs.restic` addition to `nix/hosts/a1131.nix`, dated 2026-07-16,
unrelated to this task) sitting in a1131's working tree, never committed.
Preserved via git stash across the merge/switch, restored afterward exactly
as found. Not committed — not authorized for this task; flagged to Dave.
