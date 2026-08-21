"""Tests for the ebay_publish price-drift deadlock fix (session 41).

Bug: when draft_listing.price and ebay_offer.staged_price disagreed, ebay_publish
raised a retryable RuntimeError forever, "waiting for ebay_stage to sync current
price" — but ebay_stage's idempotency guard (offer_id already exists → skip)
never re-stages without an explicit force=True, and nothing was ever sending
that. The two workers waited on each other indefinitely (confirmed live on
tgw202605060201087: staged at $340.99 from a bad category-only comp fallback,
draft corrected to $29.99, stuck retrying for 3+ days). Fix: ebay_publish now
enqueues a forced ebay_stage re-sync itself when it detects the drift.

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict

import pytest

import tgw.workers.ebay_publish as ebay_publish_mod
import tgw.provider_effects as provider_effects
from tgw.errors import TreatmentFailure
from tgw.workers.ebay_publish import EbayPublishWorker


def _item(sku: str, draft_price: float, staged_price: float) -> Dict[str, Any]:
    return {
        'sku': sku,
        'draft_listing': {'price': draft_price},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': staged_price, 'price_comps': {}},
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


def _job(tmp_path, sku):
    from tests.conftest import make_governed_ebay_job
    return make_governed_ebay_job(
        tmp_path, sku, treatment_id='ebay-publish',
    )


def test_price_mismatch_proposes_force_restage_without_direct_enqueue(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, _item(sku, draft_price=29.99, staged_price=340.99))
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )
    # Ordering guard (session 42, ebay_publish.py:148) queries the real DB for
    # in-flight upstream jobs — must be mocked offline like enqueue_job, or
    # this test hits a live Postgres connection under the test's real DSN.
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(_job(tmp_path, sku))

    assert calls == []
    evidence = caught.value.result['evidence']
    assert evidence['reason_code'] == 'PRICE_DRIFT_REQUIRES_FORCE_RESTAGE'
    assert evidence['proposed_treatment'] == 'ebay-stage'
    assert evidence['required_authority_scope'] == 'force-restage'


def test_matching_prices_do_not_enqueue_restage(tmp_path, monkeypatch):
    sku = 'tgw2'
    _write_item(tmp_path, sku, _item(sku, draft_price=29.99, staged_price=29.99))
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )
    # Ordering guard (session 42, ebay_publish.py:148) queries the real DB for
    # in-flight upstream jobs — must be mocked offline like enqueue_job, or
    # this test hits a live Postgres connection under the test's real DSN.
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod, 'publish_offer',
                        lambda cfg, offer_id: {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'})
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='effect-1', state='dispatched', result=None),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result')),
    )
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_catalog_rebuild',
        lambda *a, **k: 'catalog')
    monkeypatch.setattr(ebay_publish_mod, 'enqueue_post_push_sync', lambda *a, **k: True)
    monkeypatch.setattr(
        ebay_publish_mod.EbayPublishWorker, '_refresh_photo_verify',
        lambda *a, **k: None)
    import tgw.ebay.description as description
    monkeypatch.setattr(description, 'build_listing_description', lambda *a, **k: 'desc')

    worker = _worker(_cfg(tmp_path))
    receipt = worker.handle(_job(tmp_path, sku))

    assert not any(c['queue_name'] == 'ebay_stage' for c in calls)
    assert receipt['outcome'] == 'satisfied'
