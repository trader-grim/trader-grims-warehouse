Status: cleared
Reviewer: Claude (runner-review)
Todo: #1286   PP: PP-COHESION-001
Checked: diff (`git diff 1967819 todo/1286-catalog-check-only`) against
the todo brief's stated bug (check_only dry-run crashes with
FileNotFoundError on a fresh system), scope (catalog.py + new test only),
result manifest completeness (status/files/live-evidence/deviations all
present), and confirmed by reading `build_search_catalog`/
`build_location_tree`'s existing `source='auto'` fallback logic
(catalog.py:225-273) that switching the two hardcoded `source=` args to
`'auto'` reuses already-correct fallback behavior and does not change
non-check_only build behavior (full_catalog file exists by the time those
steps run in a real build, so 'auto' still resolves to 'full_catalog').
Summary: minimal fix — two hardcoded `source=` values replaced with the
mechanism's own `'auto'` mode; author verified the bug reproduces with the
fix stashed out; new regression test covers the fresh-system check_only
case; full suite green (2136 passed, 1 skipped). No triggers fired.
Cleared for stitch.
