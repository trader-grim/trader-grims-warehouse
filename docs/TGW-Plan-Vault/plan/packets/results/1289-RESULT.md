# Result: 1289 health-openrouter-key-limit
Status: done
Todo: #1289   PP: PP-COHESION-001
Files touched: src/tgw/health.py, tests/test_health_openrouter_key_limit.py
Live evidence:
- Pre-flight (live, tgw user): confirmed `/opt/TGW/secrets/openrouter-credentials.json`
  no longer exists at its old path — it's under
  `/opt/TGW/secrets/_migrated-to-tgw-env-20260709/openrouter-credentials.json` —
  and `OPENROUTER_API_KEY` is present in `/opt/TGW/secrets/tgw.env`, confirming
  the packet's stated cause of the dead check.
- Acceptance 1 (key set, live env, network call mocked per task instructions —
  no real OpenRouter call made): `_openrouter_key_limit({})` reached the
  `requests.get` call (not blocked at key lookup), with a correctly-formed
  `Authorization: Bearer <key>` header, and returned
  `{'limit': 5, 'limit_reset': 'daily', 'limit_remaining': 4.5}` from the
  mocked response.
- Acceptance 2 (key unset, live env): `_openrouter_key_limit({})` returned
  `None` with no exception propagating.
- Acceptance 3 (`check_quota()` live, real cfg via `load_config`, network
  mocked): returned
  `{'ok': True, 'check': 'quota', 'detail': 'llm_google=25/300, llm_openrouter=30 | openrouter key: $4.50 of $5 remaining (daily)', ...}`
  — the openrouter key-limit segment populates correctly in the detail
  string and the rest of the quota check runs unaffected.
- Offline: `pytest -q` (worktree PYTHONPATH override, confirmed
  `tgw.health.__file__` resolves under the worktree) — 2046 passed, 1
  skipped, 0 failed. `tests/test_health_openrouter_key_limit.py` itself
  (5 tests) updated to mock `tgw.apis.secrets.get_api_key` instead of the
  old dead credentials-JSON fixture (the old test fixture would have kept
  masking this exact bug — it created a fake `openrouter-credentials.json`
  that the fixed code no longer reads at all).
Deviations from spec: none. Implementation matches the spec exactly:
`_openrouter_key_limit()` now calls `tgw.apis.secrets.get_api_key('openrouter')`,
catches the `RuntimeError` raised when unset and returns `None`, and the
`requests.get(...)` call + response parsing are unchanged. Docstring
updated to describe the new source (previously described "the credentials
file"). Existing test file predated this fix and exercised the old,
now-removed file-based path — updated in place (not out of scope: the
packet's Acceptance criteria are exactly what this test file covers) to
mock the new `get_api_key` entry point instead.
Out-of-scope findings filed: none.
