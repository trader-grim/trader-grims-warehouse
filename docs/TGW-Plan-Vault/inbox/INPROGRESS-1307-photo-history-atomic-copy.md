# In progress: todo #1307 (PP-COHESION-001)

Working in isolated worktree
`/opt/TGW/src/trader-grims-warehouse/.claude/worktrees/agent-ac3aae3e23c247d78`
on branch `todo/1307-photo-history-atomic-copy` (based on catio-nix-0.0.1-alpha
@ 6f2d7ef, live-verified as the active branch).

Task: `src/tgw/workers/photo_history_recovery.py`'s `ensure_copy()` writes
recovered photos into live `ItemData/<SKU>/` via a direct `shutil.copy2`
straight to the final destination path — not atomic (a crash/interrupt
mid-copy leaves a partial/corrupt image at the live path, which
thumbnail_gen/ebay_upload/catalog_rebuild could then pick up). Fixing to
copy to a temp file in the same destination directory, then `os.replace()`
onto the final path — same pattern `items.atomic_write_json`/`context.py`
use for JSON/symlinks (no such helper currently exists for binary media;
invariant A8 in invariants.md already notes there is no general fence-level
guard for media writes, only per-feature patterns like alt_text.py's
archive-then-rename). Investigating whether "bypassing the tgw-api fence"
part of the finding has a concrete existing media-write fence function to
route through, or whether that's aspirational/no-op given A8's documented
gap.
