# PLAN: Backup, archiving, and disaster recovery (PP-BACKUP-001)

**Status: APPROVED by Dave 2026-06-11 (session 24).** Designed and verified against the
live host the same day (disks, services, rclone state, dump inventory checked — not
assumed from docs); amended through all 13 of the day's suggestions before approval.
Companion to `PLAN-nixos-migration.md`; **Phase C of this plan is a hard requirement of
that one** (the completed strategy must be declarative in the Nix flake).
Phase A build = todo #60 (Claude); operator items = todo #61 (approval ✅ done; remaining:
passphrase custody, history-point preserve gate, timer install after #60, drills).

Executor notes: house rules apply — no commits until Dave asks, `tgw health` after
config/worker changes, operator applies all systemd/root-level changes.

---

## Phase A0 — /opt/TGW btrfs migration (operator, June 2026)

**Status: PLANNED 2026-06-15 (session 31).** Decided because the 500 GB USB HDDs are
unreliable; the internal HDD (sda) has two ready btrfs partitions that supersede them as
the primary on-machine tier. This migration is a prerequisite for Phase B btrfs snapshots
and unlocks `btrfs send/receive` to the offline drive tier.

**Decision: MX keeps mx-snapshot for OS snapshots (no btrfs on nvme0n1p2). NixOS will
use btrfs for the OS layer when we migrate. This plan covers /opt/TGW only.**

### Partition layout — confirmed 2026-06-15

| Device | Label | FS | Size | Current role | Final role |
|--------|-------|----|------|-------------|------------|
| nvme0n1p3 | TGW | ext4 | 295 GB | `/opt/TGW` (live) | `/opt/TGW` root — **reformat to btrfs** |
| sda7 | TGW-DATA-SNAPSHOT-0 | btrfs | 718 GB | unmounted | `/opt/TGW/data` (permanent) + migration shuttle |
| sda5 | TGW-PLATFORM-SNAPSHOT-0 | btrfs | 80 GB | unmounted | `/opt/TGW/src` codebase (permanent) |
| sde1 | trader_grims_backup | btrfs | ~699 GB | `/opt/TGW/var/local/backups/trader_grims_warehouse` (inotify watcher target) | same — survives migration |

**PostgreSQL data directory:** `/var/lib/postgresql/17/main` — on nvme0n1p2 (root OS
disk), NOT under /opt/TGW. No WAL-corruption risk from rsyncing /opt/TGW. Still run
`pg_dump` as a safety net before starting.

**sde1 sub-mount note:** sde1 is mounted inside the /opt/TGW tree. It must be unmounted
before pivoting /opt/TGW to sda7, then remounted under the new /opt/TGW after the pivot.

### Migration sequence

**Pre-flight (before touching anything):**
```bash
# 1. Safety dump — always, regardless of migration
sudo -u tgw pg_dump --format=custom state_machine \
  -f /opt/TGW/var/backups/state_machine-premigration-$(date +%Y%m%d).dump

# 2. Verify sda7 has room (needs 187 G free)
df -h /dev/sda7   # mount it first if needed
du -sh /opt/TGW --exclude=/opt/TGW/var/local/backups

# 3. Prepare subvolumes on sda7 and sda5 if not already created
mount /dev/sda7 /mnt/sda7
btrfs subvolume create /mnt/sda7/@shuttle   # full /opt/TGW temp copy
btrfs subvolume create /mnt/sda7/@data      # permanent data/ home
btrfs subvolume create /mnt/sda7/@snapshots
mount /dev/sda5 /mnt/sda5
btrfs subvolume create /mnt/sda5/@platform  # permanent codebase home
btrfs subvolume create /mnt/sda5/@snapshots
```

**Step 1 — Hot copy (services still running):**
```bash
# rsync everything to @shuttle — excludes the backup disk sub-mount
rsync -aHAX --progress \
  --exclude=/opt/TGW/var/local/backups/ \
  /opt/TGW/ /mnt/sda7/@shuttle/
# Takes a while; data/ is ~180 G. sde1 contents stay on sde1, not copied.
```

**Step 2 — Service window (downtime begins):**
```bash
sudo systemctl stop 'tgw-worker@*' tgw-http trader-grims-backup
# Wait for workers to drain any in-flight jobs (~30 s)
sudo systemctl stop tgw-worker@token_refresh  # explicit — often last one standing

# Unmount sde1 from its sub-path (required before pivoting /opt/TGW)
sudo umount /opt/TGW/var/local/backups/trader_grims_warehouse
```

