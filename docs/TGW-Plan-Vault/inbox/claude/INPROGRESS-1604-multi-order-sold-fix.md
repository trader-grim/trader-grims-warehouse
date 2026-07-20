# In progress: todo #1604 (PP-SOLD-001)

Fixing data-loss bug in `mark_item_sold()` (`src/tgw/ebay/pull.py`) — a second
distinct sold order for the same SKU was silently dropped once the first order
zeroed quantity and flipped status to 'sold'. Converting `ebay_sale` to a list
keyed by order_id membership, updating downstream readers, backfilling the
dropped order (26-14894-40269) for `tgw202404031105366` via the fence, adding
a regression test. Working in isolated worktree
`/opt/TGW/var/worktrees/1604-multi-order-sold-fix` on branch
`todo/1604-multi-order-sold-fix`.
