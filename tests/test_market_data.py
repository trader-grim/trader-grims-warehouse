"""Tests for PP-REPRICER-001 read-only market-data provider layer."""

import json

import pytest

from tgw.ebay import market_data as md


@pytest.fixture
def cfg(tmp_path):
    return {'catalog_root': tmp_path, 'itemdata_root': tmp_path}


# ---------------------------------------------------------------------------
# current_price extraction
# ---------------------------------------------------------------------------

def test_current_price_prefers_offer():
    item = {'ebay_offer': {'price': 12.5}, 'draft_listing': {'price': 9.0}}
    assert md.current_price(item) == 12.5


def test_current_price_falls_back_to_draft():
    assert md.current_price({'draft_listing': {'price': 9.0}}) == 9.0


def test_current_price_none_when_absent_or_zero():
    assert md.current_price({}) is None
    assert md.current_price({'ebay_offer': {'price': 0}}) is None
    assert md.current_price({'ebay_offer': {'price': 'bad'}}) is None


# ---------------------------------------------------------------------------
# OwnSalesProvider
# ---------------------------------------------------------------------------

def test_own_sales_available_with_enough_sales(cfg, monkeypatch):
    monkeypatch.setattr(md, 'load_velocity_stats', lambda root: {
        'categories': {'139973': {'sold_count': 8, 'median_sale_price': 20.0,
                                  'p25_sale_price': 14.0}}})
    item = {'ebay_category_id': '139973'}
    c = md.OwnSalesProvider(cfg).comps(item)
    assert c.source == 'own_sales'
    assert c.available is True
    assert c.n == 8
    assert c.p25 == 14.0
    assert c.median == 20.0


def test_own_sales_unavailable_below_min_samples(cfg, monkeypatch):
    monkeypatch.setattr(md, 'load_velocity_stats', lambda root: {
        'categories': {'1': {'sold_count': 2, 'median_sale_price': 20.0,
                             'p25_sale_price': 14.0}}})
    c = md.OwnSalesProvider(cfg).comps({'ebay_category_id': '1'})
    assert c.available is False
    assert c.n == 2


def test_own_sales_no_category(cfg, monkeypatch):
    monkeypatch.setattr(md, 'load_velocity_stats', lambda root: {'categories': {}})
    c = md.OwnSalesProvider(cfg).comps({})
    assert c.available is False
    assert 'no category' in c.note


# ---------------------------------------------------------------------------
# BrowseCompsProvider
# ---------------------------------------------------------------------------

def test_browse_provider_wraps_suggest_price(cfg, monkeypatch):
    monkeypatch.setattr(md, 'suggest_price', lambda *a, **k: {
        'price': 9.99, 'source': 'browse:full_title',
        'comps': {'count': 6, 'p25': 8.0, 'median': 11.0, 'p75': 15.0}})
    item = {'title': 'Cool Gadget', 'ebay_category_id': '1'}
    c = md.BrowseCompsProvider(cfg).comps(item)
    assert c.source == 'browse'
    assert c.available is True
    assert c.n == 6
    assert c.p25 == 8.0
    assert c.p75 == 15.0


def test_browse_provider_no_title(cfg):
    c = md.BrowseCompsProvider(cfg).comps({'title': ''})
    assert c.available is False
    assert 'no title' in c.note


# ---------------------------------------------------------------------------
# StubProvider
# ---------------------------------------------------------------------------

def test_stub_provider_never_available(cfg):
    c = md.StubProvider(cfg).comps({'title': 'x'})
    assert c.available is False
    assert 'marketplace_insights' in c.note


# ---------------------------------------------------------------------------
# reprice_suggest blending
# ---------------------------------------------------------------------------

def _comp(source, available, p25=None, median=None, n=0):
    return md.Comps(source=source, available=available, p25=p25, median=median, n=n)


def test_reprice_prefers_own_sales(cfg, monkeypatch):
    monkeypatch.setattr(md, '_apply_floor', lambda price, cfg, cat: (price, False))
    own = _comp('own_sales', True, p25=14.0, median=20.0, n=8)
    browse = _comp('browse', True, p25=8.0, median=11.0, n=6)
    providers = [type('P', (), {'name': 'own_sales', 'comps': lambda s, i: own})(),
                 type('P', (), {'name': 'browse', 'comps': lambda s, i: browse})()]
    item = {'sku': 'tgw1', 'ebay_category_id': '1', 'ebay_offer': {'price': 30.0}}
    out = md.reprice_suggest(cfg, item, providers=providers)
    assert out['basis'] == 'own_sales'
    # p25=14.0 → to_99 → 14.99
    assert out['suggested_price'] == 14.99
    assert out['recommendation'] == 'reduce'   # 14.99 < 30 * 0.95
    assert out['applied'] is False


