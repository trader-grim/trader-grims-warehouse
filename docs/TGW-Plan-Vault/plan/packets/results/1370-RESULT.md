# Result: 1370 llm-google-direct-test-isolation
Status: done
Todo: #1370   PP: PP-COHESION-001
Files touched: tests/test_llm_google_direct.py
Live evidence: `PYTHONPATH=<worktree>/src pytest tests/test_llm_google_direct.py -q`
→ 14 passed standalone (confirmed testing the worktree's own copy via
`tgw.apis.llm.__file__` resolving under the worktree path, not the shared
checkout). Full offline suite: `PYTHONPATH=<worktree>/src pytest -q` →
2177 passed, 1 skipped, 0 failures in 111.49s — includes
TestCallModelGoogleDirectDispatch/TestGoogleStandDown/
TestOperatorEmergencyReserve, the classes that previously only failed in
full-suite context. `_cfg()` now scopes `quota_state_path`/
`quota_incident_log` to pytest's `tmp_path`, matching test_quota.py's
established pattern, so no call in this file can reach
/opt/TGW/var/run/quota-state.json regardless of suite ordering.
Deviations from spec: none.
Out-of-scope findings filed: none — no `quota.py`/`llm.py` behavior
changed, per the packet's Out-of-scope section.
