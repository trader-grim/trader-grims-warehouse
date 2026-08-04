"""Regression test for collision_report() (todo #1294, PP-COHESION-001).

collision_report() previously iterated check_collisions()'s dict return
value as if it were a list of collision dicts, which yields string keys
and raises TypeError on `c['conflict_type']`. This locks in the fix:
collision_report() must consume check_collisions()'s actual dict shape.
"""

from tgw import sku_migration


def test_collision_report_empty(monkeypatch):
    def fake_check_collisions(cfg):
        return {
            'ok': True,
            'raw_a_collisions': 0,
            'auto_resolved': 0,
            'unresolvable': 0,
            'safe_to_migrate': True,
            'resolved_pairs': [],
            'unresolvable_detail': [],
        }

    monkeypatch.setattr(sku_migration, 'check_collisions', fake_check_collisions)

    report = sku_migration.collision_report({})

    assert set(report.keys()) == {'ok', 'total', 'by_type', 'collisions', 'safe_to_migrate'}
    assert report['ok'] is True
    assert report['total'] == 0
    assert report['by_type']['auto_resolved'] + report['by_type']['unresolvable'] == report['total']
    assert report['collisions'] == []
    assert report['safe_to_migrate'] is True


def test_collision_report_with_pairs(monkeypatch):
    pairs = [
        {
            'winner': 'tgw20260101000000000',
            'loser': 'tgw20260101000000001',
            'natural_target': 'tgw202601010000000',
            'resolved_target': 'tgw2026010100000001',
        },
    ]

    def fake_check_collisions(cfg):
        return {
            'ok': False,
            'raw_a_collisions': 1,
            'auto_resolved': 1,
            'unresolvable': 0,
            'safe_to_migrate': True,
            'resolved_pairs': pairs,
            'unresolvable_detail': [],
        }

    monkeypatch.setattr(sku_migration, 'check_collisions', fake_check_collisions)

    report = sku_migration.collision_report({})

    assert report['total'] == 1
    assert report['by_type'] == {'auto_resolved': 1, 'unresolvable': 0}
    assert report['collisions'] == pairs
    # must be actual dicts (with winner/loser/natural_target/resolved_target),
    # not the string keys of the check_collisions() dict
    assert isinstance(report['collisions'][0], dict)
    assert set(report['collisions'][0].keys()) == {
        'winner', 'loser', 'natural_target', 'resolved_target',
    }
    assert report['safe_to_migrate'] is True


def test_collision_report_unresolvable(monkeypatch):
    def fake_check_collisions(cfg):
        return {
            'ok': False,
            'raw_a_collisions': 3,
            'auto_resolved': 1,
            'unresolvable': 2,
            'safe_to_migrate': False,
            'resolved_pairs': [{'winner': 'a', 'loser': 'b',
                                'natural_target': 'x', 'resolved_target': 'y'}],
            'unresolvable_detail': [{'sku': 'c'}, {'sku': 'd'}],
        }

    monkeypatch.setattr(sku_migration, 'check_collisions', fake_check_collisions)

    report = sku_migration.collision_report({})

    assert report['ok'] is False
    assert report['total'] == 3
    assert report['by_type']['auto_resolved'] + report['by_type']['unresolvable'] == report['total']
    assert report['safe_to_migrate'] is False
