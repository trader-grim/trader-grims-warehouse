"""Invariants A1/A2/A3/A5 (docs/invariants.md) — item store write rules.

A1: item JSON writes are atomic and leave no temp-file litter.
A2: sku is not bulk-editable (whitelist pin).
A3: create_item never overwrites an existing item.
A5: any field write clears the catalog_verified hall-pass (the verifiedupdate
    bypass was fixed 2026-06-10).
"""

import json

import pytest

from tgw import items


@pytest.fixture
def cfg(tmp_path):
    return {
        'itemdata_root':      tmp_path / 'ItemData',
        'location_tree_root': tmp_path / 'by-location',
        'pretty':             False,
    }


def _make_item(cfg, sku, **fields):
    return items.create_item(cfg, sku, fields)


# ---------------------------------------------------------------------------
# A1 — atomic writes
# ---------------------------------------------------------------------------

def test_atomic_write_produces_valid_json_and_no_litter(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1', 'title': 'thing'})
    assert json.loads(path.read_text(encoding='utf-8'))['title'] == 'thing'
    # temp file must have been renamed away — only the JSON remains
    assert [p.name for p in path.parent.iterdir()] == ['tgw1.json']


def test_atomic_write_overwrite_keeps_single_file(tmp_path):
    path = tmp_path / 'tgw1' / 'tgw1.json'
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 1})
    items.atomic_write_json(path, {'sku': 'tgw1', 'v': 2})
    assert json.loads(path.read_text(encoding='utf-8'))['v'] == 2
    assert [p.name for p in path.parent.iterdir()] == ['tgw1.json']


# ---------------------------------------------------------------------------
# A3 — creation never overwrites
# ---------------------------------------------------------------------------

def test_create_item_refuses_existing_sku(cfg):
    _make_item(cfg, 'tgw20260101000000000', title='first')
    with pytest.raises(FileExistsError):
        _make_item(cfg, 'tgw20260101000000000', title='second')
    # original record untouched
    doc = json.loads((cfg['itemdata_root'] / 'tgw20260101000000000'
                      / 'tgw20260101000000000.json').read_text())
    assert doc['title'] == 'first'


# ---------------------------------------------------------------------------
# A2 — sku not reachable through bulk edit
# ---------------------------------------------------------------------------

def test_bulk_edit_whitelist_excludes_sku(cfg):
    assert 'sku' not in items.BULK_FIELD_KEYS
    res = items.bulk_edit(cfg, {'sku': 'tgwx'}, 'sku', 'tgwy')
    assert res['ok'] is False


# ---------------------------------------------------------------------------
# A5 — hall-pass invalidation
# ---------------------------------------------------------------------------

def _verified_item(cfg, sku):
    _make_item(cfg, sku, title='t', location='A1')
    items.update_item(cfg, sku, 'catalog_verified',
                      {'ts': '2026-06-01T00:00:00Z', 'by': 'test'})
    return cfg['itemdata_root'] / sku / f'{sku}.json'


def test_field_write_clears_catalog_verified(cfg):
    path = _verified_item(cfg, 'tgw1')
    items.update_item(cfg, 'tgw1', 'title', 'new title')
    assert 'catalog_verified' not in json.loads(path.read_text())


def test_writing_catalog_verified_itself_persists(cfg):
    path = _verified_item(cfg, 'tgw1')
    assert 'catalog_verified' in json.loads(path.read_text())


def test_update_missing_sku_is_clean_error(cfg):
    res = items.update_item(cfg, 'tgw-nope', 'title', 'x')
    assert res['ok'] is False


def test_verifiedupdate_clears_catalog_verified(cfg):
    # verifiedupdate writes via atomic_write_json directly, so it must clear
    # the hall-pass itself (A5 gap fixed 2026-06-10).
    path = _verified_item(cfg, 'tgw1')
    items.verifiedupdate(cfg, 'tgw1', '2026-06-10')
    doc = json.loads(path.read_text())
    assert 'catalog_verified' not in doc
    assert doc['verified'] == '2026-06-10'
    assert doc['#STATUS'] == 'In Stock'
