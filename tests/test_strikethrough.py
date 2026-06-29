"""Tests for PP-STRIKE-001 strikethrough pricing gating.

Two independent gates are covered:

1. tgw.workers.ebay_price.EbayPriceWorker.handle (ebay_price.py:104-115)
   Sets draft['original_retail_price'] = round(msrp, 2) ONLY when
   float(product_lookup.msrp) > launch price. Absent / <= launch / non-numeric
   MSRP leaves the field unset. The MSRP gate lives inline in handle(), so we
   exercise the real method end-to-end with the eBay/DB/quality side-effects
   stubbed (no network, no token, no PostgreSQL) and read back the JSON the
   worker writes via atomic_write_json into tmp_path.

2. tgw.ebay.sync._build_offer_bodies (sync.py:301-311)
   pricingSummary['originalRetailPrice'] is included ONLY when
   draft['original_retail_price'] is present AND
   cfg['raw']['ebay']['strikethrough_enabled'] is True. Omitted when the flag is
   false/absent or the value is non-numeric. _get_merchant_location is stubbed so
   no account API call is made (pattern mirrored from tests/test_ebay_sync.py).
"""

import json

import pytest

import tgw.ebay.sync as sync
import tgw.workers.ebay_price as ebay_price

# ---------------------------------------------------------------------------
# Part 1 — ebay_price worker MSRP gate
# ---------------------------------------------------------------------------


@pytest.fixture
def priced_worker(tmp_path, monkeypatch):
    """An EbayPriceWorker whose handle() runs offline.

    Built via object.__new__ to bypass the DB-touching __init__. Module-level
    dependencies that handle() reaches for are stubbed so the only behaviour
    exercised is the price/MSRP logic; the worker still writes a real JSON file
    into tmp_path via the genuine atomic_write_json, which the test reads back.
    """
    # suggest_price: return a deterministic result. launch is derived as
    # to_99(comps['max'] * 1.10); with max=100.0 -> to_99(110.0) -> 110.99.
    def fake_suggest_price(cfg, title, category_name, category_id,
                           item_condition='', product_lookup=None, **kwargs):
        return {
            'price':            50.0,        # p25 target
            'source':           'browse_api',
            'comps':            {'count': 5, 'min': 40.0, 'p25': 50.0,
                                 'median': 70.0, 'max': 100.0},
            'price_confidence': 'high',
            'queried_at':       '2026-06-07T00:00:00Z',
        }

    monkeypatch.setattr(ebay_price, 'suggest_price', fake_suggest_price)
    # The real to_99 (pure math) stays in place — the launch price the MSRP gate
    # compares against is computed by production code, not stubbed.
    # Silence structured logging (no file/DB writes from log_event).
    monkeypatch.setattr(ebay_price.tgw_logging, 'log_event',
                        lambda *a, **k: None)
    # enqueue_job must not touch PostgreSQL.
    monkeypatch.setattr(ebay_price.state_machine, 'enqueue_job',
                        lambda *a, **k: None)
    # score_draft is imported lazily inside handle() from tgw.listing_quality;
    # stub it there so no real scoring/IO runs.
    import tgw.listing_quality as lq

    class _FakeQuality:
        def to_dict(self):
            return {'stub': True}

    monkeypatch.setattr(lq, 'score_draft', lambda item: _FakeQuality())

    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(ebay_price, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(ebay_price, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker = object.__new__(ebay_price.EbayPriceWorker)
    worker.config = {'itemdata_root': tmp_path, 'pretty': False, 'api_key': 'test-api-key'}
    return worker


def _write_item(tmp_path, sku, msrp, *, condition='good'):
    """Write a minimal item JSON the worker can price, return (path, dict)."""
    product_lookup = {}
    if msrp is not None:
        product_lookup['msrp'] = msrp
    item = {
        'title':     'Acme Thing',
        'condition': condition,
        'draft_listing': {
            'title':       'Acme Thing',
            'category_id': '12345',
            'category_name': 'Widgets',
        },
        'product_lookup': product_lookup,
    }
    item_dir = tmp_path / sku
    item_dir.mkdir(parents=True, exist_ok=True)
    path = item_dir / f'{sku}.json'
    path.write_text(json.dumps(item), encoding='utf-8')
    return path, item


def _run_and_read(worker, tmp_path, sku):
    worker.handle({'payload_json': {'sku': sku}})
    path = tmp_path / sku / f'{sku}.json'
    return json.loads(path.read_text(encoding='utf-8'))


def test_launch_price_is_max_times_110_to_99(priced_worker, tmp_path):
    # Establishes the launch baseline the MSRP gate compares against:
    # to_99(100.0 * 1.10) == to_99(110.0).
    path, _ = _write_item(tmp_path, 'tgw100', msrp=None)
    result = _run_and_read(priced_worker, tmp_path, 'tgw100')
    from tgw.ebay.pricing import to_99
    assert result['ebay_offer']['price'] == to_99(110.0)


def test_msrp_above_launch_sets_original_retail_price(priced_worker, tmp_path):
    # launch == to_99(110.0); MSRP 199.99 clearly exceeds it -> field set, rounded.
    _write_item(tmp_path, 'tgw101', msrp=199.99)
    result = _run_and_read(priced_worker, tmp_path, 'tgw101')
    assert result['draft_listing']['original_retail_price'] == 199.99


def test_msrp_above_launch_string_is_coerced_and_rounded(priced_worker, tmp_path):
    # MSRP may be stored as a string; float() coerces, round(.,2) applies.
    _write_item(tmp_path, 'tgw102', msrp='250.005')
    result = _run_and_read(priced_worker, tmp_path, 'tgw102')
    assert result['draft_listing']['original_retail_price'] == 250.0


def test_msrp_equal_to_launch_not_set(priced_worker, tmp_path):
    # Gate is strict >; MSRP exactly equal to launch must NOT set the field.
    from tgw.ebay.pricing import to_99
    launch = to_99(110.0)
    _write_item(tmp_path, 'tgw103', msrp=launch)
    result = _run_and_read(priced_worker, tmp_path, 'tgw103')
    assert 'original_retail_price' not in result['draft_listing']


def test_msrp_below_launch_not_set(priced_worker, tmp_path):
    _write_item(tmp_path, 'tgw104', msrp=10.0)
    result = _run_and_read(priced_worker, tmp_path, 'tgw104')
    assert 'original_retail_price' not in result['draft_listing']


def test_msrp_absent_not_set(priced_worker, tmp_path):
    _write_item(tmp_path, 'tgw105', msrp=None)
    result = _run_and_read(priced_worker, tmp_path, 'tgw105')
    assert 'original_retail_price' not in result['draft_listing']


def test_msrp_nonnumeric_not_set(priced_worker, tmp_path):
    # float('MSRP unknown') raises ValueError -> caught -> field left unset.
    _write_item(tmp_path, 'tgw106', msrp='MSRP unknown')
    result = _run_and_read(priced_worker, tmp_path, 'tgw106')
    assert 'original_retail_price' not in result['draft_listing']


def test_msrp_zero_is_falsy_and_not_set(priced_worker, tmp_path):
    # msrp_raw == 0 is falsy, so the `if msrp_raw:` guard short-circuits and the
    # try block never runs — field stays unset even though 0 < launch anyway.
    _write_item(tmp_path, 'tgw107', msrp=0)
    result = _run_and_read(priced_worker, tmp_path, 'tgw107')
    assert 'original_retail_price' not in result['draft_listing']


# ---------------------------------------------------------------------------
# Part 2 — sync._build_offer_bodies originalRetailPrice gate
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_ebay_calls(monkeypatch):
    """Stub the one account-API call _build_offer_bodies makes."""
    monkeypatch.setattr(sync, '_get_merchant_location', lambda _cfg: 'LOC-1')


def _cfg(strikethrough=None):
    """cfg with all policy IDs present (no account fallback).

    strikethrough: None -> no ebay block at all; True/False -> set the flag.
    """
    raw = {}
    if strikethrough is not None:
        raw = {'ebay': {'strikethrough_enabled': strikethrough}}
    return {
        'fulfillment_policy_id': 'FC4',
        'payment_policy_id':     'PAY1',
        'return_policy_id':      'RET1',
        'raw': raw,
    }


def _item(original_retail_price=..., **extra):
    draft = {
        'price':          '19.99',
        'title':          'Test Widget',
        'description':    'A test widget',
        'imageUrls':      ['https://example.com/1.jpg'],
        'condition_enum': 'USED_GOOD',
        'category_id':    '12345',
        'quantity':       1,
    }
    if original_retail_price is not ...:
        draft['original_retail_price'] = original_retail_price
    base = {'draft_listing': draft}
    base.update(extra)
    return base


def test_strikethrough_enabled_with_orp_includes_field():
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=199.99))
    assert offer_body['pricingSummary']['originalRetailPrice'] == {
        'currency': 'USD', 'value': '199.99',
    }


