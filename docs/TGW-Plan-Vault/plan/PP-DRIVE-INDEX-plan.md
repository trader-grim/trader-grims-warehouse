---
title: PP-DRIVE-INDEX — Long-term File Sorting & Indexing
created: 2026-07-01 (session 40)
status: Phase 0 — planning
---

# PP-DRIVE-INDEX: File Sorting & Indexing

## Overview

Long-term project to catalog, sort, deduplicate, and index all files across Dave's
external drives and Google Drive. Two tracks sharing common tooling:

- **Track A — Business (TGW):** ItemData photos, ItemArchive zips, masterarchive CSVs,
  historical catalogs. Integrates with PP-ANNEX-001 (git-annex) and PP-SEARCH-001 (recoll).
- **Track B — Personal:** Everything else — documents, photos, projects, archives spanning
  decades of accumulated data.

## Current inventory

| What | Count | Status |
|------|-------|--------|
| Normal USB drives | 6 | Not yet connected |
| Drive holster (stack) | 5 | Not yet connected |
| Google Drive | 1 account | Not yet inventoried |
| Additional devices (future) | TBD | Dave will surface as found |

Total starting point: 11 drives + cloud storage. More expected over time.

## Guiding principles

1. **Read-only first** — mount every drive read-only for initial survey. No writes until
   we have a plan for that specific drive's contents.
2. **Catalog before moving** — generate a full manifest of each drive before any file
   operations. The catalog IS the first deliverable.
3. **Batch by connection** — Dave connects 2-3 drives at a time. Each batch goes through
   the full survey pipeline independently. No dependency between batches.
4. **Deduplicate across everything** — same file on 3 drives? We find all copies, keep
   the best, record where the others were.
5. **Two-track separation** — business and personal data get separate storage roots,
   separate git-annex repos (eventually), separate recoll indexes. Common tooling.
6. **No fancy hardware required** — USB hub, the drives Dave has, the TGW server. That's it.

## Phases

### Phase 0 — Survey & Tooling (NOW)

No drives connected yet. Prep work:

- [ ] **0.1** Inventory tools available on the system:
  - `lsblk`, `smartctl`, `hdparm` (drive identification)
  - `find`, `tree`, `file` (file listing & type detection)
  - `sha256sum` / `md5sum` (integrity & dedup fingerprints)
  - `fclones` or `rmlint` (fast dedup — check availability)
  - `duf`, `ncdu` (disk usage visualization)
  - `rclone` (Google Drive access)
  - `recoll` (full-text indexing)
  - `git-annex` (content-addressed storage — Track A)
  - Install any missing tools

- [ ] **0.2** Create the catalog structure:
  ```
  /opt/TGW/data/drive-index/
    manifests/          # per-drive file listings
    fingerprints/       # sha256 sums for dedup
    reports/            # analysis summaries
    gdrive/             # Google Drive inventory
    track-a/            # TGW business data staging
    track-b/            # personal data staging
  ```

- [ ] **0.3** Google Drive initial survey (no drives needed):
  - `rclone lsf` the GDrive account
  - Estimate total size and folder structure
  - Identify TGW-relevant vs personal content

- [ ] **0.4** Design the per-drive survey script:
  - Mount read-only
  - `smartctl` identity (make/model/serial/size/health)
  - Full recursive file listing with sizes, dates, types → manifest file
  - SHA256 fingerprints for dedup (can be incremental — first pass is listing only)
  - Quick category heuristic (file extensions: photos, docs, archives, code, etc.)
  - Generate a one-page summary report per drive

### Phase 1 — First Connection (Dave plugs in 2-3 drives)

- [ ] **1.1** Run survey script on each drive
- [ ] **1.2** Cross-drive dedup report: which files appear on multiple drives?
- [ ] **1.3** Category breakdown: what's on these drives?
- [ ] **1.4** Flag TGW-relevant content (ItemData, ItemArchive, catalogs, eBay exports)
- [ ] **1.5** Present findings to Dave — decide: keep/move/archive/delete per category

### Phase 2 — Iterate (repeat for each batch of drives)

- Same pipeline, but now with cross-batch dedup awareness
- Accumulate master file index across all surveyed drives
- Start building the "where is X?" lookup

### Phase 3 — Sort & Consolidate

- [ ] Design canonical directory structure for Track A and Track B
- [ ] Move/copy files to canonical locations (with verification)
- [ ] Handle duplicates: keep best copy, record provenance
- [ ] Archive old drives once contents verified in new location

### Phase 4 — Index

- [ ] **Track A:** Promote PP-SEARCH-001 Phase 0 (recoll on ItemArchive + masterarchive)
- [ ] **Track B:** Build separate recoll index for personal data
- [ ] `tgw search` CLI integration for Track A
- [ ] Simple search CLI/script for Track B
- [ ] Google Drive indexed and searchable

### Phase 5 — git-annex (Track A, long-term)

- [ ] Promote PP-ANNEX-001 when Track A is consolidated
- [ ] git-annex repo for ItemData photos
- [ ] Tiered remotes (gdrive-active, gdrive-archive, local)
- [ ] Replace direct filesystem with annex content-addressed store

## Per-drive survey script (draft spec)

```bash
#!/usr/bin/env bash
# survey-drive.sh — read-only catalog of a single drive
# Usage: sudo bash survey-drive.sh /dev/sdX1 /opt/TGW/data/drive-index

DEVICE=$1
OUTDIR=$2/manifests
DRIVE_ID=$(lsblk -no SERIAL $DEVICE 2>/dev/null || echo "unknown")
LABEL=$(lsblk -no LABEL $DEVICE 2>/dev/null || echo "unlabeled")
MOUNT=$(mktemp -d /tmp/drive-survey.XXXXXX)

# Mount read-only
mount -o ro $DEVICE $MOUNT

# Drive identity
smartctl -i $DEVICE > $OUTDIR/${DRIVE_ID}-smart.txt 2>/dev/null

# Full file listing
find $MOUNT -type f -printf '%T@ %s %p\n' | sort -n > $OUTDIR/${DRIVE_ID}-files.txt

# Category summary
find $MOUNT -type f | sed 's/.*\.//' | sort | uniq -c | sort -rn > $OUTDIR/${DRIVE_ID}-types.txt

# Disk usage top-level
du -sh $MOUNT/*/ 2>/dev/null > $OUTDIR/${DRIVE_ID}-du.txt

# Unmount
umount $MOUNT
rmdir $MOUNT

echo "Survey complete: $OUTDIR/${DRIVE_ID}-*.txt"
```

(This is a starting point — we'll refine as we go.)

## Dependencies & relationships

- **PP-ANNEX-001** — git-annex photo store (FUTURE-IDEAS.md). Track A endpoint.
- **PP-SEARCH-001** — recoll universal index (FUTURE-IDEAS.md). Phase 0 can start now.
- **PP-LVM-001** — LVM expansion. Needed when consolidated data exceeds current storage.
- **PP-NIXOS-001** — NixOS migration. Tooling installs (recoll, git-annex, fclones) go here.

## Constraints

- USB throughput is the bottleneck — plan for overnight batch runs on large drives
- Some drives may be old/unreliable — SMART health check before heavy reads
- No rush — this is a background project that runs alongside TGW development
- Dave connects drives when convenient; no pressure on schedule
