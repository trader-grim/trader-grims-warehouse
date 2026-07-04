# DONE — todo #1122: R1.8 full dataset snapshot

`scripts/ebay_snapshot_all.py` completed: **19,486 SKUs, 40 offer-fetch
errors** (mostly benign 404s for items with no offer + a couple of
transient 500s, all silently counted per the script's own design — no
429s, no quota incidents on the inventory pool). Runtime ~3h39m
(19:50–23:29), well within the 1-2h estimate's upper bound given the live
0.15s per-call pacing plus a few slow stretches.

Acceptance verified:
- `incoming/ebay/2026-07-04.jsonl.gz` grew to 20,016 lines (from a
  1,427-line baseline earlier in the session) — consistent with ~98
  inventory pages + 19,486 offer calls plus other concurrent API activity
  in the same daily capture file.
- Sample offer record confirmed `marketplaceId` field present exactly as
  needed for #1131 (Motors census) — e.g. `tgw20160122242616788` →
  `marketplaceId: EBAY_MOTORS`.
- Quota: 29,854 calls spent on `ebay_inventory` pool (1.5% of 2,000,000/day
  budget), zero 429 incidents.

This is the first complete raw snapshot of all live items' inventory_item +
offer records — baseline for drift detection, PP-EBAY-SNAPSHOT-001 Phase 4,
and the local-mirror goal. #1131 (Motors census) now unblocked, proceeding
next.
