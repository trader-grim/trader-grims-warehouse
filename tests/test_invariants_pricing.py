"""Invariants B1–B5 (docs/invariants.md) — pricing floor, rounding, provenance.

Pure-function tests for tgw.ebay.pricing plus worker-level tests for
tgw.workers.ebay_price (offline, pattern from tests/test_strikethrough.py).

B4 (launch >= target): the worker clamps the launch price to to_99(target)
when raw comps would put it below the floored target (fixed 2026-06-10).
"""

import json

import pytest

import tgw.ebay.pricing as pricing
import tgw.workers.ebay_price as ebay_price
from tgw.ebay.pricing import to_99


@pytest.fixture(autouse=True)
def _fresh_groups_cache():
    """category-groups.json is cached in module globals — isolate each test."""
    pricing._groups_cache = None
    pricing._groups_reverse = None
    yield
    pricing._groups_cache = None
    pricing._groups_reverse = None


@pytest.fixture
def cfg(tmp_path):
    groups = {
        'global_floor': 1.99,
        'condition_factors': {'good': 0.8},
        'groups': {
            'electronics': {
                'name': 'Electronics',
                'ebay_categories': ['12345'],
                'pricing': {'floor': 5.0, 'typical_used': 20.0},
            },
        },
    }
    gp = tmp_path / 'category-groups.json'
    gp.write_text(json.dumps(groups), encoding='utf-8')
    return {'category_groups_path': str(gp)}


def _summaries(prices, condition='Good'):
    return [{'price': {'value': str(p)}, 'condition': condition} for p in prices]


# ---------------------------------------------------------------------------
# B2 — to_99 rounding properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('value,expected', [
    (15.23, 15.99),
    (16.00, 16.99),
    (15.99, 15.99),   # already a .99 point — idempotent
    (0.50,  0.99),
    (110.0, 110.99),
])
def test_to_99_lands_on_99_point(value, expected):
    assert to_99(value) == expected


@pytest.mark.parametrize('value', [0.01, 0.99, 1.0, 7.49, 19.989, 100.0, 123.45])
def test_to_99_never_lowers_a_price(value):
    result = to_99(value)
    assert result >= value
    assert round(result % 1, 2) == 0.99


# ---------------------------------------------------------------------------
# B1 — floor applies regardless of source
# ---------------------------------------------------------------------------

def test_group_floor_applied(cfg):
    assert pricing._apply_floor(2.50, cfg, '12345') == (5.0, True)


def test_global_floor_applied_for_unmapped_category(cfg):
    assert pricing._apply_floor(0.50, cfg, '99999') == (1.99, True)


def test_price_above_floor_unchanged(cfg):
    assert pricing._apply_floor(12.34, cfg, '12345') == (12.34, False)


def test_browse_p25_is_floored(cfg, monkeypatch):
    # All comps below the $5 group floor — suggested price must rise to the floor.
    monkeypatch.setattr(pricing, '_fetch_raw',
                        lambda c, q, limit=20: _summaries([2.0, 2.5, 3.0]))
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '12345')
    assert result['price'] == 5.0
    assert result['source'].startswith('browse:')
    assert result['comps']['count'] == 3


def test_group_assumption_floored_and_99(cfg, monkeypatch):
    # No comps at any browse stage → group typical_used × condition_factor,
    # floored, then to_99: 20.0 × 0.8 = 16.0 → 16.99.
    monkeypatch.setattr(pricing, '_fetch_raw', lambda c, q, limit=20: [])
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '12345',
                                   item_condition='good')
    assert result['price'] == 16.99
    assert result['source'] == 'group_assumption:Electronics'
    assert result['price_confidence'] == 'low'


def test_category_default_is_floored(cfg, monkeypatch):
    monkeypatch.setattr(pricing, '_fetch_raw', lambda c, q, limit=20: [])
    cfg['category_price_defaults'] = {'99999': 0.25}   # below global floor
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '99999')
    assert result['price'] == 1.99
    assert result['source'] == 'category_default:99999'


# ---------------------------------------------------------------------------
# B5 — insufficient data leaves price null; condition filter falls back
# ---------------------------------------------------------------------------

def test_insufficient_comps_price_is_null(cfg, monkeypatch):
    monkeypatch.setattr(pricing, '_fetch_raw', lambda c, q, limit=20: [])
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '99999')
    assert result['price'] is None
    assert result['source'] == 'insufficient_data'
    assert result['price_confidence'] == 'low'


def test_condition_filter_excludes_better_condition_comps(cfg, monkeypatch):
    # 3 same-condition comps + 2 expensive New comps: the New ones must not
    # inflate the percentiles when enough same-or-worse comps exist.
    summaries = (_summaries([10.0, 11.0, 12.0], condition='Good')
                 + _summaries([90.0, 95.0], condition='Brand New'))
    monkeypatch.setattr(pricing, '_fetch_raw', lambda c, q, limit=20: summaries)
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '12345',
                                   item_condition='good')
    assert result['source'].endswith('+cond')
    assert result['comps']['max'] == 12.0


