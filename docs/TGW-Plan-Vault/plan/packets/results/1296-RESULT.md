# Result: 1296 promo-sync-null-href
Status: done
Todo: #1296   PP: PP-COHESION-001
Files touched: src/tgw/promo.py, tests/test_promo.py
Live evidence:
- `PYTHONPATH=/opt/TGW/var/worktrees/1296-promo-sync-null-href/src python3 -c "import tgw.promo as p; print(p.__file__)"`
  → `/opt/TGW/var/worktrees/1296-promo-sync-null-href/src/tgw/promo.py` (confirmed
  testing the worktree's own copy, not the shared checkout).
- `PYTHONPATH=/opt/TGW/var/worktrees/1296-promo-sync-null-href/src python3 -m pytest -q tests/test_promo.py`
  → `44 passed in 0.80s`, including 3 new regression tests
  (`TestPromoSyncNullHref`) covering exactly the packet's 3 acceptance cases:
  1. `promotionId: None, promotionHref: None` → no `AttributeError`, entry
     skipped via existing `if not promo_id: continue`, `get_item_price_markdown`
     never called.
  2. `promotionId: "abc123"` → `get_item_price_markdown` called with `"abc123"`.
  3. `promotionId: None, promotionHref: ".../PROMO-456"` → `get_item_price_markdown`
     called with `"PROMO-456"` (href-fallback path still works).
- Full offline suite: `PYTHONPATH=.../src python3 -m pytest -q` →
  `2049 passed, 1 skipped` (no regressions elsewhere).
- No live/sandbox eBay call made — packet marks this as optional bonus only;
  the fix is pure parsing logic with no new API calls (Quota/risk: none, per
  packet).
Deviations from spec: none — one-line fix applied exactly as specified
(`promo_summary.get("promotionId") or (promo_summary.get("promotionHref") or "").split("/")[-1]`),
scope held to that single line plus new regression tests; no other part of
`cmd_promo_sync()` or any other function in `promo.py` touched.
Out-of-scope findings filed: none
