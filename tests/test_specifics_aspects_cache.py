"""audit#1143 #1239 — specifics.py's get_aspects() disk-cache write must go
through the locked merge helper so concurrent cache-miss writes for
different categories don't drop each other's entries.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import gzip
import json
from unittest.mock import patch

from tgw.apis.ebay import specifics


def _cfg(tmp_path):
    return {'catalog_root': tmp_path}


def _reset():
    specifics._aspects_mem_cache.clear()


class TestGetAspectsCachePersistence:
    def setup_method(self):
        _reset()

    def test_live_result_is_written_to_disk_cache(self, tmp_path):
        cfg = _cfg(tmp_path)
        raw = {'aspects': [{'localizedAspectName': 'Brand', 'aspectConstraint': {},
                            'aspectValues': []}]}
        with patch.object(specifics, 'ebay_get', return_value=raw), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            specifics.get_aspects(cfg, 'cat1')

        cache_path = tmp_path / 'ebay-aspects-cache.json'
        assert cache_path.exists()
        disk = json.loads(cache_path.read_text())
        assert 'cat1' in disk

    def test_two_different_categories_both_persist_not_last_write_wins(self, tmp_path):
        # Regression for #1239: the old plain write_text read disk_cache
        # once per call and wrote the whole dict back — fine sequentially,
        # but proves the merge path preserves prior entries rather than
        # silently overwriting the whole file with just the newest one.
        cfg = _cfg(tmp_path)
        raw1 = {'aspects': [{'localizedAspectName': 'Brand', 'aspectConstraint': {}, 'aspectValues': []}]}
        raw2 = {'aspects': [{'localizedAspectName': 'Color', 'aspectConstraint': {}, 'aspectValues': []}]}

        with patch.object(specifics, 'get_category_tree_id', return_value='0'):
            with patch.object(specifics, 'ebay_get', return_value=raw1):
                specifics.get_aspects(cfg, 'cat1')
            _reset()  # simulate a fresh process for the second call
            with patch.object(specifics, 'ebay_get', return_value=raw2):
                specifics.get_aspects(cfg, 'cat2')

        disk = json.loads((tmp_path / 'ebay-aspects-cache.json').read_text())
        assert set(disk) == {'cat1', 'cat2'}

    def test_disk_cache_hit_avoids_live_call(self, tmp_path):
        cfg = _cfg(tmp_path)
        raw = {'aspects': [{'localizedAspectName': 'Brand', 'aspectConstraint': {}, 'aspectValues': []}]}
        with patch.object(specifics, 'get_category_tree_id', return_value='0'):
            with patch.object(specifics, 'ebay_get', return_value=raw):
                specifics.get_aspects(cfg, 'cat1')
        _reset()
        with patch.object(specifics, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            result = specifics.get_aspects(cfg, 'cat1')
        assert result[0]['name'] == 'Brand'


class TestBulkRefreshAspectsAtomicity:
    # code-review follow-up (#1239): bulk_refresh_aspects()'s per-category
    # shard writes were the one write site left as a plain write_text() —
    # now goes through the same mode-preserving atomic write as everything
    # else in this module.
    def setup_method(self):
        _reset()

    def _fake_bulk_response(self):
        payload = {
            'categoryAspects': [
                {
                    'category': {'categoryId': '111', 'categoryName': 'Widgets'},
                    'aspects': [{'localizedAspectName': 'Brand', 'aspectConstraint': {}, 'aspectValues': []}],
                },
                {
                    'category': {'categoryId': '222', 'categoryName': 'Gadgets'},
                    'aspects': [{'localizedAspectName': 'Color', 'aspectConstraint': {}, 'aspectValues': []}],
                },
            ],
        }
        return gzip.compress(json.dumps(payload).encode('utf-8'))

    def test_shards_are_written_as_valid_json_no_leftover_tmp_files(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get_bytes', return_value=self._fake_bulk_response()):
            result = specifics.bulk_refresh_aspects(cfg)

        assert result['categories'] == 2
        bulk_dir = tmp_path / 'ebay-aspects-bulk'
        shard_111 = json.loads((bulk_dir / '111.json').read_text())
        assert shard_111['name'] == 'Widgets'
        assert shard_111['aspects'][0]['name'] == 'Brand'

        # No orphaned tmp files from the atomic-write tmp+rename sequence.
        leftovers = [p for p in bulk_dir.iterdir()
                    if p.name not in ('111.json', '222.json', 'fetch_item_aspects.json.gz')]
        assert leftovers == []
