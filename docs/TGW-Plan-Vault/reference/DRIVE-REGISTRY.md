# TGW Drive Registry

**PP-BACKUP-001 A7** — physical drive inventory, assignments, and provisioning reference.  
Last updated: 2026-06-13.

---

## Drive assignments

### 500 GB HDDs (7 drives)

| Label | Role | Rotation | Auto-sync | What's synced |
|-------|------|----------|-----------|---------------|
| `TGW-OFFLINE-A` | Rotating air-gap primary | Weekly: swap with B/C; one always off-site | On mount | ItemData + DB dumps + secrets bundle + config |
| `TGW-OFFLINE-B` | Rotating air-gap secondary | Weekly swap | On mount | Same as A |
| `TGW-OFFLINE-C` | Rotating air-gap third slot (2 off-site, 1 home) | Monthly swing | On mount | Same as A |
| `TGW-SENTRY-01` | Always-on disaster sentry (holster) | Monthly: swap with 02 | On mount | Same as OFFLINE |
| `TGW-SENTRY-02` | Sentry hot-spare / rotation twin | Monthly swap | On mount | Same as OFFLINE |
| `TGW-ARCHIVE-01` | Cold archive overflow (MasterArchive supplement) | Manual write | Manual | Cold history data |
| `TGW-ARCHIVE-02` | Cold archive redundancy | Manual write | Manual | Cold history data |

### USB drives

**Secrets rotation pair — both carry `LABEL=TGW-SECRETS` (same label, different UUID).**
The UUID logged per sync (`/opt/TGW/var/log/secrets-rotation.log`) identifies which physical
drive was used, so you know which to swap next time.

| Label | Physical drives | Role | Size | Notes |
|-------|----------------|------|------|-------|
| `TGW-SECRETS` | 3 × 1 GB USB | Encrypted secrets bundle — rotate monthly | ≥ 1 GB each | UUID-A: keychain; UUID-B: fireproof safe; UUID-C: second off-site/spare |
| `TGW-SECRETS` (partition) | Ventoy USB ×2 (TGW-BOOT-01/02) | TGW-SECRETS partition at end of each Ventoy drive | ~remaining space | Prep: `tgw-secrets-usb-prep /dev/sdX add-part` per drive |
| `TGW-BOOT-01` | 1 × Ventoy USB | NixOS install + rebuild keychain (on-site) | ≥ 4 GB | Re-flash when NixOS version changes; carries TGW-SECRETS partition |
| `TGW-BOOT-02` | 1 × Ventoy USB | DR spare boot USB — off-site / safe | ≥ 4 GB | Identical to TGW-BOOT-01; swap after NixOS updates; carries TGW-SECRETS partition |

**USB secrets carrier format:** ext4, LABEL=TGW-SECRETS, noauto fstab entry.
Mount triggers `tgw-secrets-usb@TGW-SECRETS.service` automatically via mount-unit drop-in.
Run `tgw-secrets-usb-prep` once per drive to format + wire the trigger.

**Layout on each TGW-SECRETS partition:**
```
/media/tgw/TGW-SECRETS/
  secrets/
    secrets-YYYYMMDD.tar.gz.age   ← age-encrypted bundle (3 newest kept)
  backup-age-identity.age         ← passphrase-locked age identity (for bare-metal restore)
  .tgw-sync-stamp                 ← timestamp of last sync
```

**Bare-metal restore sequence:**
```bash
mount /media/tgw/TGW-SECRETS
# Decrypt identity using passphrase from safe/wallet:
age -d -i /dev/stdin /media/tgw/TGW-SECRETS/backup-age-identity.age > /tmp/identity.txt
# Decrypt bundle:
age -d -i /tmp/identity.txt /media/tgw/TGW-SECRETS/secrets/secrets-LATEST.tar.gz.age | tar -xz
```

### Internal HDD partitions (sda — dual-boot disk, do NOT touch sda1–4 Windows)

