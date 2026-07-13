# Result: 1317 suggestions-todo-agent-validate
Status: done
Todo: #1317   PP: PP-COHESION-001

Files touched:
- src/tgw/suggestions.py
- tests/test_classify_suggestions.py

Fix detail: `src/tgw/suggestions.py`, `apply_classifications()`, `action == 'todo'`
branch (formerly line 180). Previously `agent = c.get('todo_agent', 'claude')` was
passed straight into `todo_add()` with no check, unlike the adjacent `pp_ref`
(regex allow-list, falls back to `None`) and `reasoning` (falls back to `'normal'`)
handling in the same function. Replaced with:

```python
agent = (c.get('todo_agent') or '').strip()
if agent not in ('claude', 'admin'):
    agent = 'admin'
```

Fallback chosen: `'admin'`, not `'claude'` — an invalid/hallucinated agent value
should route to a human for review rather than risk an unverified value silently
enabling autonomous Claude action. Matches the packet's own reasoning and the
project's existing "operator gate is the design" convention (routes to admin when
uncertain), and mirrors this file's own established pattern of failing toward the
safer/no-op choice (pp_ref drops to `None` rather than guessing).

Test added: `test_apply_invalid_todo_agent_falls_back_to_admin` in
`tests/test_classify_suggestions.py`, parametrized over `["gemini", "", "Claude",
"ADMIN", "  ", None]` — asserts `todo_add` is called with `"admin"` (never the raw
invalid value) for each case.

Live evidence (offline test suite, PYTHONPATH pinned to worktree):
```
PYTHONPATH=/opt/TGW/var/worktrees/1317-suggestions-todo-agent-validate/src:$PYTHONPATH pytest -q
...
2143 passed, 1 skipped, 1 warning in 54.05s
```
Confirmed `tgw.suggestions.__file__` resolves to the worktree path
(`/opt/TGW/var/worktrees/1317-suggestions-todo-agent-validate/src/tgw/suggestions.py`),
not the shared checkout, before running the suite.

Deviations from spec: none — packet allowed choosing between "existing convention"
and "admin as safest default" after checking; no more specific existing convention
was found beyond the packet's own suggestion, so `'admin'` was used as stated.

Out-of-scope findings filed: none — no new operational friction or adjacent bugs
encountered during this task.
