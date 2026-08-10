from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tgw import provider_effects
from tgw.workflow.operator_authority import OperatorAuthority


def _binding(**changes):
    binding = {
        'provider': 'ebay', 'operation': 'publish-offer',
        'entity_type': 'item', 'entity_id': 'SKU-1',
        'object_generation': 'generation-1', 'graph_id': 'graph-1',
        'treatment_id': 'ebay-publish', 'treatment_version': '1',
        'condition_hash': 'condition-1', 'request': {'offer_id': 'OFF-1'},
        'authority': {'origin': 'operator'},
    }
    binding.update(changes)
    return binding


def _row(binding, state='reserved'):
    return {
        **binding, 'effect_id': provider_effects.effect_identity(**binding),
        'request_json': binding['request'], 'authority_json': binding['authority'],
        'state': state, 'result_json': None, 'error_detail': None,
    }


def _authority(*, superseded=False, expired=False):
    now = datetime.now(UTC)
    return OperatorAuthority(
        authority_id='00000000-0000-0000-0000-000000000001',
        operator_identity='operator:dave', surface='web', entity_id='SKU-1',
        goal_profile_id='tgw.ebay_listable', goal_profile_version='1',
        object_generation='generation-1',
        pre_authority_condition_hash='pre-condition-1',
        content_identity='content-1', provider_identity='ebay:test',
        scopes=('publish',), issued_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1) if expired else now + timedelta(hours=1),
        superseded_at=now if superseded else None,
        superseded_by='replacement' if superseded else None,
    )


def test_effect_identity_binds_exact_json_native_values():
    base = provider_effects.effect_identity(**_binding(request={'value': 1}))
    assert provider_effects.effect_identity(**_binding(request={'value': True})) != base
    assert provider_effects.effect_identity(**_binding(request={'value': 1.0})) != base
    assert provider_effects.effect_identity(**_binding(request={'value': None})) != (
        provider_effects.effect_identity(**_binding(request={}))
    )


def test_reservation_takes_scope_lock_and_exact_replay_returns_prior_record():
    binding = _binding()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [_row(binding)]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection):
        result = provider_effects.reserve_provider_effect(**binding)
    assert result.effect_id == provider_effects.effect_identity(**binding)
    assert 'pg_advisory_xact_lock' in cursor.execute.call_args_list[0].args[0]
    assert cursor.execute.call_count == 2


def test_same_scope_different_exact_binding_conflicts():
    requested = _binding(request={'offer_id': 'OFF-CHANGED'})
    existing = _binding()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [_row(existing)]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection):
        with pytest.raises(provider_effects.ProviderEffectConflict):
            provider_effects.reserve_provider_effect(**requested)


def test_unresolved_older_generation_fences_new_generation_dispatch():
    older = _binding(object_generation='generation-old')
    requested = _binding(object_generation='generation-new', graph_id='graph-new')
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [_row(older, state='ambiguous')]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection):
        with pytest.raises(
            provider_effects.ProviderEffectConflict,
            match='unresolved reservation',
        ):
            provider_effects.reserve_provider_effect(**requested)
    assert cursor.execute.call_count == 2


def test_authority_lock_validation_reservation_and_dispatch_are_one_transaction():
    authority = _authority()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    binding = _binding(authority=provider_effects._authority_json(authority))
    cursor.fetchone.side_effect = [None, None, _row(binding, state='dispatched')]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    validation = {
        'entity_id': 'SKU-1', 'goal_profile_id': 'tgw.ebay_listable',
        'goal_profile_version': '1', 'object_generation': 'generation-1',
        'pre_authority_condition_hash': 'pre-condition-1',
        'content_identity': 'content-1', 'provider_identity': 'ebay:test',
    }
    effect_args = _binding()
    effect_args.pop('authority')
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection), \
         patch('tgw.workflow.operator_authority.get_authority', return_value=authority) as get, \
         patch('tgw.workflow.operator_authority.validate_authority',
               return_value=(authority, 'valid')):
        record = provider_effects.reserve_and_begin_authorized_effect(
            authority_id=authority.authority_id, authority_scope='publish',
            authority_binding=validation, **effect_args,
        )
    assert record.state == 'dispatched'
    assert get.call_args.kwargs == {'connection': connection, 'for_update': True}
    sql = [call.args[0] for call in cursor.execute.call_args_list]
    assert 'pg_advisory_xact_lock' in sql[0]
    assert 'state IN' in sql[1]
    assert 'object_generation' in sql[2]
    assert "'dispatched',NOW()" in sql[3]


