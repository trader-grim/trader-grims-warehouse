# Result: 1604 multi-order-sold-fix

Status: done
Todo: #1604   PP: PP-SOLD-001

Files touched:
- src/tgw/ebay/pull.py — `mark_item_sold()`: `ebay_sale` is now a list of
  sold-order records (append, never overwrite). Idempotency now keys on
  `order_id` membership in that list, not `status == 'sold'`. Legacy
  single-dict shape on disk is normalized to a one-element list before the
  membership check (never crashes/discards on an item not yet touched by
  the fix). New explicit "oversold" branch: a distinct order landing after
  the item is already `status == 'sold'` is still appended (quantity stays
  at 0, `status` stays `sold`), logged at WARNING with `oversold=True` on
  the `ebay_item_sold` event — never silently dropped.
- src/tgw/reports.py — `_scan_items()`'s sold-row extraction now iterates
  `item['ebay_sale']` as a list (normalizing a legacy single-dict shape),
  emitting one sold_row per order instead of reading only the first/only
  sale.
- src/tgw/velocity.py — same list-normalization + per-order iteration in
  the sold-stats bucket builder.
- tests/test_sold_recon.py — updated `test_mark_item_sold_writes_sale_block`
  for the new list shape; renamed/rewrote the idempotency test
  (`test_mark_item_sold_same_order_id_is_idempotent`); added
  `test_mark_item_sold_second_distinct_order_is_never_dropped` — the
  regression test for the exact bug (two sequential `mark_item_sold()`
  calls with different `order_id`s on the same SKU both persist, asserts
  `ebay_sale` has 2 entries).
- Grepped `src/tgw/` for all `ebay_sale` readers (`http_server.py`,
  `ebay_legacy_sync.py`, `api.py`) — no other reader assumes the old
  single-dict shape; only `reports.py` and `velocity.py` read it.
- Live backfill (production data, via the fence, script not committed —
  one-off, deleted after use): `tgw202404031105366`'s `ebay_sale` now
  contains both orders. Announced via `tgw_logging.announce_script_run()`
  per invariant E9 before touching data.

Live evidence:
- Full offline test suite: `2729 passed, 1 skipped` (verified running from
  the worktree's own copy — confirmed `tgw.ebay.pull.__file__` resolves
  under `/opt/TGW/var/worktrees/1604-multi-order-sold-fix/src/...` before
  trusting the run).
- Raw eBay capture cross-check (`/opt/TGW/incoming/ebay/2026-07-20.jsonl.gz`,
  `GetOrders` response, `OrderID=26-14894-40269`) confirmed:
  `CreatedTime=2026-07-20T17:41:21.000Z`, `Subtotal(TransactionPrice)=19.99`,
  `QuantityPurchased=1`, buyer `UserID=themilkman94` (ShippingAddress `Name`
  is "Charles Confer" — the eBay display/shipping name; the item JSON's
  `ebay_sale.buyer` field stores the eBay `UserID`, matching the field
  convention already used by the first recorded order
  `25-14896-18029`/`themilkman94`, not the shipping name — noted as a
  clarification, not a deviation, since it matches the existing schema).
- Post-fix, post-backfill `tgw202404031105366` item JSON (verbatim,
  `/opt/TGW/data/ItemData/tgw202404031105366/tgw202404031105366.json`):
  ```json
  "ebay_sale": [
      {
          "order_id": "25-14896-18029",
          "buyer": "themilkman94",
          "sale_price": 19.99,
          "quantity": 1,
          "sale_date": "2026-07-20T17:23:50.000Z",
          "synced_at": "2026-07-20T22:07:46.571534+00:00"
      },
      {
          "order_id": "26-14894-40269",
          "buyer": "themilkman94",
          "sale_price": 19.99,
          "quantity": 1,
          "sale_date": "2026-07-20T17:41:21.000Z",
          "synced_at": "2026-07-20T22:39:53.221461+00:00"
      }
  ]
  ```
  Both `status` and `draft_listing.quantity` (`sold` / `0`) unchanged by the
  backfill (item was already sold-out from order 1 — this is the exact
  "oversold, still-recorded" branch).
- Idempotency verified live: re-running the same backfill call a second
  time returned `False` (no duplicate) — `ebay_sale` stayed at exactly 2
  entries.

Deviations from spec:
- The backfill's `buyer` value used the eBay `UserID` (`themilkman94`)
  rather than the shipping `Name` ("Charles Confer") the packet's prose
  used as a placeholder — cross-checking the raw capture and the item's
  own already-recorded first order showed `buyer` is the UserID field by
  existing convention, not the display name. Flagging explicitly per the
  "no silent substitution" rule, though this is matching existing schema
  convention, not introducing a new one.
- Spec step 2 said "quantity can't go negative — that's a separate,
  distinct code path/flag, out of scope for this packet, just make sure
  the order is never silently dropped." Implemented as: an explicit
  `already_sold_out` branch that appends to `ebay_sale`, leaves `status`/
  `draft_listing.quantity` untouched, and logs `oversold=True` on the
  `ebay_item_sold` event (WARNING level) — this is a minimal marker, not a
  designed "oversold" feature; flagging in case Dave wants a stronger
  signal (e.g. a dedicated `oversold: true` field on the record itself)
  later.

Out-of-scope findings filed:
- #1606 (PP-SOLD-001) — `draft_listing.quantity` (1) vs top-level `qty` (2)
  drift on `tgw202404031105366`, confirmed by Dave's physical count that
  `qty` was correct and `draft_listing.quantity` was stale. Distinct root
  cause from the multi-order data-loss bug; may recur on other multi-qty
  items — needs its own investigation pass.
