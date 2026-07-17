Working on todos #1278/#1279 (PP-COHESION-001, SECURITY track) in worktree
`/opt/TGW/var/worktrees/1278-1279-pm-intake-fetch-url-ssrf-sizecap` on branch
`todo/1278-1279-pm-intake-fetch-url-ssrf-sizecap`. Combined packet:
`docs/TGW-Plan-Vault/plan/packets/1278-1279-pm-intake-fetch-url-ssrf-sizecap.md`.
Fixing `fetch_url()` in `src/tgw/workers/pm_intake.py` to (1) block SSRF
targets (private/loopback/link-local/reserved, including mid-redirect via
httpx event hooks) and (2) cap response body size at 5MB via streaming
before buffering. Adding tests to `tests/test_pm_intake.py`. No other files
in scope.
