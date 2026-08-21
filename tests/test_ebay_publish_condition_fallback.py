"""audit#1143 #1168 — ebay_publish.py's 25021 condition-rejection fallback.

Bug: when eBay rejects publish with errorId 25021 (category doesn't support
the granular condition), the worker retried with condition=USED_EXCELLENT
via a direct inventory_item PUT and succeeded — but never wrote the corrected
condition back into draft_listing. The next ebay_stage re-stage would
resubmit the original (rejected) condition_enum from draft_listing, get
25021 again, and repeat this same fallback dance forever — local record
permanently disagreeing with what's actually live on eBay.

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import requests

import tgw.workers.ebay_publish as ebay_publish_mod
import tgw.provider_effects as provider_effects
from tgw.workers.ebay_publish import EbayPublishWorker


def _item(sku: str, category_id: str = '12345') -> Dict[str, Any]:
    return {
        'sku': sku,
        'ebay_category_id': category_id,
        'draft_listing': {
            'price': 29.99,
            'condition_id': '4000',
            'condition_label': 'Very Good',
            'condition_enum': 'USED_VERY_GOOD',
        },
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {},
    }


def _worker(cfg: Dict[str, Any]) -> EbayPublishWorker:
    w = EbayPublishWorker.__new__(EbayPublishWorker)
    w.config = cfg
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {
        'itemdata_root': tmp_path, 'raw': {}, 'reprice_stages': [],
        'category_price_defaults': {},
        'workflow_migration': {
            'ebay_publish_provider_effect': 'workflow',
            'ebay_provider_identity': 'test-seller',
        },
    }


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


class _FakeResponse:
    def __init__(self, status_code: int, body: Dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def _http_error_25021() -> requests.exceptions.HTTPError:
    resp = _FakeResponse(400, {'errors': [{'errorId': 25021, 'message': 'condition not allowed'}]})
    return requests.exceptions.HTTPError(response=resp)


def _mock_common(monkeypatch, tmp_path):
    monkeypatch.setattr(ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod.state_machine, 'enqueue_job', lambda **k: 'job-1')
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(ebay_publish_mod, 'enqueue_post_push_sync', lambda *a, **k: True)
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='condition-effect', state='dispatched', result=None),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result')),
    )


def _job(tmp_path, sku):
    from tests.conftest import make_governed_ebay_job
    return make_governed_ebay_job(
        tmp_path, sku, treatment_id='ebay-publish', origin='operator')


def test_condition_fallback_writes_corrected_condition_back_to_draft_listing(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    calls = {'publish': 0}

    def _fake_publish_offer(cfg, offer_id):
        calls['publish'] += 1
        if calls['publish'] == 1:
            raise _http_error_25021()
        return {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'}

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)
    monkeypatch.setattr(ebay_publish_mod, 'ebay_put', lambda *a, **k: {'ok': True})
    # No cached policy for this category — condition_label falls back to 'Used'.
    monkeypatch.setattr(ebay_publish_mod.conditions, 'allowed_conditions_for_category',
                        lambda cfg, cat_id: [])

    patched = {}
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item',
                        lambda cfg, sku, fields: patched.update(fields) or {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job(tmp_path, sku))

    assert calls['publish'] == 2
    assert patched['draft_listing']['condition_enum'] == 'USED_EXCELLENT'
    assert patched['draft_listing']['condition_id'] == '3000'
    assert patched['draft_listing']['condition_label'] == 'Used'


def test_condition_fallback_uses_category_specific_label_when_available(tmp_path, monkeypatch):
    # code-review follow-up: the label must come from conditions.py's
    # canonical per-category lookup, not a hardcoded 'Used' string, when
    # the category's real eBay-returned description differs.
    sku = 'tgw3'
    _write_item(tmp_path, sku, _item(sku, category_id='999'))
    _mock_common(monkeypatch, tmp_path)

    def _fake_publish_offer(cfg, offer_id):
        if not hasattr(_fake_publish_offer, 'called'):
            _fake_publish_offer.called = True
            raise _http_error_25021()
        return {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'}

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)
    monkeypatch.setattr(ebay_publish_mod, 'ebay_put', lambda *a, **k: {'ok': True})

    def _fake_allowed(cfg, cat_id):
        assert cat_id == '999'
        return [{'condition_id': '3000', 'condition_label': 'Pre-owned - Good', 'condition_enum': 'USED_EXCELLENT'}]

    monkeypatch.setattr(ebay_publish_mod.conditions, 'allowed_conditions_for_category', _fake_allowed)

    patched = {}
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item',
                        lambda cfg, sku, fields: patched.update(fields) or {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job(tmp_path, sku))

    assert patched['draft_listing']['condition_label'] == 'Pre-owned - Good'


def test_condition_fallback_label_lookup_failure_falls_back_safely(tmp_path, monkeypatch):
    # A broken/unavailable condition-policy lookup must not fail the
    # already-succeeded publish — falls back to the safe default label.
    sku = 'tgw4'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    calls = {'publish': 0}

    def _fake_publish_offer(cfg, offer_id):
        calls['publish'] += 1
        if calls['publish'] == 1:
            raise _http_error_25021()
        return {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'}

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)
    monkeypatch.setattr(ebay_publish_mod, 'ebay_put', lambda *a, **k: {'ok': True})

    def _raise(cfg, cat_id):
        raise RuntimeError('condition policy cache unavailable')

    monkeypatch.setattr(ebay_publish_mod.conditions, 'allowed_conditions_for_category', _raise)

    patched = {}
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item',
                        lambda cfg, sku, fields: patched.update(fields) or {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job(tmp_path, sku))

    assert calls['publish'] == 2
    assert patched['draft_listing']['condition_label'] == 'Used'


def test_non_25021_error_does_not_touch_condition(tmp_path, monkeypatch):
    sku = 'tgw2'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    def _raise_other(cfg, offer_id):
        resp = _FakeResponse(400, {'errors': [{'errorId': 99999, 'message': 'some other rejection'}]})
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _raise_other)
    patched = {}
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item',
                        lambda cfg, sku, fields: patched.update(fields) or {'ok': True})

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(ebay_publish_mod.TreatmentFailure):
        worker.handle(_job(tmp_path, sku))

    assert patched.get('pipeline_error', {}).get('code') == 'ebay_rejected'
