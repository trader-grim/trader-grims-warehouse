# Result: #1285 resolver-prefix-match
Status: done
Todo: #1285   PP: PP-COHESION-001
Files touched: src/tgw/resolver.py, tests/test_resolver.py
Live evidence:
- Fix: in `resolve()` (src/tgw/resolver.py, old-format prefix-match fast
  path under `if 'sku' in selectors`), replaced the always-length-18
  comparison `s[:18] == prefix18` (where `prefix18 = q[:18]`) with
  `s[:len(q)] == q`. Previously, for a 14-17 char query, `q[:18]` was a
  no-op (query shorter than 18 chars) while `s[:18]` was 18 chars for
  any full-length candidate — two differently-sized strings compared
  equal to False every time, so the fast path was dead code for exactly
  the 14-17 char range its own guard claims to support.
- Added regression test `test_resolve_old_format_partial_sku_prefix_match`
  in tests/test_resolver.py: builds two full 20-char-format SKUs in a
  temp ItemData root, queries `resolve(cfg, sku=<17-char partial prefix>)`
  and asserts it now returns exactly the matching full SKU (previously
  would have returned empty set under the old code).
- Full offline suite run with `PYTHONPATH` pointed at this worktree's
  `src/` (confirmed via `tgw.resolver.__file__` resolving under the
  worktree path before running): `pytest -q` → `2135 passed, 1 skipped`
  (skip pre-existing/unrelated), 50.56s.
Deviations from spec: none — fix is exactly the slice-length correction
the packet described; no other resolver logic touched.
Out-of-scope findings filed: none