**Step 3 — Delta rsync (catches changes during hot copy):**
```bash
rsync -aHAX --checksum --delete \
  --exclude=/opt/TGW/var/local/backups/ \
  /opt/TGW/ /mnt/sda7/@shuttle/
# Quick — only changed files since Step 1
```

**Step 4 — Verify before committing:**
```bash
du -sh /opt/TGW /mnt/sda7/@shuttle   # sizes should be within a few MB
pg_restore --list /opt/TGW/var/backups/state_machine-premigration-*.dump | tail -5
# spot-check 3 random SKUs between source and shuttle
```

**Step 5 — Reformat nvme0n1p3 as btrfs (point of no return):**
```bash
sudo umount /opt/TGW   # unmount the ext4 partition
sudo mkfs.btrfs -L TGW -f /dev/nvme0n1p3
# Create subvolume layout
sudo mount /dev/nvme0n1p3 /mnt/tgw-new
sudo btrfs subvolume create /mnt/tgw-new/@root
sudo btrfs subvolume create /mnt/tgw-new/@snapshots
sudo umount /mnt/tgw-new
```

**Step 6 — Pivot to sda7 shuttle (services resume on HDD):**
```bash
# Mount sda7/@shuttle at /opt/TGW
sudo mount -o subvol=@shuttle /dev/sda7 /opt/TGW

# Ensure the backup mount point directory exists on sda7 and remount sde1
sudo mkdir -p /opt/TGW/var/local/backups/trader_grims_warehouse
sudo mount /dev/sde1 /opt/TGW/var/local/backups/trader_grims_warehouse

# Restart services — TGW is now running from the HDD
sudo systemctl start tgw-worker@token_refresh tgw-http
sudo systemctl start 'tgw-worker@*'
sudo systemctl start trader-grims-backup
sudo -u tgw tgw health   # confirm green before proceeding
```

**Step 7 — Populate nvme0n1p3 and sda5 (background, services running on sda7):**
```bash
# Mount the new btrfs SSD
sudo mount -o subvol=@root /dev/nvme0n1p3 /mnt/tgw-new

# Populate @root: everything except data/ and src/ (those go to sda7/@data and sda5/@platform)
rsync -aHAX --progress \
  --exclude=data/ --exclude=src/ \
  /mnt/sda7/@shuttle/ /mnt/tgw-new/

# Populate sda5/@platform with codebase
sudo mount -o subvol=@platform /dev/sda5 /mnt/sda5-platform
rsync -aHAX --progress /mnt/sda7/@shuttle/src/ /mnt/sda5-platform/

# Populate sda7/@data with data/ (still on same disk as shuttle — btrfs reflink-copy is fast)
sudo mount -o subvol=@data /dev/sda7 /mnt/sda7-data
rsync -aHAX --progress /mnt/sda7/@shuttle/data/ /mnt/sda7-data/
```

**Step 8 — Final service window (second downtime, shorter):**
```bash
sudo systemctl stop 'tgw-worker@*' tgw-http trader-grims-backup

# Final delta — catch any writes that happened during Step 7
rsync -aHAX --checksum --delete \
  --exclude=data/ --exclude=src/ \
  /mnt/sda7/@shuttle/ /mnt/tgw-new/
rsync -aHAX --checksum --delete /mnt/sda7/@shuttle/src/ /mnt/sda5-platform/
rsync -aHAX --checksum --delete /mnt/sda7/@shuttle/data/ /mnt/sda7-data/

# Take read-only btrfs snapshots of final state (timestamped)
TS=$(date +%Y%m%dT%H%M)
sudo btrfs subvolume snapshot -r /mnt/tgw-new  /mnt/tgw-new/../@snapshots/$TS-root
sudo btrfs subvolume snapshot -r /mnt/sda7-data /mnt/sda7/@snapshots/$TS-data
sudo btrfs subvolume snapshot -r /mnt/sda5-platform /mnt/sda5/@snapshots/$TS-platform
```

