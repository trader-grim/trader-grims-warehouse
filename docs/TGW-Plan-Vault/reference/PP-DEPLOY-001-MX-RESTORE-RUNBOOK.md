# PP-DEPLOY-001 — MX Snapshot Restore Image Runbook

> **⚠️ APPLICABILITY — historical, pre-NixOS. Not routine current-host
> operations.** Triaged 2026-07-18 (todo #1529/PP-RUNBOOK-001, report gap
> #10). The production host has already migrated to NixOS
> (`nixos-prod-cutover-runbook.md`, executed 2026-06-23) — this document
> describes the MX Linux era that migration replaced. Its `apt`/MX Snapshot
> commands will not work on the current host. Kept as historical
> disaster-recovery reference (Prime Directive 1 — never discard), not as
> an active procedure. **Also stale (report gap #11):** every command below
> referencing rclone remote `dbukove:` will fail outright — that remote no
> longer exists in the live config (verified live 2026-07-18:
> `sudo -u tgw rclone.conf` on tgw-prod defines only `[tgw-gdrive]`, no
> `[dbukove]`). If a genuine MX-era restore is ever needed, replace every
> `dbukove:` reference with the current `tgw-gdrive:` remote and re-verify
> paths before running anything.

**Operator runbook.** Bake one final bootable MX Linux restore image of the
current working TGW system as a safety net **before** the NixOS migration
(PP-NIXOS-001). If anything goes wrong during/after cutover, this image restores
the exact working platform.

> This is a checklist for Dave to run. It changes nothing on its own. Read it
> through once before starting. Companion: `../../../nix/README.md` (the NixOS
> target) and `PP-NIXOS-001` in the master plan.

---

## 0. Context & strategy

The TGW system is three separable layers; the restore strategy treats them
differently to keep the image small and the recovery reliable:

| Layer | What | In the OS image? | Also backed up by |
|-------|------|------------------|-------------------|
| OS + app | base MX, packages, `/opt/TGW/{src,bin,config}`, venv, systemd units | **yes** | rclone `dbukove:/TGW/{src,bin}` |
| Secrets | `/opt/TGW/secrets/` (eBay, Discogs, API key, token) | **yes** (encrypt/store securely) | — (only here + offline copy) |
| Work ledger | PostgreSQL `state_machine` DB | **as a SQL dump inside the image** | — |
| Item data | `/opt/TGW/data/ItemData/` (tens of thousands of SKU dirs + photos, large) | **no — excluded** | rclone `dbukove:/TGW/data/*` |

**Why exclude ItemData from the ISO:** the photo tree is very large (would push
the ISO to hundreds of GB) and is already mirrored to Google Drive by the
existing `trader-grims-backup` / rclone jobs. The image is the *OS + platform*
safety net; item data restores from the existing backup as a second source. If
you have ample external storage and want a single-source restore, you *may*
include ItemData (see the inclusion note in §2) — but verify the backup is
current either way.

---

## 1. Pre-snapshot checklist

Run as the appropriate user (note `sudo` where shown). Goal: a quiesced,
consistent, permission-clean system before imaging.

1. **Confirm the data backup is current** (item data is NOT in the ISO):
   ```bash
   # whatever the live backup invocation is; confirm last run succeeded
   systemctl status trader-grims-backup.service
   # spot-check the remote has recent data
   rclone lsd dbukove:/TGW/data
   ```

2. **Stop the pipeline cleanly** (let in-flight jobs drain, then stop workers
   + HTTP so Postgres and ItemData are quiescent):
   ```bash
   sudo systemctl stop 'tgw-worker@*.service' tgw-http.service
   # backup watcher too, if running:
   sudo systemctl stop trader-grims-backup.service
   ```

3. **Dump the work ledger** (PostgreSQL 17 — the DB lives outside `/opt/TGW`,
   so capture it explicitly into the tree that *is* imaged):
   ```bash
   sudo -u tgw pg_dump --format=custom state_machine \
     -f /opt/TGW/var/db-backup-PRE-SNAPSHOT-$(date +%Y%m%d).dump
   # sanity: non-zero size
   ls -lh /opt/TGW/var/db-backup-PRE-SNAPSHOT-*.dump
   ```

4. **Verify permissions** (no drift; secrets owner-only). The repo ships the
   audit:
   ```bash
   sudo bash /opt/TGW/src/trader-grims-warehouse/scripts/tgw-permissions-reset.sh --check
   # if it reports drift, fix then re-check:
   # sudo bash .../tgw-permissions-reset.sh
   ls -ld /opt/TGW/secrets          # expect drwx------ tgw tgw (0700)
   ls -l  /opt/TGW/secrets          # every file 0600
   ```

5. **Record the system state** for post-restore comparison:
   ```bash
   apt list --installed > /opt/TGW/var/apt-installed-PRE-SNAPSHOT.txt
   systemctl list-unit-files | grep -E 'tgw|postgres' \
     > /opt/TGW/var/enabled-units-PRE-SNAPSHOT.txt
   uname -a > /opt/TGW/var/uname-PRE-SNAPSHOT.txt
   ```

6. **Confirm /opt/TGW and config are local** (not network/loop mounts that the
   snapshot would miss):
   ```bash
   df -h /opt/TGW /opt/TGW/data /opt/TGW/config
   findmnt -T /opt/TGW
   ```

---

## 2. Run MX Snapshot

MX Snapshot creates a bootable live ISO of the running system.

1. Launch: **MX Tools → MX Snapshot** (GUI), or `sudo mx-snapshot`.

2. **Exclusions** — in the snapshot exclude list, add (these are large and/or
   reconstructed on boot, and item data is restored from rclone):
   - `/opt/TGW/data/ItemData`  ← the big one (see §0)
   - `/var/log`, `/var/cache`, `/tmp`, `/home/*/.cache`
   - any external/removable mounts

   **Keep** (do not exclude): `/opt/TGW/src`, `/opt/TGW/bin`, `/opt/TGW/config`,
   `/opt/TGW/secrets`, `/opt/TGW/.venvironments`, `/opt/TGW/var/*.dump`,
   `/var/lib/postgresql` (the live cluster — the SQL dump from §1.3 is the
   authoritative restore path, but keeping the cluster is a useful belt-and-
   braces), `/etc` (systemd units, postgres config).

   > Single-source option: if you want ItemData inside the ISO too, do NOT add
   > the exclusion above — expect a multi-hundred-GB ISO and ensure the target
   > medium has room. Default recommendation is to exclude and restore data from
   > rclone.

3. **Name + locate the ISO** on external/network storage separate from the
   running disk:
   ```
   tgw-mx-restore-YYYYMMDD-preNixOS.iso
   ```

4. **Checksum** and store it alongside the ISO:
   ```bash
   sha256sum tgw-mx-restore-YYYYMMDD-preNixOS.iso > tgw-mx-restore-YYYYMMDD-preNixOS.iso.sha256
   ```

5. **Secure the ISO** — it contains `/opt/TGW/secrets`. Store it encrypted or on
   access-controlled media; treat it like the secrets themselves.

6. **Write a manifest** next to the ISO noting: date, kernel (`uname -a`), what
   was excluded (esp. ItemData), the DB dump filename inside it, and the rclone
   remote/path for the excluded data.

---

## 3. Verify the image boots (do this — an unverified backup is not a backup)

1. Write the ISO to a USB stick **or** boot it in a VM (safer; no hardware
   needed):
   ```bash
   qemu-system-x86_64 -m 4096 -smp 2 -cdrom tgw-mx-restore-YYYYMMDD-preNixOS.iso -boot d
   ```
2. Confirm: GRUB appears → MX live session reaches a desktop/login.
3. Confirm the checksum still matches the stored `.sha256`.
4. Loop-mount and spot-check key roots are present:
   ```bash
   sudo mount -o loop,ro tgw-mx-restore-YYYYMMDD-preNixOS.iso /mnt
   ls /mnt   # then inspect the squashfs per MX Snapshot layout for:
            # /opt/TGW/src, /opt/TGW/config, /opt/TGW/secrets,
            # /opt/TGW/var/db-backup-PRE-SNAPSHOT-*.dump
   sudo umount /mnt
   ```
5. Record: `ISO verified <date> — bootable, kernel <version>, key roots + DB dump present`.

---

## 4. Restore procedure (recover the working platform)

Use this if a NixOS cutover (or anything else) goes wrong.

1. **Prereqs:** target machine/disk, the ISO + its `.sha256`, network access for
   rclone, and the rclone config/credentials.
2. **Boot** the target from the ISO (USB or VM). Verify checksum first.
3. **Install to disk** via the MX Snapshot restore / standard MX installer
   (writes the imaged system to the target disk). Reboot from the restored disk.
4. **First-boot service check:**
   ```bash
   systemctl status postgresql
   systemctl status tgw-http 'tgw-worker@*'
   ```
5. **Restore the work ledger** (the cluster may be stale or absent depending on
   what was imaged — the dump is authoritative):
   ```bash
   sudo -u postgres createdb -O tgw state_machine   # if missing
   sudo -u tgw pg_restore --clean --if-exists -d state_machine \
     /opt/TGW/var/db-backup-PRE-SNAPSHOT-YYYYMMDD.dump
   psql -U tgw state_machine -c '\dt'               # ledger tables present?
   ```
6. **Restore item data** (excluded from the ISO):
   ```bash
   rclone copy dbukove:/TGW/data/ItemData   /opt/TGW/data/ItemData   -P --fast-list
   rclone copy dbukove:/TGW/data/ItemCatalog /opt/TGW/data/ItemCatalog -P --fast-list
   ```
7. **Fix permissions** after large restores:
   ```bash
   sudo bash /opt/TGW/src/trader-grims-warehouse/scripts/tgw-permissions-reset.sh
   sudo bash /opt/TGW/src/trader-grims-warehouse/scripts/tgw-permissions-reset.sh --check
   ```
8. **Re-enable + start the pipeline:**
   ```bash
   sudo systemctl start tgw-http.service
   tgw restart-workers          # restarts the whole worker fleet (PP-SHELL-001)
   ```
9. **Final verification:**
   ```bash
   tgw health                   # all checks green?
   tgw todo claude              # ledger/CLI responding
   ```
   Compare `apt list --installed` and enabled units against the
   `*-PRE-SNAPSHOT.txt` records from §1.5.

---

## 5. After a successful NixOS cutover

Keep this ISO + its checksum + the rclone data backup until the NixOS system has
run clean for a meaningful shakedown period (suggest ≥2 weeks of normal
operation with `tgw health` green and the pipeline processing items). Only then
consider retiring the MX fallback. Record the retirement decision in the master
plan.
