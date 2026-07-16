# DONE: catalog_rebuild debounce fix (Postgres-catalog plan still separate/logged only)

**Status 2026-07-13:** all 3 fix steps complete and verified. Full suite
green (2111 passed, 1 skipped) as `db` — note `tgw` user can't collect
pytest in this checkout (`nix` symlink → `/home/db/tgw-flake/nix`, tgw
lacks read into db's home; pre-existing env friction, not caused by this
change, worth its own todo). Step 3 (centralize ~19 inline
`enqueue_job(queue_name="catalog_rebuild"...)` blocks across api.py,
http_server.py, scrub.py, sku_migration.py, snapshot_backfill.py, and 9
workers behind `state_machine.enqueue_catalog_rebuild(reason,
delay_seconds=30.0)`) was the piece left unfinished from the prior
session — now done. Not yet committed; not yet given a todo id/pp_ref.

**PP:** PP-NIXOS-001 (follows the catalog-rebuild-loop incident earlier today)

## Root cause of "unnecessary constant rebuilds during bursts"

`uq_queue_jobs_dedupe_key_active` correctly blocks duplicate `catalog_rebuild`
jobs while one is queued/leased/running. But the moment a job **succeeds**,
the dedupe key frees immediately. During a sustained write burst (e.g. the
ebay_sync per-SKU sweep from the earlier incident), the very next write
re-arms a fresh job exactly `not_before` (30s) later. Since a rebuild itself
takes ~57s, this produces one full 4-artifact rebuild every ~87s for the
entire duration of the burst — not a bug in the dedupe constraint, but a
missing debounce: nothing pushes `not_before` forward while new writes keep
arriving, so it fires on a fixed cadence instead of only after writes go
quiet.

## Fix

1. `state_machine.enqueue_job` gets an opt-in `debounce: bool = False` param.
   When True, uses `INSERT ... ON CONFLICT (dedupe_key) WHERE <same predicate
   as uq_queue_jobs_dedupe_key_active> DO UPDATE SET not_before =
   GREATEST(existing, new) ...` instead of a plain insert — so a duplicate
   write while a job is still queued pushes it further out rather than
   being silently dropped. Off by default — per-SKU pipeline dedupe keys
   (`ebay_stage:{sku}`, `ai_identify:{sku}`, etc.) must NOT debounce-extend,
   or a hot item could livelock its own pipeline stage indefinitely.
2. New `state_machine.enqueue_catalog_rebuild(reason, delay_seconds=30.0)`
   helper centralizes the ~20 duplicated inline
   `enqueue_job(queue_name="catalog_rebuild", dedupe_key="catalog_rebuild:pending", ...)`
   blocks scattered across http_server.py, api.py, and most workers.
3. All those call sites now call the helper instead of duplicating the
   block inline.

## Separate: Postgres-catalog plan (not implemented today, logged only)

Dave, 2026-07-12: move the main catalog off the JSON-file full-rebuild
pipeline into incremental Postgres rows (state_machine DB already running,
"it is sitting there, might as well use it") — turns each item write into
an O(1) upsert instead of an O(55k) full rebuild. Logged as a todo under
PP-NIXOS-001 for future design/scoping; the portable SQLite export
(`tgwcatalog.db`) likely stays SQLite regardless (single-file portability
is the point of that artifact) — the win is making the *master* catalog
incremental, independent of the export format.
