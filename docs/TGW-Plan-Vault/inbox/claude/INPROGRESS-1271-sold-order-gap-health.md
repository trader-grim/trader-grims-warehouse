# INPROGRESS — todo #1271 (PP-DATAINTEGRITY-001)

Wiring `sold-order-history-gaps.jsonl` (written by
`tgw.ebay.pull._record_sold_order_gap`, invariant C11 finding for #1270)
into `tgw health` the same way `quota.record_429` / offers-unresolved are
surfaced — currently the file is written but nothing ever reads it back,
so a real permanent history gap would sit invisible.

Plan: add `check_sold_order_gaps(cfg)` to `src/tgw/health.py` following the
`check_offers_unresolved` pattern (reads the JSONL registry, counts
entries, ok=True/warn if any exist, includes most-recent gap detail),
append it to `check_all()`'s checks list. Live-verified the file doesn't
exist yet on tgw-prod today (no gap has occurred) — check must handle
absent-file as the healthy/no-finding case, same as offers_unresolved does
for its absent registry.

Working in isolated worktree at
`/opt/TGW/var/worktrees/1271-sold-order-gap-health` on branch
`todo/1271-sold-order-gap-health`, off `catio-nix-0.0.1-alpha`.
