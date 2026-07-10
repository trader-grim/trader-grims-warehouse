# IN PROGRESS — overnight execution queue (2026-07-03 → 2pm 2026-07-04)

Working `plan/packets/OVERNIGHT-QUEUE-2026-07-04.md` top-to-bottom, one packet per
commit+push on `catio-nix-0.0.1-alpha` (Dave-authorized pattern for this queue).

Current: #1122 R1.8 full dataset snapshot — starting `scripts/ebay_snapshot_all.py`
as tgw user, backgrounded, 1-2h runtime. Everything else in the queue is gated on
this (esp. #1131 Motors census) or independent (#1117/#1118/#1127/#1120/#1121/#1102).

Plan: let #1122 run in background, work #1117 (P2 digest lines) while waiting,
then proceed down the queue in order. Recovery: if interrupted, check
`incoming/ebay/<date>.jsonl.gz` growth and script log for #1122 completion before
re-running (idempotent).
