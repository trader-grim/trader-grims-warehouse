"""Invariants C3/D5 (docs/invariants.md) — ebay_publish idempotency.

The operator gate lives in cmd_publish (only UNPUBLISHED offers are enqueued),
but jobs can also reach the queue directly (tgw enqueue-sku, MCP tgw_enqueue,
dead-letter requeue). The worker itself must therefore skip items that are
already live instead of re-publishing and overwriting the reprice_schedule
(which would reset the markdown clock). Guard added 2026-06-10.

Worker built via object.__new__ (pattern from tests/test_strikethrough.py);
publish_offer and the lazily-imported description builder are stubbed.
"""

import json

import pytest

import tgw.workers.ebay_publish as publish_mod
from tgw.ebay.pricing import to_99
from tgw.queue.worker_base import HardFailure

STAGES = [
    {'days': 0,  'percentile': 'max', 'label': 'launch'},
    {'days': 3,  'percentile': 'p75', 'label': 'retail'},
    {'days': 17, 'percentile': 'p25', 'label': 'move'},
]
COMPS = {'count': 5, 'min': 5.0, 'p25': 10.0, 'median': 15.0,
         'p75': 20.0, 'max': 30.0}


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_mod.tgw_logging, 'log_event', lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(publish_mod.state_machine, 'enqueue_job',
                        lambda **kw: enqueued.append(kw))
    published = []

    def fake_publish_offer(cfg, offer_id):
        published.append(offer_id)
        return {'listing_id': '110555',
                'listing_url': 'https://www.ebay.com/itm/110555',
                'status': 'PUBLISHED'}

    monkeypatch.setattr(publish_mod, 'publish_offer', fake_publish_offer)
    # build_listing_description is imported lazily inside handle()
    import tgw.ebay.description as desc
    monkeypatch.setattr(desc, 'build_listing_description',
                        lambda item, cfg: 'stub description')

    worker = object.__new__(publish_mod.EbayPublishWorker)
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    monkeypatch.setattr(publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(publish_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    worker.config = {'itemdata_root': tmp_path, 'pretty': False,
                     'reprice_stages': STAGES, 'category_price_defaults': {},
                     'api_key': 'test-api-key'}
    worker._published = published
    worker._enqueued = enqueued
    return worker


def _write(tmp_path, sku, item):
    d = tmp_path / sku
    d.mkdir(parents=True)
    path = d / f'{sku}.json'
    path.write_text(json.dumps(item), encoding='utf-8')
    return path


def _staged_item(**extra):
    item = {
        'sku': 'tgw1',
        'ebay_category_id': '12345',
        'draft_listing': {'title': 'Acme Thing', 'price': to_99(30.0)},
        'ebay_offer': {'offer_id': 'OFF1', 'status': 'UNPUBLISHED',
                       'price': to_99(30.0), 'price_comps': COMPS},
    }
    item.update(extra)
    return item


def _run(worker, sku):
    worker.handle({'payload_json': {'sku': sku}})


def test_already_active_item_is_not_republished(publisher, tmp_path):
    item = _staged_item(
        ebay_listing={'status': 'Active', 'listing_id': '110001',
                      'published_at': '2026-06-01T00:00:00+00:00'},
        reprice_schedule=[{'stage': 0, 'label': 'launch', 'price': 30.99,
                           'due_at': '2026-06-01T00:00:00+00:00',
                           'done_at': '2026-06-01T00:00:00+00:00'}],
    )
    item['ebay_offer']['status'] = 'PUBLISHED'
    path = _write(tmp_path, 'tgw1', item)
    before = path.read_text()
    _run(publisher, 'tgw1')
    assert publisher._published == []          # no eBay call
    assert path.read_text() == before          # reprice_schedule clock untouched


def test_unstaged_item_is_hard_failure(publisher, tmp_path):
    _write(tmp_path, 'tgw2', {'sku': 'tgw2', 'draft_listing': {'price': 9.99}})
    with pytest.raises(HardFailure):
        _run(publisher, 'tgw2')


def test_publish_writes_listing_and_freezes_schedule(publisher, tmp_path):
    path = _write(tmp_path, 'tgw3', _staged_item())
    _run(publisher, 'tgw3')
    after = json.loads(path.read_text(encoding='utf-8'))
    assert publisher._published == ['OFF1']
    assert after['ebay_listing']['status'] == 'Active'
    assert after['ebay_listing']['listing_id'] == '110555'
    assert after['ebay_offer']['status'] == 'PUBLISHED'
    schedule = after['reprice_schedule']
    assert [s['label'] for s in schedule] == ['launch', 'retail', 'move']
    assert schedule[0]['done_at'] is not None          # launch stamped at publish
    assert schedule[1]['done_at'] is None
    assert [s['price'] for s in schedule] == [to_99(30.0), 20.0, 10.0]


def test_replayed_job_after_publish_is_noop(publisher, tmp_path):
    # Lease-expiry replay: run handle twice — second run must not publish
    # again or rewrite the schedule stamped by the first.
    path = _write(tmp_path, 'tgw4', _staged_item())
    _run(publisher, 'tgw4')
    first = path.read_text()
    _run(publisher, 'tgw4')
    assert publisher._published == ['OFF1']    # exactly once
    assert path.read_text() == first
