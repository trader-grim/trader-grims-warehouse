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
# _llm_filter_comps — routed through the config-only model facility
# (2026-07-14, Dave: was a hardcoded openai/gpt-4o-mini raw OpenRouter call,
# bypassing tgw-models.json entirely; now pricing_comp_filter/deepseek-v4-flash)
# ---------------------------------------------------------------------------

def test_llm_filter_comps_uses_configured_task_not_hardcoded_model(monkeypatch, cfg):
    calls = []

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls.append(task)
        return json.dumps({'confidence': 'high', 'comps': [
            {'i': 0, 'keep': True, 'reason': 'match'},
            {'i': 1, 'keep': False, 'reason': 'different product'},
            {'i': 2, 'keep': True, 'reason': 'match'},
        ]})

    monkeypatch.setattr(pricing, 'call_model', fake_call_model)
    summaries = _summaries([10.0, 999.0, 12.0])

    kept_prices, confidence = pricing._llm_filter_comps(cfg, 'Acme Widget', summaries, None)

    assert calls == ['pricing_comp_filter']  # routed via tgw-models.json, not a literal model id
    assert confidence == 'high'
    assert 999.0 not in kept_prices
    assert summaries[1]['_llm_dropped'] is True
    assert summaries[0]['_llm_dropped'] is False


def test_llm_filter_comps_falls_back_on_call_model_error(monkeypatch, cfg):
    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        raise RuntimeError('no models[pricing_comp_filter] entry in tgw-models.json')

    monkeypatch.setattr(pricing, 'call_model', fake_call_model)
    summaries = _summaries([10.0, 11.0, 12.0])

    kept_prices, confidence = pricing._llm_filter_comps(cfg, 'Acme Widget', summaries, None)

    assert confidence == 'medium'
    assert sorted(kept_prices) == [10.0, 11.0, 12.0]  # unfiltered fallback
    assert all(s['_llm_dropped'] is False for s in summaries)


def test_llm_filter_comps_falls_back_on_malformed_json(monkeypatch, cfg):
    monkeypatch.setattr(pricing, 'call_model', lambda *a, **k: 'not valid json at all')
    summaries = _summaries([10.0, 11.0, 12.0])

    kept_prices, confidence = pricing._llm_filter_comps(cfg, 'Acme Widget', summaries, None)

    assert confidence == 'medium'
    assert sorted(kept_prices) == [10.0, 11.0, 12.0]


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


# ---------------------------------------------------------------------------
# P5 (PP-PHOTOSYNC-001, todo #1120) — operator price never machine-overridden
# ---------------------------------------------------------------------------

def _write_item_with_history(tmp_path, sku, price_history, ebay_offer=None):
    item = {'title': 'Acme Thing', 'condition': 'good',
            'draft_listing': {'title': 'Acme Thing', 'category_id': '12345',
                              'category_name': 'Widgets'},
            'price_history': price_history}
    if ebay_offer is not None:
        item['ebay_offer'] = ebay_offer
    d = tmp_path / sku
    d.mkdir(parents=True)
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def test_chain_enqueued_price_skips_when_operator_set_last(price_worker, tmp_path, monkeypatch):
    """A background/chain-triggered ebay_price job (no origin stamp) must not
    overwrite an operator-typed price, even if ebay_offer.price has gone
    missing (e.g. some other field-clearing path)."""
    sku = 'tgw5'
    _write_item_with_history(tmp_path, sku, [
        {'ts': '2026-07-03T10:00:00Z', 'price': 42.0, 'previous_price': None,
         'stage': None, 'label': 'price edited', 'source': 'operator'},
    ])
    called = {'n': 0}
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: (called.__setitem__('n', called['n'] + 1)
                                        or _suggestion(99.0, {'count': 3, 'min': 1, 'p25': 1,
                                                              'median': 1, 'p75': 1, 'max': 1})))
    price_worker.handle({'payload_json': {'sku': sku}})
    result = json.loads((tmp_path / sku / f'{sku}.json').read_text(encoding='utf-8'))
    assert called['n'] == 0  # never even queried comps — skip is early
    assert result.get('ebay_offer', {}).get('price') is None
    assert result['ebay_offer']['price_guard_skipped']['reason'] == 'operator_price_history'
    assert result['ebay_offer']['price_guard_skipped']['operator_price'] == 42.0
    assert all(kw['queue_name'] != 'ebay_stage' for kw in price_worker._enqueued)
    # code-review follow-up: the price_guard_skipped write above is a real
    # item mutation (invariant A7) -- must still enqueue catalog_rebuild
    # even though the early return skips the rest of the function.
    assert any(kw['queue_name'] == 'catalog_rebuild' for kw in price_worker._enqueued)


