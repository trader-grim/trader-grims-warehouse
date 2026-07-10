# Overnight execution queue — until Dave's 2pm 2026-07-04 planning session

Written by Fable at s43 close (Dave at usage limit). **Opusplan executors: work
this list top-to-bottom, one packet = one session = one commit+push on
`catio-nix-0.0.1-alpha` (Dave authorized this pattern for this queue; PR to
main stays deferred).** Standard protocol per packet: thermal check → todo
in_progress → INPROGRESS breadcrumb → build → test → LIVE-verify (PD4) →
todo done → DONE breadcrumb → commit → push → /tgw-exit. Load only: CLAUDE.md,
master plan, `pp/PP-PHOTOSYNC-001.md`, and the files each item names.

## Hard rules for this queue
- NEVER touch the operator EPS reserve; all work is background/quota-supervised.
  Dave lists tomorrow morning on fresh quota — his lane (C10) is untouchable.
- Dave-gated items are NOT in this queue: P4/#1119 (Best Offer decision),
  #1126 (nixos-rebuild switch), PP-CATALOG-INCR-001 (proposal), any Motors
  DECISION (#1129 is 2pm planning input, not executor work).
- A blocked/failed packet: persist the finding (invariant C11), mark todo with
  a note, move to the next item. Do not improvise around gates.

## The queue

1. **#1122 — R1.8 snapshot. RUN FIRST (tonight).** Packet:
   `packets/1122-r18-dataset-snapshot.md`. Background, 1–2h, inventory pool
   (~20k of 2M). Dave's GO stands since 2026-07-03.
2. **#1131 — Motors census from R1.8 capture (NEW, gated on #1122 done).**
   Zero API calls: parse `/opt/TGW/incoming/ebay/*.jsonl.gz` offer records for
   `marketplaceId` per SKU; write
   `reference/ebay-marketplace-census-2026-07-04.md` (counts per marketplace,
   full EBAY_MOTORS SKU list, cross-marketplace multi-offer SKUs = duplicate
   risks). Also patch each Motors SKU's item JSON with
   `marketplace_id` via the fence (dataset growth, PD1). This is the 2pm
   planning input for PP-EBAY-MOTORS-001.
3. **#1117 — P2: digest retry_wait liability + next-reset exposure lines.**
   Spec in `pp/PP-PHOTOSYNC-001.md` P2. ops_digest.py + SQL only.
4. **#1118 — P3: C10 detector (source-scan test of operator enqueue sites).**
   Spec in PP doc P3. Closes invariant C10's 🔶.
5. **#1127 — re-point `photos_short_on_ebay` at live truth.** Use the R1.8
   capture (fresh, whole-site, includes imageUrls) as the live-side source
   instead of a new API sweep where possible; fall back to bulk
   getInventoryItems (~98 calls) if capture is stale >24h.
6. **#1120 — P5: operator price never machine-overridden.** Spec in PP doc P5.
7. **#1121 — P6 (XS): ebay_repush orphan queue.** Spec in PP doc P6.
8. **#1102 — test-suite repair (fills remaining time; large).** Target the 236
   errors first (test_http_server.py cookie-auth fixtures, test_fence.py
   AttributeErrors). Baseline to beat: 1489 pass / 9 fail / 18 errors.

## State snapshot (for executor orientation, 2026-07-03 end-of-day)
- Done today: P1 (#1115), P7 (#1123), P9 (#1125), P10 (#1128), C10 lane,
  backlog cancel. HEAD = `1af8e31`, pushed, tree clean.
- P4 (#1119) PAUSED on Dave; do not touch.
- Memory: `project-ebay-quota-photo-desync.md`, `project-photosync-p10-motors.md`.
- 2pm 2026-07-04: Dave replans (Motors scoping, P4 decision, catalog-incr,
  nix-timer go). Have #1131's census doc ready as its primary input.
