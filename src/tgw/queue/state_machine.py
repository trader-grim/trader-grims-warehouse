from dataclasses import dataclass
from typing import Optional, Set

STATES = {
    'queued',
    'leased',
    'running',
    'retry_wait',
    'succeeded',
    'failed',
    'dead_letter',
    'cancelled',
}

ALLOWED_TRANSITIONS = {
    'queued': {'leased', 'cancelled'},
    'leased': {'running', 'queued', 'cancelled'},
    'running': {'succeeded', 'retry_wait', 'failed', 'queued', 'cancelled'},
    'retry_wait': {'queued', 'cancelled', 'dead_letter'},
    'failed': {'dead_letter', 'queued', 'cancelled'},
    'succeeded': set(),
    'dead_letter': {'queued', 'cancelled'},
    'cancelled': {'queued'},
}

@dataclass(frozen=True)
class TransitionRule:
    old_state: str
    new_state: str
    requires_worker: bool = False
    terminal: bool = False

RULES = {
    ('queued', 'leased'): TransitionRule('queued', 'leased', requires_worker=True),
    ('leased', 'running'): TransitionRule('leased', 'running', requires_worker=True),
    ('leased', 'queued'): TransitionRule('leased', 'queued'),
    ('running', 'succeeded'): TransitionRule('running', 'succeeded', requires_worker=True, terminal=True),
    ('running', 'retry_wait'): TransitionRule('running', 'retry_wait', requires_worker=True),
    ('running', 'failed'): TransitionRule('running', 'failed', requires_worker=True, terminal=True),
    ('running', 'queued'): TransitionRule('running', 'queued'),
    ('retry_wait', 'queued'): TransitionRule('retry_wait', 'queued'),
    ('retry_wait', 'dead_letter'): TransitionRule('retry_wait', 'dead_letter', terminal=True),
    ('failed', 'dead_letter'): TransitionRule('failed', 'dead_letter', terminal=True),
    ('failed', 'queued'): TransitionRule('failed', 'queued'),
    ('dead_letter', 'queued'): TransitionRule('dead_letter', 'queued'),
    ('queued', 'cancelled'): TransitionRule('queued', 'cancelled', terminal=True),
    ('leased', 'cancelled'): TransitionRule('leased', 'cancelled', terminal=True),
    ('running', 'cancelled'): TransitionRule('running', 'cancelled', terminal=True),
    ('retry_wait', 'cancelled'): TransitionRule('retry_wait', 'cancelled', terminal=True),
    ('failed', 'cancelled'): TransitionRule('failed', 'cancelled', terminal=True),
    ('dead_letter', 'cancelled'): TransitionRule('dead_letter', 'cancelled', terminal=True),
    ('cancelled', 'queued'): TransitionRule('cancelled', 'queued'),
}


def can_transition(old_state: str, new_state: str) -> bool:
    if old_state not in STATES or new_state not in STATES:
        return False
    return new_state in ALLOWED_TRANSITIONS.get(old_state, set())


def validate_transition(old_state: str, new_state: str, worker_id: Optional[str] = None) -> TransitionRule:
    if not can_transition(old_state, new_state):
        raise ValueError(f'invalid transition: {old_state} -> {new_state}')
    rule = RULES[(old_state, new_state)]
    if rule.requires_worker and not worker_id:
        raise ValueError(f'transition {old_state} -> {new_state} requires worker_id')
    return rule


def next_failure_state(attempt_count: int, max_attempts: int) -> str:
    return 'failed' if attempt_count >= max_attempts else 'retry_wait'
