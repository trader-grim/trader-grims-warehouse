# Packet: bearer-token auth check uses constant-time comparison
Todo: #1282   PP: PP-COHESION-001   Track: SECURITY batch, concurrent

## Context budget (ALL the model may load)
This packet + `src/tgw/http_server.py` (`_require_auth()` only, lines
~269-278, and its existing test file if one exists) + the todo brief
(`tgw todo brief 1282`). Nothing else.

## Spec
At `src/tgw/http_server.py:273`:
```python
if credentials and credentials.credentials == _api_key:
```
Plain string equality short-circuits on the first mismatched character —
a timing side-channel that can (in principle) let an attacker recover the
API key byte-by-byte via repeated timing measurements. The password check
40 lines below (`src/tgw/http_server.py:312`) already correctly uses
`secrets.compare_digest()` for exactly this reason — this bearer-token
check should follow the same convention. `secrets` is already imported in
this file (used at line 312).

Fix:
```python
if credentials and secrets.compare_digest(credentials.credentials.encode(), _api_key.encode()):
```
No other change.

## Dataset
None — this is an auth-check hardening, not a data write.

## Out of scope
- The password-check block (line ~312) — already correct, don't touch.
- Any other part of `_require_auth()` (the session-cookie fallback path).
- Any other function in `http_server.py`.

## Acceptance (live)
1. Call `_require_auth()` (or the equivalent endpoint) with the correct
   `_api_key` as the bearer token — confirm it still succeeds (no
   exception raised).
2. Call it with an incorrect token — confirm it still correctly raises
   `HTTPException` (401), same as before.
3. Confirm `secrets.compare_digest` is actually being called (not just
   present in the diff) — e.g. via a monkeypatch/spy on
   `secrets.compare_digest` in a test, confirming it's invoked with the
   right two arguments.
4. Run the full offline suite — confirm zero regressions to auth-gated
   endpoints.

## Quota/risk
None.
