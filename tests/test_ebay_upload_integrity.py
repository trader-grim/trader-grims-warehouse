"""PP-PHOTOSYNC-001 P1 — ebay_upload completion integrity + quota-retry cap.

The s43 incident: a job whose photos hit a quota wall got logged as
"complete (0 new)" because the old completion guard only failed when the
`uploaded` list was totally empty. An item with ANY pre-existing photos
(the common case) sailed through as a false success, silently truncating the
photo set forever. These tests pin the fix: no exit path may report success
on a shortfall, and a quota-blocked job must eventually dead-letter (visibly)
rather than re-arm forever.

Worker built via object.__new__ to skip the DB-touching __init__
(pattern from tests/test_invariants_stage_guards.py).
"""

import json

import pytest

import tgw.workers.ebay_upload as ebay_upload
from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import HardFailure
from tgw.quota import QuotaBudgetExceeded


def _write_item(tmp_path, sku, ebay_photos=None):
    d = tmp_path / sku
    d.mkdir(parents=True)
    (d / f'{sku}.json').write_text(
        json.dumps({'sku': sku, 'ebay_photos': ebay_photos or []}),
        encoding='utf-8')
    return d


def _make_photos(sku_dir, names):
    for n in names:
        (sku_dir / n).write_bytes(b'fake-jpeg-bytes')


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(ebay_upload.tgw_logging, 'log_event', lambda *a, **k: None)
    enqueued = []
    monkeypatch.setattr(ebay_upload.state_machine, 'enqueue_job',
                        lambda **kw: enqueued.append(kw))
    notified = []
    monkeypatch.setattr('tgw.notify.notify',
                        lambda *a, **k: notified.append((a, k)))

    from tests.conftest import make_fake_patch_item
    monkeypatch.setattr(ebay_upload, 'fence_patch_item', make_fake_patch_item(tmp_path))

    w = object.__new__(ebay_upload.EbayUploadWorker)
    w.config = {'itemdata_root': tmp_path, 'pretty': False}
    w._enqueued = enqueued
    w._notified = notified
    return w


def _job(sku, **payload_extra):
    return {'job_id': '00000000-0000-0000-0000-000000000000',
           'payload_json': {'sku': sku, **payload_extra},
           'attempt_count': 0, 'max_attempts': 5}


def _read(tmp_path, sku):
    return json.loads((tmp_path / sku / f'{sku}.json').read_text(encoding='utf-8'))


def test_quota_wall_never_reports_complete_with_preexisting_photos(worker, tmp_path, monkeypatch):
    """The exact s43 bug: an item with 9 pre-existing photos hits a quota wall
    on all 17 new ones. Must NOT log ebay_upload_complete or return normally
    as if successful — must requeue."""
    sku = 'tgwtest1'
    d = _write_item(tmp_path, sku, ebay_photos=[{'local': str(tmp_path / sku / '1.jpg'), 'url': 'https://x/1'}])
    _make_photos(d, ['1.jpg'] + [f'{i}.jpg' for i in range(2, 19)])  # 1 existing + 17 new

    complete_logged = []
    monkeypatch.setattr(ebay_upload.tgw_logging, 'log_event',
                        lambda ev, **k: complete_logged.append(ev) if ev == 'ebay_upload_complete' else None)

    def always_quota_blocked(cfg, photo):
        raise QuotaBudgetExceeded('quota budget exhausted for ebay_eps: 3500/5000 spent')

    monkeypatch.setattr(ebay_upload, 'upload_photo', always_quota_blocked)

    worker.handle(_job(sku))

    assert complete_logged == [], 'quota-blocked run must never log ebay_upload_complete'
    assert len(worker._enqueued) == 1
    assert worker._enqueued[0]['queue_name'] == 'ebay_upload'
    assert worker._enqueued[0]['payload']['quota_retries'] == 1


def test_quota_retry_cap_dead_letters_visibly(worker, tmp_path, monkeypatch):
    """After QUOTA_RETRY_LIMIT quota-blocked passes, the job must dead-letter
    (HardFailure) with a notify() — visible-and-stuck, not immortal-and-silent."""
    sku = 'tgwtest2'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])

    def always_quota_blocked(cfg, photo):
        raise QuotaBudgetExceeded('quota budget exhausted')

    monkeypatch.setattr(ebay_upload, 'upload_photo', always_quota_blocked)

    with pytest.raises(HardFailure):
        worker.handle(_job(sku, quota_retries=ebay_upload.QUOTA_RETRY_LIMIT))

    assert len(worker._notified) == 1
    assert len(worker._enqueued) == 0, 'must not re-arm past the retry limit'


