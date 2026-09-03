from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import tgw.provider_effects as effects
import tgw.workers.ebay_stage as stage_mod
from tgw.errors import TreatmentFailure
from tgw.item_mutation import (
    item_generation,
    item_write_lock,
    resolve_item_mutation_journal_root,
)
from tgw.provider_effects import ProviderEffect, ProviderEffectReconciliationRequired
from tgw.workers.ebay_stage import EbayStageWorker


@pytest.fixture(autouse=True)
def _condition_preflight_passes(monkeypatch):
    """Provider-effect tests isolate reservation/replay from policy lookup."""
    monkeypatch.setattr(
        stage_mod,
        'validate_listing_condition_for_stage',
        lambda *args, **kwargs: 'USED_GOOD',
    )


def _payload(item: dict | None = None) -> dict:
    payload = {
        'sku': 'SKU-1', 'entity_id': 'SKU-1',
        'treatment_id': 'ebay-stage', 'treatment_version': '1',
        'graph_id': 'graph-1', 'goal_profile_id': 'tgw.ebay_staged',
        'goal_profile_version': '1', 'object_generation': 'generation-1',
        'condition_hash': 'condition-1', 'operator_authority_id': 'authority-1',
        'pre_authority_condition_hash': 'pre-1', 'origin': 'operator',
    }
    if item is not None:
        from tgw.item_mutation import item_generation
        payload['object_generation'] = item_generation(item)
    return payload


def _effect(*, state: str = 'dispatched', result=None) -> ProviderEffect:
    return ProviderEffect(
        effect_id='e' * 64, provider='ebay', operation='stage-draft',
        entity_type='item', entity_id='SKU-1', object_generation='generation-1',
        graph_id='graph-1', treatment_id='ebay-stage', treatment_version='1',
        condition_hash='condition-1',
        request={'sku': 'SKU-1', 'content_identity': 'content-1'},
        authority={'authority_id': 'authority-1'}, state=state, result=result,
    )


def _worker() -> EbayStageWorker:
    worker = EbayStageWorker.__new__(EbayStageWorker)
    worker.config = {
        'workflow_migration': {
            'ebay_stage_provider_effect': 'workflow',
            'ebay_provider_identity': 'ebay:test',
        },
    }
    return worker


def _bound_worker(tmp_path, item: dict) -> tuple[EbayStageWorker, dict]:
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    sku = item['sku']
    item_dir = tmp_path / sku
    item_dir.mkdir(parents=True, exist_ok=True)
    (item_dir / f'{sku}.json').write_text(json.dumps(item))
    return worker, _payload(item)


def test_workflow_stage_is_one_reserved_provider_sequence(tmp_path, monkeypatch):
    item = {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}}
    worker, payload = _bound_worker(tmp_path, item)
    calls = []
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: calls.append(('reserve', kwargs)) or _effect())
    monkeypatch.setattr(stage_mod, 'stage_draft', lambda *args: calls.append(
        ('provider', args)) or {'offer_id': 'OFF-1', 'inventory_item': {}})
    monkeypatch.setattr(effects, 'finish_provider_effect',
                        lambda effect_id, **kwargs: calls.append(
                            ('finish', kwargs)) or _effect(state=kwargs['state'],
                                                          result=kwargs.get('result')))

    result, effect_id, content_identity = worker._stage_with_provider_effect(
        payload, 'SKU-1', item,
        force=False,
    )

    assert [call[0] for call in calls] == ['reserve', 'provider', 'finish']
    assert calls[0][1]['authority_scope'] == 'stage'
    assert calls[0][1]['request']['force'] is False
    assert calls[0][1]['request']['content_identity'] == content_identity
    assert result['offer_id'] == 'OFF-1'
    assert effect_id == 'e' * 64


