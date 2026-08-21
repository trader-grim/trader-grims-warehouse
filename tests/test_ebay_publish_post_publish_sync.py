"""Tests for todo #1445's fix: ebay_publish must enqueue a targeted ebay_sync
follow-up so the local ebay_live full-mirror snapshot (and photo_verify)
doesn't go stale after a plain republish.

Bug: only http_server.py's apply_revision (LISTEDITOR revision/apply) ever
enqueued ebay_sync. A normal publish/republish through ebay_publish.py never
did, confirmed live 2026-07-16 on tgw202605040949058 — 6 ebay_stage/
ebay_publish cycles ran with zero ebay_sync jobs alongside them, leaving
ebay_live 36+ hours stale relative to the latest publish. Fix: both success
paths (fresh publish, and the idempotent already-Active skip) now enqueue a
per-SKU ebay_sync job via the shared `tgw.ebay.sync.enqueue_post_push_sync()`
helper (also used by ebay_stage.py — see test_ebay_stage_post_push_sync.py —
pulled up to a shared home the same day once the duplication was noticed,
invariant C14).

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import patch

import tgw.workers.ebay_publish as ebay_publish_mod
import tgw.provider_effects as provider_effects
from tgw.workers.ebay_publish import EbayPublishWorker


def _worker(cfg: Dict[str, Any]) -> EbayPublishWorker:
    w = EbayPublishWorker.__new__(EbayPublishWorker)
    w.config = cfg
    w.queue_name = 'ebay_publish'
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


def _capture_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod, 'enqueue_post_push_sync',
        lambda sku, **kwargs: calls.append((sku, kwargs)) or True,
    )
    return calls


def _patch_common(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(
        ebay_publish_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_catalog_rebuild',
        lambda *a, **k: 'catalog')
    monkeypatch.setattr(
        ebay_publish_mod.EbayPublishWorker, '_refresh_photo_verify',
        lambda *a, **k: None)
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='publish-effect-1', state='dispatched', result=None),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result')),
    )
    monkeypatch.setattr(
        provider_effects, 'validate_succeeded_authorized_effect',
        lambda **kwargs: SimpleNamespace(result={'listing_id': 'L1'}),
    )
    import tgw.ebay.description as description
    monkeypatch.setattr(description, 'build_listing_description', lambda *a, **k: 'desc')


def test_fresh_publish_enqueues_post_publish_sync(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'title': 'Widget', 'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {},
    })
    calls = _capture_sync(monkeypatch)
    _patch_common(tmp_path, monkeypatch)
    monkeypatch.setattr(ebay_publish_mod, 'publish_offer',
                        lambda cfg, offer_id: {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'})
    worker = _worker(_cfg(tmp_path))
    receipt = worker.handle(_job(tmp_path, sku))

    assert calls == [(sku, {
        'config': worker.config,
        'source_provider_effect_id': 'publish-effect-1',
    })]
    assert receipt['outcome'] == 'satisfied'


def test_governed_sync_failure_replay_completes_outbox_without_republish(
    tmp_path, monkeypatch,
):
    sku = 'tgw-sync-repair'
    _write_item(tmp_path, sku, {
        'sku': sku, 'draft_listing': {'title': 'Widget', 'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {},
    })
    _patch_common(tmp_path, monkeypatch)
    published = []
    monkeypatch.setattr(
        ebay_publish_mod, 'publish_offer',
        lambda cfg, offer_id: published.append(offer_id) or {
            'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'},
    )
    sync_attempts = 0

    def fail_once(sku, **kwargs):
        nonlocal sync_attempts
        sync_attempts += 1
        if sync_attempts == 1:
            raise RuntimeError('sync enqueue failed')

    monkeypatch.setattr(ebay_publish_mod, 'enqueue_post_push_sync', fail_once)
    worker = _worker(_cfg(tmp_path))
    worker.owner = 'owner'
    job = _job(tmp_path, sku)
    job.update({'job_id': 'job-sync',
                'lease_token': '11111111-1111-4111-8111-111111111111',
                'queue_name': 'ebay_publish', 'attempt_count': 1})

    with patch.object(ebay_publish_mod.state_machine, 'mark_running'), \
         patch.object(
             ebay_publish_mod.state_machine, 'mark_failed',
             return_value='retry_wait',
         ) as failed, \
         patch.object(
             ebay_publish_mod.state_machine,
             'complete_treatment_and_enqueue_evaluation',
             return_value='evaluation-sync',
         ) as atomic, \
         patch.object(ebay_publish_mod.state_machine, 'mark_succeeded') as ordinary:
        worker._process(job)
        atomic.assert_not_called()
        failed.assert_called_once()
        worker._process(job)

    assert published == ['off-1']
    assert sync_attempts == 2
    assert atomic.call_args.args[2] == job['lease_token']
    receipt = atomic.call_args.args[3]
    assert receipt['receipt_schema_id'] == 'treatment-receipt/v1'
    assert receipt['graph_id'] == job['payload_json']['graph_id']
    assert receipt['entity_id'] == sku
    assert receipt['condition_hash'] == job['payload_json']['condition_hash']
    assert receipt['outcome'] == 'satisfied'
    ordinary.assert_not_called()


def test_already_active_skip_still_enqueues_post_publish_sync(tmp_path, monkeypatch):
    sku = 'tgw2'
    published_at = '2026-08-20T00:00:00+00:00'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'title': 'Widget', 'price': 29.99},
        'draft_listing_state': 'baseline', 'baseline_at': published_at,
        'ebay_offer': {
            'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {},
            'published_at': published_at,
        },
        'ebay_listing': {
            'status': 'Active', 'listing_id': 'L1',
            'published_at': published_at, 'provider_effect_id': 'publish-effect-1',
        },
    })
    calls = _capture_sync(monkeypatch)
    _patch_common(tmp_path, monkeypatch)

    worker = _worker(_cfg(tmp_path))
    receipt = worker.handle(_job(tmp_path, sku))

    assert calls[0][1]['source_provider_effect_id'] == 'publish-effect-1'
    assert receipt['outcome'] == 'satisfied'


def test_post_publish_sync_governed_dedupe_success_is_non_fatal(tmp_path, monkeypatch):
    """The governed dispatcher represents exact dedupe as successful."""
    sku = 'tgw3'
    published_at = '2026-08-20T00:00:00+00:00'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'title': 'Widget', 'price': 29.99},
        'draft_listing_state': 'baseline', 'baseline_at': published_at,
        'ebay_offer': {
            'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {},
            'published_at': published_at,
        },
        'ebay_listing': {
            'status': 'Active', 'listing_id': 'L1',
            'published_at': published_at, 'provider_effect_id': 'publish-effect-1',
        },
    })
    calls = _capture_sync(monkeypatch)
    _patch_common(tmp_path, monkeypatch)

    worker = _worker(_cfg(tmp_path))
    worker.handle(_job(tmp_path, sku))
    assert len(calls) == 1
