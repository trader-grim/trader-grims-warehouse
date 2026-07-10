# Packet: every live eBay item's inventory+offer record lands in the capture archive
Todo: #1122   PP: (dataset backfill, Data Charter)   Track: R1.8 — **Dave GO 2026-07-03**

## Context budget (ALL the model may load)
Plan core + this packet + `scripts/ebay_snapshot_all.py` + `reference/TGW-Data-Charter.md`
+ `tgw.quota` docstring. Nothing else.

## Spec
Run `scripts/ebay_snapshot_all.py` as the tgw user (env per the tgw-worker unit's
LD_LIBRARY_PATH), backgrounded, budgeter-supervised (`quota.set_context('background',
'r1.8-snapshot')` is already in the script). ~100 paged inventory calls + ~19.5k
per-SKU offer calls on the 2M/day Inventory pool. The capture fence (E7) lands every
raw response in `/opt/TGW/incoming/ebay/YYYY-MM-DD.jsonl.gz` — the archive IS the
output; the script keeps no state. Idempotent; safe to re-run after any interruption.

## Dataset
This IS the dataset packet: first complete raw snapshot of all ~19,486 live items'
inventory_item + offer records. Baseline for drift detection, PP-EBAY-SNAPSHOT-001
Phase 4, and the local-mirror goal.

## Out of scope
Parsing/backfilling item JSON from the snapshot (separate packet after inspection);
fixing per-SKU fetch errors beyond logging them; the ebay_sync worker.

## Acceptance (live)
- Script exits 0 with "SNAPSHOT COMPLETE: N SKUs, E errors" in its log.
- `zcat incoming/ebay/<today>.jsonl.gz | wc -l` grew by ≈ N(items pages) + N(offers);
  show the number and one sample record to Dave.
- `tgw ops-digest` / quota status shows ebay_inventory spend consistent with the run
  (~20k), no 429 incidents.

## Quota/risk
~20k calls on a 2,000,000/day pool (≈1%). Zero EPS/Trading/Taxonomy usage — cannot
contend with the PP-PHOTOSYNC-001 fix track or Dave's operator work. Runtime ~1–2h
with the script's 0.15s pacing.
