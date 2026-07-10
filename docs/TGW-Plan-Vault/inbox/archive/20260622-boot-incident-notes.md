# Boot Incident Notes — 2026-06-22

**Status at end of session:** Repair attempt 4 staged — commands ready to apply, reboot pending.

## Root Cause

ISO creation process (`TGWMX25-FINAL-BEFORE-NIXsnapshot-20260622_1707.iso`) removed kernel
and initrd from `/boot/` on the live running MX system while it was still running. First
symptom was KDE icons/menus disappearing mid-ISO-creation while terminal stayed up.

## Repair History

**Attempt 1 (prior session):** Booted to bare GRUB prompt. Replaced GRUB files from ISO.
Result: GRUB menu showed Windows + System setup only — no Linux entry. Kernel was missing.

**Attempt 2 (prior session):** Another GRUB repair. Made it worse — only System setup remained.

**Attempt 3 (prior session — PARTIAL):**
1. Booted April ISO (has networking)
2. rootMX25 (nvme0n1p2) auto-mounted at `/media/db/rootMX25`
3. Loop-mounted new ISO from Ventoy
4. Mounted squashfs at `/mnt/newiso/antiX/linuxfs`
5. Copied all 5 kernel versions into `/media/db/rootMX25/boot/`:
   - 6.12.48+deb13, 6.12.85+deb13, 6.12.86+deb13, 6.12.90+deb13, 6.12.90+deb13.1-amd64
6. `update-grub` in chroot found all 5 — output clean
7. EFI entry `Boot0001* MX → \EFI\MX\shimx64.efi` already present from prior repair
Result: Kernel now loads. Boot progresses to systemd, services start, then HANGS.
- samba fails, lighttpd fails, then screen freezes (no text login appears)
- GRUB graphics (theme) missing — /boot/grub/themes/ directory absent (cosmetic)

**Attempt 4 (this session — staged, not yet applied):**
Diagnosis from April ISO chroot analysis:
- `/boot` is intact: all 5 kernels + initrds present ✓
- samba/lighttpd BINARIES present ✓ — they fail for config/dep reasons, not missing files
- `initrd.img` symlink at / is MISSING (only `initrd.img.old` exists)
- `vmlinuz.old` symlink at / is MISSING
- SDDM (display manager) is the likely HANG cause — KDE libraries wiped by ISO creation,
  SDDM loops crashing/restarting silently, no text login appears because graphical target owns TTY
- NetworkManager-wait-online AND ifupdown-wait-online BOTH in network-online.target.wants — conflict

Commands to apply FROM APRIL ISO (as root) before next reboot:

```bash
# Step 1 — restore missing symlinks
cd /media/db/rootMX25
ln -s boot/initrd.img-6.12.90+deb13.1-amd64 initrd.img
ln -s boot/vmlinuz-6.12.90+deb13-amd64 vmlinuz.old

# Step 2 — bind mounts for chroot
mount -t proc proc /media/db/rootMX25/proc
mount --rbind /sys /media/db/rootMX25/sys
mount --rbind /dev /media/db/rootMX25/dev
mount --rbind /run /media/db/rootMX25/run

# Step 3 — chroot: disable SDDM, set text-mode default
chroot /media/db/rootMX25 bash
  systemctl disable sddm
  systemctl set-default multi-user.target
  exit

# Step 4 — remove ifupdown-wait-online conflict
rm /media/db/rootMX25/etc/systemd/system/network-online.target.wants/ifupdown-wait-online.service

# Step 5 — unmount
umount -l /media/db/rootMX25/proc
umount -l /media/db/rootMX25/sys
umount -l /media/db/rootMX25/dev
umount -l /media/db/rootMX25/run
```

Then reboot into MX from GRUB.

Expected result: text login on TTY1. Run `journalctl -xb` to see remaining failures.

## After Text Login — Options

1. **Repair KDE**: `apt-get install --reinstall task-kde-desktop plasma-desktop sddm`
   then `systemctl set-default graphical.target && systemctl enable sddm && reboot`

2. **NixOS cutover**: All prerequisites are met. Skip MX repair entirely.
   - disko config has `/dev/sda` placeholder — must change to `nvme0n1` before running disko
   - See `docs/TGW-Plan-Vault/plan/PLAN-nixos-migration.md` for Phase 5 steps

## Data Sync State (run from April ISO this session)

Script: `/media/db/TGW/bin/data-sync` — destination was `/media/db/TGW-ITEMDATA/`
- Did NOT complete (killed partway through)
- `/media/db/TGW-ITEMDATA/root/` — April ISO system root (NOT rootMX25) — useful for file recovery
- `/media/db/TGW-ITEMDATA/home/` — April ISO /home (minimal, not db user's data)
- `/opt/TGW/` sync: status unknown — may be partial or not run
- Whisper/Ollama model sync: status unknown
- ItemData on sde1: INTACT (separate physical disk)

## Disk Layout (confirmed)

| Device | Size | FS | Label | Role |
|--------|------|----|-------|------|
| nvme0n1p1 | 512M | vfat | EFI-SYSTEM | EFI boot partition |
| nvme0n1p2 | 175.8G | ext4 | rootMX25 | MX Linux OS root |
| nvme0n1p3 | 300.7G | btrfs | TGW | TGW data + source |
| sda5 | 80G | btrfs | tgw-catio-nix | NixOS test machine |
| sda7 | 718.1G | btrfs | TGW-SNAPSHOT-0 | db home dir snapshot |
| sde1 | 465.8G | btrfs | TGW-ITEMDATA | ItemData (intact) |
| sdf1 | 465.8G | btrfs | db-home | db home (separate disk) |
| sdd1 | 698.6G | btrfs | trader_grims_backup | Backup drive |
| sdc5 | 1.8T | ext4 | MasterArchive | Archive |

## Key Facts

- **pg_dump: DONE** before ISO creation — PostgreSQL data is safe
- **ItemData: INTACT** on sde1 (separate physical disk, untouched)
- **TGW partition: INTACT** on nvme0n1p3 (source, config, all fine)
- **NixOS cutover prerequisites ALL MET** — can skip MX repair and go straight to Phase 5
  - disko config has `/dev/sda` placeholder — must change to `nvme0n1` before running disko
- **April ISO limitation**: No networking — cannot apt-install from it directly
- **New ISO limitation**: No networking — not useful for package repair
