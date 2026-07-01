"""Tests for get_category_tree_id() disk-cache + fallback resilience (session 39,
item tgw202605060201087 investigation): the tree ID lookup was only cached in
memory per-process and had no fallback, so it stacked an extra live-call failure
point on top of every aspects/condition/search call whenever the Taxonomy API was
rate-limited — even though a marketplace's tree ID essentially never changes.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

from tgw.apis.ebay import taxonomy


def _reset():
    taxonomy._tree_id_cache = None


def _cfg(tmp_path):
    return {'catalog_root': tmp_path}


class TestGetCategoryTreeId:
    def setup_method(self):
        _reset()

    def test_live_call_result_is_cached_to_disk(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value={'categoryTreeId': '0'}) as mock_get:
            result = taxonomy.get_category_tree_id(cfg)
        assert result == '0'
        assert mock_get.call_count == 1
        assert (tmp_path / 'ebay-category-tree-id.json').exists()

    def test_fresh_process_uses_disk_cache_not_live_api(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value={'categoryTreeId': '0'}):
            taxonomy.get_category_tree_id(cfg)
        _reset()
        with patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            result = taxonomy.get_category_tree_id(cfg)
        assert result == '0'

    def test_falls_back_to_documented_default_when_live_call_fails_and_no_cache(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', side_effect=RuntimeError('429 Too Many Requests')):
            result = taxonomy.get_category_tree_id(cfg)
        assert result == taxonomy._EBAY_US_DEFAULT_TREE_ID

    def test_fallback_result_is_not_permanently_cached_to_disk(self, tmp_path):
        """A fallback guess shouldn't be written as if it were a confirmed value —
        so a later successful call can still self-correct if it were ever wrong."""
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', side_effect=RuntimeError('429')):
            taxonomy.get_category_tree_id(cfg)
        assert not (tmp_path / 'ebay-category-tree-id.json').exists()

    def test_no_catalog_root_still_works_via_live_call(self, tmp_path):
        with patch.object(taxonomy, 'ebay_get', return_value={'categoryTreeId': '0'}):
            result = taxonomy.get_category_tree_id({})
        assert result == '0'