**Step 9 — Pivot to final layout:**
```bash
# Unmount the backup sub-mount and sda7 shuttle from /opt/TGW
sudo umount /opt/TGW/var/local/backups/trader_grims_warehouse
sudo umount /opt/TGW   # unmounts sda7/@shuttle

# Update /etc/fstab — replace the old nvme0n1p3 ext4 entry with three new entries:
#
# LABEL=TGW              /opt/TGW        btrfs  subvol=@root,compress=zstd,noatime  0 0
# LABEL=TGW-DATA-SNAPSHOT-0  /opt/TGW/data   btrfs  subvol=@data,compress=zstd,noatime  0 0
# LABEL=TGW-PLATFORM-SNAPSHOT-0 /opt/TGW/src  btrfs  subvol=@platform,compress=zstd,noatime 0 0
# LABEL=trader_grims_backup /opt/TGW/var/local/backups/trader_grims_warehouse btrfs defaults 0 0
sudo nano /etc/fstab

# Mount everything via fstab
sudo mkdir -p /opt/TGW/data /opt/TGW/src
sudo mount /opt/TGW
sudo mount /opt/TGW/data
sudo mount /opt/TGW/src
sudo mount /opt/TGW/var/local/backups/trader_grims_warehouse
```

**Step 10 — Restart and verify:**
```bash
sudo systemctl start tgw-worker@token_refresh tgw-http
sudo systemctl start 'tgw-worker@*'
sudo systemctl start trader-grims-backup
sudo -u tgw tgw health
# Confirm: itemdata count, catalog, postgres, ebay_token all green
# Check worker logs for any path errors:
journalctl -u 'tgw-worker@ai_identify' -n 20
```

**Step 11 — First btrfs snapshot schedule (post-migration Phase B unlock):**

After services are confirmed healthy, set up native btrfs snapshots as the primary local
tier (replaces/supplements the rsync hardlink watcher):
- Hourly: `btrfs subvolume snapshot -r /opt/TGW @snapshots/$(date +%Y%m%dT%H%M)-root` (on nvme0n1p3)
- Hourly: same for /opt/TGW/data (on sda7) — only catches changes, CoW makes it cheap
- btrfs send/receive to sde1 (backup disk) for off-nvme redundancy
- btrfs send/receive to OFFLINE drives when plugged in (A7 tier, now fed by send not rsync)

Detail design for the snapshot schedule is Phase B §4/§6; unblock it in that session.

### Risks and mitigations

| Risk | Mitigation |
|------|------------|
| sda is a dual-boot disk (Windows on sda1–4) | Do NOT touch sda1–4. Operate only on sda5/sda7. |
| sde1 sub-mount hidden during pivot | Explicit unmount/remount sequence in Steps 2 and 6 |
| Shuttle rsync misses symlinks or ACLs | Use `-aHAX` flags; compare `du -sh` both sides before Step 5 |
| fstab error leaves system unbootable | Edit fstab in Step 9 only; keep original ext4 line commented out until Step 10 passes |
| NixOS reinstall wipes sda7/sda5 | These are on the internal HDD (sda), not the nvme. NixOS targets nvme0n1p2/p3. Safe. |

### What this enables after completion

- `/opt/TGW` root, data, and codebase all on btrfs → CoW snapshots, `send/receive`
- Phase B btrfs adoption can begin immediately (no NixOS needed for this)
- A7 offline drives receive `btrfs send` instead of rsync (faster, incremental, atomic)
- 500 GB USB HDDs deprecated as primary tier (unreliable hardware retired)
- **sde1 (trader_grims_backup, 699 GB) freed once inotify watcher is retired** — repurpose
  as `TGW-DATA-SNAPSHOT-1`: a second always-on btrfs mirror of /opt/TGW/data (sda7).
  Gives the data partition two on-machine copies before any offline drive is involved.
  Relabel with `btrfs filesystem label /dev/sde1 TGW-DATA-SNAPSHOT-1` and wire into
  the btrfs send/receive schedule alongside sda7. Do after Phase B snapshot design session.
- NixOS migration inherits clean btrfs layout on nvme0n1p3

---

## 1. Current state (verified 2026-06-11)

