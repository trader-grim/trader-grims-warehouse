"""todo-1987 — transient provider timeouts on ebay_stage/ebay_publish/ebay_upload
must be requeued with backoff, not permanently dead-lettered.

Before the fix, the three workers' generic 'except Exception as exc:' handlers
captured the real error into error_detail=f'{type(exc).__name__}: {exc}'
(correct, goes to finish_provider_effect) but re-raised a TreatmentFailure
whose *message* dropped the exception type/text ('... outcome ambiguous').
worker_base.classify_dead_letter() only ever sees repr(exc) — the message —
so a transient ReadTimeout/ConnectionError was indistinguishable from any
other ambiguous failure and always dead-lettered.

Incident: SKU tgw202505111031393, ebay_stage — 'stage-draft | ambiguous |
ReadTimeout: HTTPSConnectionPool(host=api.ebay.com, port=443): Read timed
out. (read timeout=30)'.
"""

import json
from types import SimpleNamespace

import pytest

import tgw.provider_effects as effects
import tgw.queue.worker_base as worker_base
import tgw.workers.ebay_publish as publish_mod
import tgw.workers.ebay_stage as stage_mod
import tgw.workers.ebay_upload as upload_mod
from tgw.errors import TreatmentFailure
from tgw.item_mutation import item_generation
from tgw.provider_effects import ProviderEffect
from tgw.queue import state_machine
from tgw.workers.ebay_publish import EbayPublishWorker
from tgw.workers.ebay_stage import EbayStageWorker
from tgw.workers.ebay_upload import EbayUploadWorker


def test_classify_dead_letter_requeues_a_wrapped_readtimeout():
    exc = TreatmentFailure(
        'SKU-1: provider staging outcome ambiguous '
        '(ReadTimeout: HTTPSConnectionPool(host=api.ebay.com, port=443): '
        'Read timed out. (read timeout=30))',
        {'outcome': 'ambiguous'},
    )

    action, delay = worker_base.classify_dead_letter(repr(exc))

    assert (action, delay) == ('requeue', 120)


def test_classify_dead_letter_still_dead_letters_a_true_ambiguity():
    exc = TreatmentFailure(
        'SKU-1: provider staging outcome ambiguous '
        '(KeyError: \'unexpected_field\')',
        {'outcome': 'ambiguous'},
    )

    action, delay = worker_base.classify_dead_letter(repr(exc))

    assert (action, delay) == ('dead_letter', 0)


def _effect(**overrides) -> ProviderEffect:
    fields = dict(
        effect_id='e' * 64, provider='ebay', operation='stage-draft',
        entity_type='item', entity_id='SKU-1', object_generation='generation-1',
        graph_id='graph-1', treatment_id='ebay-stage', treatment_version='1',
        condition_hash='condition-1', request={}, authority={}, state='dispatched',
    )
    fields.update(overrides)
    return ProviderEffect(**fields)


def test_ebay_stage_generic_exception_path_names_the_exception(
    tmp_path, monkeypatch,
):
    item = {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}}
    item_dir = tmp_path / 'SKU-1'
    item_dir.mkdir()
    (item_dir / 'SKU-1.json').write_text(json.dumps(item))

    worker = EbayStageWorker.__new__(EbayStageWorker)
    worker.config = {
        'itemdata_root': tmp_path,
        'workflow_migration': {
            'ebay_stage_provider_effect': 'workflow',
            'ebay_provider_identity': 'ebay:test',
        },
    }
    monkeypatch.setattr(
        stage_mod, 'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_GOOD',
    )
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: _effect(),
    )
    monkeypatch.setattr(
        stage_mod, 'stage_draft',
        lambda *args: (_ for _ in ()).throw(RuntimeError('boom')),
    )
    monkeypatch.setattr(
        effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: _effect(state=kwargs['state']),
    )

    payload = {
        'sku': 'SKU-1', 'entity_id': 'SKU-1',
        'treatment_id': 'ebay-stage', 'treatment_version': '1',
        'graph_id': 'graph-1', 'goal_profile_id': 'tgw.ebay_staged',
        'goal_profile_version': '1',
        'object_generation': item_generation(item),
        'condition_hash': 'condition-1', 'operator_authority_id': 'authority-1',
        'pre_authority_condition_hash': 'pre-1', 'origin': 'operator',
    }

    with pytest.raises(TreatmentFailure) as caught:
        worker._stage_with_provider_effect(payload, 'SKU-1', item, force=False)

    assert 'RuntimeError' in str(caught.value)
    assert 'boom' in str(caught.value)
    action, _delay = worker_base.classify_dead_letter(repr(caught.value))
    assert action == 'dead_letter'  # RuntimeError isn't a listed transient pattern


