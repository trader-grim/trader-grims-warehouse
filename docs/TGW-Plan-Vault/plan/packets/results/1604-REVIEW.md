# Review: todo #1604 multi-order-sold-fix

status: cleared
reviewer: Claude (main session, 2026-07-20)
branch: todo/1604-multi-order-sold-fix
base: catio-nix-0.0.1-alpha @ ad5491c (correct — diffed against catio-nix-0.0.1-alpha,
      not `main`, which is a stale ancestor per project convention)

## What was checked

- Result manifest (`1604-RESULT.md`, read via `git show` off the branch):
  status/files-touched/live-evidence all present, no sanity-check failure.
- Diff vs `catio-nix-0.0.1-alpha`: 6 files, all within declared scope
  (`src/tgw/ebay/pull.py` core fix, `reports.py`/`velocity.py` readers,
  `tests/test_sold_recon.py`, the inbox breadcrumb and this packet's own
  result manifest). No packet doc existed (dispatched directly, not via
  `/tgw-packet`) — treated the original dispatch prompt as the spec; no
  todo/pp_ref mismatch (#1604 / PP-SOLD-001 consistent throughout).
- Core fix (`mark_item_sold`): `ebay_sale` is now a list, appended not
  overwritten; idempotency keys on `order_id` membership, not
  `status == 'sold'`; legacy single-dict shape normalized on read; new
  "oversold" branch records a further distinct order on an already-sold-out
  item instead of dropping it, logged at WARNING with `oversold=True`.
  Matches the dispatched spec exactly — no unrequested scope.
- Readers (`reports.py`, `velocity.py`): both correctly normalize legacy
  dict shape and iterate the full list, one row/stat per order — verified
  this also fixes a latent under-count in velocity stats for multi-qty
  sales (previously only the first order counted).
- Tests: the old idempotency test asserted the buggy behavior itself
  ("second call: already sold -> False, no change") — branch correctly
  recognized this and split it into a true-idempotency test (same
  order_id twice) plus the actual regression test (different order_id
  must persist). Full offline suite: 2729 passed, 1 skipped, confirmed run
  against the worktree's own code copy.
- Live evidence: raw eBay capture cross-checked for order
  26-14894-40269's actual field values before backfill; post-backfill item
  JSON shown verbatim with both orders present; idempotency re-verified
  live (second backfill call returned False, no duplicate).
- Deviation (buyer field = eBay UserID, not shipping display name):
  reasonable, matches existing schema convention already used by the first
  recorded order, correctly flagged rather than silently done.
- Out-of-scope finding (draft_listing.quantity vs qty drift) correctly
  filed as a separate todo (#1606), not fixed inline.

## Trigger check (step 3)

None fired. No out-of-scope files, no invariant violation, no live/prod
write before stitch (the one production write — the backfill — was
explicit todo-#1604 scope: recovering the specific order this task exists
to fix, done via the fence, announced per invariant E9, one-off script
deleted after use), no manifest sanity failure.

## Outcome

Cleared for stitch. Not merged — per contract, stitch is a separate
explicit step.
