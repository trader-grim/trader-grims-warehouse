# DONE — todo #1079: PP-CATPICK-001 Phase 1 — category_candidates backfill

`scripts/catpick_backfill_candidates.py` backfills `category_candidates`
(id + name + full ancestor path, root-first) onto every group in
`category-groups.json`, sourced entirely from the on-disk eBay category
tree cache (`ItemCatalog/ebay-category-tree.json`) — zero live API calls,
per the packet's own constraint. Full ancestor path (not just leaf name)
per the design's own rationale: a bare leaf like "Books" is ambiguous out
of context; the branch disambiguates without a separate hint field.

Applied live: all 25 groups now carry `category_candidates` matching their
`ebay_categories` count exactly. 2 stale category IDs found not in the tree
cache (`manuals: 34210`, `tools_hand: 43994`) — kept as bare-ID fallback
rather than silently dropped, flagged in the script's own output for
review (likely retired/merged eBay categories — worth a manual check next
time the tree cache refreshes).

4 unit tests for the pure ancestor-path logic (root-first ordering,
top-level case, unknown-ID fallback, cycle-defensiveness). Full suite:
1775 pass / 1 skipped / 0 fail / 0 errors (was 1771).

Phase 2 (the actual group-shortlist-first picker UI/logic) remains FROZEN
until R1 drains, per the plan.
