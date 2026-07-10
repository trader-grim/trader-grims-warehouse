Session 40 — PM/PA introduction with Dave. First session together.

## Completed
- Session startup, thermal NORMAL, plan check clean
- Saved Dave as user (Dave Buko, TGW project lead/stakeholder)
- Explored FUTURE-IDEAS.md: PP-ANNEX-001 (git-annex photo store) and PP-SEARCH-001 (recoll universal index)
- Created PP-DRIVE-INDEX plan at docs/TGW-Plan-Vault/plan/PP-DRIVE-INDEX-plan.md
- Installed tools on NixOS flake: smartctl, ncdu, fclones, git-annex, recoll, duf, rmlint

## Google Drive survey (dbukove)
- 15 top-level dirs excluding TGW/ and TGW-secrets/
- z.dedupe was just a dedupe staging folder (z pushed to bottom of list)
- Mixed personal + business content
- Survey saved to /opt/TGW/data/drive-index/gdrive/survey-20260701.md

## Drive survey — batch 1

### porche (sdd5, 1.8T ext4 "MasterArchive") — the main history drive
- SMART: PASSED (historical airflow temp warning)
- Mounts at ~/devices/porche, usually lives at data/history
- Contains history going back to 2012-13 (pre-TGW)
- history/ItemArchive/ = 54,683 per-SKU life-story zips (162 GB total)
  - NOT just item snapshots — each zip has every JSON revision (timestamped),
    every eBay report row, photos, sync-conflict artifacts
  - Median zip ~2.8 MB, 46,758 over 1MB, 511 over 10MB
  - This is the authoritative historical archive for the business
- history/ top-level: 260 CSVs, 160 JPGs, 144 XLSX, 47 PDF + dirs
  - eBay reports, financial statements, Magento exports, all_skus_locations
- history/job_archive/ = 16,346 old jobs
- history/catalog-backup/ = 258 catalog snapshots
- Large blobs: ii3.home.tar.gz (29GB), tgwm1-import.sql (1.9GB), Bitnami Magento OVA (1GB)

### blk1tb (sdh1, 932G, 300G used)
- Pre-normalization TGW snapshot (~May 2026)
- One of ~3 point-in-time snapshots marking major milestones
- 55,419 ItemData dirs + ItemCatalog + task-intake docs
- ItemArchive zips are authoritative — snapshots are reference/checkpoint only

### 500b3 (sdb1, 466G btrfs "db-home")
- Old system home backup
- db/ = Dave's personal home (photos, PDFs, manuals, downloads, music, videos)
- tgw/ = old tgw user home (Claude, Aider, cursor, Dart/Flutter)
- linuxbrew/, root/ = tool installations, likely recreatable
- SMART: PASSED

### Additional drives noted but not yet surveyed
- trader_grims_backup (sdc1, 699G btrfs) — label suggests TGW backup
- TGW-VAULT (sdf3, 10G btrfs) — small, likely Ventoy + some data
- Two Ventoy boot USBs (sde, sdf)
- "new drive" added to ~/devices/ — not yet identified (SMART error noted, needs powered hub in morning)

## Key cleanup targets identified
- **Meme/junk photos** in personal directories (db/, Pictures/, Downloads/)
- **Old browser profiles** in personal directories
- Wait for powered USB hub before processing the drive that gave SMART errors

## Decisions
- All external drives are archives — we identify, dedup, then merge/organize
- ItemArchive zips (per-SKU life stories) are the authoritative history
- Point-in-time snapshots only needed if they contain data not in ItemArchive
- Wait on indexing — organize and inventory all drives first
- blk1tb/snapshots can be reduced after confirming ItemArchive coverage