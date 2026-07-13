# Result: 1295 reports-pct-zero
Status: done
Todo: #1295   PP: PP-COHESION-001
Files touched:
- src/tgw/reports.py (`_pct()` — return "—" only when `total` is falsy)
- tests/test_report_sales.py (updated `test_stage_pcts_computed`'s stale
  assertion that a genuine 0.0% renders as "—"; added `_pct` import and
  three direct unit tests for `_pct`: zero-n-with-real-total, zero-total,
  normal case)

Live evidence:
```
>>> _pct(0, 10)
'0.0%'
>>> _pct(0, 0)
'—'
>>> _pct(5, 10)
'50.0%'
```
All three match the packet's Acceptance section exactly. Full test file
`tests/test_report_sales.py`: 63 passed. Full repo `pytest -q`: 2049 passed,
1 skipped (pre-existing, unrelated).

Deviations from spec: none.

Out-of-scope findings filed: none — no adjacent issues found in `_pct()` or
its immediate callers during this change; the one existing test asserting
the old (buggy) `"—"` output for a genuine 0% was updated in place since it
tests `_pct`'s output directly (not a caller-behavior change, per packet's
out-of-scope note).
