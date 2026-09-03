import hashlib
import json
from types import SimpleNamespace

import pytest
import requests

import tgw.provider_effects as effects
import tgw.workers.ebay_upload as worker_mod
from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import HardFailure
from tgw.quota import QuotaBudgetExceeded


def _setup(tmp_path, monkeypatch):
    sku = 'effect-sku'
    directory = tmp_path / sku
    directory.mkdir()
    item = {'sku': sku}
    (directory / f'{sku}.json').write_text(json.dumps(item))
    photo = directory / 'front.jpg'
    photo.write_bytes(b'raw')
    from tests.conftest import make_fake_patch_item
    monkeypatch.setattr(worker_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(worker_mod.tgw_logging, 'log_event', lambda *a, **k: None)
    worker = object.__new__(worker_mod.EbayUploadWorker)
    worker.config = {
        'itemdata_root': tmp_path,
        'workflow_migration': {
            'ebay_upload_provider_effect': 'workflow',
            'ebay_provider_identity': 'seller-1',
        },
    }
    from tgw.item_mutation import item_generation
    payload = {
        'sku': sku, 'treatment_id': 'ebay-upload', 'treatment_version': '1',
        'graph_id': 'graph', 'goal_profile_id': 'tgw-ebay-listable',
        'goal_profile_version': '1', 'object_generation': item_generation(item),
        'condition_hash': 'condition', 'operator_authority_id': 'authority',
        'pre_authority_condition_hash': 'pre-condition',
    }
    return worker, sku, photo, {'payload_json': payload}


def _effect(state='dispatched', result=None):
    return SimpleNamespace(effect_id='effect-1', state=state, result=result)


def test_workflow_effect_binds_exact_prepared_bytes_and_persists_receipt(
    tmp_path, monkeypatch,
):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    prepared_bytes = b'exact-resized-outbound-bytes'
    prepared = SimpleNamespace(
        photo_path=photo, image_bytes=prepared_bytes, mime='image/jpeg',
    )
    order = []
    monkeypatch.setattr(worker_mod, 'prepare_upload',
                        lambda cfg, path: (order.append('prepare') or prepared))
    captured = {}

    def reserve(**kwargs):
        order.append('reserve')
        captured.update(kwargs)
        return _effect()

    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect', reserve)
    monkeypatch.setattr(worker_mod, 'upload_prepared',
                        lambda cfg, value: (order.append('post') or 'https://eps/front'))
    monkeypatch.setattr(
        effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: (order.append('finish') or _effect('succeeded', kwargs['result'])),
    )

    receipt = worker.handle(job)

    assert order == ['prepare', 'reserve', 'post', 'finish']
    assert captured['operation'] == 'upload-site-hosted-picture:q0'
    assert captured['entity_type'] == 'item-photo'
    assert captured['entity_id'] == f'{sku}:front.jpg'
    assert captured['request']['prepared_content_sha256'] == hashlib.sha256(
        prepared_bytes).hexdigest()
    item = json.loads((tmp_path / sku / f'{sku}.json').read_text())
    assert item['ebay_photos'][0]['provider_effect_id'] == 'effect-1'
    assert receipt['provider_effect_ids'] == ('effect-1',)
    from tgw.item_mutation import item_generation
    assert receipt['evidence']['resulting_generation'] == item_generation(item)


def test_workflow_upload_rejects_fence_response_without_committed_generation(
    tmp_path, monkeypatch,
):
    worker, _sku, photo, job = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(worker_mod, 'prepare_upload', lambda cfg, path: SimpleNamespace(
        photo_path=photo, image_bytes=b'bytes', mime='image/jpeg'))
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: _effect())
    monkeypatch.setattr(worker_mod, 'upload_prepared',
                        lambda *args: 'https://eps/front')
    monkeypatch.setattr(
        effects,
        'finish_provider_effect',
        lambda effect_id, **kwargs: _effect('succeeded', kwargs['result']),
    )
    monkeypatch.setattr(worker_mod, 'fence_patch_item',
                        lambda *args, **kwargs: {'ok': True})

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(job)

    assert caught.value.result['outcome'] == 'reconciliation_required'
    assert caught.value.result['evidence']['reason_code'] == (
        'CANONICAL_PROJECTION_AFTER_UPLOAD_FAILED'
    )


def test_timeout_after_dispatch_is_ambiguous_and_stops(tmp_path, monkeypatch):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(worker_mod, 'prepare_upload', lambda cfg, path: SimpleNamespace(
        photo_path=photo, image_bytes=b'bytes', mime='image/jpeg'))
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: _effect())
    monkeypatch.setattr(worker_mod, 'upload_prepared',
                        lambda *args: (_ for _ in ()).throw(requests.Timeout('lost')))
    finished = []
    monkeypatch.setattr(
        effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: (finished.append(kwargs) or _effect(kwargs['state'])),
    )

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(job)

    assert finished[0]['state'] == 'ambiguous'
    assert caught.value.result['outcome'] == 'ambiguous'
    assert caught.value.result['evidence']['provider_effect_id'] == 'effect-1'


