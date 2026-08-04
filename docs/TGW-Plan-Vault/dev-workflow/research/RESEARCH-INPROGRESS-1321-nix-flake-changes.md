# INPROGRESS #1321 — nix flake: ssh key rotation, hermes removal, vivaldi, lan-mouse/firefox a1131 fixes

Working in `~/tgw-flake` (canonical flake repo, separate from this Python repo).

## Status: edits complete, `nix flake check` clean, NOT YET applied/rebuilt, NOT committed

Dave controls git history — left uncommitted for his review. Nothing live yet.

## What changed
1. SSH key rotation: `nix/os/users.nix` (db user only, root left alone per Dave)
   and `nix/hosts/a1131.nix` (claude user) now use
   `ssh-ed25519 AAAA...h+vX db@tgw-prod-2026` (already live in
   `~/.ssh/authorized_keys` on tgw-prod, pairs with `~/.ssh/id_ed25519_new`),
   replacing the old undocumented key that was never actually rotated in the
   repo since 2026-07-06.
2. Hermes fully removed from the flake: `hermes-agent` input, `nixosModules`
   wiring on tgw-prod, `nix/os/hermes.nix` import + file deleted. `flake.lock`
   auto-updated. Per Dave: moving to userspace, same as aider-chat/pipx.
3. Vivaldi added to `nix/hosts/a1131.nix` only (Dave: "just a1131 for now").
4. NEW — lan-mouse login prompt (a1131 only): KDE Plasma 6.3.6's kwin
   InputCapture/libei backend has no persistent-grant storage (confirmed: no
   permission-store file under ~/.local/share; kwin logs re-request the
   consent every lan-mouse.service start i.e. every login). Dave chose a
   workaround over switching a1131's session to Sway: added
   `systemd.user.services.lan-mouse-autoaccept` (gated to KDE sessions only)
   that uses `kdotool` to close the LanMouse setup GUI window and `ydotool`
   to send Enter for kwin's consent overlay (not kdotool-targetable — it's
   part of kwin's own compositor UI, not a normal window). Added `kdotool` to
   a1131's systemPackages.
5. NEW — Firefox restore-session prompt (a1131 only, confirmed NOT present on
   tgw-prod — its sessionCheckpoints.json is fully clean). a1131 has no
   sessionCheckpoints.json at all: Firefox never finishes its own
   quit-application bookkeeping there before systemd's shutdown SIGTERMs the
   session. Added `systemd.user.services.firefox-graceful-quit` — on session
   stop (logout/reboot/shutdown) it SIGTERMs firefox specifically and waits
   up to 20s before letting the rest of the teardown proceed. Explicitly
   approved by Dave after the auto-mode classifier correctly paused for
   confirmation (this wasn't part of his original 3-item ask).

## Not touched
- Pre-existing uncommitted WIP (`nix/hosts/tgw-prod.nix` fileSystems +
  `nix/tgw/backup.nix` RequiresMountsFor, todo #1262 backup-drive mount fix).
- root's authorizedKeys in `nix/os/users.nix` — Dave said leave alone.

## Next steps (Dave)
1. Review diff, commit when ready.
2. `sudo nixos-rebuild switch --flake path:~/tgw-flake#a1131` — applies new
   claude-user SSH key, Vivaldi, kdotool, lan-mouse-autoaccept,
   firefox-graceful-quit. **Verify the new SSH key connects before closing
   the current session** (key-only account, no password fallback).
3. `sudo nixos-rebuild switch --flake path:~/tgw-flake#tgw-prod` (or
   `tgw-rebuild`) — applies db-user SSH key rotation + Hermes removal.
   Same verify-before-disconnect caution.
4. Live-verify all 5 items per CLAUDE.md rule #4 (done = verified live):
   - `systemctl status hermes-agent` reports unit not found
   - `vivaldi` launches on a1131
   - Log in to a1131's Plasma session fresh, confirm no lan-mouse
     accept/setup-window interruption (or note if the timing/window-name
     match needs tuning — this was written without a live login to test
     against, first real login is the actual test)
   - Reboot a1131 with Firefox open + a few tabs, confirm no restore prompt
     on next launch
