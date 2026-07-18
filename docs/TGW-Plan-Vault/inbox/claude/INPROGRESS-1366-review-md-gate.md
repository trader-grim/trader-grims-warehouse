Working on todo #1366 (PP-HERMES-EA-001) in worktree
`/opt/TGW/var/worktrees/1366-review-md-gate`, branch `todo/1366-review-md-gate`.

Building a mechanical pre-stitch check (`scripts/check_review_md.py`) that
verifies `docs/TGW-Plan-Vault/plan/packets/results/<id>-REVIEW.md` exists
before a todo's branch is stitched, plus a test
(`tests/test_check_review_md.py`) and a short pointer note in
`.claude/skills/tgw-runner-review/SKILL.md` near the REVIEW.md write step
(around line 125, per the todo body — verifying live since line numbers
drift).

Root cause of the original silent-skip (6/7 concurrent-batch todos missing
-REVIEW.md, discovered/backfilled 2026-07-13) was NOT confirmed per the
todo body — this task is scoped to the mechanical gate only, not a root
cause investigation.