| Label | Device | Role | Status |
|-------|--------|------|--------|
| `TGW-DATA-SNAPSHOT-0` | sda7 (btrfs, 718 GB) | Permanent `/opt/TGW/data` home + Phase A0 migration shuttle | Ready — Phase A0 migration pending |
| `TGW-PLATFORM-SNAPSHOT-0` | sda5 (btrfs, 80 GB) | Permanent `/opt/TGW/src` codebase home | Ready — Phase A0 migration pending |

### inotify backup disk (sde — to be repurposed)

| Label | Device | Current role | Future role |
|-------|--------|-------------|-------------|
| `trader_grims_backup` | sde1 (btrfs, 699 GB) | inotify rsync-hardlink target; mounted at `/opt/TGW/var/local/backups/trader_grims_warehouse` | **TGW-DATA-SNAPSHOT-1** once Phase B retires the inotify watcher — second always-on btrfs mirror of /opt/TGW/data; relabel + wire into btrfs send/receive schedule |

### Existing drives (adopt later — history cleanup in progress)

| Label | Device | Role | Status |
|-------|--------|------|--------|
| `MasterArchive` | sdf, 1.8T ext4 | Cold archive primary silo (keep label) | 81% full; ext4 stays until NixOS migration |
| `TGW-HISTORY-01` | sdg (WD10EALS 1TB ext4) | History archive — adopt after `tgw history-index` completes | 52% full; relabel then |

---

## Filesystem: btrfs

All new 500 GB drives use **btrfs** (`compress=zstd, noatime`). Existing drives (`sdc1` snapshot disk already btrfs; `sdf5`/`sdg1` ext4 — leave until NixOS reinstall).

**Subvolume layout on each 500 GB drive:**
```
/media/tgw/<LABEL>/
  @data/           ← live sync target; rsync writes here
    TGW/
      ItemData/    ← 180 G (current size)
      db/          ← daily pg_dump files
      secrets/     ← encrypted secrets bundle
      config/      ← tgw-api-config.json + category-groups.json
  @snapshots/      ← read-only post-sync snapshots (auto-pruned, keep last 8)
    2026-06-13T1430/
    ...
```

btrfs CoW means snapshots of unchanged files (most of ItemData after first sync) cost near-zero space.

---

## QR label content

QR codes are generated by `bin/tgw-offline-setup` during provisioning (printed to stdout). The content for each drive follows this template:

```
<LABEL>
<ROLE ONE-LINER>
Format: btrfs | Size: <SIZE>
Mount: /media/tgw/<LABEL>
Auto-sync: mount triggers tgw-offline-sync
Setup: <DATE>
```

Print via your label maker's QR import, or paste into any QR generator (qrencode, online tool). Apply label to the drive itself and the storage case/sleeve.

---

## Provisioning a new drive

```bash
# As root — run once per drive:
sudo /opt/TGW/src/trader-grims-warehouse/bin/tgw-offline-setup TGW-OFFLINE-A /dev/sdX

# What it does:
#   mkfs.btrfs -L TGW-OFFLINE-A /dev/sdX
#   creates @data + @snapshots subvolumes
#   adds /etc/fstab entry (noauto)
#   wires systemd mount-unit drop-in
#   prints QR label content
```

**Test the setup (after provisioning):**
```bash
mount /media/tgw/TGW-OFFLINE-A          # triggers sync service
journalctl -u tgw-offline-sync@TGW-OFFLINE-A -f   # watch progress
umount /media/tgw/TGW-OFFLINE-A         # safe to unplug
```

Do this twice before calling a drive "in service" (A7 done-when criterion).

---

## Rotation schedule

### OFFLINE drives (air-gap tier)

```
Week 1:  A is home (syncing), B + C are off-site
Week 2:  B comes home, A goes off-site, C stays off-site
Week 3:  C comes home, B goes off-site, A stays off-site
         → always ≥2 copies off-site at any moment
```