def test_operator_origin_reprice_overrides_operator_history(price_worker, tmp_path, monkeypatch):
    """The Re-price button's consent signal is the origin='operator' stamp on
    its own job (it already cleared ebay_offer.price/draft.price itself) —
    this must still compute a fresh price even though price_history's last
    entry says 'operator' (that's the price BEING replaced)."""
    sku = 'tgw6'
    _write_item_with_history(tmp_path, sku, [
        {'ts': '2026-07-03T10:00:00Z', 'price': 42.0, 'previous_price': None,
         'stage': None, 'label': 'price edited', 'source': 'operator'},
    ])
    comps = {'count': 3, 'min': 10.0, 'p25': 10.0, 'median': 12.0, 'p75': 14.0, 'max': 15.0}
    monkeypatch.setattr(ebay_price, 'suggest_price', lambda *a, **k: _suggestion(10.0, comps))
    price_worker.handle({'payload_json': {'sku': sku, 'origin': 'operator'}})
    result = json.loads((tmp_path / sku / f'{sku}.json').read_text(encoding='utf-8'))
    assert result['ebay_offer']['price'] is not None
    assert 'price_guard_skipped' not in result.get('ebay_offer', {})


def test_non_operator_history_does_not_trigger_guard(price_worker, tmp_path, monkeypatch):
    """A price_history entry from anything other than a literal 'operator'
    source (e.g. the price reducer, or a worker-attributed caller id) must
    not block the auto-chain — only genuine operator edits do."""
    sku = 'tgw7'
    _write_item_with_history(tmp_path, sku, [
        {'ts': '2026-07-03T10:00:00Z', 'price': 20.0, 'previous_price': 25.0,
         'stage': 1, 'label': 'scheduled reduction', 'source': 'ebay_price_reducer'},
    ])
    comps = {'count': 3, 'min': 10.0, 'p25': 10.0, 'median': 12.0, 'p75': 14.0, 'max': 15.0}
    monkeypatch.setattr(ebay_price, 'suggest_price', lambda *a, **k: _suggestion(10.0, comps))
    price_worker.handle({'payload_json': {'sku': sku}})
    result = json.loads((tmp_path / sku / f'{sku}.json').read_text(encoding='utf-8'))
    assert result['ebay_offer']['price'] is not None


def test_already_priced_item_still_idempotent_with_operator_history(price_worker, tmp_path, monkeypatch):
    """Existing idempotent skip (ebay_offer.price already set) is unaffected
    by the new guard — both paths agree to leave the item alone, for
    different reasons."""
    sku = 'tgw8'
    _write_item_with_history(
        tmp_path, sku,
        [{'ts': '2026-07-03T10:00:00Z', 'price': 42.0, 'previous_price': None,
          'stage': None, 'label': 'price edited', 'source': 'operator'}],
        ebay_offer={'price': 42.0},
    )
    called = {'n': 0}
    monkeypatch.setattr(ebay_price, 'suggest_price',
                        lambda *a, **k: called.__setitem__('n', called['n'] + 1))
    price_worker.handle({'payload_json': {'sku': sku}})
    assert called['n'] == 0
