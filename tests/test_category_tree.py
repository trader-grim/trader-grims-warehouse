"""Tests for the local category-tree cache used by the web UI category picker
(todo #1078, PP-LISTEDITOR-001) — search / raw-ID lookup / tree browse, all
served from a cached tree so the item-detail page never hits the live,
quota-limited eBay Taxonomy get_category_suggestions endpoint per keystroke.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

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
    taxonomy._motors_tree_index_cache = None
    taxonomy._motors_tree_roots_cache = None


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


class TestTreeCacheNeverAutoExpires:
    """Session 41: Dave's design (stated twice) is that the tree cache has no
    time-based expiry — eBay announces taxonomy changes, they're rare, so there is
    no reason to burn a live re-fetch on a schedule. A 30-day auto-expiry shipped
    instead and silently caused 3+ days of perpetual quota exhaustion in production
    because the disk cache never actually got a chance to persist past that window
    under real usage. These tests lock in "no auto-expiry" as a regression guard."""

    def setup_method(self):
        _reset_caches()

    def test_arbitrarily_old_cache_is_still_used(self, tmp_path):
        import json
        cache_path = tmp_path / 'ebay-category-tree.json'
        # 10 years old — must still be honored; only manual refresh invalidates it.
        cache_path.write_text(json.dumps({'_cached_at': 0, 'tree': _FAKE_TREE}))
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_tree_index(cfg)
        assert index['111']['name'] == 'US Coins'


class TestRefreshCategoryTreeCache:
    def setup_method(self):
        _reset_caches()

    def test_refresh_forces_live_fetch_and_overwrites_cache(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=_FAKE_TREE):
            taxonomy._ensure_tree_index(cfg)  # populate stale cache

        _reset_caches()
        updated_tree = {
            'rootCategoryNode': {
                'category': {'categoryId': '0', 'categoryName': 'Root'},
                'childCategoryTreeNodes': [
                    {'category': {'categoryId': '9', 'categoryName': 'New Category'},
                     'leafCategoryTreeNode': True},
                ],
            },
        }
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', return_value=updated_tree) as mock_get:
            count = taxonomy.refresh_category_tree_cache(cfg)

        assert mock_get.call_count == 1
        assert count == 1
        _reset_caches()
        with patch.object(taxonomy, 'get_category_tree_id', return_value='0'), \
             patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_tree_index(cfg)
        assert set(index) == {'9'}


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
        assert results[0]['path'] == 'Collectibles > Coins > US Coins'

    def test_search_accepts_numeric_category_id(self, tmp_path):
        cfg = self._warm(tmp_path)
        results = taxonomy.search_categories_local(cfg, '111')
        assert results[0] == {
            'id': '111',
            'name': 'US Coins',
            'path': 'Collectibles > Coins > US Coins',
        }

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
        assert {c['path'] for c in children} == {
            'Collectibles > Coins',
            'Collectibles > Stamps',
        }

    def test_children_of_leaf_is_empty(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.get_category_children(cfg, '2') == []

    def test_unknown_parent_is_empty(self, tmp_path):
        cfg = self._warm(tmp_path)
        assert taxonomy.get_category_children(cfg, '999999') == []


class TestSnapshotOnlyOperatorTaxonomy:
    def setup_method(self):
        _reset_caches()

    def test_disk_snapshot_resolves_without_tree_id_or_provider(self, tmp_path):
        (tmp_path / 'ebay-category-tree.json').write_text(
            json.dumps({'_cached_at': 0, 'tree': _FAKE_TREE}),
            encoding='utf-8',
        )
        cfg = _cfg(tmp_path)
        with patch.object(
            taxonomy, 'get_category_tree_id',
            side_effect=AssertionError('must not resolve a live tree ID'),
        ), patch.object(
            taxonomy, 'ebay_get',
            side_effect=AssertionError('must not call eBay'),
        ):
            node = taxonomy.get_cached_category_node(cfg, '111')
            results = taxonomy.search_categories_cached(cfg, '111')
            children = taxonomy.get_cached_category_children(cfg, '11')

        assert node == {
            'id': '111',
            'name': 'US Coins',
            'path': 'Collectibles > Coins > US Coins',
            'leaf': True,
            'marketplace_id': 'EBAY_US',
            'source': 'taxonomy-snapshot',
        }
        assert results == [node]
        assert children == [node]

    def test_missing_snapshot_never_falls_through_to_provider(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(
            taxonomy, 'ebay_get',
            side_effect=AssertionError('must not call eBay'),
        ):
            assert taxonomy.get_cached_category_node(cfg, '111') is None
            with pytest.raises(taxonomy.CategoryTaxonomySnapshotUnavailable):
                taxonomy.search_categories_cached(cfg, 'coins')
            with pytest.raises(taxonomy.CategoryTaxonomySnapshotUnavailable):
                taxonomy.get_cached_category_children(cfg, None)

    def test_corrupt_snapshot_never_falls_through_to_provider(self, tmp_path):
        (tmp_path / 'ebay-category-tree.json').write_text('{broken', encoding='utf-8')
        cfg = _cfg(tmp_path)
        with patch.object(
            taxonomy, 'ebay_get',
            side_effect=AssertionError('must not call eBay'),
        ):
            assert taxonomy.get_cached_category_node(cfg, '111') is None
