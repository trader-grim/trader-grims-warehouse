"""Tests for /api/ebay/category-context conditions (session 39, item tgw202605060201087
finding): this endpoint used to build the conditions list via the fabricated
_CONDITION_ID_MAP (fanning eBay's single "Used" conditionId 3000 into three invented
grades). It now goes through the same real per-category policy lookup used by the
initial page render.

All eBay/external calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import tgw.ebay.pricing as pricing_mod
import tgw.http_server as http_server


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


def test_conditions_come_from_real_policy_not_fabricated_map(tmp_path):
    http_server._cfg = _cfg(tmp_path)
    allowed = [
        {"condition_id": "1000", "condition_label": "New", "condition_enum": "NEW"},
        {"condition_id": "3000", "condition_label": "Used", "condition_enum": "USED_EXCELLENT"},
    ]
    with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=allowed), \
         patch("tgw.apis.ebay.specifics.get_aspects", return_value=[]):
        result = http_server.ebay_category_context("165806")

    enums = {c["enum"] for c in result["conditions"]}
    assert enums == {"NEW", "USED_EXCELLENT"}
    # must not contain the fabricated extra grades for the single "Used" bucket
    assert "USED_GOOD" not in enums
    assert "USED_ACCEPTABLE" not in enums


def test_conditions_empty_when_policy_lookup_fails(tmp_path):
    http_server._cfg = _cfg(tmp_path)
    with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category",
               side_effect=RuntimeError("boom")), \
         patch("tgw.apis.ebay.specifics.get_aspects", return_value=[]):
        result = http_server.ebay_category_context("165806")

    assert result["conditions"] == []


def test_aspects_error_distinguishes_lookup_failure_from_genuine_empty(tmp_path):
    """No real eBay category has zero specifics — an empty aspects list must carry
    aspects_error so the UI can say 'lookup failed', not 'no specifics'."""
    http_server._cfg = _cfg(tmp_path)
    with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=[]), \
         patch("tgw.apis.ebay.specifics.get_aspects",
               side_effect=RuntimeError("429 Client Error: Too Many Requests")):
        result = http_server.ebay_category_context("165806")

    assert result["aspects"] == []
    assert "429" in result["aspects_error"]


def test_no_aspects_error_when_lookup_succeeds(tmp_path):
    http_server._cfg = _cfg(tmp_path)
    with patch("tgw.apis.ebay.conditions.allowed_conditions_for_category", return_value=[]), \
         patch("tgw.apis.ebay.specifics.get_aspects",
               return_value=[{"name": "Country/Region of Manufacture", "required": True,
                              "mode": "FREE_TEXT", "allowed_values": []}]):
        result = http_server.ebay_category_context("165806")

    assert result["aspects_error"] is None
    assert len(result["aspects"]) == 1


def test_editor_script_exposes_category_aspect_max_length():
    """A taxonomy aspect limit must become an actual input maxlength hint."""
    script = http_server._CATEGORY_CONTEXT_IIFE
    assert "maxlength=" in script
    assert "maxHint" in script
