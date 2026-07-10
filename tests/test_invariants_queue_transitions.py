"""Invariant D1/D2 (docs/invariants.md) — queue job state transition matrix.

The Python-side matrix in tgw.queue.state_machine is the declared contract for
job lifecycle transitions. The SQL in the same module is what actually runs;
these tests freeze the declared matrix so any edit to it is a deliberate,
reviewed change rather than drift.
"""

import pytest

from tgw.queue import state_machine as sm


def test_happy_path_transitions_allowed():
    for old, new in [('queued', 'leased'), ('leased', 'running'),
                     ('running', 'succeeded'), ('running', 'retry_wait'),
                     ('retry_wait', 'queued'), ('running', 'failed'),
                     ('failed', 'dead_letter')]:
        assert sm.can_transition(old, new), f'{old} -> {new} must be allowed'


def test_succeeded_is_terminal():
    for state in sm.STATES:
        assert not sm.can_transition('succeeded', state)


def test_no_state_reaches_running_except_leased():
    sources = [old for old in sm.STATES if sm.can_transition(old, 'running')]
    assert sources == ['leased']


def test_dead_letter_never_auto_requeues_to_running():
    # dead_letter -> queued exists (operator requeue) but never dead_letter -> running/leased
    assert sm.can_transition('dead_letter', 'queued')
    assert not sm.can_transition('dead_letter', 'running')
    assert not sm.can_transition('dead_letter', 'leased')


def test_expired_lease_exhausted_attempts_reach_dead_letter():
    # recover_expired_jobs() must be able to land exhausted lease-expired jobs
    # in dead_letter directly, from either leased (never started) or running
    # (crashed mid-run) — otherwise they become invisible zombies in 'failed',
    # missed by dead_letter_count/CLI/MCP tools and the stall watchdog
    # (todo #1200 / audit#1143).
    assert sm.can_transition('leased', 'dead_letter')
    assert sm.can_transition('running', 'dead_letter')


def test_unknown_states_rejected():
    assert not sm.can_transition('queued', 'bogus')
    assert not sm.can_transition('bogus', 'queued')


def test_every_allowed_transition_has_a_rule():
    for old, news in sm.ALLOWED_TRANSITIONS.items():
        for new in news:
            assert (old, new) in sm.RULES, f'missing rule for {old} -> {new}'


def test_worker_attributed_transitions_require_worker_id():
    # Claiming, running, and completing are worker actions; doing them
    # anonymously must be rejected (lease-owner CAS, invariant D2).
    for old, new in [('queued', 'leased'), ('leased', 'running'),
                     ('running', 'succeeded'), ('running', 'failed'),
                     ('running', 'retry_wait')]:
        with pytest.raises(ValueError):
            sm.validate_transition(old, new, worker_id=None)
        rule = sm.validate_transition(old, new, worker_id='host:123')
        assert rule.requires_worker


def test_invalid_transition_raises():
    with pytest.raises(ValueError):
        sm.validate_transition('succeeded', 'queued', worker_id='host:123')


def test_next_failure_state_boundary():
    # attempt_count >= max_attempts is the dead-letter boundary; one below retries.
    assert sm.next_failure_state(4, 5) == 'retry_wait'
    assert sm.next_failure_state(5, 5) == 'failed'
    assert sm.next_failure_state(6, 5) == 'failed'
