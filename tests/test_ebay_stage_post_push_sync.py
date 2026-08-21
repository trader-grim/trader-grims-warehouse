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
from types import SimpleNamespace
from typing import Any, Dict

import tgw.workers.ebay_stage as ebay_stage_mod
import tgw.provider_effects as provider_effects
from tgw.workers.ebay_stage import EbayStageWorker


def _worker(cfg: Dict[str, Any]) -> EbayStageWorker:
    w = EbayStageWorker.__new__(EbayStageWorker)
    w.config = cfg
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {
        'itemdata_root': tmp_path, 'raw': {},
        'workflow_migration': {
            'ebay_stage_provider_effect': 'workflow',
            'ebay_provider_identity': 'test-seller',
        },
    }


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def _job(tmp_path, sku, **payload_extra):
    from tests.conftest import make_governed_ebay_job
    return make_governed_ebay_job(
        tmp_path, sku, treatment_id='ebay-stage',
        goal_profile_id='tgw.ebay_staged', **payload_extra,
    )


def _capture_sync(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ebay_stage_mod, 'enqueue_post_push_sync',
        lambda sku, **kwargs: calls.append((sku, kwargs)) or True,
    )
    return calls


def _patch_common(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ebay_stage_mod.state_machine, 'active_jobs_for_sku', lambda *a, **k: [])
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(
        ebay_stage_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(
        ebay_stage_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(ebay_stage_mod, 'stage_draft', lambda cfg, sku, item: {
        'offer_id': 'off-1',
        'inventory_item': {'product': {'imageUrls': ['http://x/1.jpg']}},
    })
    monkeypatch.setattr(
        provider_effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='stage-effect-1', state='dispatched', result=None),
    )
    monkeypatch.setattr(
        provider_effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result')),
    )


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
    calls = _capture_sync(monkeypatch)
    _patch_common(monkeypatch, tmp_path)

    worker = _worker(_cfg(tmp_path))
    result = worker.handle(_job(tmp_path, sku))

    assert result['receipt_schema_id'] == 'treatment-receipt/v1'
    assert result['outcome'] == 'satisfied'

    assert calls == [(sku, {
        'config': worker.config,
        'source_provider_effect_id': 'stage-effect-1',
    })]


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
    calls = _capture_sync(monkeypatch)
    _patch_common(monkeypatch, tmp_path)

    worker = _worker(_cfg(tmp_path))
    result = worker.handle(_job(
        tmp_path, sku, force=True, origin='operator',
    ))

    assert result['outcome'] == 'satisfied'
    assert calls[0][1]['source_provider_effect_id'] == 'stage-effect-1'
