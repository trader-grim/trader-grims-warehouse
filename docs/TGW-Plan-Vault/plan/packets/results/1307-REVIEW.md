# Review: 1307 photo_history_recovery.py atomic-copy fix
Status: cleared
Reviewer: Claude (runner-review, 2026-07-14 morning)
Branch: todo/1307-photo-history-atomic-copy

Checked:
- Manifest sanity: status/files-touched/live-evidence all present;
  PYTHONPATH-under-worktree confirmation present in live evidence.
- Diff vs merge-base (6f2d7ef): 4 files, all in scope — the fixed
  function, its own self-authored result manifest + breadcrumb, and one
  existing test file (carve-out). No out-of-scope files touched.
- Spec conformance: ensure_copy() now writes to a same-directory temp
  file then os.replace()s onto the final path, with cleanup on any
  exception — matches the temp+os.replace pattern used elsewhere
  (invariants.md A1). `os` already imported, no missing-import bug.
- Honest deviation flagged and correctly reasoned: the finding text's
  "bypassing the tgw-api fence" half is not actionable as a single-file
  fix — confirmed live that no fence function for media writes exists
  anywhere in the codebase (invariants.md A8 already documents this as an
  accepted gap). Executor implemented only the atomic-write half rather
  than inventing new fence architecture out of packet scope — correct
  call per the stop-condition rule.
- Out-of-scope finding correctly filed as todo #1383 (verified live via
  `tgw todo brief 1383`) rather than silently fixed or dropped.
- No live/production writes attempted.

No trigger fired. Cleared for stitch.
