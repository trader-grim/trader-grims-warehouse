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
from unittest.mock import patch

import pytest

import tgw.workers.ebay_publish as publish_mod
from tgw.ebay.pricing import to_99
from tgw.errors import TreatmentFailure
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
    monkeypatch.setattr(publish_mod.state_machine, 'active_jobs_for_sku',
                        lambda sku, queues: [])
    monkeypatch.setattr(publish_mod.state_machine, 'enqueue_job',
                        lambda **kw: enqueued.append(kw))
    monkeypatch.setattr(publish_mod.state_machine, 'enqueue_catalog_rebuild',
                        lambda reason: enqueued.append({'catalog_reason': reason}))
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
    # reprice_schedule_enabled=True: these tests exercise the (now opt-in)
    # schedule-minting path — minting is OFF by default since session 42.
    worker.config = {'itemdata_root': tmp_path, 'pretty': False,
                     'reprice_stages': STAGES, 'category_price_defaults': {},
                     'reprice_schedule_enabled': True,
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


def _governed_job(sku):
    return {
        'job_id': 'job-1', 'queue_name': 'ebay_publish',
        'entity_type': 'item', 'entity_id': sku,
        'attempt_count': 1, 'max_attempts': 3,
        'payload_json': {
            'sku': sku, 'entity_id': sku, 'object_id': sku,
            'treatment_id': 'ebay-publish', 'treatment_version': '1',
            'graph_id': 'graph-1', 'goal_profile_id': 'tgw.ebay_listable',
            'goal_profile_version': '1', 'object_generation': 'generation-1',
        },
    }


def test_already_active_item_is_not_republished(publisher, tmp_path, monkeypatch):
    # ebay_get unstubbed here on purpose: it fails (no token/network in test
    # env), _refresh_photo_verify catches that and returns None, so no write
    # happens — proving the skip path doesn't touch anything ELSE besides
    # photo_verify. The refresh behavior itself is pinned separately below.
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


def test_already_active_item_refreshes_photo_verify(publisher, tmp_path, monkeypatch):
    """PP-PHOTOSYNC-001 P1: photo_verify must refresh even when publish is
    skipped as already-Active — an operator ebay_update pushes new photos
    live without ever re-publishing, and photo_verify was found stale (s43,
    tgw202606021133367 showed 9/9 from the original publish while 24 photos
    were actually live) until a manual ebay-pull."""
    monkeypatch.setattr(publish_mod, 'ebay_get', lambda cfg, path: {
        'product': {'imageUrls': ['https://x/1', 'https://x/2', 'https://x/3']}})
    item = _staged_item(
        ebay_listing={'status': 'Active', 'listing_id': '110001',
                      'published_at': '2026-06-01T00:00:00+00:00'},
        ebay_submitted={'inventory_item': {'product': {
            'imageUrls': ['https://x/1', 'https://x/2', 'https://x/3']}}},
    )
    item['ebay_offer']['status'] = 'PUBLISHED'
    _write(tmp_path, 'tgw1', item)
    _run(publisher, 'tgw1')
    after = json.loads((tmp_path / 'tgw1' / 'tgw1.json').read_text(encoding='utf-8'))
    pv = after['ebay_listing']['photo_verify']
    assert pv['submitted_count'] == 3
    assert pv['confirmed_count'] == 3


def test_unstaged_item_retries_not_dead_letter(publisher, tmp_path):
    # Session 42: publish is enqueued alongside the draft chain, so "not staged
    # yet" is a normal in-flight state — retryable RuntimeError, never a
    # HardFailure (which would dead-letter a healthy pipeline).
    _write(tmp_path, 'tgw2', {'sku': 'tgw2', 'draft_listing': {'price': 9.99}})
    with pytest.raises(RuntimeError) as exc_info:
        _run(publisher, 'tgw2')
    assert not isinstance(exc_info.value, HardFailure)
    assert 'not staged' in str(exc_info.value)


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


def test_satisfied_receipt_is_after_projection_and_invalidations(
    publisher, tmp_path, monkeypatch,
):
    _write(tmp_path, 'tgw-order', _staged_item())
    order = []
    from tests.conftest import make_fake_fence_write, make_fake_patch_item
    write = make_fake_fence_write(tmp_path)
    patch_item = make_fake_patch_item(tmp_path)

    def ordered_write(*args, **kwargs):
        order.append('canonical')
        return write(*args, **kwargs)

    def ordered_patch(*args, **kwargs):
        order.append('baseline')
        return patch_item(*args, **kwargs)

    monkeypatch.setattr(publish_mod, 'fence_ebay_write', ordered_write)
    monkeypatch.setattr(publish_mod, 'fence_patch_item', ordered_patch)
    monkeypatch.setattr(
        publish_mod.state_machine, 'enqueue_catalog_rebuild',
        lambda reason: order.append('catalog'),
    )
    monkeypatch.setattr(
        publish_mod, 'enqueue_post_push_sync',
        lambda sku: order.append('sync'),
    )

    receipt = publisher.handle({'payload_json': {'sku': 'tgw-order'}})

    assert order == ['canonical', 'baseline', 'catalog', 'sync']
    assert receipt is None  # legacy unbound completion stays non-treatment


def test_governed_fresh_publish_completes_through_atomic_evaluation_outbox(
    publisher, tmp_path,
):
    sku = 'tgw-governed-fresh'
    _write(tmp_path, sku, _staged_item())
    publisher.owner = 'owner'
    publisher.queue_name = 'ebay_publish'

    with patch.object(publish_mod.state_machine, 'mark_running'), \
         patch.object(
             publish_mod.state_machine,
             'complete_treatment_and_enqueue_evaluation',
             return_value='evaluation-1',
         ) as atomic, \
         patch.object(publish_mod.state_machine, 'mark_succeeded') as ordinary:
        publisher._process(_governed_job(sku))

    assert publisher._published == ['OFF1']
    receipt = atomic.call_args.args[2]
    assert receipt == {
        'receipt_schema_id': 'treatment-receipt/v1',
        'treatment_id': 'ebay-publish', 'treatment_version': '1',
        'graph_id': 'graph-1', 'outcome': 'satisfied',
        'established_conditions': ['published'],
        'artifacts': [f'item:{sku}'],
    }
    ordinary.assert_not_called()


def test_canonical_projection_failure_requires_reconciliation_not_retry(
    publisher, tmp_path, monkeypatch,
):
    path = _write(tmp_path, 'tgw-projection-failure', _staged_item())
    monkeypatch.setattr(
        publish_mod, 'fence_ebay_write',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('canonical projection failed')
        ),
    )

    with pytest.raises(TreatmentFailure, match='reconciliation required') as caught:
        publisher.handle({'payload_json': {
            'sku': 'tgw-projection-failure',
            'treatment_id': 'ebay-publish', 'treatment_version': '1',
            'graph_id': 'graph-1', 'origin': 'operator',
        }})

    assert json.loads(path.read_text()).get('ebay_listing') is None
    assert publisher._published == ['OFF1']
    receipt = caught.value.result
    assert receipt['outcome'] == 'reconciliation_required'
    assert receipt['graph_id'] == 'graph-1'
    assert receipt['evidence'] == {
        'reason_code': 'CANONICAL_PROJECTION_AFTER_PUBLISH_FAILED',
        'provider': 'ebay',
        'offer_id': 'OFF1',
        'listing_id': '110555',
        'listing_url': 'https://www.ebay.com/itm/110555',
        'provider_status': 'PUBLISHED',
        'projection_error': 'RuntimeError: canonical projection failed',
        'operator_origin': True,
    }


