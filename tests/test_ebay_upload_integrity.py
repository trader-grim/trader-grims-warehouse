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
