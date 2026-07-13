Status: cleared
Reviewer: Claude (runner-review)
Todo: #1285   PP: PP-COHESION-001
Checked: diff (`git diff 1967819 todo/1285-resolver-prefix-match`) against
the todo brief's stated bug (dead-code prefix-match fast path for 14-17
char queries), scope (resolver.py + new test only, no config/secrets/eBay
scope changes), result manifest completeness (status/files/live-evidence/
deviations all present), invariants.md (no relevant SKU-resolution
invariant touched beyond the fix itself).
Summary: minimal, exactly-scoped fix — `s[:len(q)] == q` replaces the
always-False `s[:18] == q[:18]` comparison; new regression test covers the
14-17 char range; full suite green (2135 passed, 1 skipped) run with
PYTHONPATH pinned to the worktree. No triggers fired. Cleared for stitch.