- Plug in the returning drive → auto-syncs → sticker the date on the sleeve → shelve
- On-site drive auto-syncs every time it's inserted
- Aim: ≤1 week RPO for the air-gap tier (plug in a returning drive at least weekly)

### SENTRY drives (always-on tier)

- `TGW-SENTRY-01`: stays in holster, connected to the machine
- Monthly: swap SENTRY-01 ↔ SENTRY-02 (bring 02 in, 01 goes off-site for the month)
- The incoming drive auto-syncs on first mount; becomes the new always-on drive

### SECRETS USB drives (both LABEL=TGW-SECRETS)

- After any credential change: run `tgw-secrets-backup`, then plug in each drive in turn
- UUID-A: keychain / pocket; UUID-B: fireproof safe; UUID-C: second off-site or spare
- Plug in one drive at a time — automount fires for whichever is present (same label)
- After any credential change: run `tgw-secrets-backup`, then sync each drive in turn
- The A3 timer runs monthly automatically; check rotation log: `/opt/TGW/var/log/secrets-rotation.log`
- With 3 dedicated drives + 2 Ventoy partitions you always have ≥2 copies off-machine

---

## Snapshot retention on offline drives

Each drive keeps the last **8 snapshots** (managed automatically by `tgw-offline-sync`). For a weekly-rotation OFFLINE drive, 8 snapshots = ~2 months of history on the drive itself. To adjust: change `SNAPSHOT_KEEP` in `bin/tgw-offline-sync`.

To browse snapshots on a plugged-in drive:
```bash
mount /media/tgw/TGW-OFFLINE-A
ls /media/tgw/TGW-OFFLINE-A/@snapshots/
# Pick a snapshot and explore:
ls /media/tgw/TGW-OFFLINE-A/@snapshots/2026-06-13T1430/TGW/ItemData/
```

---

## Status tracker

| Label | Provisioned | First sync | In rotation | Notes |
|-------|-------------|------------|-------------|-------|
| TGW-OFFLINE-A | — | — | — | sdd (Toshiba500) — connected, ready to format |
| TGW-OFFLINE-B | — | — | — | |
| TGW-OFFLINE-C | — | — | — | |
| TGW-SENTRY-01 | — | — | — | |
| TGW-SENTRY-02 | — | — | — | |
| TGW-ARCHIVE-01 | — | — | — | |
| TGW-ARCHIVE-02 | — | — | — | |
| TGW-SECRETS (keychain) | 2026-06-19 | 2026-06-19 | ✅ | 1 GB USB — uuid: `7160f77b-4465-441f-87d2-9635d540b1a3` |
| TGW-SECRETS (safe) | 2026-06-19 | 2026-06-19 | ✅ | 1 GB USB — uuid: assign from list below |
| TGW-SECRETS (spare/off-site) | 2026-06-19 | 2026-06-19 | ✅ | 1 GB USB — uuid: assign from list below |
| TGW-SECRETS (TGW-BOOT-01 partition) | 2026-06-19 | 2026-06-19 | ✅ | Ventoy USB partition — uuid: assign from list below |
| TGW-SECRETS (TGW-BOOT-02 partition) | 2026-06-19 | 2026-06-19 | ✅ | Ventoy USB partition — uuid: assign from list below |
| TGW-BOOT-01 | 2026-06-19 | — | ✅ | Ventoy USB (on-site) |
| TGW-BOOT-02 | 2026-06-19 | — | ✅ | Ventoy USB (off-site/safe) |

**All 5 UUIDs synced 2026-06-19 — assign to physical drives above:**
- `7160f77b-4465-441f-87d2-9635d540b1a3`
- `bac14e6c-2e96-4b39-b425-beb7d428b6f6`
- `61bab8cf-e317-43aa-9501-2be6b463178c`
- `457261bc-95a8-46d1-a99f-0c9cc5d95a84`
- `a319cac6-35f2-4a6c-810c-ccf0ae91a9eb`