def test_quota_retry_preserves_operator_origin(worker, tmp_path, monkeypatch):
    sku = 'tgwtest3'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])
    monkeypatch.setattr(ebay_upload, 'upload_photo',
                        lambda cfg, photo: (_ for _ in ()).throw(QuotaBudgetExceeded('x')))

    worker.handle(_job(sku, origin='operator'))

    assert worker._enqueued[0]['payload']['origin'] == 'operator'


def test_workflow_quota_wall_returns_bound_timer_without_self_enqueue(
    worker, tmp_path, monkeypatch,
):
    sku = 'tgwtest-timer'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])
    worker.config['workflow_migration'] = {'ebay_upload_quota_timer': 'workflow'}
    monkeypatch.setattr(ebay_upload.time, 'time', lambda: 1_000.0)
    monkeypatch.setattr(
        ebay_upload, 'upload_photo',
        lambda cfg, photo: (_ for _ in ()).throw(QuotaBudgetExceeded('quota')),
    )
    job = _job(
        sku, origin='operator', treatment_id='ebay-upload',
        treatment_version='1', graph_id='graph-1', object_generation='gen-1',
        condition_hash='condition-1',
    )

    receipt = worker.handle(job)

    assert worker._enqueued == []
    assert receipt['outcome'] == 'transient_backoff'
    assert receipt['receipt_schema_id'] == 'treatment-wait-receipt/v1'
    assert receipt['evidence'] == {
        'reason_code': 'EBAY_UPLOAD_QUOTA_BLOCKED',
        'quota_retries': 1,
        'uploaded_this_attempt': 0,
        'uploaded_total': 0,
        'operator_origin': True,
        'changed': False,
    }
    timer = receipt['timer']
    assert timer['not_before'] == 1_000.0 + 6 * 3600
    assert timer['payload']['object_generation']
    assert timer['payload']['condition_hash']
    assert timer['payload']['origin'] == 'operator'
    assert timer['dedupe_key'] == (
        f"workflow-timer:{timer['payload']['graph_id']}:ebay-upload:quota:1"
    )


def test_workflow_quota_timer_requires_generation_bound_dispatch(
    worker, tmp_path, monkeypatch,
):
    sku = 'tgwtest-unbound'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])
    worker.config['workflow_migration'] = {'ebay_upload_quota_timer': 'workflow'}
    provider_calls = []
    monkeypatch.setattr(ebay_upload, 'upload_photo',
                        lambda cfg, photo: provider_calls.append(photo))
    before = _read(tmp_path, sku)

    with pytest.raises(HardFailure, match='missing bound identity'):
        worker.handle(_job(sku))

    assert provider_calls == []
    assert _read(tmp_path, sku) == before
    assert worker._enqueued == []


def test_invalid_quota_timer_selector_fails_before_provider_or_item_effect(
    worker, tmp_path, monkeypatch,
):
    sku = 'tgwtest-invalid-mode'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])
    worker.config['workflow_migration'] = {'ebay_upload_quota_timer': 'surprise'}
    provider_calls = []
    monkeypatch.setattr(ebay_upload, 'upload_photo',
                        lambda cfg, photo: provider_calls.append(photo))
    before = _read(tmp_path, sku)

    with pytest.raises(HardFailure, match='invalid workflow_migration'):
        worker.handle(_job(sku))

    assert provider_calls == []
    assert _read(tmp_path, sku) == before
    assert worker._enqueued == []


def test_workflow_quota_timer_preserves_partial_progress_and_rebinds_snapshot(
    worker, tmp_path, monkeypatch,
):
    sku = 'tgwtest-partial-timer'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg', '2.jpg'])
    worker.config['workflow_migration'] = {'ebay_upload_quota_timer': 'workflow'}

    def first_then_quota(cfg, photo):
        if photo.name == '2.jpg':
            raise QuotaBudgetExceeded('quota')
        return 'https://x/1.jpg'

    monkeypatch.setattr(ebay_upload, 'upload_photo', first_then_quota)
    receipt = worker.handle(_job(
        sku, treatment_id='ebay-upload', treatment_version='1',
        graph_id='prior-graph', object_generation='prior-generation',
        condition_hash='prior-condition',
    ))

    assert len(_read(tmp_path, sku)['ebay_photos']) == 1
    assert receipt['evidence']['uploaded_this_attempt'] == 1
    assert receipt['evidence']['changed'] is True
    assert receipt['timer']['payload']['object_generation'] != 'prior-generation'
    assert receipt['timer']['payload']['graph_id'] != 'prior-graph'