| Layer | State | Verdict |
|---|---|---|
| **Local continuous snapshots** | `trader-grims-backup.service` (inotify + rsync hardlink), enabled+active, writes to a **dedicated 699 GB disk** (`/dev/sde1` → `/opt/TGW/var/local/backups/trader_grims_warehouse/`), 201 G used / 490 G free, ~31 timestamped snapshots per subtree, current to within minutes. Covers `bin config data docs src`. Resident cost: **6.2 G RAM** (peak 12.6 G) watching ~1 M+ files. | ✅ healthy (RAM cost noted for Phase B) |
| **Cloud copy** | rclone remote `dbukove:TGW/` (Google Drive) mirrors the same trees + logs. Last sync **2026-05-14/16**. No timer, no cron. **Correction (Dave, 19:53): the staleness is deliberate** — syncing was stopped when a possible-data-manipulation point was reached; the 2026-05-14 copy is a **kept known-good history point**, not neglect. It must be preserved before any new sync runs (A2 pre-step). | 🟡 frozen on purpose; still needs a scheduled current tier |
| **PostgreSQL ledger** | `state_machine` = 23 MB. **Zero dumps exist anywhere.** File snapshots cannot give a consistent copy (live WAL). `todo_items` (the canonical task queue) is not re-derivable. | 🔴 unprotected |
| **Secrets** | `/opt/TGW/secrets/` is in **no backup tier at all** — not snapshotted, not on Drive (correct that it's not there *unencrypted*, but there's no encrypted copy either). Disk loss ⇒ eBay OAuth re-consent + re-provision every API key. | 🔴 unprotected |
| **Cold archive** | MasterArchive (`/dev/sdc5`, ext4, repaired 2026-06-11): ItemData history 584 G, ItemArchive 163 G (54 K zips, 40 % indexed), job_archive 371 G, etc. (GEMINI-007 inventory). GDrive holds old `ItemArchive` (2024) and `Photo Archive` (2020) copies. | 🟡 exists, partially indexed |
| **System image** | PP-DEPLOY-001 MX restore ISO: runbook written, **not yet baked** (NixOS plan Phase 1). | 🟡 pending |
| **Plan vault** | Syncthing-replicated to other devices + git-tracked. | ✅ multi-copy |
| **Source repo** | git + GitHub private remote. | ✅ off-machine |
| **Tools available** | rsync, rclone, gpg, systemd timers. restic/borg **not installed** (apt-available). | — |

**Live data sizes:** ItemData 180 G, ItemCatalog 1.8 G (derived — regenerable),
ledger 23 MB, `/opt/TGW` partition 187 G used / 295 G.

### What this means in RPO terms today

| Failure | Recovery point today |
|---|---|
| Fat-fingered file/folder | minutes (snapshot disk) |
| `/opt/TGW` nvme dies | minutes for files — **but ledger: total loss; secrets: total loss** |
| Host/site loss (both disks) | **27 days and growing** (stale Drive copy); ledger+secrets: total loss |
| Ledger corruption | total loss (no dump to restore) |

### 1b. Operator-contributed resources (Dave, suggestions 2026-06-11 17:25–17:51)

- **Stack of empty 500 GB HDDs + USB3 drive holster** → rotating offline tier:
  auto-sync when a named partition is mounted (systemd mount-unit trigger —
  `tgw-offline-sync@.service` bound to `media-tgw-<name>.mount`). Swap-and-shelve gives
  air-gapped copies the cloud tier can't. Folded into Phase A as **A7**.
- **A few attached USB HDDs reassignable to "disaster sentry duty"** — some always-on,
  some soft-switch (like MasterArchive). Drives/chassis can be swapped for better
  positioning. Inventory + assignment is part of A7.
- **USB key, named partition, for secrets** — same mount-trigger mechanism, scoped to
  the encrypted secrets bundle. Better custody story than cloud-only: the key lives in
  a pocket/safe. Folded into **A3** as the preferred secrets destination (cloud copy
  stays as the burn-down fallback).
- **Google Drive: 2 TB total, ~600 GB free**, and a known problem: Google has written
  **duplicate same-name files in the same directory**; `rclone dedupe` times out on the
  full dataset. Fix: run it **chunked per subdirectory** (`rclone dedupe --by-hash
  dbukove:TGW/data/ItemData/<chunk>`) or delegate to Antigravity as a bounded batch
  task. Dedupe + merging strays into `data/history` frees the duplicate space entirely.
  Added as **A8**. Quota risk B3 is softer than feared with 600 G free now and more
  after dedupe.
