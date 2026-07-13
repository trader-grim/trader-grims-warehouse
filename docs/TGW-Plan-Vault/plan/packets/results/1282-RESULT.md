# Result: 1282 http-server-constant-time-auth
Status: done
Todo: #1282   PP: PP-COHESION-001
Files touched: src/tgw/http_server.py, tests/test_http_server.py
Live evidence:
- `src/tgw/http_server.py:273` changed from
  `if credentials and credentials.credentials == _api_key:` to
  `if credentials and secrets.compare_digest(credentials.credentials.encode(), _api_key.encode()):`
  — exactly the spec's fix, no other line in `_require_auth()` touched.
- Added `test_bearer_auth_uses_constant_time_compare` in
  `tests/test_http_server.py` (next to the existing bad/missing-token auth
  tests), monkeypatching `http_server.secrets.compare_digest` with a spy
  that delegates to the real implementation:
  - correct bearer token against `/api/items` → 200, spy invoked with
    `(API_KEY.encode(), API_KEY.encode())`
  - wrong bearer token against `/api/items` → 401, spy invoked with
    `(b"wrong", API_KEY.encode())`
- Ran with PYTHONPATH pinned to this worktree's `src/` (confirmed via
  `python3 -c "import tgw.http_server as h; print(h.__file__)"` resolving
  to `/opt/TGW/var/worktrees/1282-http-server-constant-time-auth/src/tgw/http_server.py`,
  not the shared checkout):
  - `pytest -q tests/test_http_server.py -k "auth or bearer or constant_time"`
    → 33 passed, 224 deselected
  - full offline suite `pytest -q` → 2047 passed, 1 skipped, 0 failed
    (zero regressions to auth-gated endpoints or anything else)
Deviations from spec: none — applied the exact diff given in the packet,
touched nothing else in `_require_auth()`, left the password-check block
and the session-cookie fallback path untouched.
Out-of-scope findings filed: none
