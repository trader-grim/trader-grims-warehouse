Todo #1317 (PP-COHESION-001) — DONE, stitched. suggestions.py now validates
the LLM-classified todo_agent field against the {'claude','admin'} allow-list,
falling back to 'admin' (human review) for anything else. Reviewed clean,
full suite green.
