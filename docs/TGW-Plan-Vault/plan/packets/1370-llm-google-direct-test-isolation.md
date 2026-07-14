# Packet: test_llm_google_direct.py leaks into the real shared quota-state file
Todo: #1370   PP: PP-COHESION-001   Track: follow-up cleanup batch (#1369-1374)

## Context budget (ALL the model may load)
This packet + `tests/test_llm_google_direct.py` (whole file, it's one small
module) + `tests/test_quota.py` lines 1-30 (the `_cfg(tmp_path, **raw)`
pattern to match) + `src/tgw/quota.py`'s `_state_path()`/`precheck()`/
`record()`/`record_429()` (read only, do not change quota.py itself).
Nothing else — do not read or touch `src/tgw/apis/llm.py`'s call_model body
beyond confirming the `quota.precheck(cfg, 'llm_google')` call site already
found (see below).

## Verified live before this packet was written
- `tests/test_llm_google_direct.py`'s module-level `_cfg()` (line 20) returns
  a bare `{'raw': {}}` with no `quota_state_path` override.
- `src/tgw/quota.py::_state_path()` (line 131) falls back to
  `_DEFAULT_STATE_PATH = '/opt/TGW/var/run/quota-state.json'` — the real
  production quota-state file — whenever `cfg['raw']` has no
  `quota_state_path` key.
- Three test classes in this file call `llm_mod.call_model(...)` with
  `provider='google_direct'` (and related dispatch/stand-down/reserve
  variants) without mocking `tgw.quota.precheck`/`record`/`record_429`:
  `TestCallModelGoogleDirectDispatch`, `TestGoogleStandDown`,
  `TestOperatorEmergencyReserve`. Each of these calls hits the real
  `quota.precheck(cfg, 'llm_google')` / `quota.record(cfg, 'llm_google')`
  inside `src/tgw/apis/llm.py::call_model` (confirmed at the call sites
  reading real state from `_DEFAULT_STATE_PATH`) — so every run of this
  test file mutates the actual production quota-state file on disk.
  `TestCallGoogleDirect` (line 65) calls `_call_google_direct` directly,
  not `call_model`, so it never reaches `quota.*` — no change needed there,
  but it still uses the same shared `_cfg()` helper, so the fix applies
  uniformly rather than only to the one currently-failing test.
- `tests/test_quota.py` already established the correct pattern: a
  `_cfg(tmp_path, **raw)` helper (line 23) that sets `quota_state_path` and
  `quota_incident_log` under pytest's per-test `tmp_path`. This packet
  brings `test_llm_google_direct.py` in line with that existing convention,
  not inventing a new one.
- Reproduces only in full-suite runs (order/state-dependent), passes in
  isolation — consistent with a shared-mutable-file bug, not a logic bug in
  `llm.py` itself. Do not "fix" `llm.py` — the fix is test isolation only.

## Spec

1. Change `_cfg()` (line 20) to `_cfg(tmp_path)`, adding
   `'quota_state_path': str(tmp_path / 'quota-state.json')` and
   `'quota_incident_log': str(tmp_path / 'quota-incidents.jsonl')` to the
   returned `'raw'` dict — same two keys, same shape as
   `tests/test_quota.py`'s `_cfg(tmp_path, **raw)`.
2. Update every call site listed above (`_cfg()` → `_cfg(tmp_path)`) and add
   `tmp_path` to the enclosing test method's parameter list wherever it
   isn't already present (all currently take `monkeypatch`; add `tmp_path`
   alongside it — pytest supplies both as built-in fixtures, no fixture
   wiring needed beyond the parameter name).
3. Do not add a `tmp_path` parameter to any test method that doesn't call
   `_cfg()` — none exist in this file besides the ones listed, but verify
   this holds after your edit (grep `_cfg()` again, confirm every remaining
   call site has `tmp_path` in scope).

## Dataset
None — this only changes test fixtures; no ItemData/queue/local storage
schema or content changes.

## Out of scope
- Any change to `src/tgw/quota.py` or `src/tgw/apis/llm.py`.
- Any other test file's quota isolation — if the same pattern is missing
  elsewhere, file it as a new todo, don't fix it in this packet.
- The `#1374` LD_LIBRARY_PATH/psycopg2 worktree-pytest issue — separate
  todo, unrelated to this fix even though both are in the same follow-up
  batch.

## Acceptance (live)
1. `PYTHONPATH=<worktree>/src:$PYTHONPATH pytest tests/test_llm_google_direct.py -q`
   passes standalone (already did before the fix — must still pass after).
2. Full offline suite: `PYTHONPATH=<worktree>/src:$PYTHONPATH pytest -q`
   passes with zero failures, specifically confirm
   `test_success_does_not_touch_openrouter` (and the rest of
   `TestCallModelGoogleDirectDispatch`/`TestGoogleStandDown`/
   `TestOperatorEmergencyReserve`) pass in the full-suite run, not just
   alone — this is the actual regression this packet closes.
3. Confirm the real `/opt/TGW/var/run/quota-state.json` is untouched by a
   test run (e.g. compare its mtime/contents before and after running this
   test file) — proves the isolation actually took effect, not just that
   the flaky ordering happened not to trigger this run.

## Quota/risk
None — pure test-isolation fix, no behavior change to any shipped code path.
