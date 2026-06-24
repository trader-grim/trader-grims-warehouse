# Boot Repair — Session 40 Notes (2026-06-23)

## Current Status

MX boots to text login ✅. Two remaining issues:

1. **Root mounted read-only** — ext4 journal on nvme0n1p2 dirty from ISO creation crash
2. **`/opt/TGW` not auto-mounting** — `nofail` in fstab causes a race; `mount -a` fixes manually

## Root RO — Correct Diagnosis

**KDE Partition Manager already ran fsck — filesystem is clean.** The ro mount is NOT a
filesystem error. Root cause is different:

The kernel always mounts root `ro` initially. `systemd-remount-fs.service` remounts it `rw`
during boot. If that service fails or doesn't run, root stays ro.

## Diagnosing Root RO (from inside MX after login)

```bash
# 1. Confirm it's actually ro and why
grep " / " /proc/mounts          # check mount options
cat /proc/cmdline                # check kernel params — look for stray 'ro'

# 2. Check if the remount service failed
systemctl status systemd-remount-fs.service

# 3. Check ext4 messages
dmesg | grep -iE "ext4|remount|readonly|error"

# 4. Quick manual fix (test if remount is possible)
sudo mount -o remount,rw /
```

## After Diagnosing — Next Steps

If `systemd-remount-fs` failed → check why (look at `journalctl -u systemd-remount-fs`).
If `ro` is in `/proc/cmdline` → GRUB kernel params need editing (`/etc/default/grub`, then `update-grub`).
If remount works manually → root filesystem is fine; something is preventing auto-remount at boot.

```bash
# Once root is rw, mount /opt/TGW
sudo mount -a

# Verify TGW
tgw health
```

## Permanent Fix for /opt/TGW Auto-Mount

Edit `/etc/fstab`, change the /opt/TGW line to add `x-systemd.automount`:

```
UUID=5b7d0a39-5962-4ca1-a0dc-6018060b4be3   /opt/TGW   btrfs   nofail,x-systemd.automount   0 0
```

## What Was Investigated This Session

- Boot 0 in MX journal was a 2-second user session (PID 1940), not a system boot
- `default.target → multi-user.target` confirmed ✅ (set in Attempt 4)
- `ifupdown-wait-online` removed ✅
- `display-manager.service` symlink still present (sddm.service) but harmless — multi-user.target doesn't pull in graphical.target
- GRUB has `quiet splash` — Plymouth was suspected but login IS present on TTY1 (text login works)
- ext4 journal dirty is the confirmed root cause of ro mount
- `/nix btrfs defaults` (no nofail) in fstab — low risk since tgw-catio-nix (sda5) is healthy

## Disk Layout (confirmed)

| Device | Label | Role |
|--------|-------|------|
| nvme0n1p1 | EFI-SYSTEM | EFI boot |
| nvme0n1p2 | rootMX25 | MX Linux root (ext4, currently dirty) |
| nvme0n1p3 | TGW | TGW data + source (btrfs, intact) |
| sda5 | tgw-catio-nix | NixOS test (btrfs, healthy) |
| sde1 | TGW-ITEMDATA | ItemData (intact, separate disk) |

## NixOS Cutover Still Available

All prerequisites met. Skip MX repair entirely if preferred.
- Fix `/dev/sda` → `nvme0n1` in disko config before running disko
- See `docs/TGW-Plan-Vault/plan/PLAN-nixos-migration.md` Phase 5
