"""Tests for invariant C14 / todo #1467's fix: ebay_stage must enqueue a
targeted ebay_sync follow-up so the local ebay_live full-mirror snapshot
doesn't go stale after a successful stage.

Bug: ebay_stage.py never enqueued ebay_sync at all — only ebay_publish did
(and only after being fixed the same day, todo #1445), and only when
ebay_stage's own already-live republish trigger happened to chain into it.
Since ebay_stage is the worker behind the far more common "Update Listing"
button on an already-live item, this is the actual mechanism behind
"why do we keep having to manually re-sync" (Dave, 2026-07-16). Fix: a
successful stage now unconditionally calls the shared
`tgw.ebay.sync.enqueue_post_push_sync()` helper (also used by
ebay_publish.py — see test_ebay_publish_post_publish_sync.py).

All eBay API calls and state_machine/fence writes are mocked — tests pass
completely offline.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import tgw.workers.ebay_stage as ebay_stage_mod
from tgw.workers.ebay_stage import EbayStageWorker


def _worker(cfg: Dict[str, Any]) -> EbayStageWorker:
    w = EbayStageWorker.__new__(EbayStageWorker)
    w.config = cfg
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {'itemdata_root': tmp_path, 'raw': {}}


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def _capture_enqueue(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ebay_stage_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )
    return calls


def _patch_common(monkeypatch):
    monkeypatch.setattr(
        ebay_stage_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    monkeypatch.setattr(ebay_stage_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(ebay_stage_mod, 'fence_patch_item', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(ebay_stage_mod, 'stage_draft', lambda cfg, sku, item: {
        'offer_id': 'off-1',
        'inventory_item': {'product': {'imageUrls': ['http://x/1.jpg']}},
    })


def test_fresh_stage_enqueues_post_push_sync(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {
            'category_id': '12345', 'price': 19.99, 'title': 'A Widget',
            'imageUrls': ['http://x/1.jpg'],
        },
        'ebay_offer': {},
        'ebay_listing': {},
    })
    calls = _capture_enqueue(monkeypatch)
    _patch_common(monkeypatch)

    worker = _worker(_cfg(tmp_path))
    result = worker.handle({'payload_json': {'sku': sku}})

    # A direct operator stage is a legacy queue job, not a workflow treatment
    # receipt.  It must finish normally rather than be dead-lettered by the
    # queue's strict workflow-receipt validator.
    assert result == {'status': 'staged', 'offer_id': 'off-1', 'entity_id': sku}

    sync_calls = [c for c in calls if c['queue_name'] == 'ebay_sync']
    assert len(sync_calls) == 1
    assert sync_calls[0]['payload'] == {'sku': sku, 'reason': 'post_push'}
    assert sync_calls[0]['dedupe_key'] == f'ebay_sync:post_push:{sku}'


def test_forced_restage_of_live_item_enqueues_post_push_sync(tmp_path, monkeypatch):
    """The "Update Listing" flow: an already-live item, force=True,
    origin=operator — the far more common real-world path than a fresh
    stage."""
    sku = 'tgw2'
    _write_item(tmp_path, sku, {
        'sku': sku,
        'draft_listing': {
            'category_id': '12345', 'price': 19.99, 'title': 'A Widget',
            'imageUrls': ['http://x/1.jpg'],
        },
        'ebay_offer': {'offer_id': 'off-1', 'price': 19.99},
        'ebay_listing': {'status': 'Active', 'listing_id': 'L1'},
    })
    calls = _capture_enqueue(monkeypatch)
    _patch_common(monkeypatch)

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku, 'force': True, 'origin': 'operator'}})

    sync_calls = [c for c in calls if c['queue_name'] == 'ebay_sync']
    assert len(sync_calls) == 1
    assert sync_calls[0]['payload'] == {'sku': sku, 'reason': 'post_push'}
    assert sync_calls[0]['dedupe_key'] == f'ebay_sync:post_push:{sku}'
