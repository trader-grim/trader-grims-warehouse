"""A provider condition rejection must return to explicit operator choice.

The former 25021 path silently rewrote the provider inventory item to
USED_EXCELLENT and retried publish.  Category changes now expose a
same-or-worse suggestion but never apply it automatically, so the provider
worker must record the rejection once and stop.

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest
import requests

import tgw.ebay.sync as ebay_sync
import tgw.provider_effects as provider_effects
import tgw.workers.ebay_publish as ebay_publish_mod
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
    from tests.conftest import make_fake_fence_write

    monkeypatch.setattr(ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod.state_machine, 'enqueue_job', lambda **k: 'job-1')
    monkeypatch.setattr(
        ebay_publish_mod,
        'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_VERY_GOOD',
    )
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path),
    )
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


def test_condition_hold_precedes_publish_effect_reservation(tmp_path, monkeypatch):
    sku = 'tgw-condition-hold'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ebay_publish_mod,
        'validate_listing_condition_for_stage',
        ebay_sync.validate_listing_condition_for_stage,
    )
    monkeypatch.setattr(
        ebay_sync,
        'item_condition_required_for_category',
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        ebay_sync,
        'allowed_conditions_for_category',
        lambda *args, **kwargs: [{
            'condition_id': '4000',
            'condition_label': 'Very Good',
            'condition_enum': 'USED_VERY_GOOD',
        }],
    )
    monkeypatch.setattr(
        provider_effects,
        'reserve_and_begin_authorized_effect',
        lambda **kwargs: pytest.fail(
            'publish effect must not be reserved while condition policy is held'
        ),
    )
    monkeypatch.setattr(
        ebay_publish_mod,
        'publish_offer',
        lambda *args, **kwargs: pytest.fail(
            'provider publish must not run while condition policy is held'
        ),
    )

    with pytest.raises(ValueError, match='condition policy is unresolved'):
        _worker(_cfg(tmp_path)).handle(_job(tmp_path, sku))


def test_condition_rejection_is_recorded_without_retry_or_grade_rewrite(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    calls = {'publish': 0}

    def _fake_publish_offer(cfg, offer_id):
        calls['publish'] += 1
        raise _http_error_25021()

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)

    patched = {}
    from tests.conftest import make_fake_patch_item
    fake_patch = make_fake_patch_item(tmp_path)
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item',
        lambda cfg, sku, fields, **kwargs: (
            patched.update(fields) or fake_patch(cfg, sku, fields, **kwargs)
        ),
    )

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(ebay_publish_mod.TreatmentFailure):
        worker.handle(_job(tmp_path, sku))

    assert calls['publish'] == 1
    assert 'draft_listing' not in patched
    assert patched['pipeline_error']['code'] == 'ebay_rejected'
    assert patched['pipeline_error']['field'] == 'condition_enum'


def test_condition_rejection_does_not_consult_a_fallback_label(tmp_path, monkeypatch):
    sku = 'tgw3'
    _write_item(tmp_path, sku, _item(sku, category_id='999'))
    _mock_common(monkeypatch, tmp_path)

    def _fake_publish_offer(cfg, offer_id):
        raise _http_error_25021()

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)

    patched = {}
    from tests.conftest import make_fake_patch_item
    fake_patch = make_fake_patch_item(tmp_path)
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item',
        lambda cfg, sku, fields, **kwargs: (
            patched.update(fields) or fake_patch(cfg, sku, fields, **kwargs)
        ),
    )

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(ebay_publish_mod.TreatmentFailure):
        worker.handle(_job(tmp_path, sku))

    assert 'draft_listing' not in patched
    assert patched['pipeline_error']['code'] == 'ebay_rejected'


def test_repeated_condition_rejection_still_dispatches_only_once(tmp_path, monkeypatch):
    sku = 'tgw4'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    calls = {'publish': 0}

    def _fake_publish_offer(cfg, offer_id):
        calls['publish'] += 1
        raise _http_error_25021()

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _fake_publish_offer)

    patched = {}
    from tests.conftest import make_fake_patch_item
    fake_patch = make_fake_patch_item(tmp_path)
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item',
        lambda cfg, sku, fields, **kwargs: (
            patched.update(fields) or fake_patch(cfg, sku, fields, **kwargs)
        ),
    )

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(ebay_publish_mod.TreatmentFailure):
        worker.handle(_job(tmp_path, sku))

    assert calls['publish'] == 1
    assert 'draft_listing' not in patched
    assert patched['pipeline_error']['code'] == 'ebay_rejected'


def test_non_25021_error_does_not_touch_condition(tmp_path, monkeypatch):
    sku = 'tgw2'
    _write_item(tmp_path, sku, _item(sku))
    _mock_common(monkeypatch, tmp_path)

    def _raise_other(cfg, offer_id):
        resp = _FakeResponse(400, {'errors': [{'errorId': 99999, 'message': 'some other rejection'}]})
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr(ebay_publish_mod, 'publish_offer', _raise_other)
    patched = {}
    from tests.conftest import make_fake_patch_item
    fake_patch = make_fake_patch_item(tmp_path)
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item',
        lambda cfg, sku, fields, **kwargs: (
            patched.update(fields) or fake_patch(cfg, sku, fields, **kwargs)
        ),
    )

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(ebay_publish_mod.TreatmentFailure):
        worker.handle(_job(tmp_path, sku))

    assert patched.get('pipeline_error', {}).get('code') == 'ebay_rejected'