- **GDrive three-role model (Dave)** — matches and sharpens this plan's shape:
  (1) current copy of ItemData + critical files, atomically updated via rclone (= A2);
  (2) a couple of **rotating backup-style syncs taken from a snapshot** of `/opt/TGW`
  (= Phase B restic versioning does this properly; interim: dated `--backup-dir` trash);
  (3) the complete rebuild kit — "even if everything here burned to ashes" (= Phase C §5).
- **Possible DLT tape drive + tapes in inventory** — Dave checks for an interface.
  Curiosity tier: if it works, tapes are a fine annual cold archive; do not plan around
  it until hardware is confirmed.

## 2. Protection targets

| Store | Canonical? | Tiers required | RPO target (Phase A) |
|---|---|---|---|
| ItemData (180 G incl. media) | YES | snapshot + cloud | minutes local / ≤24 h cloud |
| PostgreSQL `state_machine` | YES (work state, todos, sku_history) | daily dump → snapshot disk + cloud | ≤24 h |
| Secrets | YES (operator-provisioned) | **encrypted** copy → snapshot disk + cloud | ≤ next change |
| Config (`tgw-api-config.json`, category-groups) | YES | snapshot + cloud + site-config repo (NixOS Phase 0.5) | minutes |
| Repo / plan vault | YES | git+GitHub / Syncthing+git | already met |
| Derived (catalogs, thumbnails, velocity) | no | none — regenerable by invariant | n/a |
| Cold history (MasterArchive) | archive | index + selective cloud | Phase B |

## 3. Phase A — MX now (tools at hand; no new software)

Ordered by risk closed per effort. A1–A3 are systemd template work (repo files, operator
installs); A4 is a normal code task; A5–A6 are operator drills/policy.

**A1 — Daily ledger dump (closes the worst gap; handoff §6 step 3).**
*Do:* `tgw-db-backup.service` (oneshot, `User=tgw`) + `.timer` (daily 03:30):
```
pg_dump -U tgw --format=custom state_machine \
  -f /opt/TGW/var/backups/trader_grims_warehouse/db/state_machine-$(date +%%Y%%m%%d).dump
rsync -a /opt/TGW/var/backups/trader_grims_warehouse/db/ \
  /opt/TGW/var/local/backups/trader_grims_warehouse/db/
find .../db/ -name '*.dump' -mtime +35 -delete   # both locations
```
~5 MB/day compressed — retention is a non-issue. Dump lands on the nvme tree **and** the
snapshot disk; A2 carries it to Drive.
*Done when:* two consecutive timer runs logged; `pg_restore --list` reads the newest dump.

**A2 — Scheduled cloud sync (brings the off-machine tier current).**
*PRE-STEP — preserve the frozen history point first (gate, do not skip):* the existing
`dbukove:TGW/` copy is a deliberate pre-manipulation known-good snapshot (Dave 19:53).
Before the first sync: `rclone copy dbukove:TGW dbukove:TGW-historypoint-20260514`
(server-side copy — fast, no local bandwidth, no quota doubled thanks to Drive
dedup-by-content for identical files; verify with `rclone size` both sides). Only then
enable the timer.
*Do:* `tgw-cloud-sync.service` + `.timer` (daily 02:30, before A1 — or after; order is
cosmetic): `rclone sync` of the existing scope (`bin config data docs logs src`) plus the
new `var/backups/.../db/`, with
`--backup-dir dbukove:TGW-trash/$(date +%%Y%%m%%d)` so deletions/overwrites land in a
dated trash rather than propagating destructively (cheap ransomware/oops insurance), and
`--log-file /opt/TGW/var/log/rclone-sync.log`. First run after 27 days will be large;
subsequent nightly deltas small.
*Done when:* `rclone lsd dbukove:TGW/data` shows a fresh date two mornings running.

**A3 — Encrypted secrets backup (closes the silent total-loss).**
*Do:* small script `tgw-secrets-backup`:
`tar -C /opt/TGW -cz secrets | gpg --symmetric --cipher-algo AES256 -o
secrets-$(date +%%Y%%m%%d).tar.gz.gpg`, written to the snapshot disk + rclone'd to
`dbukove:TGW-secrets/` + **preferred: synced to a named USB-key partition on mount**
(Dave 17:51 — the A7 mount-trigger mechanism scoped to the secrets bundle; the key
lives in a pocket/safe, cloud stays the burn-down fallback). Timer monthly **+ run
manually after any credential change**.
**Operator decision:** passphrase custody — written down off-machine (safe/wallet);
without it the backup is useless, with it on this disk the encryption is theater.
*Refinements (Dave, 19:44 + 19:50):*
- **At least 2 rotated USB keys** — a single corrupt drive must not be a total restore
  failure. Rotate on each refresh; both carry the same named partition label so the A7
  mount-trigger serves either.
