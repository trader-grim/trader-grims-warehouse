"""Tests for the eBay Motors category tree cache (todo #1255,
PP-EBAY-MOTORS-001) — mirrors tests/test_category_tree.py's pattern for the
EBAY_US tree, but for the genuinely separate Motors tree (tree ID 100,
confirmed live 2026-07-09, todo #1254: a real Motors category 404s against
tree 0 and only resolves under tree 100).

Replaces the per-category live-call stopgap sync.py used from todo #1254
with a proper local disk+memory cache, same never-auto-expires contract as
the EBAY_US tree cache.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from tgw.apis.ebay import taxonomy

# A tiny fake Motors tree: Parts & Accessories(1) -> Brakes(11, leaf)
#                                                  -> Lighting(12, leaf)
_FAKE_MOTORS_TREE = {
    'rootCategoryNode': {
        'category': {'categoryId': '0', 'categoryName': 'Root'},
        'childCategoryTreeNodes': [
            {
                'category': {'categoryId': '1', 'categoryName': 'Parts & Accessories'},
                'childCategoryTreeNodes': [
                    {
                        'category': {'categoryId': '11', 'categoryName': 'Brakes'},
                        'leafCategoryTreeNode': True,
                    },
                    {
                        'category': {'categoryId': '12', 'categoryName': 'Lighting'},
                        'leafCategoryTreeNode': True,
                    },
                ],
            },
        ],
    },
}


def _reset_caches():
    taxonomy._tree_index_cache = None
    taxonomy._tree_roots_cache = None
    taxonomy._motors_tree_index_cache = None
    taxonomy._motors_tree_roots_cache = None


def _cfg(tmp_path):
    return {'catalog_root': tmp_path, 'ebay_token_path': tmp_path / 'unused.json'}


class TestMotorsTreeIndexBuild:
    def setup_method(self):
        _reset_caches()

    def test_fetches_from_tree_100_and_caches_to_disk(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE) as mock_get:
            index = taxonomy._ensure_motors_tree_index(cfg)
        assert set(index) == {'1', '11', '12'}
        assert mock_get.call_count == 1
        assert mock_get.call_args[0][1] == '/commerce/taxonomy/v1/category_tree/100'
        assert (tmp_path / 'ebay-motors-category-tree.json').exists()

    def test_second_call_uses_disk_cache_not_live_api(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE):
            taxonomy._ensure_motors_tree_index(cfg)
        _reset_caches()  # simulate a fresh process
        with patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_motors_tree_index(cfg)
        assert index['11']['name'] == 'Brakes'

    def test_arbitrarily_old_cache_is_still_used(self, tmp_path):
        # Same never-auto-expire contract as the EBAY_US tree cache.
        cache_path = tmp_path / 'ebay-motors-category-tree.json'
        cache_path.write_text(json.dumps({'_cached_at': 0, 'tree': _FAKE_MOTORS_TREE}))
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_motors_tree_index(cfg)
        assert index['11']['name'] == 'Brakes'


class TestRefreshMotorsCategoryTreeCache:
    def setup_method(self):
        _reset_caches()

    def test_refresh_forces_live_fetch_and_overwrites_cache(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE):
            taxonomy._ensure_motors_tree_index(cfg)  # populate stale cache

        _reset_caches()
        updated_tree = {
            'rootCategoryNode': {
                'category': {'categoryId': '0', 'categoryName': 'Root'},
                'childCategoryTreeNodes': [
                    {'category': {'categoryId': '99', 'categoryName': 'New Motors Category'},
                     'leafCategoryTreeNode': True},
                ],
            },
        }
        with patch.object(taxonomy, 'ebay_get', return_value=updated_tree) as mock_get:
            count = taxonomy.refresh_motors_category_tree_cache(cfg)

        assert mock_get.call_count == 1
        assert count == 1
        _reset_caches()
        with patch.object(taxonomy, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            index = taxonomy._ensure_motors_tree_index(cfg)
        assert set(index) == {'99'}


class TestIsMotorsCategory:
    def setup_method(self):
        _reset_caches()

    def test_true_for_a_category_in_the_motors_tree(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE):
            assert taxonomy.is_motors_category(cfg, '11') is True

    def test_false_for_a_category_not_in_the_motors_tree(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE):
            assert taxonomy.is_motors_category(cfg, '99999') is False

    def test_empty_category_id_is_false_without_a_call(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get',
                          side_effect=AssertionError('should not call ebay_get for an empty category_id')):
            assert taxonomy.is_motors_category(cfg, '') is False

    def test_fetch_failure_fails_closed_to_false(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', side_effect=Exception('400 Bad Request')):
            assert taxonomy.is_motors_category(cfg, '11') is False

    def test_synthetic_root_id_is_never_a_real_category_match(self, tmp_path):
        # Code-review follow-up: '0' is the one id present in BOTH trees'
        # raw data (each tree's own synthetic rootCategoryNode) — verified
        # live against production data to have zero real overlap otherwise.
        # _build_index() must keep excluding the root from the indexed set
        # so '0' can never look like a real Motors (or EBAY_US) category.
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE):
            assert taxonomy.is_motors_category(cfg, '0') is False

    def test_second_lookup_uses_cache_not_a_second_live_fetch(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(taxonomy, 'ebay_get', return_value=_FAKE_MOTORS_TREE) as mock_get:
            taxonomy.is_motors_category(cfg, '11')
            taxonomy.is_motors_category(cfg, '12')
            taxonomy.is_motors_category(cfg, '99999')
        assert mock_get.call_count == 1  # one tree fetch serves every category_id check


class TestSnapshotOnlyMotorsOperatorTaxonomy:
    def setup_method(self):
        _reset_caches()

    def test_resolve_search_and_browse_use_motors_snapshot_without_provider(
        self, tmp_path,
    ):
        (tmp_path / 'ebay-motors-category-tree.json').write_text(
            json.dumps({'_cached_at': 0, 'tree': _FAKE_MOTORS_TREE}),
            encoding='utf-8',
        )
        cfg = _cfg(tmp_path)
        with patch.object(
            taxonomy, 'ebay_get',
            side_effect=AssertionError('must not call eBay'),
        ):
            node = taxonomy.get_cached_category_node(cfg, '11')
            results = taxonomy.search_categories_cached(cfg, 'brakes')
            roots = taxonomy.get_cached_category_children(cfg, None)

        assert node == {
            'id': '11',
            'name': 'Brakes',
            'path': 'Parts & Accessories > Brakes',
            'leaf': True,
            'marketplace_id': 'EBAY_MOTORS_US',
            'source': 'taxonomy-snapshot',
        }
        assert results == [node]
        assert roots == [
            {
                'id': '1',
                'name': 'Parts & Accessories',
                'path': 'Parts & Accessories',
                'leaf': False,
                'marketplace_id': 'EBAY_MOTORS_US',
                'source': 'taxonomy-snapshot',
            }
        ]
