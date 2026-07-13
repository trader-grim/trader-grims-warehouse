Status: cleared
Reviewer: Claude (runner-review)
Todo: #1317   PP: PP-COHESION-001
Checked: diff (`git diff f219b4b todo/1317-suggestions-todo-agent-validate`)
against the todo brief's stated bug (unvalidated todo_agent from LLM
classification flowing straight into a real todo record), scope
(suggestions.py + new test only), result manifest completeness.
Summary: minimal allow-list check — `todo_agent` normalized/stripped, falls
back to `'admin'` (not `'claude'`) for anything outside the exact
`{'claude','admin'}` set, correctly reasoned as safer (routes to human
review rather than enabling unverified autonomous action — matches this
project's standing operator-gate-is-the-design principle). Test is
parametrized over 6 invalid-value shapes (wrong provider, empty, wrong
case x2, whitespace, None) and asserts the exact safe call. Full suite
green (2143 passed, 1 skipped). No triggers fired. Cleared for stitch.
