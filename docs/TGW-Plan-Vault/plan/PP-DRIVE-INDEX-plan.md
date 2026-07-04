---
title: PP-DRIVE-INDEX — Long-term File Sorting & Indexing
created: 2026-07-01 (session 40)
status: Phase 0 partially DONE (2026-07-04) — see update below
---

## UPDATE 2026-07-04 — Phase 4's Track A recoll piece landed early, merge point found

Built independently today (todo #1066, PP-SEARCH-001 Phase 0) without
knowing this plan existed — found and merged on Dave's prompt after a
drive-space conversation surfaced it. **This satisfies Phase 4's Track A
checkbox ahead of schedule:** a live recoll index at `/opt/TGW/.recoll/`
covering `ItemArchive` (zip contents transparently indexed), `ItemCatalog`
(incl. the 235MB `historical-master-catalog.json`), the `history/` symlink
(picks up MasterArchive automatically whenever that drive is mounted —
no config change needed), and the plan vault. 441,374 docs indexed, real
recovery-style queries live-verified (the exact "49 missing item JSONs"
scenario this plan and `project-universal-index` memory both call out).
Config: `/opt/TGW/.recoll/recoll.conf` (outside git, tgw's `$HOME` — see
`CLAUDE.md` key-paths table).

**What this doesn't yet do** (the rest of this plan, still fully open):
Phases 0.1–0.4's drive-survey tooling, Phase 1's per-drive manifests/SMART
checks, the cross-drive dedup report (1.2), Track B (personal data)
entirely, and Google Drive inventory. None of the 11 drives/holster stack
this plan describes have been surveyed — today's index only covers what
was already mounted on the live TGW server.

**Why this matters right now (2026-07-04 drive-space conversation):**
`/opt/TGW` (the live ItemData/ItemCatalog partition) is at 83% full, 48G
free — the real near-term pressure point, not `/nix`. This plan's own
Phase 1.2 (cross-drive dedup) is exactly the space-recovery lever Dave
described ("the recoll project will also identify a lot of duplicates and
I will recover a lot of space") — years of accumulated ItemData/ItemArchive
history plausibly has real duplicate photos/files. Recommend: run a
dedup pass (fclones/rmlint, or a sha256-fingerprint scan per Phase 0.1's
own tooling list) against the currently-indexed scope *first* — no new
drives need connecting for that, it's a scan against what's already
mounted and already indexed — before deciding whether/what to offload onto
`sdi` (idle 500G bus-powered USB drive, see `PLAN-backup-dr.md` /
`DRIVE-REGISTRY.md` for the current drive-power-management policy this
project should follow: reliable bus-powered USB stays attached, dock-housed
3.5" drives get connected only when actively surveyed, to respect Dave's
generator-power constraint).

## UPDATE 2026-07-04 (later same day, todo #1140) — first drive indexed, Phase 0.4 script built

**Dave: "we can index the ext documents though right? Then plan an
affordable crawler to do the rest once we know the scope."** Answer: yes
to both, and both done same day.

- **`sdi` (the idle 500G bus-powered USB drive) is now indexed.** Mounted
  read-only at `/opt/TGW/mnt/db-home` (manual mount, not fstab — matches
  the power-off-when-idle policy above; unmount when not actively
  querying). Contains `db/` (700 db:users — mirrors the `/home/db`
  policy, `tgw` uid 900 can't read it at all, so recoll silently skips
  that subtree with zero extra config needed), plus world-readable
  `root/` and `linuxbrew/`. Added to recoll's topdirs alongside
  `.ssh`/`.gnupg`/`.aws`/`.config` in `skippedNames` (belt-and-suspenders
  — permissions already block `db/`, but any world-readable dotfile
  elsewhere on future drives shouldn't get its contents extracted either).
  Content is a broad personal-document mishmash (invoices, photos, music,
  PDFs, archives) — Track B territory, confirmed by direct inspection
  before indexing.

- **Phase 0.4's per-drive survey script is built:**
  `scripts/survey_drive.sh` — read-only mount, `smartctl` identity, full
  file listing (path/size/mtime), extension-based type breakdown,
  top-level `du`, one-page summary report, auto-unmount via trap. No
  hashing/dedup fingerprinting yet (deliberately deferred to Phase 1.2 —
  this script's whole point is a *cheap* first look so Dave can prioritize
  which of the ~11 drives are worth the time before committing to a full
  pass). Live-verified against `sdf1` (a Ventoy stick) — clean mount,
  report, and auto-cleanup.

**Not yet done:** running the survey script against the drives that
aren't currently connected (`sdd`/`sdh`/the "drive holster" stack of 5 +
6 more USB drives per this plan's original inventory) — those need Dave
to physically connect them, one batch at a time, per this plan's own
Phase 1 design ("batch by connection"). The tools/config now needed to
do this cheaply, incrementally, and safely all exist as of today.

**Correction, same day (Dave):** ran a first dedup pass (`fclones`)
against `ItemArchive`+`ItemCatalog` as a test — found only 232 redundant
files / 210.8MB. **Dave clarified this is expected and not the point:**
there won't be much dedup *within* `ItemData` itself (each item's photos
are unique to that item, not duplicates of each other). **The real
dedup value is cross-drive consolidation** — the same file backed up
across multiple external drives over the years, which is exactly this
plan's original Phase 1.2 goal ("same file on 3 drives? find all copies,
keep the best, record where the others were"). That only becomes
findable once more of the ~11 drives are surveyed and indexed, not from
scanning what's already on the live `/opt/TGW` partition.

**Standing requirement, confirmed (Dave):** he must personally approve
every data-deletion decision from this project — matches the existing
operator-gate philosophy (invariant C9) applied to data deletion, not
just AI listing output. This is why he originally wanted Hermes running
this project: a long, one-at-a-time approval queue (which drive/file to
delete once a duplicate is confirmed) is exactly Hermes's PM-admin shape,
not a single Claude session's. Any dedup/consolidation tooling built for
this project should assume: identify + report candidates, never delete
unattended, and structure output as an approval queue Hermes (or Dave
directly) can work through incrementally.

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
