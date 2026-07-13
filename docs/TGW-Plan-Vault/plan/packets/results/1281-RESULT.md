# Result: 1281 readiness-html-escape
Status: done
Todo: #1281   PP: PP-COHESION-001
Files touched: src/tgw/readiness.py, tests/test_readiness_html_escape.py
Live evidence:
- `readiness_html([ReadinessField(..., value="<script>alert(1)</script>", ...)])`
  output contains `&lt;script&gt;alert(1)&lt;/script&gt;` and does not contain
  a literal `<script>` tag (acceptance check 1, encoded as
  `test_script_tag_in_value_cannot_inject_html`).
- Plain value `'123 · Cell Phones'` (no special chars) produces
  byte-identical output to the pre-fix rendering (acceptance check 2,
  `test_plain_value_with_no_special_chars_is_byte_identical` — asserts
  exact expected HTML string).
- `value=None` still renders `val_html` as `""` (acceptance check 3,
  `test_none_value_still_renders_empty_val_html`).
- `f.label` (always a hardcoded literal, e.g. `"eBay title"`) remains
  unescaped, pinned explicitly by
  `test_label_remains_unescaped_hardcoded_literal` (acceptance check 4).
- Full offline suite: `PYTHONPATH=/opt/TGW/var/worktrees/1281-readiness-html-escape/src:$PYTHONPATH python3 -m pytest -q`
  → `2124 passed, 1 skipped, 1 warning in 45.83s` (acceptance check 5, zero
  regressions). Confirmed `tgw.readiness.__file__` resolves under the
  worktree path, not the shared checkout, before running.
Deviations from spec: none — change matches the packet's exact diff
(`import html as _html` at top of file; `val_html` line wraps
`str(f.value)` in `_html.escape()`; `f.label` interpolation, icon,
background/border colors untouched).
Out-of-scope findings filed: none
