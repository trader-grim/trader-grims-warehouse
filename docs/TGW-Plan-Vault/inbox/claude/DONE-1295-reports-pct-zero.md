Working todo #1295 (PP-COHESION-001) on branch `todo/1295-reports-pct-zero`: fixing
`_pct()` in `src/tgw/reports.py` so `total > 0, n == 0` returns `"0.0%"` instead of the
misleading `"—"` (em-dash stays correct only when `total` is falsy). Pure-function fix,
no existing test file found for reports.py so adding a new minimal test file. Per packet
`docs/TGW-Plan-Vault/plan/packets/1295-reports-pct-zero.md`.
