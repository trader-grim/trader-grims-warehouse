# RESEARCH-1143: scripts/ subsystem cohesion+correctness audit

Part of todo #1143 (full-codebase cohesion+correctness audit, staged per-subsystem).
This slice: `scripts/`, 17 ad-hoc/one-off Python scripts / 3,533 lines (bulk
backfills, audits, photo repair, evals — not the always-running worker pipeline, but
several mutate live eBay listings or ItemData directly). Fifth subsystem after
`workers/`, `apis/ebay/`, `http_server.py`, `queue/state-machine`.

## Method

Workflow tool, 4 file-groups (mutation/backfill, scrub/normalize, photo, audit/eval),
each candidate finding then adversarially verified by 3 independent agents (2-of-3
survival bar). First attempt hit the session rate limit mid-Verify (5 findings'
votes failed); resumed same run — cached Find results + completed verifies carried
over, only the failed votes re-ran. 37 agents total (combined), ~1.64M subagent
tokens.

## Result: 11/11 candidate findings confirmed, 0 refuted

| Todo | Severity | File:line | Summary |
|------|----------|-----------|---------|
| #1204 | invariant | ebay_backfill_offers.py:100 | bypasses the tgw-api fence entirely — reads/writes item JSON directly via `atomic_write_json` instead of `apis.fence.ebay_write`; races with concurrent ebay_sync/ebay_publish fence writes (lost-update), skips protected-subfield merge |
| #1205 | invariant | ebay_backfill_offers.py:142 | same script never enqueues `catalog_rebuild` (invariant A7) — catalog/thumbnails stay stale after a fleet-wide backfill |
| #1207 | **correctness** | ebay_normalize.py:115 | never sets `quota.set_context('background', ...)` before its fence writes — http_server treats every write as operator-originated, auto-enqueuing a live `force=True` eBay push for ~19k items despite the script's own docstring claiming "No eBay API calls" — unflagged bulk eBay write with real EPS-quota cost |
| #1206 | correctness | requeue_ebay_draft_402_dead_letters.py:78 | dedupe key uses a fresh timestamp every run with no run-once guard — a second `--apply` re-requeues the same dead-letter rows, burning a second full round of AI-drafting cost |
| #1208 | invariant | data_scrub_magento.py:86 | `--execute` writes item JSON via plain `open('w')+json.dump`, bypassing the fence and atomic-write pattern, no archive-before-write — crash mid-write is unrecoverable data loss |
| #1209 | invariant | recompile_category_backfill.py:148 | order-dependency with the sibling scrub script can silently destroy an item's last category signal entirely |
| #1210 | correctness | photosync_canary_probe.py:96 | price field stringified on one side of a diff but left numeric on the other — canary always FAILs on priced items, training operators to ignore real alerts |
| #1211 | invariant | photo_repair_iss013.py:292 | deletes a wrongly-created photo based only on byte-size match, no content-hash check, no archive-before-delete |
| #1212 | invariant | photo_repair_iss013.py:270 | core alt-photo rename has no archive-before-manipulation step — substitutes a weaker Btrfs-snapshot-only safety net for the established copy-to-history convention, undocumented deviation |
| #1214 | correctness | ebay_motors_census.py:90 | `--apply` bakes stale marketplaceId data from arbitrarily old captures with no recency check, and unconditionally overwrites SKUs its own report flags as ambiguous — silently auto-resolves the exact case it tells the operator not to auto-resolve |
| #1213 (low prio) | cohesion | photo_repair_iss013.py:55 | `ITEMDATA_ROOT` hardcoded instead of read from config, unlike its sibling script |

**Priority note:** #1207 (unflagged bulk eBay write burning quota) and #1204/#1205
(fence-bypass + missing catalog_rebuild) are the standouts — these are the kind of
scripts that get re-run without a second thought since they look like one-off
utilities, but can silently repeat exactly the failure classes CLAUDE.md's Prime
Directives were written to prevent.

## Remaining subsystems queue

nix flake. This is the last one — #1143 will be complete after it.
