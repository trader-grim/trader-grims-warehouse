"""Tests for category validation via Taxonomy getCategorySuggestions (todo #93).

All eBay API calls are mocked — tests pass completely offline.

Covers:
  - _validate_category_suggestion() helper in ebay_draft
  - catalog-verify rule 'category_suggestion_mismatch' in _verify_item
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

_TAX_PATCH = 'tgw.apis.ebay.taxonomy.get_category_suggestions'


def _suggestions(*pairs):
    """Build getCategorySuggestions response list from (id, name) pairs."""
    return [
        {'category': {'categoryId': cid, 'categoryName': name}}
        for cid, name in pairs
    ]


# ---------------------------------------------------------------------------
# _validate_category_suggestion unit tests
# ---------------------------------------------------------------------------

class TestValidateCategorySuggestion:
    """Disabled 2026-07-02 (session 41): _validate_category_suggestion no longer makes
    a live getCategorySuggestions call (session-39 audit finding #3 — it duplicated
    ai_identify's category call on every drafted item and was a confirmed contributor
    to repeated Taxonomy API 429 exhaustion). It's now an unconditional stub that
    always returns 'unavailable' regardless of input or mocked API response — these
    tests confirm the taxonomy API is never touched. The agreed/mismatch comparison
    logic these tests used to cover was removed with the live call; re-add equivalent
    coverage if the helper is ever re-enabled (see docstring in ebay_draft.py)."""

    def _call(self, title, resolved_id, raw_suggestions, top_n=5):
        from tgw.workers.ebay_draft import _validate_category_suggestion
        with patch(_TAX_PATCH, return_value=raw_suggestions) as mocked:
            result = _validate_category_suggestion({}, title, resolved_id, top_n=top_n)
        return result, mocked

    def test_always_unavailable_and_never_calls_taxonomy_api(self):
        suggestions = _suggestions(('12345', 'Antique Pocket Watches'), ('99999', 'Other'))
        result, mocked = self._call('Silver Elgin pocket watch', '12345', suggestions)
        assert result['category_agreement'] == 'unavailable'
        assert result['category_suggestions'] == []
        mocked.assert_not_called()

    def test_unavailable_on_api_exception(self):
        from tgw.workers.ebay_draft import _validate_category_suggestion
        with patch(_TAX_PATCH, side_effect=ConnectionError('eBay unreachable')) as mocked:
            result = _validate_category_suggestion({}, 'title', '12345')
        assert result['category_agreement'] == 'unavailable'
        assert result['category_suggestions'] == []
        mocked.assert_not_called()


# ---------------------------------------------------------------------------
# catalog-verify rule: category_suggestion_mismatch
# ---------------------------------------------------------------------------

class TestCatalogVerifyMismatchRule:

    def _item_dir(self, tmp_path: Path, sku: str) -> Path:
        d = tmp_path / sku
        d.mkdir()
        (d / f"{sku}.jpg").write_bytes(b"\xff\xd8")   # minimal fake photo
        return d

    def _doc(self, sku, agreement, suggestions=None):
        return {
            "sku": sku,
            "title": "A vintage lens",
            "location": "A1",
            "draft_listing": {
                "category_id": "12345",
                "category_name": "Antique Watches",
                "category_agreement": agreement,
                "category_suggestions": suggestions or [],
            },
        }

    def test_mismatch_raises_warning(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "mismatch",
                        [{"category_id": "99999", "category_name": "Camera Lenses"}])
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        rules = [v["rule"] for v in viols]
        assert "category_suggestion_mismatch" in rules

    def test_mismatch_is_warning_severity(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "mismatch",
                        [{"category_id": "99999", "category_name": "Camera Lenses"}])
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        mismatch = next(v for v in viols if v["rule"] == "category_suggestion_mismatch")
        assert mismatch["severity"] == "warning"

    def test_agreed_does_not_raise_violation(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "agreed",
                        [{"category_id": "12345", "category_name": "Antique Watches"}])
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        assert "category_suggestion_mismatch" not in [v["rule"] for v in viols]

    def test_unavailable_does_not_raise_violation(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "unavailable")
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        assert "category_suggestion_mismatch" not in [v["rule"] for v in viols]

    def test_no_draft_listing_no_violation(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = {"sku": sku, "title": "A watch", "location": "A1"}
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        assert "category_suggestion_mismatch" not in [v["rule"] for v in viols]

    def test_mismatch_detail_includes_top_suggestion_name(self, tmp_path):
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "mismatch",
                        [{"category_id": "7777", "category_name": "Film Cameras"},
                         {"category_id": "8888", "category_name": "Other"}])
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        mismatch = next(v for v in viols if v["rule"] == "category_suggestion_mismatch")
        assert "Film Cameras" in mismatch["detail"]

    def test_mismatch_empty_suggestions_still_fires(self, tmp_path):
        """mismatch with no suggestions (API returned nothing) still produces a warning."""
        from tgw.api import _verify_item
        sku = "tgw001"
        doc = self._doc(sku, "mismatch")   # suggestions=[]
        viols = _verify_item(sku, self._item_dir(tmp_path, sku), doc)
        assert "category_suggestion_mismatch" in [v["rule"] for v in viols]


# ---------------------------------------------------------------------------
# Integration guard: category '99' fallback skips validation
# ---------------------------------------------------------------------------

class TestCategory99Guard:
    def test_guard_condition_present_in_source(self):
        """Confirm the worker skips _validate_category_suggestion for fallback cat."""
        import inspect

        import tgw.workers.ebay_draft as draft_mod
        src = inspect.getsource(draft_mod.EbayDraftWorker.handle)
        assert "category_id != '99'" in src or "!= '99'" in src