- **Small-file history archive folded into the bundle:** a rolling zip collecting
  *historical versions* of small high-value files (configs, `tgw.source` lineage —
  years of csv→json→now migrations live in those diffs, db dumps). The snapshot disk
  already keeps ~31 versions and git covers the repo, but the keychain/cloud bundle
  should carry its own self-contained history so a bare restore has the lineage too.
- **The rebuild keychain (19:44):** the end-state physical artifact — secrets bundle +
  gpg keys + a ready NixOS install USB, on a keychain, restore-ready. See Phase C §5b.
*Done when:* a test round-trip (`gpg -d | tar -tz`) lists the expected files, and the
passphrase exists somewhere that survives the house burning down with the server.

**A4 — Backup-freshness health check (the watcher for the watchers).**
*Do:* `check_backups(cfg)` in `health.py` (pattern: `check_ownership`): age of newest
`db/*.dump` (>26 h ⇒ red), age of newest rclone log success line (>26 h ⇒ red), newest
file under the snapshot tree (>1 h ⇒ yellow), newest secrets bundle (>40 days ⇒ yellow).
Unit-tested with mocked filesystem. This is the same check already specified for the Nix
module in `PLAN-nixos-migration.md` §9.1 — build it once here, it migrates with the tree.
*Done when:* `tgw health` shows the four ages; goes red when a timer is deliberately
stopped for a day (test by date-faking files, not by actually skipping a night).

**A5 — Restore drills (a backup is a rumor until restored).**
- Ledger: `createdb scratch && pg_restore -d scratch <newest.dump>` → row counts match.
- ItemData: pick 3 random SKUs; diff snapshot copy and Drive copy against live.
- Secrets: the A3 round-trip.
- Record wall-clock times in the vault — these are the RTO numbers the NixOS cutover
  window estimate needs (Phase 4 rehearsal reuses them).

**A6 — Archive policy (the "archiving" leg).**
- MasterArchive is the **cold tier**; GEMINI-007's cleanup order stands: consolidate
  zips → index loose CSVs → complete ItemArchive index to 100 % → offload cold data.
  `tgw history-index` (unblocked since the drive repair) is the tool to build.
- Policy line to adopt: *nothing is deleted from a hot tier until it is indexed in a
  cold tier* — the index is what makes an archive an archive instead of a junk drawer.
- GDrive `ItemArchive`/`Photo Archive` copies (2024/2020) are cold-tier replicas; leave.
- **Legacy-format triage (Dave, 17:12 + 17:47):** for `magento/` (129 G), `GarageSale/`
  (33 G) and similar — confirm once whether the formats are recoverable. General rule:
  accessible → keep + index; inaccessible-but-maybe → cold storage; useless → **discard
  and record the decision so no one ever spends time on it again** (the decision log
  entry is the deliverable). **Magento + GarageSale specifically: KEEP-COLD, known
  future value** — together they can fill historical gaps, recover lost SKUs, and
  reunite photos with descriptions. After database recovery is exhausted, a photo-set
  crawler could reconstruct items from images. Low priority by design: collect info
  now, act only when worthwhile — most likely when affiliate marketing makes even
  already-sold items displayable assets.

