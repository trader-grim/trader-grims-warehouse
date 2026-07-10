"""audit#1143 (todo #1182): trading_call()'s 429/call-limit retry+backoff
logic previously lived only inline in get_best_offers(), even though
get_orders() and get_my_ebay_selling() hit the exact same trading_call()
choke point and could 429 just as easily. All three now share
_trading_call_retrying().

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tgw.apis.ebay.trading import get_best_offers, get_my_ebay_selling, get_orders


class _FakeElement:
    """Minimal stand-in for ET.Element -- .find()/.findtext() return
    None/empty so wrapper parsing code just sees 'no results'."""

    def find(self, *a, **k):
        return None

    def findtext(self, *a, **k):
        return None

    def findall(self, *a, **k):
        return []


def _make_flaky_trading_call(fail_times, monkeypatch):
    calls = {'n': 0}

    def _fake(cfg, call_name, xml_body, timeout=60, site_id='0'):
        calls['n'] += 1
        if calls['n'] <= fail_times:
            raise RuntimeError('Trading API call failed: 429 rate limited')
        return _FakeElement()

    monkeypatch.setattr('tgw.apis.ebay.trading.trading_call', _fake)
    monkeypatch.setattr('tgw.apis.ebay.trading.time.sleep', lambda *_a, **_k: None)
    return calls


def test_get_orders_retries_on_429(monkeypatch):
    calls = _make_flaky_trading_call(2, monkeypatch)
    now = datetime.now(timezone.utc)
    list(get_orders({}, now, now))
    assert calls['n'] == 3


def test_get_my_ebay_selling_retries_on_429(monkeypatch):
    calls = _make_flaky_trading_call(2, monkeypatch)
    list(get_my_ebay_selling({}))
    assert calls['n'] == 3


def test_get_best_offers_still_retries_on_429(monkeypatch):
    calls = _make_flaky_trading_call(2, monkeypatch)
    list(get_best_offers({}))
    assert calls['n'] == 3


def test_get_orders_raises_after_exhausting_retries(monkeypatch):
    _make_flaky_trading_call(99, monkeypatch)
    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError):
        list(get_orders({}, now, now))


def test_get_orders_does_not_retry_non_rate_limit_errors(monkeypatch):
    calls = {'n': 0}

    def _fake(cfg, call_name, xml_body, timeout=60, site_id='0'):
        calls['n'] += 1
        raise RuntimeError('Trading API call failed: some other error')

    monkeypatch.setattr('tgw.apis.ebay.trading.trading_call', _fake)
    monkeypatch.setattr('tgw.apis.ebay.trading.time.sleep', lambda *_a, **_k: None)

    now = datetime.now(timezone.utc)
    with pytest.raises(RuntimeError):
        list(get_orders({}, now, now))
    assert calls['n'] == 1
