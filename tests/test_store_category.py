"""PP-STORE-001 Phase 1 — store category ID support.

Three areas tested (all offline, no eBay token needed):
  * _get_store_category_id (ebay_draft helper) — reads category-groups.json
  * _build_offer_bodies (sync) — uses draft.store_category_id via store cats cache
  * store-category set command logic — updates category-groups.json
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tgw.ebay.sync as sync
from tgw.workers.ebay_draft import _get_store_category_id

# ---------------------------------------------------------------------------
# Fixtures shared across sections
# ---------------------------------------------------------------------------

@pytest.fixture
def cat_groups_file(tmp_path):
    data = {
        "version": 1,
        "updated": "2026-06-12",
        "groups": {
            "books": {
                "name": "Books",
                "store_category": "",
                "store_category_id": None,
            },
            "electronics_remotes": {
                "name": "Remotes",
                "store_category": "TV & Audio Accessories",
                "store_category_id": 55555,
            },
        },
    }
    p = tmp_path / "category-groups.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.fixture
def cfg_with_groups(cat_groups_file):
    return {
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw': {},
        'category_groups_path':  str(cat_groups_file),
    }


# ---------------------------------------------------------------------------
# _get_store_category_id (ebay_draft helper)
# ---------------------------------------------------------------------------

def test_get_store_category_id_returns_id_when_set(cfg_with_groups):
    item = {'category_group': 'electronics_remotes'}
    assert _get_store_category_id(item, cfg_with_groups) == 55555


def test_get_store_category_id_returns_none_when_null(cfg_with_groups):
    item = {'category_group': 'books'}
    assert _get_store_category_id(item, cfg_with_groups) is None


def test_get_store_category_id_returns_none_for_unknown_group(cfg_with_groups):
    item = {'category_group': 'nonexistent_group'}
    assert _get_store_category_id(item, cfg_with_groups) is None


def test_get_store_category_id_returns_none_when_no_group(cfg_with_groups):
    assert _get_store_category_id({}, cfg_with_groups) is None


def test_get_store_category_id_returns_none_on_bad_path():
    cfg = {'category_groups_path': '/nonexistent/path/to/file.json'}
    assert _get_store_category_id({'category_group': 'books'}, cfg) is None


# ---------------------------------------------------------------------------
# _build_offer_bodies — store category ID path (PP-STORE-001)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_ebay_calls(monkeypatch):
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')


def _item(**extra):
    base = {
        'draft_listing': {
            'price':          '9.99',
            'title':          'Remote Control',
            'description':    'A remote.',
            'imageUrls':      ['https://example.com/1.jpg'],
            'condition_enum': 'USED_GOOD',
            'category_id':    '14999',
            'quantity':       1,
        },
    }
    base.update(extra)
    return base


@pytest.fixture
def cfg_sync():
    return {
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw': {},
    }


def test_offer_uses_store_category_name_from_id(cfg_sync, monkeypatch):
    # draft has store_category_id; store cats cache has a matching entry.
    monkeypatch.setattr(sync, '_get_store_categories_cached',
                        lambda _cfg: [{'id': '55555', 'name': 'TV & Audio Accessories',
                                       'path': 'TV & Audio Accessories'}])
    item = _item()
    item['draft_listing']['store_category_id'] = 55555
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    assert offer['storeCategoryNames'] == ['TV & Audio Accessories']


def test_offer_falls_back_to_config_when_id_not_in_cache(cfg_sync, monkeypatch):
    # draft has store_category_id but cache lookup fails to match.
    monkeypatch.setattr(sync, '_get_store_categories_cached',
                        lambda _cfg: [])
    # Config-based fallback should still fire.
    monkeypatch.setattr(sync, '_resolve_store_category_names',
                        lambda _cfg, _cat: ['Fallback Category'])
    item = _item()
    item['draft_listing']['store_category_id'] = 55555
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', item)
    assert offer['storeCategoryNames'] == ['Fallback Category']


def test_offer_uses_config_names_when_no_id_in_draft(cfg_sync, monkeypatch):
    monkeypatch.setattr(sync, '_resolve_store_category_names',
                        lambda _cfg, _cat: ['Config Category'])
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', _item())
    assert offer['storeCategoryNames'] == ['Config Category']


def test_offer_omits_store_category_when_no_id_and_no_config(cfg_sync, monkeypatch):
    monkeypatch.setattr(sync, '_resolve_store_category_names',
                        lambda _cfg, _cat: None)
    _, offer = sync._build_offer_bodies(cfg_sync, 'tgw0001', _item())
    assert 'storeCategoryNames' not in offer


# ---------------------------------------------------------------------------
# store-category set command — writes category-groups.json
# ---------------------------------------------------------------------------

def _make_cg(tmp_path, groups=None):
    data = {
        "version": 1,
        "updated": "2026-01-01",
        "groups": groups or {
            "books": {"name": "Books", "store_category": "", "store_category_id": None},
        },
    }
    p = tmp_path / "category-groups.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _run_set(cg_path: Path, group: str, store_id: int,
             store_cats=None) -> dict:
    """
    Extract the set-command logic so we can test it without the full CLI stack.
    Returns the parsed category-groups.json after the write.
    """
    data = json.loads(cg_path.read_text(encoding='utf-8'))
    groups = data['groups']
    if group not in groups:
        raise KeyError(group)
    groups[group]['store_category_id'] = store_id
    if store_cats:
        matched = next((c for c in store_cats if c['id'] == str(store_id)), None)
        if matched:
            groups[group]['store_category'] = matched['name']
    cg_path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    return json.loads(cg_path.read_text(encoding='utf-8'))


def test_set_writes_store_category_id(tmp_path):
    p = _make_cg(tmp_path)
    result = _run_set(p, 'books', 12345)
    assert result['groups']['books']['store_category_id'] == 12345


def test_set_also_updates_name_when_store_cats_provided(tmp_path):
    p = _make_cg(tmp_path)
    cats = [{'id': '12345', 'name': 'Books & Fiction', 'path': 'Books & Fiction'}]
    result = _run_set(p, 'books', 12345, store_cats=cats)
    assert result['groups']['books']['store_category_id'] == 12345
    assert result['groups']['books']['store_category'] == 'Books & Fiction'


def test_set_raises_on_unknown_group(tmp_path):
    p = _make_cg(tmp_path)
    with pytest.raises(KeyError):
        _run_set(p, 'nonexistent', 99999)


def test_set_id_to_none_clears_it(tmp_path):
    p = _make_cg(tmp_path, groups={
        'books': {'name': 'Books', 'store_category': 'Old', 'store_category_id': 12345},
    })
    result = _run_set(p, 'books', None)  # type: ignore[arg-type]
    assert result['groups']['books']['store_category_id'] is None
