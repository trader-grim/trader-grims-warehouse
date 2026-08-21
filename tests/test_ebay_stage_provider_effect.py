from __future__ import annotations

import json

import pytest

import tgw.provider_effects as effects
import tgw.workers.ebay_stage as stage_mod
from tgw.errors import TreatmentFailure
from tgw.provider_effects import ProviderEffect, ProviderEffectReconciliationRequired
from tgw.workers.ebay_stage import EbayStageWorker


def _payload() -> dict:
    return {
        'sku': 'SKU-1', 'entity_id': 'SKU-1',
        'treatment_id': 'ebay-stage', 'treatment_version': '1',
        'graph_id': 'graph-1', 'goal_profile_id': 'tgw.ebay_staged',
        'goal_profile_version': '1', 'object_generation': 'generation-1',
        'condition_hash': 'condition-1', 'operator_authority_id': 'authority-1',
        'pre_authority_condition_hash': 'pre-1', 'origin': 'operator',
    }


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


def test_workflow_stage_is_one_reserved_provider_sequence(monkeypatch):
    worker = _worker()
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
        _payload(), 'SKU-1', {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}},
        force=False,
    )

    assert [call[0] for call in calls] == ['reserve', 'provider', 'finish']
    assert calls[0][1]['authority_scope'] == 'stage'
    assert calls[0][1]['request']['force'] is False
    assert calls[0][1]['request']['content_identity'] == content_identity
    assert result['offer_id'] == 'OFF-1'
    assert effect_id == 'e' * 64


def test_succeeded_restart_repairs_locally_without_second_provider_call(monkeypatch):
    worker = _worker()
    succeeded = _effect(
        state='succeeded', result={'offer_id': 'OFF-1', 'inventory_item': {}},
    )
    monkeypatch.setattr(effects, 'reserve_and_begin_authorized_effect',
                        lambda **kwargs: succeeded)
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))

    result, effect_id, _ = worker._stage_with_provider_effect(
        _payload(), 'SKU-1', {'sku': 'SKU-1', 'draft_listing': {'title': 'A'}},
        force=False,
    )

    assert result['offer_id'] == 'OFF-1'
    assert effect_id == succeeded.effect_id
    assert provider_calls == []


def test_unfinished_or_changed_content_never_blind_replays(monkeypatch):
    worker = _worker()
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
            _payload(), 'SKU-1',
            {'sku': 'SKU-1', 'draft_listing': {'title': 'changed'}},
            force=False,
        )

    assert caught.value.result['outcome'] == 'reconciliation_required'
    assert provider_calls == []


def test_default_selector_is_governed():
    worker = EbayStageWorker.__new__(EbayStageWorker)
    worker.config = {'raw': {}}
    assert worker._provider_effect_mode() == 'workflow'


def test_forced_restage_has_distinct_identity_and_stronger_scope(monkeypatch):
    worker = _worker()
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
        _payload(), 'SKU-1', {'sku': 'SKU-1'}, force=True,
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
    (item_dir / f'{sku}.json').write_text(json.dumps({
        'sku': sku, 'epid': 'EPID-1',
        'draft_listing': {'title': 'A', 'category_id': '123', 'price': 10.0,
                          'imageUrls': ['https://example/image.jpg']},
        'ebay_offer': {}, 'ebay_listing': {},
    }))
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
            **_payload(), 'goal_profile_id': 'tgw.ebay_listable',
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
    assert receipt['object_generation'] == _payload()['object_generation']
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
    (item_dir / f'{sku}.json').write_text(json.dumps({
        'sku': sku, 'epid': 'EPID-1',
        'draft_listing': {'title': 'A', 'category_id': '123', 'price': 10.0,
                          'imageUrls': ['https://example/image.jpg']},
        'ebay_offer': {}, 'ebay_listing': {},
    }))
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
                **_payload(), 'goal_profile_id': 'tgw.ebay_listable',
            },
        })


def test_already_staged_governed_replay_syncs_before_satisfied(
    tmp_path, monkeypatch,
):
    from tgw.workflow.operator_authority import listing_content_identity

    sku = 'SKU-1'
    item = {
        'sku': sku, 'draft_listing': {'title': 'A'},
        'ebay_offer': {'offer_id': 'OFF-1'}, 'ebay_listing': {},
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
        state='succeeded', result={'offer_id': 'OFF-1', 'inventory_item': {}},
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
        'entity_type': 'item', 'entity_id': sku, 'payload_json': _payload(),
    })

    assert provider_calls == []
    assert sync_calls == [(sku, {
        'config': worker.config, 'source_provider_effect_id': 'e' * 64,
    })]
    assert receipt['outcome'] == 'satisfied'


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
    (item_dir / f'{sku}.json').write_text(json.dumps({
        'sku': sku, 'ebay_listing': {'status': 'Active', 'listing_id': 'L1'},
        'ebay_offer': {'offer_id': 'OFF-1', 'status': 'PUBLISHED'},
    }))
    worker = _worker()
    worker.config['itemdata_root'] = tmp_path
    monkeypatch.setattr(stage_mod.state_machine, 'active_jobs_for_sku',
                        lambda *args: [])
    provider_calls = []
    monkeypatch.setattr(stage_mod, 'stage_draft',
                        lambda *args: provider_calls.append(args))
    payload = _payload()
    payload.update({'force': force, 'origin': origin})

    with pytest.raises(TreatmentFailure) as caught:
        worker.handle({
            'entity_type': 'item', 'entity_id': sku, 'payload_json': payload,
        })

    assert caught.value.result['outcome'] == 'failed'
    assert caught.value.result['evidence']['reason_code'] == reason_code
    assert provider_calls == []
