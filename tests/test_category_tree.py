"""Tests for the local category-tree cache used by the web UI category picker
(todo #1078, PP-LISTEDITOR-001) — search / raw-ID lookup / tree browse, all
served from a cached tree so the item-detail page never hits the live,
quota-limited eBay Taxonomy get_category_suggestions endpoint per keystroke.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

from tgw.apis.ebay import taxonomy

# A tiny fake tree: Collectibles(1) -> Coins(11) -> US Coins(111, leaf)
#                                    -> Stamps(12, leaf)
#                 -> Antiques(2, leaf, no children)
_FAKE_TREE = {
    'rootCategoryNode': {
        'category': {'categoryId': '0', 'categoryName': 'Root'},
        'childCategoryTreeNodes': [
            {
                'category': {'categoryId': '1', 'categoryName': 'Collectibles'},
                'childCategoryTreeNodes': [
                    {
                        'category': {'categoryId': '11', 'categoryName': 'Coins'},
                        'childCategoryTreeNodes': [
                            {
                                'category': {'categoryId': '111', 'categoryName': 'US Coins'},
                                'leafCategoryTreeNode': True,
                            },
                        ],
                    },
                    {
                        'category': {'categoryId': '12', 'categoryName': 'Stamps'},
                        'leafCategoryTreeNode': True,
                    },
                ],
            },
            {
                'category': {'categoryId': '2', 'categoryName': 'Antiques'},
                'leafCategoryTreeNode': True,
            },
        ],
    },
}


def _reset_caches():
    taxonomy._tree_index_cache = None
    taxonomy._tree_roots_cache = None
    taxonomy._tree_id_cache = None


def _cfg(tmp_path):
    return {'catalog_root': tmp_path, 'ebay_token_path': tmp_path / 'unused.json'}


class TestTreeIndexBuild:
    def setup_method(self):
        _reset_caches()

    def test_fetches_and_caches_to_disk(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE) as mock_get:
            index = taxonomy._ensure_tree_index(cfg)
        assert set(index) == {'1', '11', '111', '12', '2'}
        assert mock_get.call_count == 1
        assert (tmp_path / 'ebay-category-tree.json').exists()

    def test_second_call_uses_disk_cache_not_live_api(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            taxonomy._ensure_tree_index(cfg)
        _reset_caches()  # simulate a fresh process
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_tree_index(cfg)
        assert index['111']['name'] == 'US Coins'

    def test_leaf_and_branch_flags(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            index = taxonomy._ensure_tree_index(cfg)
        assert index['1']['leaf'] is False
        assert index['111']['leaf'] is True
        assert index['2']['leaf'] is True


class TestSearchCategoriesLocal:
    def setup_method(self):
        _reset_caches()

    def _warm(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            taxonomy._ensure_tree_index(cfg)
        return cfg

    def test_search_matches_leaf_only(self, tmp_path):
        cfg = self._warm(tmp_path)
        results = taxonomy.search_categories_local(cfg, 'coins')
        ids = {r['id'] for r in results}
        assert '111' in ids  # leaf match
        assert '11' not in ids  # branch node excluded even though name contains query

    def test_search_includes_breadcrumb_path(self, tmp_path):
        cfg = self._warm(tmp_path)
        results = taxonomy.search_categories_local(cfg, 'US Coins')
        assert results[0]['path'] == 'Collectibles > Coins'

    def test_search_empty_query_returns_nothing(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.search_categories_local(cfg, '') == []

    def test_search_no_live_api_call(self, tmp_path):
        cfg = self._warm(tmp_path)
        with patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            taxonomy.search_categories_local(cfg, 'stamps')


class TestGetCategoryNode:
    def setup_method(self):
        _reset_caches()

    def _warm(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            taxonomy._ensure_tree_index(cfg)
        return cfg

    def test_resolves_valid_id_with_full_breadcrumb(self, tmp_path):
        cfg = self._warm(tmp_path)
        node = taxonomy.get_category_node(cfg, '111')
        assert node['name'] == 'US Coins'
        assert node['path'] == 'Collectibles > Coins > US Coins'
        assert node['leaf'] is True

    def test_unknown_id_returns_none(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.get_category_node(cfg, '999999') is None

    def test_branch_id_reports_not_leaf(self, tmp_path):
        cfg = self._warm(tmp_path)
        node = taxonomy.get_category_node(cfg, '11')
        assert node['leaf'] is False


class TestGetCategoryChildren:
    def setup_method(self):
        _reset_caches()

    def _warm(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            taxonomy._ensure_tree_index(cfg)
        return cfg

    def test_root_children_when_parent_none(self, tmp_path):
        cfg = self._warm(tmp_path)
        children = taxonomy.get_category_children(cfg, None)
        names = {c['name'] for c in children}
        assert names == {'Collectibles', 'Antiques'}

    def test_children_of_branch(self, tmp_path):
        cfg = self._warm(tmp_path)
        children = taxonomy.get_category_children(cfg, '1')
        names = {c['name'] for c in children}
        assert names == {'Coins', 'Stamps'}

    def test_children_of_leaf_is_empty(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.get_category_children(cfg, '2') == []

    def test_unknown_parent_is_empty(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.get_category_children(cfg, '999999') == []
