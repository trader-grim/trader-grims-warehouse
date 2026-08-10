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
from typing import Any, Dict
from unittest.mock import patch

import psycopg2.errors

import tgw.workers.ebay_publish as ebay_publish_mod
from tgw.workers.ebay_publish import EbayPublishWorker


def _worker(cfg: Dict[str, Any]) -> EbayPublishWorker:
    w = EbayPublishWorker.__new__(EbayPublishWorker)
    w.config = cfg
    w.queue_name = 'ebay_publish'
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {'itemdata_root': tmp_path, 'raw': {}, 'reprice_stages': [], 'category_price_defaults': {}}


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def _capture_enqueue(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )
    return calls


def test_fresh_publish_enqueues_post_publish_sync(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {},
    })
    calls = _capture_enqueue(monkeypatch)
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod, 'publish_offer',
                        lambda cfg, offer_id: {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'})
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_catalog_rebuild', lambda *a, **k: 'catalog')

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku}})

    sync_calls = [c for c in calls if c['queue_name'] == 'ebay_sync']
    assert len(sync_calls) == 1
    assert sync_calls[0]['payload'] == {'sku': sku, 'reason': 'post_push'}
    assert sync_calls[0]['dedupe_key'] == f'ebay_sync:post_push:{sku}'


def test_governed_sync_failure_replay_completes_outbox_without_republish(
    tmp_path, monkeypatch,
):
    sku = 'tgw-sync-repair'
    _write_item(tmp_path, sku, {
        'sku': sku, 'draft_listing': {'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {},
    })
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    published = []
    monkeypatch.setattr(
        ebay_publish_mod, 'publish_offer',
        lambda cfg, offer_id: published.append(offer_id) or {
            'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'},
    )
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_catalog_rebuild', lambda *a, **k: 'catalog')
    sync_attempts = 0

    def fail_once(sku):
        nonlocal sync_attempts
        sync_attempts += 1
        if sync_attempts == 1:
            raise RuntimeError('sync enqueue failed')

    monkeypatch.setattr(ebay_publish_mod, 'enqueue_post_push_sync', fail_once)
    worker = _worker(_cfg(tmp_path))
    worker.owner = 'owner'
    job = {
        'job_id': 'job-sync', 'queue_name': 'ebay_publish',
        'entity_type': 'item', 'entity_id': sku,
        'attempt_count': 1, 'max_attempts': 3,
        'payload_json': {
            'sku': sku, 'entity_id': sku, 'object_id': sku,
            'treatment_id': 'ebay-publish', 'treatment_version': '1',
            'graph_id': 'graph-sync',
            'goal_profile_id': 'tgw.ebay_listable',
            'goal_profile_version': '1', 'object_generation': 'generation-sync',
        },
    }

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
    receipt = atomic.call_args.args[2]
    assert receipt['receipt_schema_id'] == 'treatment-receipt/v1'
    assert receipt['graph_id'] == 'graph-sync'
    assert receipt['outcome'] == 'satisfied'
    ordinary.assert_not_called()


def test_already_active_skip_still_enqueues_post_publish_sync(tmp_path, monkeypatch):
    sku = 'tgw2'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {'status': 'Active', 'listing_id': 'L1'},
    })
    calls = _capture_enqueue(monkeypatch)
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku}})

    sync_calls = [c for c in calls if c['queue_name'] == 'ebay_sync']
    assert len(sync_calls) == 1
    assert sync_calls[0]['payload'] == {'sku': sku, 'reason': 'post_push'}
    assert sync_calls[0]['dedupe_key'] == f'ebay_sync:post_push:{sku}'


def test_post_publish_sync_enqueue_collision_is_non_fatal(tmp_path, monkeypatch):
    """A colliding dedupe_key (sync already queued/running for this SKU) must
    not turn a successful publish into a failed job."""
    sku = 'tgw3'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {'price': 29.99},
        'ebay_offer': {'offer_id': 'off-1', 'staged_price': 29.99, 'price_comps': {}},
        'ebay_listing': {'status': 'Active', 'listing_id': 'L1'},
    })

    def _raise_unique_violation(**k):
        raise psycopg2.errors.UniqueViolation('duplicate dedupe_key')

    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job', _raise_unique_violation)
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku}})  # must not raise
