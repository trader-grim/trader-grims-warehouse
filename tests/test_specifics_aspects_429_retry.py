"""todo #1394 / PP-DEADLETTER-001: the Taxonomy API's
get_item_aspects_for_category call had zero retry on a 429 -- 12 ebay_draft
jobs dead-lettered on it. _fetch_aspects_live() now retries with backoff on
429, respecting Retry-After if eBay sends one, and still raises after the
retry budget is exhausted (no silent empty-result swallow).

All eBay calls are mocked -- tests pass completely offline.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from tgw.apis.ebay import specifics


def _cfg(tmp_path):
    return {'catalog_root': tmp_path}


def _http_429(retry_after=None):
    resp = requests.Response()
    resp.status_code = 429
    if retry_after is not None:
        resp.headers['Retry-After'] = str(retry_after)
    return requests.exceptions.HTTPError('429 Too Many Requests', response=resp)


class TestFetchAspectsLive429Retry:
    def setup_method(self):
        specifics._aspects_mem_cache.clear()

    def test_429_once_then_succeeds(self, tmp_path, monkeypatch):
        success = {'aspects': [{'localizedAspectName': 'Brand', 'aspectConstraint': {},
                                 'aspectValues': []}]}
        calls = []

        def _fake_ebay_get(cfg, path, params=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_429()
            return success

        sleeps = []
        monkeypatch.setattr(specifics.time, 'sleep', lambda s: sleeps.append(s))
        with patch.object(specifics, 'ebay_get', side_effect=_fake_ebay_get), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            result = specifics._fetch_aspects_live(_cfg(tmp_path), 'cat1')

        assert result[0]['name'] == 'Brand'
        assert len(calls) == 2
        assert sleeps == [5]

    def test_persistent_429_raises_after_retry_budget(self, tmp_path, monkeypatch):
        calls = []

        def _fake_ebay_get(cfg, path, params=None):
            calls.append(1)
            raise _http_429()

        sleeps = []
        monkeypatch.setattr(specifics.time, 'sleep', lambda s: sleeps.append(s))
        with patch.object(specifics, 'ebay_get', side_effect=_fake_ebay_get), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            with pytest.raises(requests.exceptions.HTTPError):
                specifics._fetch_aspects_live(_cfg(tmp_path), 'cat1')

        # 3 attempts total (max_retries default), 2 sleeps between them, no hang
        assert len(calls) == specifics._AAC_MAX_RETRIES
        assert len(sleeps) == specifics._AAC_MAX_RETRIES - 1

    def test_200_on_first_try_no_sleep_no_retry(self, tmp_path, monkeypatch):
        success = {'aspects': [{'localizedAspectName': 'Color', 'aspectConstraint': {},
                                 'aspectValues': []}]}
        calls = []

        def _fake_ebay_get(cfg, path, params=None):
            calls.append(1)
            return success

        sleeps = []
        monkeypatch.setattr(specifics.time, 'sleep', lambda s: sleeps.append(s))
        with patch.object(specifics, 'ebay_get', side_effect=_fake_ebay_get), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            result = specifics._fetch_aspects_live(_cfg(tmp_path), 'cat1')

        assert result[0]['name'] == 'Color'
        assert len(calls) == 1
        assert sleeps == []

    def test_non_429_http_error_raises_immediately_no_retry(self, tmp_path, monkeypatch):
        resp = requests.Response()
        resp.status_code = 500
        calls = []

        def _fake_ebay_get(cfg, path, params=None):
            calls.append(1)
            raise requests.exceptions.HTTPError('500 Server Error', response=resp)

        sleeps = []
        monkeypatch.setattr(specifics.time, 'sleep', lambda s: sleeps.append(s))
        with patch.object(specifics, 'ebay_get', side_effect=_fake_ebay_get), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            with pytest.raises(requests.exceptions.HTTPError):
                specifics._fetch_aspects_live(_cfg(tmp_path), 'cat1')

        assert len(calls) == 1
        assert sleeps == []

    def test_retry_after_header_extends_wait(self, tmp_path, monkeypatch):
        success = {'aspects': []}
        calls = []

        def _fake_ebay_get(cfg, path, params=None):
            calls.append(1)
            if len(calls) == 1:
                raise _http_429(retry_after=30)
            return success

        sleeps = []
        monkeypatch.setattr(specifics.time, 'sleep', lambda s: sleeps.append(s))
        with patch.object(specifics, 'ebay_get', side_effect=_fake_ebay_get), \
             patch.object(specifics, 'get_category_tree_id', return_value='0'):
            specifics._fetch_aspects_live(_cfg(tmp_path), 'cat1')

        assert sleeps == [30]  # Retry-After (30) beats the default 5s backoff
