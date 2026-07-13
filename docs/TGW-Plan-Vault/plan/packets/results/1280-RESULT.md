# Result: 1280 aider-mcp-secrets-facility
Status: done
Todo: #1280   PP: PP-COHESION-001
Files touched: src/tgw/aider_mcp_server.py
Live evidence:
- Module resolved from worktree path, not shared checkout: `tgw.aider_mcp_server.__file__` ==
  `/opt/TGW/var/worktrees/1280-aider-mcp-secrets-facility/src/tgw/aider_mcp_server.py`.
- With real facility env vars sourced from `secrets_root/tgw.env` (as `tgw` user):
  `_load_api_keys()` returned `keys present: ['ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY']`,
  `anthropic populated: True`, `openrouter populated: True` — previously always empty due to
  the dead `anthropic-credentials.json`/`openrouter-credentials.json` paths (files moved to
  `secrets_root/_migrated-to-tgw-env-20260709/` by the 2026-07-09 migration, #1252).
- With both env vars unset: `_load_api_keys()` returned `{}` — no exception propagated,
  matching the original best-effort "silently skip if unavailable" contract.
- Full offline suite (`pytest -q`, `PYTHONPATH` pinned to worktree `src/`, confirmed via
  `__file__` check above before running): `2046 passed, 1 skipped, 1 warning in 34.71s`.
  Zero regressions.
Deviations from spec: Removed the now-unused `_SECRETS_ROOT` constant (checked first —
  grepped the whole file, confirmed no other reference) since the packet explicitly said to
  check whether it's used elsewhere before removing; it was not. `json` import kept — it is
  used elsewhere in the file (multiple `json.dumps` calls in other functions), per the
  packet's caveat.
Out-of-scope findings filed: none — no adjacent issues found while working strictly within
  `_load_api_keys()` per the packet's declared scope.