def test_condition_preflight_fails_before_provider_effect_reservation(
    tmp_path, monkeypatch
):
    item = {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}}
    worker, payload = _bound_worker(tmp_path, item)
    monkeypatch.setattr(
        stage_mod,
        'validate_listing_condition_for_stage',
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError('explicit listing condition required')
        ),
    )
    monkeypatch.setattr(
        effects,
        'reserve_and_begin_authorized_effect',
        lambda **kwargs: pytest.fail('invalid condition must not reserve an effect'),
    )

    with pytest.raises(ValueError, match='explicit listing condition required'):
        worker._stage_with_provider_effect(payload, 'SKU-1', item, force=False)


def test_succeeded_restart_repairs_locally_without_second_provider_call(tmp_path, monkeypatch):
    item = {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}}
    worker, payload = _bound_worker(tmp_path, item)
    succeeded = _effect(
        state='succeeded', result={
            'offer_id': 'OFF-1', 'inventory_item': {},
            'ebay_environment': 'production',
            'endpoint': 'https://api.ebay.com',
        },
    )
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: succeeded)
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))

    result, effect_id, _ = worker._stage_with_provider_effect(
        payload, 'SKU-1', item,
        force=False,
    )

    assert result['offer_id'] == 'OFF-1'
    assert effect_id == succeeded.effect_id
    assert provider_calls == []


def test_unfinished_or_changed_content_never_blind_replays(tmp_path, monkeypatch):
    item = {'sku': 'SKU-1', 'draft_listing': {'title': 'changed'}}
    worker, payload = _bound_worker(tmp_path, item)
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: (_ for _ in ()).throw(
            ProviderEffectReconciliationRequired(_effect(state='ambiguous'))
        ),
    )
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))

    with pytest.raises(TreatmentFailure) as caught:
        worker._stage_with_provider_effect(
            payload, 'SKU-1', item,
            force=False,
        )

    assert caught.value.result['outcome'] == 'reconciliation_required'
    assert provider_calls == []


def test_default_selector_is_governed():
    worker = EbayStageWorker.__new__(EbayStageWorker)
    worker.config = {'raw': {}}
    assert worker._provider_effect_mode() == 'workflow'


def test_forced_restage_has_distinct_identity_and_stronger_scope(tmp_path, monkeypatch):
    item = {'sku': 'SKU-1'}
    worker, payload = _bound_worker(tmp_path, item)
    captured = {}
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect',
        lambda **kwargs: captured.update(kwargs) or _effect(),
    )
    monkeypatch.setattr(stage_mod, 'stage_draft', lambda *args: {
        'offer_id': 'OFF-1', 'inventory_item': {},
    })
    monkeypatch.setattr(effects, 'finish_provider_effect',
                        lambda *args, **kwargs: _effect(state='succeeded'))

    worker._stage_with_provider_effect(
        payload, 'SKU-1', item, force=True,
    )

    assert captured['authority_scope'] == 'force-restage'
    assert captured['request']['force'] is True


