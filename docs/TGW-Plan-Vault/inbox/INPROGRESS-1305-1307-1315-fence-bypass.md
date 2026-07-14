# In progress — 2026-07-14 morning: PP-COHESION-001 fence-bypass batch

Continuing the stitch backlog from the 2026-07-13 evening session (see
handoff.md). Dispatching the three remaining independent-file
fence-bypass findings via tgw-coder (branch-per-task, worktree-isolated),
to be reviewed/stitched with tgw-runner-review:

- #1305 — itemdata_scrub.py bypasses tgw-api fence (direct
  atomic_write_json on hand-built path, recursive regex key deletion)
- #1307 — photo_history_recovery.py ensure_copy() uses shutil.copy2
  directly into live ItemData/<SKU>/, bypassing fence + atomic-write
- #1315 — scrub.py data_scrub_pass1/data_scrub_qty_repair/
  data_scrub_size_class_backfill call atomic_write_json() without
  archive_root (invariant E5, archive-before-overwrite never fires)

These are independent files (handoff.md confirms not a shared-root
cluster like the earlier 4-item config.py cluster), so dispatching
concurrently per the established cadence rule (this continues an
already-graduated sequence from the 2026-07-13 evening 3rd stitch cycle,
not a fresh sequence needing 2-in-a-row-clean first).

Todo #1286 ("in progress: tgw-coder") confirmed stale/orphaned this
session — body has no real task content, just a placeholder. Left alone,
flagged for cleanup, not dispatched.

Skipped-for-now: empty inbox file
`plan-invariant-supporting-infrastructure.md` (0 bytes) and Tigwa's
non-blocking WoL BIOS check request — neither needs action from this
thread right now.