def test_condition_filter_falls_back_below_min_comps(cfg, monkeypatch):
    # Only 2 same-condition comps — filter would drop below MIN_COMPS, so the
    # unfiltered set must be used instead of returning no price.
    summaries = (_summaries([10.0, 11.0], condition='Good')
                 + _summaries([20.0, 21.0], condition='Brand New'))
    monkeypatch.setattr(pricing, '_fetch_raw', lambda c, q, limit=20: summaries)
    result = pricing.suggest_price(cfg, 'Acme Widget', 'Widgets', '12345',
                                   item_condition='good')
    assert result['price'] is not None
    assert not result['source'].endswith('+cond')
    assert result['comps']['count'] == 4


# ---------------------------------------------------------------------------
# Worker level — B3 provenance, B4 launch >= target, B5 stage gating
# ---------------------------------------------------------------------------

@pytest.fixture
def price_worker(tmp_path, monkeypatch):
    """EbayPriceWorker running offline (no DB, no eBay, no quality scoring)."""
    monkeypatch.setattr(ebay_price.tgw_logging, 'log_event', lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_price.state_machine, 'enqueue_job',
                        lambda **kw: enqueued.append(kw))
    import tgw.listing_quality as lq

    class _Q:
        def to_dict(self):
            return {'stub': True}

    monkeypatch.setattr(lq, 'score_draft', lambda item: _Q())

    worker = object.__new__(ebay_price.EbayPriceWorker)
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(ebay_price, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(ebay_price, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker.config = {'itemdata_root': tmp_path, 'pretty': False, 'api_key': 'test-api-key'}
    worker._enqueued = enqueued
    return worker


def _suggestion(price, comps):
    return {'price': price, 'source': 'browse:full_title', 'comps': comps,
            'price_confidence': 'medium', 'velocity_hint': None,
            'queried_at': '2026-06-10T00:00:00Z'}


def _write_item(tmp_path, sku):
    item = {'title': 'Acme Thing', 'condition': 'good',
            'draft_listing': {'title': 'Acme Thing', 'category_id': '12345',
                              'category_name': 'Widgets'}}
    d = tmp_path / sku
    d.mkdir(parents=True)
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def _run(worker, tmp_path, sku):
    _write_item(tmp_path, sku)
    worker.handle({'payload_json': {'sku': sku}})
    return json.loads((tmp_path / sku / f'{sku}.json').read_text(encoding='utf-8'))


def test_price_written_with_full_provenance(price_worker, tmp_path, monkeypatch):
    comps = {'count': 5, 'min': 40.0, 'p25': 50.0, 'median': 70.0,
             'p75': 80.0, 'max': 100.0}
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: _suggestion(50.0, comps))
    result = _run(price_worker, tmp_path, 'tgw1')
    offer = result['ebay_offer']
    assert offer['price'] == to_99(100.0 * 1.10)
    assert offer['target_price'] == 50.0
    assert offer['price_source'] == 'browse:full_title'
    assert offer['price_comps'] == comps
    assert offer['priced_at']


def test_unpriced_item_never_enqueues_stage(price_worker, tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: _suggestion(None, {}))
    result = _run(price_worker, tmp_path, 'tgw2')
    assert result['ebay_offer']['price'] is None
    assert all(kw['queue_name'] != 'ebay_stage' for kw in price_worker._enqueued)


def test_priced_item_enqueues_stage(price_worker, tmp_path, monkeypatch):
    comps = {'count': 3, 'min': 10.0, 'p25': 10.0, 'median': 12.0,
             'p75': 14.0, 'max': 15.0}
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: _suggestion(10.0, comps))
    _run(price_worker, tmp_path, 'tgw3')
    assert any(kw['queue_name'] == 'ebay_stage' for kw in price_worker._enqueued)


def test_launch_price_at_least_target_price(price_worker, tmp_path, monkeypatch):
    # suggest_price floored p25 up to $5.00, but every comp sits near $1 —
    # raw launch would be to_99(1.00 * 1.10) = 1.99 < target 5.00; the worker
    # must clamp to to_99(target) so the markdown never raises the price
    # (B4 gap fixed 2026-06-10).
    comps = {'count': 3, 'min': 0.80, 'p25': 0.90, 'median': 0.95,
             'p75': 1.00, 'max': 1.00}
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: _suggestion(5.0, comps))
    result = _run(price_worker, tmp_path, 'tgw4')
    offer = result['ebay_offer']
    assert offer['price'] >= offer['target_price']
    assert offer['price'] == to_99(5.0)   # clamp keeps the .99 convention