def test_second_projection_failure_and_active_replay_never_false_satisfy(
    publisher, tmp_path, monkeypatch,
):
    sku = 'tgw-second-projection'
    _write(tmp_path, sku, _staged_item(
        draft_listing_state='baseline',
        baseline_at='2026-01-01T00:00:00+00:00',
    ))
    publisher.owner = 'owner'
    publisher.queue_name = 'ebay_publish'
    from tests.conftest import make_fake_fence_write
    monkeypatch.setattr(publish_mod, 'fence_ebay_write', make_fake_fence_write(tmp_path))
    monkeypatch.setattr(
        publish_mod, 'fence_patch_item',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError('baseline/history write failed')
        ),
    )
    job = _governed_job(sku)

    with patch.object(publish_mod.state_machine, 'mark_running'), \
         patch.object(publish_mod.state_machine, 'mark_dead_letter') as dead, \
         patch.object(
             publish_mod.state_machine,
             'complete_treatment_and_enqueue_evaluation',
         ) as atomic, \
         patch('tgw.notify.notify'):
        publisher._process(job)
        first = dead.call_args.kwargs['result']
        assert first['outcome'] == 'reconciliation_required'
        assert first['evidence']['reason_code'] == 'POST_PUBLISH_PROJECTION_FAILED'
        atomic.assert_not_called()

        # A forced/manual replay sees Active and a stale baseline marker from
        # before this publish. It must preserve the reconciliation gate, not
        # publish again or claim success.
        publisher._process(job)
        second = dead.call_args.kwargs['result']
        assert second['outcome'] == 'reconciliation_required'
        assert second['evidence']['reason_code'] == 'POST_PUBLISH_PROJECTION_INCOMPLETE'

    assert publisher._published == ['OFF1']
    assert dead.call_count == 2
    atomic.assert_not_called()