@pytest.mark.parametrize('change', ['expired', 'superseded'])
def test_atomic_reservation_replays_succeeded_before_current_authority_validation(change):
    dispatch_authority = _authority()
    now = datetime.now(UTC)
    if change == 'expired':
        current_authority = replace(dispatch_authority, expires_at=now - timedelta(seconds=1))
    else:
        current_authority = replace(
            dispatch_authority, superseded_at=now, superseded_by='replacement',
        )
    binding = _binding(authority=provider_effects._authority_json(dispatch_authority))
    row = _row(binding, state='succeeded')
    row['result_json'] = {'listing_id': 'L1'}
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [None, row]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    authority_binding = {
        'entity_id': dispatch_authority.entity_id,
        'goal_profile_id': dispatch_authority.goal_profile_id,
        'goal_profile_version': dispatch_authority.goal_profile_version,
        'object_generation': dispatch_authority.object_generation,
        'pre_authority_condition_hash': dispatch_authority.pre_authority_condition_hash,
        'content_identity': dispatch_authority.content_identity,
        'provider_identity': dispatch_authority.provider_identity,
    }
    effect_args = _binding()
    effect_args.pop('authority')
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection), \
         patch('tgw.workflow.operator_authority.get_authority',
               return_value=current_authority), \
         patch('tgw.workflow.operator_authority.validate_authority') as validate:
        result = provider_effects.reserve_and_begin_authorized_effect(
            authority_id=dispatch_authority.authority_id,
            authority_scope='publish', authority_binding=authority_binding,
            **effect_args,
        )
    assert result.state == 'succeeded'
    assert result.result == {'listing_id': 'L1'}
    validate.assert_not_called()


@pytest.mark.parametrize('change', ['expired', 'superseded'])
def test_succeeded_repair_replay_accepts_exact_historical_authority(change):
    dispatch_authority = _authority()
    now = datetime.now(UTC)
    authority = (
        replace(dispatch_authority, expires_at=now - timedelta(seconds=1))
        if change == 'expired' else
        replace(dispatch_authority, superseded_at=now, superseded_by='replacement')
    )
    expected = _binding(authority=provider_effects._authority_json(dispatch_authority))
    effect_id = provider_effects.effect_identity(**expected)
    row = _row(expected, state='succeeded')
    row['effect_id'] = effect_id
    row['result_json'] = {'listing_id': 'L1'}
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.return_value = row
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    authority_binding = {
        'entity_id': authority.entity_id,
        'goal_profile_id': authority.goal_profile_id,
        'goal_profile_version': authority.goal_profile_version,
        'object_generation': authority.object_generation,
        'pre_authority_condition_hash': authority.pre_authority_condition_hash,
        'provider_identity': authority.provider_identity,
    }
    expected_without_authority = dict(expected)
    expected_without_authority.pop('authority')
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection), \
         patch('tgw.workflow.operator_authority.get_authority', return_value=authority):
        result = provider_effects.validate_succeeded_authorized_effect(
            effect_id=effect_id, authority_id=authority.authority_id,
            authority_scope='publish', authority_binding=authority_binding,
            expected_binding=expected_without_authority,
        )
    assert result.state == 'succeeded'


def test_atomic_reservation_returns_exact_rejected_terminal_record():
    authority = _authority()
    binding = _binding(authority=provider_effects._authority_json(authority))
    row = _row(binding, state='rejected')
    row['error_detail'] = 'HTTP 422'
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.fetchone.side_effect = [None, row]
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor
    authority_binding = {
        'entity_id': authority.entity_id,
        'goal_profile_id': authority.goal_profile_id,
        'goal_profile_version': authority.goal_profile_version,
        'object_generation': authority.object_generation,
        'pre_authority_condition_hash': authority.pre_authority_condition_hash,
        'content_identity': authority.content_identity,
        'provider_identity': authority.provider_identity,
    }
    effect_args = _binding()
    effect_args.pop('authority')
    with patch.object(provider_effects.state_machine, '_conn', return_value=connection), \
         patch('tgw.workflow.operator_authority.get_authority', return_value=authority), \
         patch('tgw.workflow.operator_authority.validate_authority') as validate:
        result = provider_effects.reserve_and_begin_authorized_effect(
            authority_id=authority.authority_id, authority_scope='publish',
            authority_binding=authority_binding, **effect_args,
        )
    assert result.state == 'rejected'
    validate.assert_not_called()


def test_schema_sources_include_durable_provider_effect_contract():
    queue_dir = Path(__file__).parents[1] / 'src/tgw/queue'
    for name in ('schema.sql', 'live_schema.sql'):
        schema = (queue_dir / name).read_text()
        assert 'provider_effects' in schema
        assert "'dispatched','succeeded','rejected','ambiguous','reconciliation_required'" in schema
        assert 'UNIQUE (provider, operation, entity_type, entity_id, object_generation)' in schema
        assert 'uq_provider_effects_unresolved_entity' in schema
