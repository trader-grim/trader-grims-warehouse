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

import psycopg2.errors

import tgw.workers.ebay_publish as ebay_publish_mod
from tgw.workers.ebay_publish import EbayPublishWorker


def _worker(cfg: Dict[str, Any]) -> EbayPublishWorker:
    w = EbayPublishWorker.__new__(EbayPublishWorker)
    w.config = cfg
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

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku}})

    sync_calls = [c for c in calls if c['queue_name'] == 'ebay_sync']
    assert len(sync_calls) == 1
    assert sync_calls[0]['payload'] == {'sku': sku, 'reason': 'post_push'}
    assert sync_calls[0]['dedupe_key'] == f'ebay_sync:post_push:{sku}'


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
