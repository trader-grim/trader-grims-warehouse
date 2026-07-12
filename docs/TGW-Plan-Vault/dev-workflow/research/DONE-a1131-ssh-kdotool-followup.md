# DONE: a1131 SSH + kdotool/ydotool follow-up (2026-07-11)

Dave reported after today's nix update (a1131 generation 48, uncommitted changes in
~/tgw-flake): SSH still "permission denied (publickey)" for him, and the new
lan-mouse-autoaccept kdotool/ydotool automation didn't work.

## Root causes found + fixed

1. **Ordering cycle (kdotool/ydotool never ran)**: `lan-mouse-autoaccept.service`
   declared `after = [ "lan-mouse.service" ]` + `wantedBy = [ "graphical-session.target" ]`,
   while `lan-mouse.service` itself is `after = [ "graphical-session.target" ]` — a real
   systemd ordering cycle. journalctl confirmed it being detected and the start job
   silently deleted at both of today's session starts (11:07, 11:11). Fix: changed
   `after` to `graphical-session.target` (matches lan-mouse.service's own pattern).
2. **ydotool socket path + missing group (fleet-wide, pre-existing)**: even with the
   cycle fixed, `ydotool` client defaults to `/run/user/<uid>/.ydotool_socket`, but
   `ydotoold` (via `programs.ydotool`) listens at `/run/ydotoold/socket` (group
   `ydotool`, mode 0660) — and `db` was never a member of that group anywhere in the
   flake. This affected tgw-prod's keyd macroboard automation too, not just this new
   a1131 feature. Fixed: added `db` to the `ydotool` group in `nix/os/users.nix`
   (shared file) + `Environment = "YDOTOOL_SOCKET=/run/ydotoold/socket"` on the
   autoaccept service.
3. **SSH "permission denied"**: NOT a config bug — a1131's own `db` account had no
   login private key at all (only a GitHub deploy key), so `ssh db@a1131` run *from
   a1131 itself* (loopback, or to tgw-prod) had nothing to offer. tgw-prod → a1131
   always worked fine. Fixed pragmatically: copied the existing
   `~/.ssh/id_ed25519_new` (`db@tgw-prod-2026`, already authorized everywhere) onto
   a1131, verified loopback and a1131→tgw-prod both work. Added a `Host tgw-prod`
   entry to a1131's `~/.ssh/config` (mirrors tgw-prod's existing `Host a1131` entry)
   so plain `scp`/`ssh db@tgw-prod` works without `-i`.

## Deploy verification

- a1131: generation 48 → 50 (cycle fix, then group+socket fix). Live `ydotool key`
  test with the socket env var returned exit 0. `nixos-rebuild switch --target-host`
  must run WITHOUT local `sudo` — root has no matching SSH key; `--use-remote-sudo`
  already handles remote privilege escalation via db's passwordless sudo on a1131.
  (First attempt with local `sudo` silently timed out mid-build; exit code from a
  `... | tail -N` pipe reports `tail`'s status, not the real command's — masked the
  failure. Don't pipe deploy commands through `tail` when the exit code matters.)
- tgw-prod: generation 81 → 82 (ydotool group only). `tgw health` (correctly run as
  `sudo -u tgw`, not `db` — secrets are owner-only 0600/0700, no group bits) is clean
  except 3 pre-existing/tracked items unrelated to this work: `backups` (mid-fix from
  an earlier WIP session, PP-BACKUP-001/#1258, new drive mount already staged
  uncommitted in `nix/hosts/tgw-prod.nix`/`nix/tgw/backup.nix`), `nats` (missing
  Python module), `ebay_sync_fallback` (already tracked as todo #1077).
- a1131's own `tgw health` showing different failures (postgres, ownership, thumbnail
  variance) is expected, NOT a regression — a1131 is client-tier: Postgres is gated
  off entirely there (`services.tgw.enablePostgres`, #1220), and its data/Ollama are
  separate from tgw-prod's (read-only NFS mount + local Ollama instance).

## Outstanding

All uncommitted in `~/tgw-flake` — this session's fixes ride alongside a larger
in-progress WIP diff (hermes.nix removal, PP-BACKUP-001 drive mounts, etc. from an
earlier session). Do NOT commit until Dave reviews the whole diff together. Dave will
verify ydotoold behavior live later and reopen if anything's still off.
