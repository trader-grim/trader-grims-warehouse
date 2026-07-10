"""Tests for best_condition_for_enum() wiring into /api/ebay/category-context
(session 39, follow-up to item tgw202605060201087): this function correctly
implements "never upgrade condition on category change" but was defined and never
called anywhere — so switching category live in the UI left a stale/invalid enum
in place instead of remapping to the nearest same-or-worse real option.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import tgw.ebay.pricing as pricing_mod
import tgw.http_server as http_server
from tgw.apis.ebay.conditions import best_condition_for_enum

# Real per-category policy shapes confirmed from the live cache during investigation:
# generic ("California" 165806-style) categories carry only New/Used (1000/3000);
# books/media categories carry the finer New/Like New/Very Good/Good/Acceptable set
# (1000/2750/4000/5000/6000) and do NOT include 3000 at all.
_GENERIC_POLICY = [("1000", "New"), ("3000", "Used")]
_BOOKS_POLICY = [
    ("1000", "Brand New"), ("2750", "Like New"),
    ("4000", "Very Good"), ("5000", "Good"), ("6000", "Acceptable"),
]


def setup_function(_):
    pricing_mod._groups_cache = None
    pricing_mod._groups_reverse = None


def _cfg(tmp_path):
    groups_path = tmp_path / "category-groups.json"
    groups_path.write_text(json.dumps({"groups": {}}), encoding="utf-8")
    return {
        "category_groups_path": str(groups_path),
        "catalog_root": tmp_path,
        "fulfillment_policy_id": "",
    }


class TestBestConditionForEnumNeverUpgrades:
    """Direct unit tests for the remap function itself."""

    def test_generic_used_switching_to_books_maps_to_good_not_like_new(self):
        # Dave's exact reported scenario: item was "Used" (enum USED_EXCELLENT,
        # legacy 3000), category changes to a books-type category that has no 3000
        # at all. Must land on "Good" (5000, same-or-worse), never "Like New" (2750,
        # an upgrade) or "Very Good" (4000, also an upgrade).
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"165806": _GENERIC_POLICY, "1105": _BOOKS_POLICY}):
            result = best_condition_for_enum({}, "1105", "USED_EXCELLENT")
        assert result == {"condition_id": "5000", "condition_label": "Good", "condition_enum": "USED_GOOD"}

    def test_direct_hit_when_enum_still_valid(self):
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"165806": _GENERIC_POLICY}):
            result = best_condition_for_enum({}, "165806", "USED_EXCELLENT")
        assert result == {"condition_id": "3000", "condition_label": "Used", "condition_enum": "USED_EXCELLENT"}

    def test_new_condition_preserved_across_category_change(self):
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"1105": _BOOKS_POLICY}):
            result = best_condition_for_enum({}, "1105", "NEW")
        assert result["condition_enum"] == "NEW"

    def test_no_valid_remap_when_all_allowed_are_better(self):
        # item is "For Parts", new category's worst option is "Very Good" — cannot
        # honestly remap upward, must return None (needs manual review).
        policy = [("1000", "Brand New"), ("2750", "Like New")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition_for_enum({}, "X", "FOR_PARTS_OR_NOT_WORKING")
        assert result is None

    def test_unknown_source_enum_returns_none(self):
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": _BOOKS_POLICY}):
            result = best_condition_for_enum({}, "X", "NOT_A_REAL_ENUM")
        assert result is None

    def test_ambiguous_enum_never_upgrades_to_better_alias(self):
        # LIKE_NEW is ambiguous: '2750' (rank 3, "Like New") and '2990'
        # (rank 6, "Pre-loved Refurbished") both map to it. If the item was
        # actually graded Pre-loved Refurbished, remapping must never land
        # on the better-ranked '2750' alias just because the new category
        # happens to allow both — audit#1143 / todo #1178.
        policy = [("1000", "Brand New"), ("2750", "Like New"), ("2990", "Pre-loved Refurbished")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition_for_enum({}, "X", "LIKE_NEW")
        assert result == {"condition_id": "2990", "condition_label": "Pre-loved Refurbished",
                           "condition_enum": "LIKE_NEW"}

    def test_ambiguous_enum_falls_back_to_manual_review_not_upgrade(self):
        # Same ambiguous LIKE_NEW enum, but the new category only allows the
        # better-ranked '2750' alias (no same-or-worse option exists). Must
        # return None for manual review rather than silently upgrading.
        policy = [("1000", "Brand New"), ("2750", "Like New")]
        with patch("tgw.apis.ebay.conditions._get_policies", return_value={"X": policy}):
            result = best_condition_for_enum({}, "X", "LIKE_NEW")
        assert result is None


class TestCategoryContextConditionRemap:
    """Integration through the /api/ebay/category-context endpoint."""

    def test_remap_returned_when_current_condition_invalid_for_new_category(self, tmp_path):
        http_server._cfg = _cfg(tmp_path)
        allowed = [
            {"condition_id": cid, "condition_label": lbl,
             "condition_enum": {"1000": "NEW", "2750": "LIKE_NEW", "4000": "USED_VERY_GOOD",
                                "5000": "USED_GOOD", "6000": "USED_ACCEPTABLE"}[cid]}
            for cid, lbl in _BOOKS_POLICY
        ]
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed), \
             patch("tgw.apis.ebay.conditions.best_condition_for_enum",
                   return_value={"condition_id": "5000", "condition_label": "Good", "condition_enum": "USED_GOOD"}), \
             patch("tgw.apis.ebay.specifics.get_aspects", return_value=[]):
            result = http_server.ebay_category_context("1105", current_condition="USED_EXCELLENT")

        assert result["condition_remap"] == {"enum": "USED_GOOD", "label": "Good"}

    def test_no_remap_when_current_condition_still_valid(self, tmp_path):
        http_server._cfg = _cfg(tmp_path)
        allowed = [{"condition_id": "3000", "condition_label": "Used", "condition_enum": "USED_EXCELLENT"}]
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed), \
             patch("tgw.apis.ebay.specifics.get_aspects", return_value=[]):
            result = http_server.ebay_category_context("165806", current_condition="USED_EXCELLENT")

        assert result["condition_remap"] is None

    def test_no_current_condition_param_skips_remap_lookup(self, tmp_path):
        http_server._cfg = _cfg(tmp_path)
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=[]), \
             patch("tgw.apis.ebay.specifics.get_aspects", return_value=[]):
            result = http_server.ebay_category_context("165806")

        assert result["condition_remap"] is None
