status: cleared
reviewer: Claude (main session)
todo: #1311
pp_ref: PP-COHESION-001 (matches)

Diff matches packet spec exactly: items.create_item() now creates the
parent directory before writing (closing the real behavior gap), and
create_item_endpoint() now delegates to items.create_item() instead of
duplicating the logic inline, translating FileExistsError to the existing
409 HTTPException. _SKU_RE validation and enqueue_catalog_rebuild left
untouched per spec. Scope: http_server.py + items.py + their tests +
result manifest only. Live evidence: 2182 passed/1 skipped full suite,
worktree module resolution confirmed. No invariant violations, no
out-of-scope findings. Cleared for stitch.
