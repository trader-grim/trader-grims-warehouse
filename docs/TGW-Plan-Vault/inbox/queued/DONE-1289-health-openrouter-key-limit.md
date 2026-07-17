Working on todo #1289 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1289-health-openrouter-key-limit` on branch
`todo/1289-health-openrouter-key-limit`. Fixing
`_openrouter_key_limit()` in `src/tgw/health.py` to read the OpenRouter
key via `tgw.apis.secrets.get_api_key('openrouter')` instead of the
dead `openrouter-credentials.json` path (moved by the 2026-07-09
secrets migration, #1252). Scope: `health.py` only, no other files.
Plan: replace direct file read, catch RuntimeError from get_api_key,
return None on missing key; add offline test with monkeypatched
get_api_key + mocked requests.get. Not yet committed.
