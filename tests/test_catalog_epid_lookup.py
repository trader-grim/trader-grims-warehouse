"""PP-PHOTOSYNC-001 P10 (found live 2026-07-04) — lookup_epid must gracefully
skip on a 400 the same way it does on 401/403: eBay returns 400 (not
401/403) when commerce.catalog.readonly was never granted at all, as opposed
to an expired token for a scope the app does have. Before this fix, any
staging attempt for a barcoded item retried forever on this exact call.
"""

import requests

from tgw.apis.ebay.catalog import lookup_epid


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(response=resp)


def test_epid_lookup_returns_none_on_400(monkeypatch):
    import tgw.apis.ebay.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, 'ebay_get',
                        lambda cfg, path, params=None: (_ for _ in ()).throw(_http_error(400)))
    assert lookup_epid({}, '54199034971') is None


def test_epid_lookup_returns_none_on_401(monkeypatch):
    import tgw.apis.ebay.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, 'ebay_get',
                        lambda cfg, path, params=None: (_ for _ in ()).throw(_http_error(401)))
    assert lookup_epid({}, '54199034971') is None


def test_epid_lookup_returns_none_on_404(monkeypatch):
    import tgw.apis.ebay.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, 'ebay_get',
                        lambda cfg, path, params=None: (_ for _ in ()).throw(_http_error(404)))
    assert lookup_epid({}, '54199034971') is None


def test_epid_lookup_propagates_other_errors(monkeypatch):
    import pytest

    import tgw.apis.ebay.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, 'ebay_get',
                        lambda cfg, path, params=None: (_ for _ in ()).throw(_http_error(500)))
    with pytest.raises(requests.exceptions.HTTPError):
        lookup_epid({}, '54199034971')


def test_epid_lookup_returns_epid_on_success(monkeypatch):
    import tgw.apis.ebay.catalog as catalog_mod
    monkeypatch.setattr(catalog_mod, 'ebay_get', lambda cfg, path, params=None: {
        'productSummaries': [{'epid': '12345678'}]})
    assert lookup_epid({}, '54199034971') == '12345678'
