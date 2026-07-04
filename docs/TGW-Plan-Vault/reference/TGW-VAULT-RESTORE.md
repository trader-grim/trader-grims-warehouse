# TGW-VAULT USB — restore reference (PP-BACKUP-001, todo #1052)

Companion to `scripts/tgw-restore.sh`. Covers what's on the stick, how it got
there, and the two restore paths: **existing host** (just needs data back)
and **bare metal** (no NixOS host at all yet).

Naming note: earlier plan drafts called this `TGW-SECRETS-A`/`TGW-SECRETS-B`
and `TGW-SNAPSHOT-0`. Session 38 (2026-06-22) replaced that scheme with a
single `TGW-VAULT` btrfs USB. This doc reflects the current scheme.

## What's on the stick

Stamped by `scripts/tgw-usb-stamp.sh` (auto-fires on insert via
`nix/tgw/usb-vault.nix`'s udev rule, production host only):

| Subvolume | Contents |
|---|---|
| `secrets/` | rsync mirror of `/opt/TGW/secrets/` (age keys, eBay tokens, API keys) — mode 700/600 |
| `dumps/` | `pg_dump -Fc state_machine` per stamp, named `state_machine-<UTC-STAMP>.pgdump`; `latest.pgdump` symlink always points at the newest; only the 2 most recent kept |
| `flake/` | `git bundle` of the full repo (`tgw.bundle`) — clone with `git clone /mnt/tgw-vault/flake/tgw.bundle` |

## Path 1 — existing NixOS host, restoring data/secrets only

```bash
sudo bash scripts/tgw-restore.sh --source usb --dry-run   # confirm the plan first
sudo bash scripts/tgw-restore.sh --source usb
```

Stops workers, copies `secrets/` and `dumps/` off the stick, restores
secrets to `/opt/TGW/secrets/` (root:tgw ownership, 700/600 modes),
`pg_restore`s the newest dump, and tells you how to verify.

## Path 2 — bare metal (no host, or full rebuild)

1. Boot the NixOS installer (Ventoy partition on `TGW-BOOT-01`/`TGW-BOOT-02`,
   or a plain NixOS minimal ISO if you don't have a Ventoy stick handy).
2. Insert/mount the `TGW-VAULT` stick:
   ```bash
   mount /dev/disk/by-label/TGW-VAULT /mnt/tgw-vault
   ```
3. Clone the flake bundle and install:
   ```bash
   git clone /mnt/tgw-vault/flake/tgw.bundle /tmp/tgw-flake
   cd /tmp/tgw-flake
   nixos-install --flake .#tgw-prod
   ```
   Remote path (from another machine with SSH to the target, no physical
   access needed after first boot):
   ```bash
   nix run github:nix-community/nixos-anywhere -- \
     --flake .#tgw-prod \
     root@<target-ip>
   ```
4. Reboot into the freshly installed host, log in as `tgw` (see below), then
   restore data:
   ```bash
   mount /dev/disk/by-label/TGW-VAULT /mnt/tgw-vault
   cd /opt/TGW/src/trader-grims-warehouse
   sudo bash scripts/tgw-restore.sh --source usb
   ```

## Operator checks (do these on every restore, not just the first one)

- **User/password:** the `tgw` service account has no interactive password
  by design — access is via `sudo -u tgw`, not direct login. If a fresh
  install prompts for a `tgw` account password, that's a config drift from
  the flake, not expected behavior; don't set one, fix the flake instead.
- **uid=900 check:** confirm the restored host still assigns `tgw` uid 900
  (some of `/opt/TGW`'s stored file ownership assumes this):
  ```bash
  id tgw   # expect uid=900(tgw) gid=900(tgw)
  ```
  `tgw health`'s `ownership` check also verifies this and flags drift.
- **Secrets permissions:** after `tgw-restore.sh --source usb` runs, verify
  the restored tree is still locked down (the script sets this, but always
  re-check after any manual `cp`/`rsync` on `/opt/TGW/secrets/`):
  ```bash
  find /opt/TGW/secrets -type d ! -perm 700 -print   # expect empty
  find /opt/TGW/secrets -type f ! -perm 600 -print   # expect empty
  ```

## Verify the restore actually works (Prime Directive 4 — don't stop at "ran clean")

```bash
sudo -u tgw tgw health                                    # expect all-green core checks
sudo -u tgw tgw enqueue-sku --queue echo <any-sku>         # round-trip probe
systemctl start tgw-worker@echo.service
journalctl -u tgw-worker@echo.service -n 20                # confirm the job actually ran
```

See also: `PP-DEPLOY-001-MX-RESTORE-RUNBOOK.md` (pre-NixOS MX safety-net
image — different layer, same disaster-recovery goal) and
`plan/PLAN-backup-dr.md` §A3/A7 (full backup-tier design and rotation
schedule this stick is one piece of).