def test_replayed_job_after_publish_is_noop(publisher, tmp_path):
    # Lease-expiry replay: run handle twice — second run must not publish
    # again or rewrite the schedule stamped by the first.
    path = _write(tmp_path, 'tgw4', _staged_item())
    _run(publisher, 'tgw4')
    first = path.read_text()
    _run(publisher, 'tgw4')
    assert publisher._published == ['OFF1']    # exactly once
    assert path.read_text() == first


def test_default_publish_mints_no_schedule(publisher, tmp_path):
    # Session 42 (Dave): the pipeline does not change prices unsupervised.
    # Without reprice_schedule_enabled, publish stores an EMPTY schedule and
    # the reducer therefore never touches the item.
    publisher.config = dict(publisher.config)
    publisher.config.pop('reprice_schedule_enabled')
    path = _write(tmp_path, 'tgw-nosched', _staged_item())
    _run(publisher, 'tgw-nosched')
    after = json.loads(path.read_text(encoding='utf-8'))
    assert after['ebay_listing']['status'] == 'Active'
    assert after['reprice_schedule'] == []


def test_publish_history_records_actual_price_not_launch(publisher, tmp_path):
    # Session 42 (Dave, tgw202605060201087): history said $309.99 while the
    # listing was live at $29.99. The event must record what eBay actually has.
    item = _staged_item()
    # consistent draft/staged price of 29.99 — but the schedule's launch stage
    # computes to $30.99 (max→.99); history must record 29.99, not 30.99
    item['draft_listing']['price'] = 29.99
    item['ebay_offer']['price'] = 29.99
    item['ebay_offer']['staged_price'] = 29.99
    path = _write(tmp_path, 'tgw-hist', item)
    _run(publisher, 'tgw-hist')
    after = json.loads(path.read_text(encoding='utf-8'))
    ev = after['price_history'][-1]
    assert ev['label'] == 'Published to eBay'
    assert ev['price'] == 29.99


def test_publish_waits_for_inflight_upstream_stages(publisher, tmp_path, monkeypatch):
    # Session 42 race: 'List on eBay' enqueued publish alongside the draft chain
    # and publish went live with the OLD staged offer. Publish must wait.
    from tgw.queue.worker_base import classify_dead_letter
    monkeypatch.setattr(publish_mod.state_machine, 'active_jobs_for_sku',
                        lambda sku, queues: ['ebay_draft'])
    _write(tmp_path, 'tgw-race', _staged_item())
    with pytest.raises(RuntimeError, match='pipeline steps still running') as exc:
        _run(publisher, 'tgw-race')
    assert publisher._published == []
    action, delay = classify_dead_letter(str(exc.value))
    assert (action, delay) == ('requeue', 60)
