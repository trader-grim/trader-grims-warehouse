Todo #1308 (PP-COHESION-001, invariant E9) — DONE, stitched.
photo_history_recovery.py's main() now calls announce_script_run() before
touching ItemData or the queue. Reviewed clean, full suite green. Spun off
todo #1369 (audit workers/*.py and tools/*.py for the same gap — turned out
this project has zero real callers of announce_script_run() today, only a
docstring example; original packet briefing wrongly assumed otherwise).
