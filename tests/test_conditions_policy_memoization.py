"""audit#1143 (todo #1182): conditions._get_policies() was re-reading and
re-parsing the full ~2.7MB on-disk policy cache on every call, unlike its
sibling caches (taxonomy._tree_id_cache, specifics._aspects_mem_cache) which
hold the parsed result in memory for the process lifetime. This verifies the
disk cache is now only read once per process, and that an explicit
refresh_condition_policies() call updates the in-memory copy rather than
being shadowed by it.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import tgw.apis.ebay.conditions as conditions


def setup_function(_):
    conditions._policies_mem_cache = None
    conditions._required_mem_cache = {}


def _cfg(tmp_path):
    return {'catalog_root': tmp_path}


def _write_disk_cache(tmp_path, policies, required=None):
    path = tmp_path / 'ebay-condition-policies.json'
    path.write_text(json.dumps({
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'policies': policies,
        'item_condition_required': required or {},
    }), encoding='utf-8')


def test_get_policies_reads_disk_cache_only_once(tmp_path, monkeypatch):
    _write_disk_cache(tmp_path, {'165806': [['3000', 'Used']]})
    reads = {'n': 0}
    real_load = conditions._load_cache

    def _counting_load(cfg):
        reads['n'] += 1
        return real_load(cfg)

    monkeypatch.setattr(conditions, '_load_cache', _counting_load)

    cfg = _cfg(tmp_path)
    first = conditions._get_policies(cfg)
    second = conditions._get_policies(cfg)
    third = conditions._get_policies(cfg)

    assert first == second == third
    assert reads['n'] == 1


def test_refresh_condition_policies_updates_mem_cache(tmp_path, monkeypatch):
    _write_disk_cache(tmp_path, {'165806': [['3000', 'Used']]})
    cfg = _cfg(tmp_path)

    # prime the mem cache with the stale disk copy
    conditions._get_policies(cfg)
    assert conditions._policies_mem_cache == {'165806': [('3000', 'Used')]}

    def _fake_ebay_get(cfg, path):
        return {
            'itemConditionPolicies': [
                {'categoryId': '165806', 'itemConditions': [
                    {'conditionId': '1000', 'conditionDescription': 'New'},
                ]},
            ],
        }

    monkeypatch.setattr(conditions, 'ebay_get', _fake_ebay_get)
    conditions.refresh_condition_policies(cfg)

    # the freshly-refreshed value, not the stale primed one, must come back
    assert conditions._get_policies(cfg) == {'165806': [('1000', 'New')]}


def test_refresh_accepts_condition_without_optional_description(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        conditions,
        'ebay_get',
        lambda *args, **kwargs: {
            'itemConditionPolicies': [{
                'categoryId': '171175',
                'itemConditions': [
                    {'conditionId': '5000'},
                    {'conditionId': '6000', 'conditionDescription': 'Acceptable'},
                ],
            }],
        },
    )

    refreshed = conditions.refresh_condition_policies(cfg)

    assert refreshed['171175'] == [
        ('5000', '5000'), ('6000', 'Acceptable'),
    ]


def test_cold_cache_recognizes_true_false_and_invalid_requirement_flags(
    tmp_path, monkeypatch
):
    _write_disk_cache(
        tmp_path,
        {
            'required': [['5000', 'Good']],
            '108857': [],
            'invalid': [['3000', 'Used']],
        },
        required={'required': True, '108857': False, 'invalid': 'not-a-boolean'},
    )
    monkeypatch.setattr(
        conditions,
        'ebay_get',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError('fresh disk cache must not call a provider')
        ),
    )

    cfg = _cfg(tmp_path)

    assert conditions.condition_policy_for_category(cfg, 'required') == {
        'recognized': True,
        'item_condition_required': True,
        'required_flag_valid': True,
        'conditions': [{
            'condition_id': '5000',
            'condition_label': 'Good',
            'condition_enum': 'USED_GOOD',
        }],
    }
    assert conditions.condition_policy_for_category(cfg, '108857') == {
        'recognized': True,
        'item_condition_required': False,
        'required_flag_valid': True,
        'conditions': [],
    }
    assert conditions.condition_policy_for_category(cfg, 'invalid') == {
        'recognized': True,
        'item_condition_required': None,
        'required_flag_valid': False,
        'conditions': [{
            'condition_id': '3000',
            'condition_label': 'Used',
            'condition_enum': 'USED_EXCELLENT',
        }],
    }
    assert conditions.condition_policy_for_category(cfg, 'missing')['recognized'] is False
    assert conditions.condition_policy_census(cfg) == {
        'schema': 'tgw-ebay-condition-policy-census/v1',
        'category_count': 3,
        'required_flag_coverage': 2,
        'required_flag_missing_or_invalid': 1,
        'expected_distinct_condition_id_sets': 26,
        'actual_distinct_condition_id_sets': 3,
        'condition_id_sets': [[], ['3000'], ['5000']],
        'drift': True,
    }


def test_warm_cache_keeps_policy_and_requirement_snapshot(tmp_path, monkeypatch):
    _write_disk_cache(
        tmp_path,
        {'108857': []},
        required={'108857': False},
    )
    reads = []
    real_load = conditions._load_cache

    def counting_load(cfg):
        reads.append(True)
        return real_load(cfg)

    monkeypatch.setattr(conditions, '_load_cache', counting_load)
    cfg = _cfg(tmp_path)

    cold = conditions.condition_policy_for_category(cfg, '108857')
    _write_disk_cache(
        tmp_path,
        {'108857': [['1000', 'New']]},
        required={'108857': True},
    )
    warm = conditions.condition_policy_for_category(cfg, '108857')

    assert cold == warm
    assert cold['item_condition_required'] is False
    assert cold['conditions'] == []
    assert reads == [True]


def test_condition_policy_census_deduplicates_ids_within_each_category(tmp_path):
    _write_disk_cache(
        tmp_path,
        {
            'single': [['1000', 'New']],
            'duplicate-row': [['1000', 'New'], ['1000', 'New duplicate']],
        },
        required={'single': True, 'duplicate-row': True},
    )

    report = conditions.condition_policy_census(_cfg(tmp_path), expected_sets=1)

    assert report['actual_distinct_condition_id_sets'] == 1
    assert report['condition_id_sets'] == [['1000']]
    assert report['drift'] is False


def test_refresh_preserves_recognized_optional_category_without_conditions(
    tmp_path, monkeypatch
):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        conditions,
        'ebay_get',
        lambda *args, **kwargs: {
            'itemConditionPolicies': [{
                'categoryId': '108857',
                'itemConditionRequired': False,
                'itemConditions': [],
            }],
        },
    )

    refreshed = conditions.refresh_condition_policies(cfg)

    assert refreshed == {'108857': []}
    assert conditions.condition_policy_for_category(cfg, '108857') == {
        'recognized': True,
        'item_condition_required': False,
        'required_flag_valid': True,
        'conditions': [],
    }