**A7 — Rotating offline drive tier (Dave's HDD stack + holster).**
*Built 2026-06-13 (session 29 cont.):* `bin/tgw-offline-setup` provisions a drive
(mkfs.btrfs, @data/@snapshots subvolumes, fstab, systemd drop-in, QR label output);
`bin/tgw-offline-sync` updated for btrfs snapshot-after-sync (backward-compatible with
flat layout); drive registry at `reference/DRIVE-REGISTRY.md`.

**Drive assignments (7 × 500 GB HDDs):**

| Label | Role | Rotation |
|-------|------|----------|
| `TGW-OFFLINE-A` | Rotating air-gap primary | Weekly: swap A/B/C; ≥2 off-site at all times |
| `TGW-OFFLINE-B` | Rotating air-gap secondary | Weekly swap |
| `TGW-OFFLINE-C` | Third rotation slot | Monthly swing |
| `TGW-SENTRY-01` | Always-on holster (daily sync) | Monthly: swap with 02 |
| `TGW-SENTRY-02` | Sentry hot-spare / rotation twin | Monthly swap |
| `TGW-ARCHIVE-01` | Cold archive overflow | Manual write |
| `TGW-ARCHIVE-02` | Archive redundancy | Manual write |

**USB drives:** `TGW-SECRETS-A` (keychain), `TGW-SECRETS-B` (off-site/safe), `TGW-BOOT-01` and `TGW-BOOT-02` (NixOS install + TGW kit USBs — see below).

**Boot/kit USB drives (2 × 16 GB, prepared identically — 2026-06-19):**
- Label: `TGW-BOOT-01` (active, on-site), `TGW-BOOT-02` (DR spare, off-site/safe)
- Layout: Ventoy (GPT + hybrid MBR, legacy-boot compatible) with 400 MB `tgw-kit`
  partition at the end
- Ventoy partition: NixOS minimal ISO + MX Linux restore ISO
- `tgw-kit` partition (ext4, 400 MB): flake config, site-config clone, schema SQL,
  age-encrypted secrets
- Bare-metal restore starts here: boot USB → mount `tgw-kit` → `nixos-install --flake`
- Preparation procedure: `PLAN-nixos-migration.md` Phase 2.5
- Keep current: `git pull` on flake + site-config dirs when either repo changes

**Existing drives to adopt later:** `MasterArchive` (sdf, 1.8T) stays labeled; `sdg` (WD10EALS 1TB) → `TGW-HISTORY-01` after `tgw history-index` completes.

**Format:** All new drives use btrfs (`compress=zstd`, `noatime`). Each drive gets `@data` + `@snapshots` subvolumes; sync creates a read-only btrfs snapshot after each run (last 8 kept). Existing drives (sdc1 already btrfs; sdf5/sdg1 ext4 — stay until NixOS migration).

*Done when:* inserting a labeled drive produces a logged, stamped sync with no further
operator action, twice per drive. First drive: `sdd` (currently labeled `Toshiba500`) → `TGW-OFFLINE-A`.

**A8 — GDrive dedupe (precondition for quota headroom).**
*Do:* chunked `rclone dedupe --by-hash` per subdirectory (timeout-proof), or hand the
chunk list to Antigravity as a bounded batch task; merge recovered strays into
`data/history`. *Done when:* `rclone about dbukove:` shows the duplicate space freed and
a spot-check finds no same-name twins.

### Phase A result

| Failure | RPO after Phase A |
|---|---|
| File oops | minutes |
| nvme loss | minutes (files) / ≤24 h (ledger) / ≤last-change (secrets) |
| Host/site loss | ≤24 h everything |
| Ledger corruption | ≤24 h |

## 4. Phase B — upgrade path (PP-BACKUP-001 proper)

Adopt when Phase A has run clean for a few weeks and the suite design session happens.

1. **Repo split first** (already decided, session 16): `trader-grims-backup` to its own
   repository; the watcher stays frozen and running.
2. **Engine: restic** (recommendation). One tool replaces the rsync+rclone+gpg layering:
   encrypted at rest, content-deduplicated, snapshot-versioned, **native rclone backend**
   (`restic -r rclone:dbukove:TGW-restic`) so Google Drive becomes a versioned encrypted
   repository, `forget --prune` retention policies, `check --read-data` integrity scrubs.
   Borg was considered and rejected: no native cloud backend (needs ssh target). restic
   is in Debian/nixpkgs — installs cleanly on both sides of the migration.
3. **`tgw backup` CLI** wrapping it: `status` (the A4 ages + restic snapshots),
   `run [tier]`, `verify` (scrub + restore-sample), `restore` (guided). Health stays wired.
4. **Watcher decision** (design question for the Phase B session): keep inotify watcher
   for the instant-local tier alongside restic timers, or retire it — 6.2 G resident RAM
   for 1 M-file watches is real money on a 32 G box that also runs Ollama. A restic run
   every 30–60 min from the snapshot disk may be good enough; the snapshot disk itself
   provides the instant tier.
5. **Cold-tier integration:** `tgw history-index` output (A6) becomes the manifest that
   lets MasterArchive contents be selectively pushed to the restic cloud repo.
6. **btrfs evaluation (Dave, 19:33):** migrate the data filesystems to btrfs — CoW
   snapshots become the instant-local tier (replacing the 6.2 G-RAM inotify watcher
   outright), `btrfs send/receive` feeds the A7 offline drives incrementally, and rclone
   syncs *from a read-only snapshot* (atomic source, no mid-sync mutation). Evaluate
   against restic and the current rsync-hardlink watcher in the Phase B design session.
   **Natural adoption moment: the NixOS reinstall** (PLAN-nixos Phase 5 wipes the disk
   anyway — choosing btrfs then costs nothing; adopting it on MX first would be its own
   risky migration for a host we're leaving). Decision criteria: RAM cost, restore
   ergonomics, NixOS declarative support (first-class), interaction with the 295 G
   `/opt/TGW` partition layout.

## 5. Phase C — Nix flake integration (required end state)

The completed strategy must be **declarative in the flake** — this also resolves the
NixOS plan's **R9** (the module's backup unit references a binary the package doesn't
build) properly instead of by deletion:

1. `services.tgw.backup` module options: `pgDump.{enable,schedule,retentionDays}`,
   `cloudSync.{enable,schedule,remote,backupDir}`, `secretsBackup.{enable,schedule}`,
   and (Phase B) `restic.{enable,repos,keep*}` — each emitting the systemd
   service+timer pairs from Phase A, byte-equivalent behavior.
2. Secrets rule unchanged (NixOS plan R11): nothing secret in the Nix store or git;
   the gpg passphrase and restic repo password arrive only via out-of-band restore.
3. `check_backups` ships with the package — `tgw health` is identical on both OSes.
4. **Recovery equation extended and drilled** (NixOS plan Phase 4 rehearsal adds one
   line): `NixOS flake + site-config repo + secrets restore + ItemData restore +
   newest ledger dump = running system, tgw health green including backup freshness`.
5. **Google Drive rebuild kit** (one folder): pointer to the ISO, flake/site-config repo
   URLs, the secrets bundle, this plan, and the restore runbook. The kit is what a
   bare-metal stranger needs to rebuild the warehouse.

   **5b. The rebuild keychain (Dave, 19:44) — the kit in pocket form:** secrets bundle +
   gpg keys + small-config history zip + a ready NixOS install USB, on a physical
   keychain, restore-ready at all times (≥2 rotated copies per A3). **Pre-assurance:**
   mock restoration to the spare machine proves the keychain works before it's ever
   needed — and if that goes well, **building the production Nix server from the
   keychain is the POC**, which is exactly PLAN-nixos Phase 4's dress rehearsal / the
   spare-promotion cutover path with the keychain as its input. One artifact, drilled
   on real hardware, that answers "everything burned — now what?" with "this keyring."

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| B1 | First A2 run uploads ~weeks of churn over residential bandwidth | run manually once in an off-hours window before enabling the timer |
| B2 | gpg passphrase lost ⇒ secrets bundle worthless | A3 custody decision is a gate, not a footnote; verify annually |
| B3 | Drive quota (180 G ItemData + history already there) | `rclone about dbukove:` before first sync; prune `TGW-trash/` dated dirs >90 d |
| B4 | Snapshot disk (`sde1`) dies silently | A4 yellow on snapshot-age catches write-stops; disk SMART is operator routine; cloud tier is the redundancy |
| B5 | Dump runs while a migration batch writes sku_history | `--format=custom` is a consistent snapshot by construction (single transaction) — no action needed, noted for confidence |
| B6 | rclone sync propagates a local catastrophe upward | `--backup-dir` dated trash (A2) is the rewind; restic versioning (Phase B) is the real fix |

## 7. Verification (standing, after Phase A)

Daily (automatic): `tgw health` includes the four backup ages.
Weekly (operator, 2 min): newest dump readable (`pg_restore --list`), rclone log tail clean.
Monthly: one random-SKU restore diff; quarterly: full A5 drill + record times.

---

No production code or system units are changed under this plan until Dave approves it.
On approval: A1–A3 unit/script files land in `etc/systemd/` + `bin/` as a normal
reviewed change (todo seeded), A4 is a normal code task (todo seeded), A5/A6 are
operator items, Phase C rides PLAN-nixos-migration Phase 0.2/0.7.
