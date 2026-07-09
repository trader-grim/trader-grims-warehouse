"""Tests for best_condition() (audit#1143 #1252, code-review follow-up to
#1178): several _ITEM_CONDITION_PREFERRED lists are not rank-ascending (a
later fallback entry can be a BETTER real-world condition than the primary
entry), so using MIN across the list as the same-or-worse floor could hand
out a genuinely better condition than the item's true grade once the
primary/fallback entries aren't directly allowed in a category. Fixed to
MAX, mirroring best_condition_for_enum()'s #1178 fix.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

from tgw.apis.ebay.conditions import best_condition


class TestBestConditionNeverUpgrades:
    def test_refurbished_falls_back_to_manual_review_not_a_better_tier(self):
        # 'refurbished' -> ['2500' rank6, '3500' rank5, '2000' rank4] (conditions.py).
        # None of the preferred ids are allowed here; the only allowed
        # condition is '2010' (rank4, Excellent Refurbished) — genuinely
        # better than the item's true 'refurbished' grade (rank6). Must
        # return None (manual review), never silently upgrade to '2010'.
        policy = [("1000", "Brand New"), ("2010", "Excellent Refurbished")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition({}, "X", "refurbished")
        assert result is None

    def test_refurbished_accepts_a_same_or_worse_allowed_condition(self):
        # Same scenario, but the category also allows '2030' (rank6, Good
        # Refurbished) — genuinely same-or-worse than 'refurbished' (rank6).
        # Must land there, not on the better '2010' (rank4).
        policy = [("1000", "Brand New"), ("2010", "Excellent Refurbished"),
                  ("2030", "Good Refurbished")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition({}, "X", "refurbished")
        assert result == {"condition_id": "2030", "condition_label": "Good Refurbished",
                           "condition_enum": "GOOD_REFURBISHED"}

    def test_direct_hit_on_primary_preferred_id_still_works(self):
        # Sanity: the common case (primary preferred id directly allowed)
        # is unaffected by the MIN->MAX change.
        policy = [("1000", "Brand New"), ("2500", "Seller Refurbished")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition({}, "X", "refurbished")
        assert result == {"condition_id": "2500", "condition_label": "Seller Refurbished",
                           "condition_enum": "SELLER_REFURBISHED"}

    def test_used_good_still_resolves_normally(self):
        # 'used: good' -> ['5000' rank7, '3000' rank7] (both same rank) —
        # unaffected by MIN vs MAX, confirms no regression for the common,
        # rank-flat case.
        policy = [("1000", "Brand New"), ("3000", "Used")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition({}, "X", "used: good")
        assert result == {"condition_id": "3000", "condition_label": "Used",
                           "condition_enum": "USED_EXCELLENT"}

    def test_no_policy_for_category_returns_none(self):
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={}):
            result = best_condition({}, "X", "used")
        assert result is None
