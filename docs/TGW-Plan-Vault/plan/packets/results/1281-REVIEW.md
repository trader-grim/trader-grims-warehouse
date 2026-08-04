# Review: 1281 readiness-html-escape
Status: cleared — concurrent batch (post 2-in-a-row graduation), stitching independently.
Reviewer: Claude (main session, tgw-runner-review)

Checked: Spec — `val_html` line wraps `str(f.value)` in `html.escape()`,
exactly as specced; `f.label`'s interpolation deliberately untouched
(confirmed a hardcoded literal at every `_f(...)` call site, not
item-derived). Out-of-scope — only `readiness.py` + its new test file
touched. Invariants — n/a (operator-facing HTML widget, no ItemData write
path). Live evidence — re-verified independently: 4 new tests cover
script-tag escaping, byte-identical no-op output for plain values (full
string equality check, not just substring), `None`-value guard behavior,
and an explicit pin on `label` staying unescaped (exactly the assumption-
guard the packet's acceptance criterion #4 asked for). Confirmed
`tgw.readiness.__file__` resolves under the worktree path, full offline
suite 2124 passed/1 skipped/0 failed — matches executor's reported
numbers. No deviations from spec, no out-of-control triggers fired.

Stitched.
