"""Tests for _build_condition_options() (session 39, item tgw202605060201087 finding).

The condition dropdown used to always render a fixed 10-option generic enum list,
plus a fabricated per-conditionId table (_CONDITION_ID_MAP) that invented three
separate grades (USED_EXCELLENT/GOOD/ACCEPTABLE) under eBay's single "Used"
conditionId (3000) — none of which eBay's Seller Hub actually offers for
categories whose real policy only allows that one bucket. It's now sourced from
the real per-category eBay Metadata API condition policy.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

from tgw.http_server import _build_condition_options


def _fake_allowed(pairs):
    """pairs: list of (condition_id, condition_label, condition_enum)."""
    return [
        {"condition_id": cid, "condition_label": label, "condition_enum": enum}
        for cid, label, enum in pairs
    ]


class TestBuildConditionOptions:
    def test_uses_real_category_policy_not_generic_list(self):
        # category 165806 ("California") real policy: only New + Used allowed
        allowed = _fake_allowed([("1000", "New", "NEW"), ("3000", "Used", "USED_EXCELLENT")])
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed):
            html, invalid = _build_condition_options("USED_EXCELLENT", "165806")
        assert "USED_EXCELLENT" in html
        assert ">Used<" in html
        # must NOT fabricate grades eBay never offered for this category
        assert "USED_GOOD" not in html
        assert "USED_ACCEPTABLE" not in html
        assert invalid is False

    def test_falls_back_to_generic_list_when_no_category_policy(self):
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=[]):
            html, invalid = _build_condition_options("USED_GOOD", "999999999")
        assert "USED_GOOD" in html
        assert "USED_ACCEPTABLE" in html  # generic fallback list is present
        assert invalid is False

    def test_no_category_id_uses_generic_fallback(self):
        html, invalid = _build_condition_options("NEW", "")
        assert "NEW" in html
        assert "FOR_PARTS_OR_NOT_WORKING" in html
        assert invalid is False

    def test_stale_invalid_enum_surfaced_not_silently_dropped(self):
        """A previously-saved enum that isn't valid for this category (e.g. set
        before this fix, or category changed since) must still show up — flagged
        — rather than vanish, so the operator notices and corrects it. The
        returned `invalid` flag (PP-CONDITION-ENUM-001 / todo #1562) is what
        the caller uses to redden the <select> border on initial render."""
        allowed = _fake_allowed([("1000", "New", "NEW"), ("3000", "Used", "USED_EXCELLENT")])
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed):
            html, invalid = _build_condition_options("USED_GOOD", "165806")
        assert 'value="USED_GOOD" selected' in html
        assert "not valid for this category" in html
        assert invalid is True

    def test_policy_lookup_failure_does_not_crash(self):
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category",
                   side_effect=RuntimeError("boom")):
            html, invalid = _build_condition_options("NEW", "165806")
        assert "NEW" in html  # falls back to generic list
        assert invalid is False

    def test_duplicate_enums_across_condition_ids_deduped(self):
        # Two different legacy conditionIds mapping to the same enum shouldn't double-render
        allowed = _fake_allowed([("3000", "Used", "USED_EXCELLENT"), ("3010", "Refurb", "USED_EXCELLENT")])
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed):
            html, invalid = _build_condition_options("USED_EXCELLENT", "165806")
        assert html.count('value="USED_EXCELLENT"') == 1
        assert invalid is False

    def test_garbage_human_label_flagged_invalid(self):
        """PP-CONDITION-ENUM-001 / todo #1562 — the exact live-incident shape:
        condition_enum corrupted to a raw human label ("Very Good") instead
        of a real Inventory API enum."""
        allowed = _fake_allowed([("4000", "Used - Very Good", "USED_VERY_GOOD")])
        with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed):
            html, invalid = _build_condition_options("Very Good", "165806")
        assert invalid is True
        assert 'value="Very Good" selected' in html
