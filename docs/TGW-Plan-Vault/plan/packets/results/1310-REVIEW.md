status: cleared
reviewer: Claude (main session)
todo: #1310
pp_ref: PP-COHESION-001 (matches)

Diff matches packet spec exactly: delete_asset() archives the target
photo via items._archive_before_overwrite before target.unlink(), guarded
on archive_root being truthy (null-safe). Scope: http_server.py + its
tests + result manifest only. Live evidence: both new tests pass,
2179 passed/1 skipped full suite, worktree module resolution confirmed.
No invariant violations, no out-of-scope findings. Cleared for stitch.