def test_success_persists_exact_stage_evidence_before_satisfied_receipt(
    tmp_path, monkeypatch,
):
    from tests.conftest import make_fake_fence_write

    sku = 'SKU-1'
    item_dir = tmp_path / sku
    item_dir.mkdir()
    item = {
        'sku': sku, 'epid': 'EPID-1',
        'draft_listing': {'title': 'A', 'category_id': '123', 'price': 10.0,
                          'imageUrls': ['https://example/image.jpg']},
        'ebay_offer': {}, 'ebay_listing': {},
    }
    (item_dir / f'{sku}.json').write_text(json.dumps(item))
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku',
                        lambda *args: [])
    writes = []
    fake_write = make_fake_fence_write(tmp_path)

    def capture_write(*args, **kwargs):
        writes.append(kwargs)
        return fake_write(*args, **kwargs)

    monkeypatch.setattr(stage_mod, 'fence_ebay_write', capture_write)
    sync_calls = []
    monkeypatch.setattr(
        stage_mod,
        'enqueue_post_push_sync',
        lambda value, **kwargs: sync_calls.append((value, kwargs)),
    )
    monkeypatch.setattr(
        worker, '_stage_with_provider_effect',
        lambda *args, **kwargs: (
            {'offer_id': 'OFF-1', 'inventory_item': {'sku': sku}},
            'e' * 64, 'content-1',
        ),
    )

    worker.owner = 'owner-1'
    worker.queue_name = 'ebay_stage'
    job = {
        'job_id': 'stage-fresh',
        'lease_token': '22222222-2222-4222-8222-222222222222',
        'queue_name': 'ebay_stage', 'entity_type': 'item', 'entity_id': sku,
        'attempt_count': 1, 'max_attempts': 3,
        'payload_json': {
            **_payload(item), 'goal_profile_id': 'tgw.ebay_listable',
        },
    }
    monkeypatch.setattr(stage_mod.state_machine, 'mark_running', lambda *_: True)
    completed = []
    monkeypatch.setattr(
        stage_mod.state_machine, 'complete_treatment_and_enqueue_evaluation',
        lambda job_id, owner, token, receipt: completed.append(
            (job_id, owner, token, receipt)
        ) or 'evaluation-stage',
    )
    worker._process(job)
    receipt = completed[0][3]

    assert writes[0]['ebay_offer']['provider_effect_id'] == 'e' * 64
    assert writes[0]['ebay_offer']['stage_content_identity'] == 'content-1'
    assert receipt['outcome'] == 'satisfied'
    assert completed[0][2] == job['lease_token']
    assert receipt['goal_profile_id'] == 'tgw.ebay_listable'
    assert receipt['goal_profile_version'] == _payload()['goal_profile_version']
    assert receipt['object_generation'] == _payload(item)['object_generation']
    assert receipt['condition_hash'] == _payload()['condition_hash']
    assert receipt['entity_id'] == sku
    assert receipt['evidence']['provider_effect_id'] == 'e' * 64
    item = json.loads((item_dir / f'{sku}.json').read_text())
    from tgw.item_mutation import item_generation
    assert receipt['evidence']['resulting_generation'] == item_generation(item)
    assert sync_calls == []


def test_workflow_stage_rejects_fence_response_without_committed_generation(
    tmp_path, monkeypatch,
):
    sku = 'SKU-1'
    item_dir = tmp_path / sku
    item_dir.mkdir()
    item = {
        'sku': sku, 'epid': 'EPID-1',
        'draft_listing': {'title': 'A', 'category_id': '123', 'price': 10.0,
                          'imageUrls': ['https://example/image.jpg']},
        'ebay_offer': {}, 'ebay_listing': {},
    }
    (item_dir / f'{sku}.json').write_text(json.dumps(item))
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku',
                        lambda *args: [])
    monkeypatch.setattr(stage_mod, 'fence_ebay_write',
                        lambda *args, **kwargs: {'ok': True})
    monkeypatch.setattr(
        worker, '_stage_with_provider_effect',
        lambda *args, **kwargs: (
            {'offer_id': 'OFF-1', 'inventory_item': {'sku': sku}},
            'e' * 64, 'content-1',
        ),
    )

    with pytest.raises(stage_mod.HardFailure, match='committed generation'):
        worker.handle({
            'entity_type': 'item', 'entity_id': sku,
            'payload_json': {
                **_payload(item), 'goal_profile_id': 'tgw.ebay_listable',
            },
        })


