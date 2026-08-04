# Result: 1367 picklist-line-html-escape
Status: done
Todo: #1367   PP: PP-COHESION-001
Files touched:
- src/tgw/ebay/description.py (one-line fix: escape `pl` at the HTML-embedding
  call site in `build_listing_description()`; `picklist_line()`'s own return
  value is unchanged)
- tests/test_ebay_description_picklist_html_escape.py (new — 4 tests covering
  the packet's Acceptance items 1-4)

Live evidence:
- New test file run in isolation (worktree copy confirmed via
  `tgw.ebay.description.__file__` resolving under the worktree path, not the
  shared checkout):
  ```
  tests/test_ebay_description_html_escape.py .... (4 passed, pre-existing #1276 tests unaffected)
  tests/test_ebay_description_picklist_html_escape.py .... (4 passed)
  8 passed in 0.15s
  ```
  - `build_listing_description({'title': 'Nice <b>item</b>', 'sku': 'tgw123', 'draft_listing': {}}, {})`
    → picklist `<p>` contains literal `Nice &lt;b&gt;item&lt;/b&gt;`, no real `<b>` element. (Acceptance 1)
  - `build_listing_description({'title': '<script>alert(1)</script>', ...}, {})`
    → returned string contains no literal `<script>` tag anywhere. (Acceptance 2)
  - `picklist_line({'title': '<script>alert(1)</script>', 'sku': 'tgw123', 'location': 'A1', 'ebay_listing': {}})`
    → `'tgw-pl::=::A1:=:<script>alert(1)</script>:=:tgw123:=:null'` — byte-identical
    raw/unescaped output, confirming other consumers' contract (warehouse
    picking, Google Sheet sync, tgw.source) is untouched. (Acceptance 3)
  - Plain-ASCII title/description input (`{'description': 'A gently used
    widget in excellent condition.', 'sku': 'tgw1', 'location': 'A1', 'title':
    'Widget'}`) → output unchanged from pre-fix behavior (`html.escape` is a
    no-op on plain ASCII with no `<`/`>`/`&`). (Acceptance 4)
- Full offline suite, run from inside the worktree with
  `LD_LIBRARY_PATH=$NIX_LD_LIBRARY_PATH PYTHONPATH=<worktree>/src:$PYTHONPATH
  python3 -m pytest -q`:
  ```
  2201 passed, 1 skipped, 1 warning in 246.24s (0:04:06)
  ```
  Zero regressions. (Acceptance 5)

Deviations from spec: none — applied exactly the one-line diff specified in
the packet (`_html.escape(pl)` at the call site only, `picklist_line()`
itself untouched).

Out-of-scope findings filed: none — no new findings surfaced during this
packet; `bp_html` and `picklist_line()`'s own return value were left
untouched per the packet's explicit out-of-scope list.