def test_ebay_publish_generic_exception_path_names_the_exception(
    tmp_path, monkeypatch,
):
    item = {'sku': 'SKU-1', 'ebay_offer': {'offer_id': 'OFF1'}}
    item_dir = tmp_path / 'SKU-1'
    item_dir.mkdir()
    (item_dir / 'SKU-1.json').write_text(json.dumps(item))

    worker = EbayPublishWorker.__new__(EbayPublishWorker)
    worker.config = {
        'itemdata_root': tmp_path,
        'workflow_migration': {
            'ebay_publish_provider_effect': 'workflow',
            'ebay_provider_identity': 'test-seller',
        },
    }
    monkeypatch.setattr(
        publish_mod, 'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_GOOD',
    )
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: _effect(operation='publish-offer'),
    )
    monkeypatch.setattr(
        publish_mod, 'publish_offer',
        lambda *args: (_ for _ in ()).throw(
            ConnectionError('HTTPSConnectionPool: read timed out')
        ),
    )
    monkeypatch.setattr(
        effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: _effect(state=kwargs['state']),
    )

    payload = {
        'sku': 'SKU-1', 'entity_id': 'SKU-1',
        'treatment_id': 'ebay-publish', 'treatment_version': '1',
        'graph_id': 'graph-1', 'goal_profile_id': 'tgw.ebay_listable',
        'goal_profile_version': '1',
        'object_generation': item_generation(item),
        'condition_hash': 'condition-1', 'operator_authority_id': 'authority-1',
        'pre_authority_condition_hash': 'pre-1',
    }

    with pytest.raises(TreatmentFailure) as caught:
        worker._publish_with_provider_effect(payload, 'SKU-1', 'OFF1', item)

    assert 'ConnectionError' in str(caught.value)
    action, delay = worker_base.classify_dead_letter(repr(caught.value))
    assert (action, delay) == ('requeue', 120)


def test_ebay_upload_generic_exception_path_names_the_exception(
    tmp_path, monkeypatch,
):
    sku = 'SKU-1'
    directory = tmp_path / sku
    directory.mkdir()
    (directory / f'{sku}.json').write_text(json.dumps({'sku': sku}))
    photo = directory / 'front.jpg'
    photo.write_bytes(b'raw')

    from tests.conftest import make_fake_patch_item
    monkeypatch.setattr(upload_mod, 'fence_patch_item', make_fake_patch_item(tmp_path))
    monkeypatch.setattr(upload_mod.tgw_logging, 'log_event', lambda *a, **k: None)

    worker = EbayUploadWorker.__new__(EbayUploadWorker)
    worker.config = {
        'itemdata_root': tmp_path,
        'workflow_migration': {
            'ebay_upload_provider_effect': 'workflow',
            'ebay_provider_identity': 'seller-1',
        },
    }
    payload = {
        'sku': sku, 'treatment_id': 'ebay-upload', 'treatment_version': '1',
        'graph_id': 'graph', 'goal_profile_id': 'tgw-ebay-listable',
        'goal_profile_version': '1', 'object_generation': 'generation',
        'condition_hash': 'condition', 'operator_authority_id': 'authority',
        'pre_authority_condition_hash': 'pre-condition',
    }
    job = {'payload_json': payload}

    monkeypatch.setattr(
        upload_mod, 'prepare_upload',
        lambda cfg, path: SimpleNamespace(
            photo_path=path, image_bytes=b'bytes', mime='image/jpeg',
        ),
    )
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: SimpleNamespace(
            effect_id='effect-1', state='dispatched', result=None,
        ),
    )
    monkeypatch.setattr(
        upload_mod, 'upload_prepared',
        lambda *args: (_ for _ in ()).throw(RuntimeError('unexpected provider crash')),
    )
    monkeypatch.setattr(
        effects, 'finish_provider_effect',
        lambda effect_id, **kwargs: SimpleNamespace(
            effect_id=effect_id, state=kwargs['state'], result=kwargs.get('result'),
        ),
    )

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle(job)

    assert 'RuntimeError' in str(caught.value)
    assert 'unexpected provider crash' in str(caught.value)


def _job(attempt_count=1, max_attempts=5):
    return {
        'job_id': 'j1',
        'lease_token': '44444444-4444-4444-8444-444444444444',
        'attempt_count': attempt_count,
        'max_attempts': max_attempts,
        'payload_json': {},
    }


class _TreatmentFailureWorker(worker_base.QueueWorker):
    """Exercises the live _process() dispatch path, not just classify_dead_letter
    in isolation — this is what the previous round's fix never actually wired up:
    TreatmentFailure is a HardFailure subclass, so 'except HardFailure' caught it
    before 'except Exception' (whose classify_dead_letter call) ever ran."""

    message: str

    def __init__(self):
        self.queue_name = 'ebay_stage'
        self.owner = 'test:1'

    def handle(self, job):
        raise TreatmentFailure(self.message, {'outcome': 'ambiguous'})


def test_process_requeues_a_treatment_failure_wrapping_a_readtimeout(monkeypatch):
    calls = {'requeue': None, 'dead_letter': False}
    monkeypatch.setattr(state_machine, 'mark_running', lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine, 'requeue_with_backoff',
        lambda job_id, owner, token, delay, detail: calls.__setitem__('requeue', delay),
    )
    monkeypatch.setattr(
        state_machine, 'mark_dead_letter',
        lambda *a, **k: calls.__setitem__('dead_letter', True),
    )

    w = _TreatmentFailureWorker()
    w.message = (
        'SKU-1: provider staging outcome ambiguous '
        '(ReadTimeout: HTTPSConnectionPool(host=api.ebay.com, port=443): '
        'Read timed out. (read timeout=30))'
    )
    w._process(_job())

    assert calls['requeue'] == 120
    assert calls['dead_letter'] is False


def test_process_still_dead_letters_a_true_ambiguity_treatment_failure(monkeypatch):
    calls = {'requeue': False, 'dead_letter': False}
    monkeypatch.setattr(state_machine, 'mark_running', lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine, 'requeue_with_backoff',
        lambda *a, **k: calls.__setitem__('requeue', True),
    )
    monkeypatch.setattr(
        state_machine, 'mark_dead_letter',
        lambda *a, **k: calls.__setitem__('dead_letter', True),
    )

    w = _TreatmentFailureWorker()
    w.message = "SKU-1: provider staging outcome ambiguous (KeyError: 'unexpected_field')"
    w._process(_job())

    assert calls['dead_letter'] is True
    assert calls['requeue'] is False