def test_orp_value_is_formatted_two_decimals():
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=200))
    assert offer_body['pricingSummary']['originalRetailPrice']['value'] == '200.00'


def test_orp_string_value_is_coerced():
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price='149.9'))
    assert offer_body['pricingSummary']['originalRetailPrice']['value'] == '149.90'


def test_flag_false_omits_field():
    cfg = _cfg(strikethrough=False)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=199.99))
    assert 'originalRetailPrice' not in offer_body['pricingSummary']


def test_flag_absent_omits_field():
    # No ebay block in cfg['raw'] at all -> defaults to disabled.
    cfg = _cfg(strikethrough=None)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=199.99))
    assert 'originalRetailPrice' not in offer_body['pricingSummary']


def test_orp_absent_omits_field_even_when_enabled():
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001', _item())
    assert 'originalRetailPrice' not in offer_body['pricingSummary']


def test_orp_nonnumeric_omits_field():
    # float('lots') raises ValueError -> caught -> field omitted, no raise.
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price='lots'))
    assert 'originalRetailPrice' not in offer_body['pricingSummary']


def test_orp_zero_is_falsy_omits_field():
    # original_retail of 0 is falsy -> `if original_retail and ...` short-circuits.
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=0))
    assert 'originalRetailPrice' not in offer_body['pricingSummary']


def test_pricing_summary_price_present_regardless_of_strikethrough():
    # Sanity: the base price is unaffected by the strikethrough gate.
    cfg = _cfg(strikethrough=True)
    _, offer_body = sync._build_offer_bodies(cfg, 'tgw0001',
                                             _item(original_retail_price=199.99))
    assert offer_body['pricingSummary']['price'] == {
        'currency': 'USD', 'value': '19.99',
    }
