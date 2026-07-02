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
from typing import Any, Dict

import pytest

import tgw.workers.ebay_publish as ebay_publish_mod
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
    return {'itemdata_root': tmp_path, 'raw': {}, 'reprice_stages': [], 'category_price_defaults': {}}


def _write_item(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir()
    (d / f'{sku}.json').write_text(json.dumps(item), encoding='utf-8')


def test_price_mismatch_enqueues_forced_restage(tmp_path, monkeypatch):
    sku = 'tgw1'
    _write_item(tmp_path, sku, _item(sku, draft_price=29.99, staged_price=340.99))
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )

    worker = _worker(_cfg(tmp_path))
    with pytest.raises(RuntimeError, match=r'requested a forced ebay_stage re-sync'):
        worker.handle({'payload_json': {'sku': sku}})

    assert len(calls) == 1
    assert calls[0]['queue_name'] == 'ebay_stage'
    assert calls[0]['payload'] == {'sku': sku, 'force': True}
    assert calls[0]['dedupe_key'] == f'ebay_stage:force:{sku}'


def test_matching_prices_do_not_enqueue_restage(tmp_path, monkeypatch):
    sku = 'tgw2'
    _write_item(tmp_path, sku, _item(sku, draft_price=29.99, staged_price=29.99))
    calls = []
    monkeypatch.setattr(
        ebay_publish_mod.state_machine, 'enqueue_job',
        lambda **k: calls.append(k) or 'job-1',
    )
    monkeypatch.setattr(ebay_publish_mod, 'publish_offer',
                        lambda cfg, offer_id: {'listing_id': 'L1', 'listing_url': 'http://x', 'status': 'PUBLISHED'})
    monkeypatch.setattr(ebay_publish_mod, 'fence_ebay_write', lambda *a, **k: {'ok': True})
    monkeypatch.setattr(ebay_publish_mod, 'fence_patch_item', lambda *a, **k: {'ok': True})

    worker = _worker(_cfg(tmp_path))
    worker.handle({'payload_json': {'sku': sku}})

    assert not any(c['queue_name'] == 'ebay_stage' for c in calls)
