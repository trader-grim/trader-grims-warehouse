# INPROGRESS #1178 — best_condition_for_enum MIN-rank upgrade bug

conditions.py:280 best_condition_for_enum() used MIN rank across an enum's
ambiguous source conditionIds (e.g. LIKE_NEW -> {2750 rank3, 2990 rank6}),
then tried best-ranked id first in the direct-hit loop. This can silently
upgrade an item's condition on category change (e.g. Pre-loved Refurbished
2990 -> Like New 2750), which is exactly the mis-grading regression this
function exists to prevent (see module docstring: "Core rule: NEVER upgrade
condition").

Fix: use MAX (worst-case) rank across ambiguous source ids as item_rank, and
restrict the direct-hit loop to only the worst-ranked source id(s) — any
better-ranked alias is never a safe direct match since we can't know which
real conditionId the item actually had.

Existing tests unaffected (they only exercise unambiguous enums: NEW->{1000},
USED_EXCELLENT->{3000}). Adding new tests for the ambiguous LIKE_NEW case.

## Resolution

Fixed src/tgw/apis/ebay/conditions.py best_condition_for_enum():
- item_rank now MAX (worst-case) across ambiguous source conditionIds, was MIN
- direct-hit loop restricted to only the worst-ranked source id(s)

Added 2 tests to tests/test_condition_remap.py covering the ambiguous
LIKE_NEW enum case (2750 rank3 / 2990 rank6):
- never upgrades to the better-ranked alias when both are allowed
- falls back to None (manual review) when only the better alias is allowed,
  rather than upgrading

Evidence: `python -m pytest -q tests/test_condition_remap.py
tests/test_category_context_conditions.py tests/test_condition_options.py`
→ 20 passed. Full suite has 9 pre-existing unrelated failures (model_routing,
quota, invariants_pricing) confirmed present before this change via git stash
— out of scope for #1178.
