"""Tests for the per-category aspects disk+memory cache (todo #1078 follow-on).

get_aspects() used to hit eBay's Taxonomy get_item_aspects_for_category live on
every item-detail page view — a major contributor to Taxonomy API quota
exhaustion (billed per-App-ID, default 5,000 calls/day). Aspects for a category
are stable for weeks, so results are now cached to disk per category_id.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

from tgw.apis.ebay import specifics

_RAW_ASPECTS = {
    'aspects': [
        {
            'localizedAspectName': 'Brand',
            'aspectConstraint': {'aspectRequired': True, 'aspectMode': 'FREE_TEXT'},
            'aspectValues': [],
        },
        {
            'localizedAspectName': 'Color',
            'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'SELECTION_ONLY'},
            'aspectValues': [{'localizedValue': 'Red'}, {'localizedValue': 'Blue'}],
        },
        {
            # todo #1711: MPN used to be in _SKIP_ASPECTS and get filtered out
            # here — now a real aspect like any other, must survive structuring.
            'localizedAspectName': 'MPN',
            'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
            'aspectValues': [],
        },
    ],
}


def _reset_cache():
    specifics._aspects_mem_cache.clear()


def _cfg(tmp_path):
    return {'catalog_root': tmp_path, 'ebay_token_path': tmp_path / 'unused.json'}


class TestGetAspectsCaching:
    def setup_method(self):
        _reset_cache()

    def test_structures_result_with_no_names_filtered(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS):
            result = specifics.get_aspects(cfg, '12345')
        names = {a['name'] for a in result}
        assert names == {'Brand', 'Color', 'MPN'}
        color = next(a for a in result if a['name'] == 'Color')
        assert color['allowed_values'] == ['Red', 'Blue']

    def test_model_and_related_aspects_reach_editor_not_globally_skipped(self, tmp_path):
        """Regression, todo #1711 (item tgw202605031215465, eBay category 20673
        'Food Processors'; model EV-11PC9 / model_number 'U IB-8063'): the
        listing editor's Item Specifics form is built entirely from
        get_aspects()'s return value (via /api/ebay/category-context and
        /api/ebay/aspects) -- it has no other source for which standard
        fields exist to edit. _SKIP_ASPECTS used to drop MPN, Model, Unit
        Quantity, and Unit Type before they ever reached that response, so
        the editor had no Model field at all, for any category that defines
        one as a real item specific.

        These four were skipped on the theory that "operator/product-lookup
        handles these" elsewhere -- but the one mechanism that was supposed
        to do that (workers/ebay_draft.py's _PL_ASPECT_MAP, which maps
        product_lookup['mpn'] onto the 'MPN'/'Model' aspects) can only fill
        an aspect name that's actually present in `aspects` in the first
        place, so it silently never fired for these four names either. There
        is no other first-class editor field that is both rendered in the
        eBay Item Specifics form AND actually populated back into
        draft_listing.item_specifics for any of them (item['model'] /
        item['model_number'] are read-only fields pull.py copies FROM a
        already-live eBay listing's aspects -- there is no path the other
        direction). With no genuine equivalent covering them, they must
        reach the editor like any other category aspect.
        """
        raw = {
            'aspects': [
                {
                    'localizedAspectName': 'Model',
                    'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                    'aspectValues': [],
                },
                {
                    'localizedAspectName': 'MPN',
                    'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                    'aspectValues': [],
                },
                {
                    'localizedAspectName': 'Unit Quantity',
                    'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                    'aspectValues': [],
                },
                {
                    'localizedAspectName': 'Unit Type',
                    'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                    'aspectValues': [],
                },
            ],
        }
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=raw):
            result = specifics.get_aspects(cfg, '20673')
        names = {a['name'] for a in result}
        assert names == {'Model', 'MPN', 'Unit Quantity', 'Unit Type'}
        assert not hasattr(specifics, '_SKIP_ASPECTS')

    def test_california_prop_65_no_longer_filtered(self, tmp_path):
        """Session 39, item tgw202605060201087: Prop 65 was wrongly treated as
        skippable legal boilerplate — it's a real, near-universal eBay aspect and
        must be shown/fillable like any other."""
        raw = {
            'aspects': [
                {
                    'localizedAspectName': 'California Prop 65 Warning',
                    'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                    'aspectValues': [],
                },
            ],
        }
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=raw):
            result = specifics.get_aspects(cfg, '12345')
        assert {a['name'] for a in result} == {'California Prop 65 Warning'}

    def test_first_call_hits_live_api_once(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            specifics.get_aspects(cfg, '12345')
        assert mock_get.call_count == 1
        assert (tmp_path / 'ebay-aspects-cache.json').exists()

    def test_second_call_same_process_uses_memory_cache(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            specifics.get_aspects(cfg, '12345')
            specifics.get_aspects(cfg, '12345')
        assert mock_get.call_count == 1

    def test_fresh_process_uses_disk_cache_not_live_api(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS):
            specifics.get_aspects(cfg, '12345')
        _reset_cache()  # simulate a new process — memory cache gone, disk remains
        with patch.object(specifics, 'ebay_get', side_effect=AssertionError('must not hit live API')):
            result = specifics.get_aspects(cfg, '12345')
        assert {a['name'] for a in result} == {'Brand', 'Color', 'MPN'}

    def test_different_categories_cached_independently(self, tmp_path):
        cfg = _cfg(tmp_path)
        other = {'aspects': [{'localizedAspectName': 'Size',
                               'aspectConstraint': {'aspectRequired': False, 'aspectMode': 'FREE_TEXT'},
                               'aspectValues': []}]}
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', side_effect=[_RAW_ASPECTS, other]) as mock_get:
            r1 = specifics.get_aspects(cfg, '111')
            r2 = specifics.get_aspects(cfg, '222')
        assert mock_get.call_count == 2
        assert {a['name'] for a in r1} == {'Brand', 'Color', 'MPN'}
        assert {a['name'] for a in r2} == {'Size'}

    def test_old_disk_cache_is_permanent_no_refetch(self, tmp_path):
        # Session 42 / R0.4: cached aspects never auto-expire (same policy as
        # the category tree) — an arbitrarily old entry is served with NO live
        # call. Refresh happens only via `tgw warm-ebay-aspects`.
        cfg = _cfg(tmp_path)
        cache_path = tmp_path / 'ebay-aspects-cache.json'
        import json
        cache_path.write_text(json.dumps({
            '12345': {'_cached_at': 0, 'aspects': [{'name': 'Vintage', 'required': False,
                                                     'mode': 'FREE_TEXT', 'allowed_values': []}]},
        }))
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            result = specifics.get_aspects(cfg, '12345')
        assert mock_get.call_count == 0
        assert {a['name'] for a in result} == {'Vintage'}

    def test_bulk_shard_fallback_no_live_call(self, tmp_path):
        # A category absent from the per-category cache but present in the
        # bulk shard dir is served from the shard with no live call.
        cfg = _cfg(tmp_path)
        import json
        bulk = tmp_path / 'ebay-aspects-bulk'
        bulk.mkdir()
        (bulk / '777.json').write_text(json.dumps({
            '_cached_at': 1, 'name': 'Widgets',
            'aspects': [{'name': 'Size', 'required': True,
                         'mode': 'SELECTION_ONLY', 'allowed_values': ['S', 'M']}],
        }))
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            result = specifics.get_aspects(cfg, '777')
        assert mock_get.call_count == 0
        assert result == [{'name': 'Size', 'required': True,
                           'mode': 'SELECTION_ONLY', 'allowed_values': ['S', 'M']}]


class TestWarmMissingAspects:
    """Opportunistic end-of-run cache warm-up (session 39, Dave's idea): use
    whatever Taxonomy API quota is left today to fill in categories actually
    sold in but not yet cached — called from ebay_sync's periodic run."""

    def setup_method(self):
        _reset_cache()

    def test_warms_uncached_categories_up_to_the_cap(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            warmed = specifics.warm_missing_aspects(cfg, ['111', '222', '333'], max_new=5)
        assert warmed == 3
        assert mock_get.call_count == 3

    def test_skips_already_cached_categories(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            specifics.get_aspects(cfg, '111')  # already cached
            mock_get.reset_mock()
            warmed = specifics.warm_missing_aspects(cfg, ['111', '222'], max_new=5)
        assert warmed == 1  # only 222 was new
        assert mock_get.call_count == 1

    def test_dedupes_repeated_category_ids(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            warmed = specifics.warm_missing_aspects(cfg, ['111', '111', '111'], max_new=5)
        assert warmed == 1
        assert mock_get.call_count == 1

    def test_respects_max_new_cap(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            warmed = specifics.warm_missing_aspects(cfg, ['1', '2', '3', '4', '5'], max_new=2)
        assert warmed == 2
        assert mock_get.call_count == 2

    def test_stops_immediately_on_first_failure_self_throttling(self, tmp_path):
        """Quota exhausted mid-batch — must stop, not retry or skip ahead."""
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get',
                          side_effect=[_RAW_ASPECTS, RuntimeError('429 Too Many Requests'), _RAW_ASPECTS]) as mock_get:
            warmed = specifics.warm_missing_aspects(cfg, ['111', '222', '333'], max_new=5)
        assert warmed == 1  # only the first succeeded before the failure
        assert mock_get.call_count == 2  # never attempted the third

    def test_empty_category_ids_are_skipped(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch.object(specifics, 'get_category_tree_id', return_value='0'), \
             patch.object(specifics, 'ebay_get', return_value=_RAW_ASPECTS) as mock_get:
            warmed = specifics.warm_missing_aspects(cfg, ['', None, '111'], max_new=5)
        assert warmed == 1
        assert mock_get.call_count == 1
