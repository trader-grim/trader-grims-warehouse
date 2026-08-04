# Review: 1282 http-server-constant-time-auth
Status: cleared — stitched in `c3d0611` ("SECURITY batch, concurrent 3/3").
RECONSTRUCTED RETROACTIVELY 2026-07-13 from `1282-RESULT.md` evidence +
the merge commit; no contemporaneous REVIEW.md was written (compliance
gap — same concurrent-batch pattern as #1280/#1284/#1288/#1291/#1297).
Reviewer: Claude (retroactive reconstruction)

Checked (from RESULT.md's own reported evidence): exact spec fix applied
at `http_server.py:273` — bearer-token compare switched from `==` to
`secrets.compare_digest(...)`, nothing else in `_require_auth()` touched.
New regression test `test_bearer_auth_uses_constant_time_compare`
monkeypatches `compare_digest` with a spy delegating to the real
implementation, confirms it's actually invoked on both correct- and
wrong-token paths (200/401 respectively). Ran against the worktree's own
module (`__file__` resolution confirmed, not the shared checkout).
Targeted run: 33 passed, 224 deselected. Full offline suite: 2047 passed,
1 skipped, 0 failed. No deviations from spec, no out-of-scope findings.

Stitched.
