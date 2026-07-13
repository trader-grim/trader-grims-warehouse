Working on todo #1290 (PP-COHESION-001) in worktree
`/opt/TGW/var/worktrees/1290-logging-jsonl-path` on branch
`todo/1290-logging-jsonl-path`. Fixing `src/tgw/logging.py`'s
`setup_logging()` JSON-log-path derivation at line ~149 — `str.replace()`
is a no-op when `filename` has no `.log` substring, so the `or
'tgw.jsonl'` fallback never fires and the JSON handler silently collides
with the main log file. Applying the explicit if/else fix from the
packet, adding a regression test to `tests/test_logging.py`, running
pytest, then writing the result manifest and committing.
