# Result: 1290 logging-jsonl-path
Status: done
Todo: #1290   PP: PP-COHESION-001
Files touched: src/tgw/logging.py, tests/test_logging.py
Live evidence: subprocess run of setup_logging() confirms both cases:
  Case 1 (log_file='custom', no '.log' extension) → handler paths
  `[.../custom, .../tgw.jsonl]`, distinct inode=True (previously would have
  collapsed to the same file due to the str.replace() no-op bug).
  Case 2 (log_file='custom.log') → handler paths
  `[.../custom.log, .../custom.jsonl]`, distinct inode=True.
  Full test suite: `PYTHONPATH=<worktree>/src python -m pytest -q` →
  2048 passed, 1 skipped, 1 warning (offline, whole repo, not just the
  touched file).
Deviations from spec: none — applied the exact if/endswith fix given in
  the packet. Added two regression tests to tests/test_logging.py
  covering both acceptance cases (not explicitly requested by name in the
  packet body, but required by the todo brief's constraint "new behavior
  gets tests").
Out-of-scope findings filed: none. (Noted but not acted on: this
  repo/worktree setup relies on a PYTHONPATH env var hardcoded to the
  shared checkout's src/ dir rather than the worktree — had to override
  PYTHONPATH explicitly to test the worktree's own code instead of the
  shared checkout's. Not a logging.py bug and out of this packet's scope;
  no todo filed since it's a known property of how worktrees are invoked
  here, not a new discovery, and didn't block acceptance.)