def test_reprice_falls_back_to_browse_when_own_unavailable(cfg, monkeypatch):
    monkeypatch.setattr(md, '_apply_floor', lambda price, cfg, cat: (price, False))
    own = _comp('own_sales', False)
    browse = _comp('browse', True, p25=8.0, median=11.0, n=6)
    providers = [type('P', (), {'name': 'own_sales', 'comps': lambda s, i: own})(),
                 type('P', (), {'name': 'browse', 'comps': lambda s, i: browse})()]
    item = {'sku': 'tgw2', 'ebay_category_id': '1', 'ebay_offer': {'price': 8.50}}
    out = md.reprice_suggest(cfg, item, providers=providers)
    assert out['basis'] == 'browse'
    assert out['suggested_price'] == 8.99
    assert out['recommendation'] == 'hold'   # 8.99 within ±thresholds of 8.50


def test_reprice_no_data(cfg, monkeypatch):
    providers = [type('P', (), {'name': 'stub', 'comps': lambda s, i: _comp('stub', False)})()]
    out = md.reprice_suggest(cfg, {'sku': 'tgw3', 'ebay_offer': {'price': 5}}, providers=providers)
    assert out['suggested_price'] is None
    assert out['basis'] is None
    assert out['recommendation'] == 'unknown'
    assert 'no market data' in out['rationale']


def test_reprice_set_when_no_current_price(cfg, monkeypatch):
    monkeypatch.setattr(md, '_apply_floor', lambda price, cfg, cat: (price, False))
    browse = _comp('browse', True, p25=8.0, n=6)
    providers = [type('P', (), {'name': 'browse', 'comps': lambda s, i: browse})()]
    out = md.reprice_suggest(cfg, {'sku': 'tgw4', 'ebay_category_id': '1'}, providers=providers)
    assert out['current_price'] is None
    assert out['recommendation'] == 'set'


# ---------------------------------------------------------------------------
# cmd_reprice_suggest CLI integration
# ---------------------------------------------------------------------------

def test_cmd_reprice_suggest_reads_items(tmp_path, monkeypatch):
    import tgw.api as api
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    sku = 'tgw202601010000050'
    d = itemdata / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(
        {'sku': sku, 'title': 'Thing', 'ebay_offer': {'price': 20.0}}), encoding='utf-8')
    cfg = {'itemdata_root': itemdata, 'catalog_root': tmp_path}

    monkeypatch.setattr('tgw.ebay.market_data.reprice_suggest',
                        lambda c, item, **k: {'ok': True, 'sku': item['sku'],
                                              'current_price': 20.0, 'suggested_price': 9.99,
                                              'delta_pct': -50.0, 'recommendation': 'reduce',
                                              'rationale': 'test', 'applied': False})
    out = api.cmd_reprice_suggest(cfg, skus=[sku])
    assert out['ok'] is True
    assert out['count'] == 1
    assert out['items'][0]['recommendation'] == 'reduce'
    assert out['applied'] is False


def test_cmd_reprice_suggest_no_match(tmp_path):
    import tgw.api as api
    cfg = {'itemdata_root': tmp_path, 'catalog_root': tmp_path}
    out = api.cmd_reprice_suggest(cfg)
    assert out['ok'] is True
    assert out['count'] == 0
    assert 'no items matched' in out['note']


def test_cmd_reprice_suggest_negative_limit_not_end_slice(tmp_path, monkeypatch):
    """bug #008: --limit -5 must not slice from the end (skus[:-5])."""
    import tgw.api as api
    itemdata = tmp_path / 'ItemData'
    itemdata.mkdir()
    skus = []
    for i in range(3):
        sku = f'tgw2026010100000006{i}'
        d = itemdata / sku
        d.mkdir()
        (d / f'{sku}.json').write_text(json.dumps({'sku': sku, 'title': 'T'}), encoding='utf-8')
        skus.append(sku)
    cfg = {'itemdata_root': itemdata, 'catalog_root': tmp_path}
    monkeypatch.setattr('tgw.ebay.market_data.reprice_suggest',
                        lambda c, item, **k: {'ok': True, 'sku': item['sku']})
    out = api.cmd_reprice_suggest(cfg, skus=skus, limit=-5)
    assert out['count'] == 3   # all, not skus[:-5] == []
