# Result: 1276 ebay-description-html-escape
Status: done
Todo: #1276   PP: PP-COHESION-001
Files touched: src/tgw/ebay/description.py, tests/test_ebay_description_html_escape.py
Live evidence:
- `PYTHONPATH=/opt/TGW/var/worktrees/1276-ebay-description-html-escape/src python3 -m pytest -q tests/test_ebay_description_html_escape.py` → `4 passed in 0.08s`
- `build_listing_description({'description': 'Nice <b>item</b>, buy now!'}, {})` now returns
  `<p>Nice &lt;b&gt;item&lt;/b&gt;, buy now!</p>...` (verified via test, literal `<b>` element no longer present).
- `build_listing_description({'description': '<script>alert(1)</script>'}, {})` returns
  `<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>...` — no literal `<script>` tag.
- Plain-prose input (`'A gently used widget in excellent condition.'`) produces byte-identical
  output to pre-fix behavior (no `&lt;`/`&gt;` introduced, escaping is a no-op for prose with
  no special chars).
- Confirmed testing against the worktree's own copy, not the shared checkout:
  `python3 -c "import tgw.ebay.description as d; print(d.__file__)"` →
  `/opt/TGW/var/worktrees/1276-ebay-description-html-escape/src/tgw/ebay/description.py`.
- Full offline suite: `PYTHONPATH=.../src python3 -m pytest -q` → `2115 passed, 1 skipped, 1 warning in 42.91s`, zero regressions.
Deviations from spec: none — only `ai_desc` escaped via `html.escape()`; `bp_html` (operator
config) and `pl` (picklist line) left untouched exactly as specced.
Out-of-scope findings filed: #1367 (picklist_line()'s unescaped `title` interpolation into the
HTML picklist line — same injection class, not fixed here per packet's explicit scope boundary).
