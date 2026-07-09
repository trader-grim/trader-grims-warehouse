Todo #1234 (audit#1143 merged #1165+#1166) — DONE.

Shared home: worker_base.py:231 (#1201 backoff wrapper) handles retry
classification only, not terminal-failure alerting/rescheduling — added a
new, adjacent hook instead of overloading it.

Fix (one shared mechanism, both call sites):
- state_machine.mark_failed() now returns 'retry_wait' | 'dead_letter'
  instead of None, so the caller can detect a terminal transition.
- worker_base.QueueWorker._process(): on HardFailure AND on retries-exhausted
  dead_letter (previously silent — no notify() at all on that path), calls
  new hook self._on_terminal_failure(job, error_text). Default: no-op.
- token_refresh.py / velocity_stats.py override _on_terminal_failure to call
  their existing self._reschedule(), so a dead-lettered check still enqueues
  the next one instead of ending the chain.
- Bonus fix found in the same code path: the retries-exhausted dead_letter
  branch had NO notify() at all before this change (only HardFailure did) —
  now both dead_letter paths alert.

Evidence:
- New tests/test_worker_base_terminal_failure.py (4 cases: HardFailure hook
  fires, exhausted-retries hook fires, retry_wait does NOT fire the hook,
  default no-op doesn't raise) — 4/4 pass.
- Full offline suite: 1853 passed, 10 pre-existing failures unrelated to this
  diff (test_model_routing.py / test_llm_google_direct.py — stale
  google_direct vs openrouter expectations, memory:
  project-google-direct-migration; test_invariants_pricing.py — untouched by
  this change). Targeted run
  `pytest -k "worker_base or token_refresh or velocity_stats or state_machine"`
  = 8 passed, 1 skipped.
- Diff scope confirmed minimal: state_machine.py, worker_base.py,
  token_refresh.py, velocity_stats.py + 1 new test file.

No deviations from spec. Out-of-scope items found: none new (the 10 failing
tests are pre-existing and already tracked by the google_direct rollback).
