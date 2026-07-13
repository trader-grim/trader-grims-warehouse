# Review: 1280 aider-mcp-secrets-facility
Status: cleared — stitched in `9716ef5` ("SECURITY batch, concurrent 2/3").
RECONSTRUCTED RETROACTIVELY 2026-07-13 from `1280-RESULT.md` evidence +
the merge commit; no contemporaneous REVIEW.md was written (compliance
gap — the mandated review-artifact write got dropped for this
concurrent-batch run; see [[feedback-lone-task-stitch-authority]] session
notes / todo filed for the pattern).
Reviewer: Claude (retroactive reconstruction)

Checked (from RESULT.md's own reported evidence): module resolved from
the worktree path, not the shared checkout, before any test ran. Real
facility env vars from `secrets_root/tgw.env` produced populated
Anthropic/OpenRouter keys via `_load_api_keys()` — previously always empty
due to the dead per-provider credentials JSON paths. Both-unset case still
returns `{}` with no exception, preserving the original best-effort
contract. Full offline suite: 2046 passed, 1 skipped, 0 failed. Deviation
reported and accepted: removed the now-dead `_SECRETS_ROOT` constant after
confirming (grep) no other reference; `json` import correctly kept since
used elsewhere in the file. No out-of-scope findings filed.

Stitched.
