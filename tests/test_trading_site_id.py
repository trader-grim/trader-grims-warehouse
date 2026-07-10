"""PP-EBAY-MOTORS-001 follow-up (todo #1214, Dave 2026-07-09): every Trading
API call used to hardcode SiteID=0 (EBAY_US) regardless of which
marketplace the target listing actually lives on. trading_call() now takes
an optional site_id, and every public wrapper accepts the same
marketplace_id string already used elsewhere in the codebase
(item['marketplace_id']), translated internally — callers never need to
know Trading API's separate SiteID scheme.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from tgw.apis.ebay.trading import (
    _resolve_site_id,
    end_item,
    get_api_access_rules,
    get_best_offers,
    get_my_ebay_selling,
    get_orders,
    get_store_categories,
    respond_to_best_offer,
    revise_item_pictures,
    revise_item_sku,
)


def test_resolve_site_id_defaults_to_ebay_us():
    assert _resolve_site_id(None) == '0'
    assert _resolve_site_id('') == '0'


def test_resolve_site_id_translates_motors():
    assert _resolve_site_id('EBAY_MOTORS') == '100'


def test_resolve_site_id_translates_ebay_us_explicitly():
    assert _resolve_site_id('EBAY_US') == '0'


def test_resolve_site_id_unknown_marketplace_falls_back_to_default():
    assert _resolve_site_id('EBAY_GB') == '0'


def _capture_site_id(monkeypatch):
    calls = []
    monkeypatch.setattr(
        'tgw.apis.ebay.trading.trading_call',
        lambda cfg, call_name, xml_body, timeout=60, site_id='0': calls.append(site_id) or _fake_root(call_name),
    )
    return calls


class _FakeElement:
    """Minimal stand-in for ET.Element — enough for .find()/.findtext() to
    return None/empty so the wrapper functions' response-parsing code paths
    just see "no results" instead of raising."""

    def find(self, *a, **k):
        return None

    def findtext(self, *a, **k):
        return None

    def findall(self, *a, **k):
        return []


def _fake_root(call_name):
    return _FakeElement()


def test_end_item_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    end_item({}, '12345', marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_end_item_defaults_to_ebay_us(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    end_item({}, '12345')
    assert calls == ['0']


def test_revise_item_sku_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    revise_item_sku({}, '12345', 'tgwNEW', marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_revise_item_pictures_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    revise_item_pictures({}, '12345', ['https://eps/1.jpg'], marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_respond_to_best_offer_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    respond_to_best_offer({}, 'O1', '12345', 'Accept', marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_get_store_categories_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    get_store_categories({}, marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_get_api_access_rules_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    get_api_access_rules({}, marketplace_id='EBAY_MOTORS')
    assert calls == ['100']


def test_get_orders_passes_motors_site_id(monkeypatch):
    from datetime import datetime, timezone
    calls = _capture_site_id(monkeypatch)
    now = datetime.now(timezone.utc)
    list(get_orders({}, now, now, marketplace_id='EBAY_MOTORS'))
    assert calls == ['100']


def test_get_my_ebay_selling_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    list(get_my_ebay_selling({}, marketplace_id='EBAY_MOTORS'))
    assert calls == ['100']


def test_get_best_offers_passes_motors_site_id(monkeypatch):
    calls = _capture_site_id(monkeypatch)
    list(get_best_offers({}, marketplace_id='EBAY_MOTORS'))
    assert calls == ['100']
