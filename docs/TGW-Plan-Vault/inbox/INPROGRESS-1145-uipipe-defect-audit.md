
## FLEET PHOTO INTEGRITY SWEEP COMPLETE (2026-07-05 ~03:00, todo #1154)

Ran on a1131 over the ro NFS mount (3.4h, 270,525 photos decoded, zero
thermal impact on tgw-prod). Report: /opt/TGW/var/reports/photo-integrity-2026-07-05.tsv

- **206 damaged files / 149 SKUs (0.076% of fleet)**
- 205/206 carry the Feb-2022 migration mtime — ONE event, confirmed:
  148 zero-byte + 42 truncated-at-64KiB-multiple + 15 other corruption.
  1 stray from 2016-06.
- **30 of the 149 are LIVE listings** (shoppers may see missing/partial
  photos) — these are the recovery priority. 0 staged, 119 unlisted.
- The 4 drain-discovered SKUs are all in the roster (validates the sweep).

Next (per photo-integrity-mitigation plan): recovery shopping list is final
for PP-DRIVE-INDEX Phase 1; the 30 live SKUs get first pull when archive
drives connect. photo_files_readable catalog-verify rule = next code packet.