def test_workflow_quota_cap_is_structured_operator_attention(
    worker, tmp_path, monkeypatch,
):
    sku = 'tgwtest-workflow-cap'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg'])
    worker.config['workflow_migration'] = {'ebay_upload_quota_timer': 'workflow'}
    monkeypatch.setattr(
        ebay_upload, 'upload_photo',
        lambda cfg, photo: (_ for _ in ()).throw(QuotaBudgetExceeded('quota')),
    )

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(_job(
            sku, quota_retries=ebay_upload.QUOTA_RETRY_LIMIT,
            treatment_id='ebay-upload', treatment_version='1',
            graph_id='graph-1', object_generation='gen-1',
            condition_hash='condition-1', origin='operator',
        ))

    evidence = caught.value.result['evidence']
    assert evidence['reason_code'] == 'EBAY_UPLOAD_QUOTA_RETRY_LIMIT'
    assert evidence['operator_attention_required'] is True
    assert evidence['operator_origin'] is True
    assert worker._enqueued == []


def test_partial_photo_failure_raises_not_silently_succeeds(worker, tmp_path, monkeypatch):
    """A non-quota per-photo failure (e.g. a corrupt file) must not be masked
    either — any shortfall raises so worker_base retries, never silent success."""
    sku = 'tgwtest4'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg', '2.jpg'])

    def one_fails(cfg, photo):
        if photo.name == '2.jpg':
            raise RuntimeError('UploadSiteHostedPictures failed: corrupt image')
        return 'https://x/1'

    monkeypatch.setattr(ebay_upload, 'upload_photo', one_fails)

    with pytest.raises(RuntimeError, match='1/2 new photos uploaded'):
        worker.handle(_job(sku))

    # The one that DID succeed must be persisted, not lost.
    doc = _read(tmp_path, sku)
    assert len(doc['ebay_photos']) == 1


def test_full_success_still_reports_complete(worker, tmp_path, monkeypatch):
    """Sanity check: the happy path is unchanged."""
    sku = 'tgwtest5'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg', '2.jpg'])
    monkeypatch.setattr(ebay_upload, 'upload_photo',
                        lambda cfg, photo: f'https://x/{photo.name}')

    events = []
    monkeypatch.setattr(ebay_upload.tgw_logging, 'log_event',
                        lambda ev, **k: events.append((ev, k)))

    worker.handle(_job(sku))

    complete_events = [k for ev, k in events if ev == 'ebay_upload_complete']
    assert len(complete_events) == 1
    assert complete_events[0]['total'] == 2
    assert complete_events[0]['new'] == 2
    doc = _read(tmp_path, sku)
    assert len(doc['ebay_photos']) == 2


def test_no_photos_on_disk_persists_durable_finding(worker, tmp_path):
    """Invariant C11 (todo #1303): the no-photos guard must persist a durable
    finding on the item JSON (queryable by catalog-verify), not just log and
    silently succeed leaving the item stalled forever."""
    sku = 'tgwtest7'
    _write_item(tmp_path, sku)  # no photos created on disk

    worker.handle(_job(sku))

    doc = _read(tmp_path, sku)
    blocked = doc.get('ebay_upload_blocked')
    assert blocked is not None, 'no-photos guard must persist ebay_upload_blocked'
    assert blocked['reason'] == 'no_photos_on_disk'
    assert blocked['detected_at']


def test_full_success_clears_prior_no_photos_finding(worker, tmp_path, monkeypatch):
    """Self-healing: once photos are added back and upload succeeds, the
    prior ebay_upload_blocked finding must be cleared, not left stale."""
    sku = 'tgwtest8'
    d = _write_item(tmp_path, sku)
    # Simulate a previously-recorded finding from an earlier no-photos pass.
    doc = _read(tmp_path, sku)
    doc['ebay_upload_blocked'] = {'reason': 'no_photos_on_disk', 'detected_at': 'x'}
    (d / f'{sku}.json').write_text(json.dumps(doc), encoding='utf-8')

    _make_photos(d, ['1.jpg'])
    monkeypatch.setattr(ebay_upload, 'upload_photo',
                        lambda cfg, photo: f'https://x/{photo.name}')

    worker.handle(_job(sku))

    doc = _read(tmp_path, sku)
    assert not doc.get('ebay_upload_blocked'), 'finding must be cleared on full success'


def test_network_error_persists_partial_progress_before_reraising(worker, tmp_path, monkeypatch):
    """A prior latent bug: on a network error mid-loop, photos that already
    succeeded THIS pass were never persisted, so a retry would re-upload them
    from scratch (wasting quota). Must persist before re-raising."""
    import requests

    sku = 'tgwtest6'
    d = _write_item(tmp_path, sku)
    _make_photos(d, ['1.jpg', '2.jpg'])

    def first_ok_second_times_out(cfg, photo):
        if photo.name == '2.jpg':
            raise requests.exceptions.Timeout('timed out')
        return 'https://x/1'

    monkeypatch.setattr(ebay_upload, 'upload_photo', first_ok_second_times_out)

    with pytest.raises(requests.exceptions.Timeout):
        worker.handle(_job(sku))

    doc = _read(tmp_path, sku)
    assert len(doc['ebay_photos']) == 1, 'the photo that succeeded before the timeout must be persisted'
