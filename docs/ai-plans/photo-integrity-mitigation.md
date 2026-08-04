# photo-integrity-mitigation: fleet-wide damaged-photo detection, archive-drive recovery, ingest-verification prevention

**Status:** Draft — 2026-07-04 (session 45; detection sweep launched same night)
**PP ref: PP-DATAINTEGRITY-001** (2026-07-11 — was split across 3 PPs with
no single owner, which is exactly why legs 2/3 sat untagged; consolidated
under one PP. PP-UIPIPE-001 no longer exists as its own PP either, folded
into PP-EDITOR-001 same session.) Recovery still rides PP-DRIVE-INDEX-001
Phase 1; prevention's endgame still depends on PP-ANNEX-001 — those
relationships are unchanged, just no longer the *owning* PP designation.

## Problem / motivation

Three photos (2 SKUs from 2016, 1 from 2017) were found damaged by tonight's
drain — the first full-fleet decode in years. Root cause: the Feb 2022 bulk
migration truncated files at 64KiB boundaries (interrupted buffered copy);
nothing content-verified the copy, and nothing since decodes photos until a
worker needs them, so the damage sat silent for 3.5 years. Unknown how many
more exist. Dave: "we need to check all the images for bad ones" + recover
from the archive drive set + (annex) make this class impossible.

## Constraints (from settled architecture)

- Detection is read-only — no fence needed for the sweep; any REPAIR (photo
  restore) writes through the fence/pipeline (C10 repush), never direct.
- Thermal: full-fleet decode is heavy — run the CPU on a1131 over the ro NFS
  mount (shared-machine design); tgw-prod pays sequential disk reads only.
  Monitor thermal.status during the run.
- Findings persist durably (C11): the roster is a report file now, and
  `photo_files_readable` becomes a broker/catalog-verify rule so future
  damage surfaces within a day, not years.
- Data charter: recovered originals are archived before replacing the
  damaged file (archive-before-after); damaged files are MOVED to a quarantine
  dir, never deleted (E5).

## Proposed approach — three legs

**1. DETECT (tonight, running):** full PIL decode sweep of every
ItemData photo, executed as claude@a1131 against /opt/TGW/mnt/tgw-prod/data,
writing `/opt/TGW/var/reports/photo-integrity-<date>.tsv` (sku, file, size,
mtime, error) + a summary line. Signature analysis on results: 64KiB-multiple
sizes = Feb-2022 copy victims; other errors classified separately.
Then: rule `photo_files_readable` added to catalog-verify (cheap incremental:
only decode files whose (size,mtime) changed since last pass, stored in a
sidecar index — full decode only nightly-sampled or on-demand).

**2. RECOVER (rides PP-DRIVE-INDEX Phase 1):** the roster is the shopping
list. Per drive connected during consolidation: survey_drive.sh manifest →
match roster filenames → hash + PIL-verify the candidate → copy to staging →
fence-mediated replace (damaged original → quarantine dir with provenance
note) → C10 repush chain if the item is listed → fresh-eBay verify. Every
recovery recorded on the item. Items whose photos are NOT found on any drive
get a durable `photo_unrecoverable` finding (operator decides: relist with
remaining photos / reshoot / retire).

**3. PREVENT (two horizons):**
- Now: any bulk copy/migration tooling (usb restore, consolidation moves)
  gets verify-after-copy (sha256 source vs dest) — one shared helper.
  Intake already fences writes; add decode-verify at intake so a bad camera
  file is rejected at the door, not discovered at listing time.
- Endgame (PP-ANNEX-001, argument #1): annex ingest hashes by construction —
  an interrupted copy CANNOT register; `git annex fsck` audits the whole
  store on demand. The Feb-2022 class becomes impossible.

## Files to change

| File | Change |
|------|--------|
| (tonight) sweep script on a1131 | one-off, writes the TSV roster — no repo change needed |
| `src/tgw/api.py` (catalog-verify) | `photo_files_readable` rule + (size,mtime) decode cache |
| `src/tgw/...` bulk-copy helpers | shared verify-after-copy (sha256) for usb-restore/consolidation paths |
| intake worker(s) | decode-verify at ingest, reject bad files loudly |
| `docs/TGW-Plan-Vault/plan/PP-DRIVE-INDEX-plan.md` | recovery workflow appended to Phase 1 |

## Acceptance criteria

- [ ] Sweep TSV exists with 100% ItemData photo coverage; summary reports total/bad/by-signature
- [ ] `tgw catalog-verify` flags a deliberately truncated test photo within one pass
- [ ] One damaged photo recovered end-to-end from an archive drive: hash-verified, quarantined original, fence-replaced, repushed, fresh eBay read shows it live
- [ ] Bulk-copy helper refuses to report success on a mid-file kill (test with SIGKILL mid-copy)
- [ ] Intake rejects a corrupt file with a persisted finding, not a silent skip

## Open questions

- Quarantine location: `ItemData/<sku>/.quarantine/` vs central `var/quarantine/<sku>/`? (recommend central — keeps item dirs clean, one place to audit)
- Decode-verify at intake: full `im.load()` (catches truncation, slower) vs header-only `im.verify()` (fast, misses tail truncation)? (recommend full load — intake is not hot-path)
- Roster items with zero recoverable copies: standing operator queue in the console (broker `surface`) — agreed?