def test_quota_preflight_happens_before_effect_reservation(tmp_path, monkeypatch):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        worker_mod, 'prepare_upload',
        lambda cfg, path: (_ for _ in ()).throw(QuotaBudgetExceeded('quota')),
    )
    calls = []
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(worker_mod.state_machine, 'enqueue_job', lambda **kwargs: None)

    worker.handle(job)

    assert calls == []


def test_quota_after_success_preserves_authority_binding_and_ledger_progress(
    tmp_path, monkeypatch,
):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    second = photo.parent / 'side.jpg'
    second.write_bytes(b'side')
    worker.config['workflow_migration']['ebay_upload_quota_timer'] = 'workflow'

    def prepare(cfg, path):
        return SimpleNamespace(photo_path=path, image_bytes=path.name.encode(),
                               mime='image/jpeg')

    monkeypatch.setattr(worker_mod, 'prepare_upload', prepare)
    reservations = []
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: (reservations.append(kwargs) or _effect()),
    )

    def dispatch(cfg, prepared):
        if prepared.photo_path == second:
            raise worker_mod.UploadQuotaExceeded('provider quota')
        return 'https://eps/front'

    monkeypatch.setattr(worker_mod, 'upload_prepared', dispatch)
    monkeypatch.setattr(effects, 'finish_provider_effect',
                        lambda effect_id, **kwargs: _effect(kwargs['state'], kwargs.get('result')))
    monkeypatch.setattr(
        worker, '_current_workflow_binding',
        lambda sku: (_ for _ in ()).throw(AssertionError('must preserve binding')),
    )

    receipt = worker.handle(job)

    assert receipt['outcome'] == 'transient_backoff'
    assert receipt['timer']['payload']['object_generation'] == job['payload_json'][
        'object_generation'
    ]
    assert receipt['timer']['payload']['condition_hash'] == 'condition'
    assert receipt['timer']['payload']['quota_effect_epochs'] == {'side.jpg': 1}
    assert [call['operation'] for call in reservations] == [
        'upload-site-hosted-picture:q0', 'upload-site-hosted-picture:q0',
    ]
    item = json.loads((tmp_path / sku / f'{sku}.json').read_text())
    assert item.get('ebay_photos', []) == []


def test_workflow_effect_requires_binding_before_provider_or_item_write(
    tmp_path, monkeypatch,
):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    del job['payload_json']['graph_id']
    calls = []
    monkeypatch.setattr(worker_mod, 'prepare_upload',
                        lambda *args: calls.append('prepare'))

    with pytest.raises(HardFailure, match='missing binding'):
        worker.handle(job)

    assert calls == []


def test_invalid_effect_selector_fails_closed(tmp_path, monkeypatch):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    worker.config['workflow_migration']['ebay_upload_provider_effect'] = 'invalid'
    with pytest.raises(HardFailure, match='invalid workflow_migration'):
        worker.handle(job)


def test_provider_success_followed_by_projection_cas_conflict_requires_reconciliation(
    tmp_path, monkeypatch,
):
    worker, sku, photo, job = _setup(tmp_path, monkeypatch)
    item_path = tmp_path / sku / f'{sku}.json'
    prepared = SimpleNamespace(
        photo_path=photo, image_bytes=b'exact-provider-bytes', mime='image/jpeg',
    )
    monkeypatch.setattr(worker_mod, 'prepare_upload', lambda *_args: prepared)
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect', lambda **_kwargs: _effect(),
    )
    monkeypatch.setattr(
        worker_mod, 'upload_prepared', lambda *_args: 'https://eps/front',
    )

    def finish_after_operator_edit(effect_id, **kwargs):
        current = json.loads(item_path.read_text(encoding='utf-8'))
        current['operator_note'] = 'concurrent edit must survive'
        item_path.write_text(json.dumps(current), encoding='utf-8')
        return _effect(kwargs['state'], kwargs.get('result'))

    monkeypatch.setattr(effects, 'finish_provider_effect', finish_after_operator_edit)

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(job)

    receipt = caught.value.result
    assert receipt['outcome'] == 'reconciliation_required'
    assert receipt['evidence']['reason_code'] == (
        'CANONICAL_PROJECTION_AFTER_UPLOAD_FAILED'
    )
    assert receipt['provider_effect_ids'] == ('effect-1',)
    assert receipt['evidence']['provider_effect_id'] == 'effect-1'
    persisted = json.loads(item_path.read_text(encoding='utf-8'))
    assert persisted['operator_note'] == 'concurrent edit must survive'
    assert 'ebay_photos' not in persisted