def test_already_staged_governed_replay_syncs_before_satisfied(
    tmp_path, monkeypatch,
):
    from tgw.workflow.operator_authority import listing_content_identity

    sku = 'SKU-1'
    staged_at = '2026-08-22T12:00:00+00:00'
    inventory_item = {'sku': sku}
    item = {
        'sku': sku, 'draft_listing': {'title': 'A'},
        'ebay_offer': {
            'offer_id': 'OFF-1', 'status': 'UNPUBLISHED',
            'staged_at': staged_at, 'ebay_environment': 'production',
        },
        'ebay_submitted': {
            'inventory_item': inventory_item, 'staged_at': staged_at,
        },
        'ebay_listing': {},
    }
    content_identity = listing_content_identity(item)
    item['ebay_offer'].update({
        'provider_effect_id': 'e' * 64,
        'stage_content_identity': content_identity,
    })
    item_dir = tmp_path / sku
    item_dir.mkdir()
    (item_dir / f'{sku}.json').write_text(json.dumps(item))
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku',
                        lambda *args: [])
    succeeded = _effect(
        state='succeeded', result={
            'offer_id': 'OFF-1', 'inventory_item': inventory_item,
            'ebay_environment': 'production', 'endpoint': 'https://api.ebay.com',
        },
    )
    monkeypatch.setattr(effects, 'validate_succeeded_authorized_effect',
                        lambda **kwargs: succeeded)
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))
    sync_calls = []
    monkeypatch.setattr(stage_mod, 'enqueue_post_push_sync',
                        lambda value, **kwargs: sync_calls.append((value, kwargs)))

    receipt = worker.handle({
        'entity_type': 'item', 'entity_id': sku, 'payload_json': _payload(item),
    })

    assert provider_calls == []
    assert sync_calls == [(sku, {
        'config': worker.config, 'source_provider_effect_id': 'e' * 64,
    })]
    assert receipt['outcome'] == 'satisfied'


def test_stage_crash_after_projection_replays_satisfied_without_provider_dispatch(
    tmp_path, monkeypatch,
):
    from tests.conftest import make_fake_fence_write

    sku = 'SKU-1'
    data_root = tmp_path / 'data'
    itemdata_root = data_root / 'ItemData'
    item = {
        'sku': sku, 'epid': 'EPID-1',
        'draft_listing': {
            'title': 'A', 'category_id': '123', 'price': 10.0,
            'imageUrls': ['https://example/image.jpg'],
        },
        'ebay_offer': {}, 'ebay_listing': {},
    }
    item_dir = itemdata_root / sku
    item_dir.mkdir(parents=True)
    item_path = item_dir / f'{sku}.json'
    item_path.write_text(json.dumps(item), encoding='utf-8')
    worker = _worker()
    worker.config.update({
        'data_root': data_root,
        'itemdata_root': itemdata_root,
    })
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku', lambda *_: [])
    monkeypatch.setattr(
        stage_mod, 'fence_ebay_write', make_fake_fence_write(itemdata_root),
    )
    monkeypatch.setattr(stage_mod, 'enqueue_post_push_sync', lambda *_a, **_k: None)

    provider_calls = []
    monkeypatch.setattr(
        stage_mod,
        'stage_draft',
        lambda *_args: provider_calls.append('stage') or {
            'offer_id': 'OFF-1', 'inventory_item': {'sku': sku},
        },
    )
    dispatched = _effect()
    monkeypatch.setattr(
        effects, 'reserve_and_begin_authorized_effect', lambda **_kwargs: dispatched,
    )
    succeeded = _effect(state='succeeded', result={
        'offer_id': 'OFF-1', 'inventory_item': {'sku': sku},
        'ebay_environment': 'production', 'endpoint': 'https://api.ebay.com',
    })
    monkeypatch.setattr(
        effects, 'finish_provider_effect', lambda *_args, **_kwargs: succeeded,
    )
    validated = []
    monkeypatch.setattr(
        effects,
        'validate_succeeded_authorized_effect',
        lambda **kwargs: validated.append(kwargs) or succeeded,
    )
    payload = {
        **_payload(item), 'goal_profile_id': 'tgw.ebay_listable',
    }
    job = {'entity_type': 'item', 'entity_id': sku, 'payload_json': payload}

    first = worker.handle(job)
    projected = json.loads(item_path.read_text(encoding='utf-8'))
    assert item_generation(projected) != payload['object_generation']
    second = worker.handle(job)

    assert first['outcome'] == second['outcome'] == 'satisfied'
    assert provider_calls == ['stage']
    assert len(validated) == 1
    assert validated[0]['authority_binding']['content_identity'] == (
        projected['ebay_offer']['stage_content_identity']
    )
    assert second['evidence']['resulting_generation'] == item_generation(projected)

    changed_content = json.loads(json.dumps(projected))
    changed_content['draft_listing']['title'] = 'changed after stage'
    item_path.write_text(json.dumps(changed_content), encoding='utf-8')
    with pytest.raises(TreatmentFailure) as content_mismatch:
        worker.handle(job)
    assert content_mismatch.value.result['evidence']['reason_code'] == (
        'PROVIDER_EFFECT_REPLAY_INVALID'
    )

    changed_environment = json.loads(json.dumps(projected))
    changed_environment['ebay_offer']['ebay_environment'] = 'sandbox'
    item_path.write_text(json.dumps(changed_environment), encoding='utf-8')
    with pytest.raises(TreatmentFailure) as environment_mismatch:
        worker.handle(job)
    assert environment_mismatch.value.result['evidence']['reason_code'] == (
        'PROVIDER_EFFECT_REPLAY_INVALID'
    )
    assert provider_calls == ['stage']


