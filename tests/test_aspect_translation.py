"""Tests for tgw.ebay.aspect_translation — the named Set A -> Set B
translation function (todo #1416, PP-LISTEDITOR-001), extracted from
workers/ebay_draft.py's former inline "Phase 2b" prefill logic.
"""

from unittest.mock import patch

from tgw.ebay.aspect_translation import translate_inventory_to_ebay_draft

_ASPECTS = [
    {"name": "Type", "required": True, "mode": "SELECTION_ONLY",
     "allowed_values": ["Brooch", "Necklace"]},
    {"name": "Brand", "required": False, "mode": "FREE_TEXT", "allowed_values": []},
    {"name": "Model", "required": True, "mode": "FREE_TEXT", "allowed_values": []},
    {"name": "Metal", "required": False, "mode": "SELECTION_ONLY",
     "allowed_values": ["Gold", "Silver"]},
]


def _get_aspects(cfg, category_id):
    return _ASPECTS


def test_translates_matching_keys_only():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Type": "Brooch", "SomeUnrelatedKey": "x"}, "12345", {})
    assert result == {"Type": "Brooch"}


def test_selection_only_value_not_in_allowed_values_skipped():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Type": "Lapel Pin"}, "12345", {})
    assert result == {}


def test_free_text_aspect_passes_through():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Brand": "Unbranded"}, "12345", {})
    assert result == {"Brand": "Unbranded"}


def test_identity_model_is_projected_when_the_category_requires_it():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Model": "Sensor 2"}, "12345", {})
    assert result == {"Model": "Sensor 2"}


def test_already_filled_keys_are_skipped():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Type": "Necklace"}, "12345", {}, already_filled={"Type": "Brooch"})
    assert result == {}


def test_empty_and_falsy_values_skipped():
    with patch("tgw.ebay.aspect_translation.get_aspects", _get_aspects):
        result = translate_inventory_to_ebay_draft(
            {"Brand": "", "Metal": None}, "12345", {})
    assert result == {}


def test_category_99_returns_empty_without_calling_get_aspects():
    with patch("tgw.ebay.aspect_translation.get_aspects") as m:
        result = translate_inventory_to_ebay_draft({"Type": "Brooch"}, "99", {})
    assert result == {}
    m.assert_not_called()