@pytest.mark.parametrize(
    ('force', 'origin', 'reason_code'),
    [
        (False, 'operator', 'ACTIVE_LISTING_REQUIRES_FORCE'),
        (True, 'workflow', 'OPERATOR_ORIGIN_REQUIRED'),
    ],
)
def test_governed_live_pre_effect_guards_are_structured_not_success(
    tmp_path, monkeypatch, force, origin, reason_code,
):
    sku = 'SKU-1'
    item_dir = tmp_path / sku
    item_dir.mkdir()
    item = {
        'sku': sku, 'ebay_listing': {'status': 'Active', 'listing_id': 'L1'},
        'ebay_offer': {'offer_id': 'OFF-1', 'status': 'PUBLISHED'},
    }
    (item_dir / f'{sku}.json').write_text(json.dumps(item))
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku',
                        lambda *args: [])
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))
    payload = _payload(item)
    payload.update({'force': force, 'origin': origin})

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle({
            'entity_type': 'item', 'entity_id': sku, 'payload_json': payload,
        })

    assert caught.value.result['outcome'] == 'failed'
    assert caught.value.result['evidence']['reason_code'] == reason_code
    assert provider_calls == []


def test_stage_reservation_serializes_with_every_canonical_writer(
    tmp_path, monkeypatch,
):
    sku = 'SKU-1'
    data_root = tmp_path / 'data'
    itemdata_root = data_root / 'ItemData'
    item = {'sku': sku, 'draft_listing': {'title': 'A'}}
    item_dir = itemdata_root / sku
    item_dir.mkdir(parents=True)
    item_path = item_dir / f'{sku}.json'
    item_path.write_text(json.dumps(item), encoding='utf-8')
    worker = _worker()
    worker.config.update({
        'data_root': data_root,
        'itemdata_root': itemdata_root,
    })
    payload = _payload(item)

    lock_attempted = threading.Event()
    reserve_called = threading.Event()
    provider_calls = []
    original_item_write_lock = stage_mod.item_write_lock

    def observed_item_write_lock(journal_root, locked_sku):
        lock_attempted.set()
        return original_item_write_lock(journal_root, locked_sku)

    monkeypatch.setattr(stage_mod, 'item_write_lock', observed_item_write_lock)
    monkeypatch.setattr(
        effects,
        'reserve_and_begin_authorized_effect',
        lambda **_kwargs: reserve_called.set() or _effect(),
    )
    monkeypatch.setattr(
        stage_mod,
        'stage_draft',
        lambda *_args: provider_calls.append(True) or {
            'offer_id': 'OFF-1', 'inventory_item': {},
        },
    )

    shared_root = resolve_item_mutation_journal_root(worker.config)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with item_write_lock(shared_root, sku):
            future = pool.submit(
                worker._stage_with_provider_effect,
                payload,
                sku,
                item,
                force=False,
            )
            assert lock_attempted.wait(timeout=1)
            assert not reserve_called.wait(timeout=0.2)
            newer = {**item, 'operator_note': 'must survive'}
            item_path.write_text(json.dumps(newer), encoding='utf-8')

        with pytest.raises(stage_mod.HardFailure, match='generation conflict'):
            future.result(timeout=2)

    assert not reserve_called.is_set()
    assert provider_calls == []
    assert json.loads(item_path.read_text(encoding='utf-8')) == newer
