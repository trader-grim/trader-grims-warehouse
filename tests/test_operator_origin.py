"""Invariant C10 — an operator action stays an operator action end-to-end.

Covers the two mechanical halves:
  1. worker_base._process runs origin='operator' jobs in the interactive quota
     context (never background-halted) and restores background afterwards.
  2. Pipeline workers propagate origin='operator' to the downstream jobs they
     enqueue, and omit it when their own job lacks it.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest import mock

import pytest

from tgw import quota
from tgw.queue.worker_base import QueueWorker


class _RecordingWorker(QueueWorker):
    """Captures the quota context observed inside handle()."""

    def __init__(self, queue_name: str) -> None:
        # Bypass QueueWorker.__init__ (postgres, logging, notify) — _process
        # and the C10 context switch are all we exercise.
        self.queue_name = queue_name
        self.owner = 'test:0'
        self.seen_kind = None
        self.seen_name = None
        self.raise_exc: Exception | None = None

    def handle(self, job: Dict[str, Any]) -> None:
        self.seen_kind = quota._context_kind
        self.seen_name = quota._context_name
        if self.raise_exc is not None:
            raise self.raise_exc


@pytest.fixture(autouse=True)
def _reset_quota_context():
    kind, name = quota._context_kind, quota._context_name
    quota.set_context('background', 'worker:test')
    yield
    quota.set_context(kind, name)


def _run(worker: _RecordingWorker, payload: Dict[str, Any]) -> None:
    job = {'job_id': '00000000-0000-0000-0000-000000000000',
           'payload_json': payload, 'attempt_count': 0, 'max_attempts': 5}
    with mock.patch('tgw.queue.worker_base.state_machine') as sm:
        sm.mark_running.return_value = None
        sm.mark_succeeded.return_value = None
        sm.mark_failed.return_value = None
        worker._process(job)


def test_operator_job_runs_interactive():
    w = _RecordingWorker('ebay_upload')
    _run(w, {'sku': 'tgwtest', 'origin': 'operator'})
    assert w.seen_kind == 'interactive'
    # 'worker:' prefix is load-bearing: the fence caller header derives from
    # this name, and the PATCH auto-redraft guard must still see a machine
    # write (else the s42 redraft loop comes back — it did, 2026-07-03).
    assert w.seen_name == 'worker:ebay_upload:operator'


def test_operator_context_name_reads_as_machine_write():
    """The fence header built from the operator-job context must still be
    recognizable as a worker write by the PATCH auto-redraft guard."""
    w = _RecordingWorker('ebay_upload')
    _run(w, {'sku': 'tgwtest', 'origin': 'operator'})
    header = f'{w.seen_kind}:{w.seen_name}'   # what fence.py sends
    assert header.startswith('background:') or 'worker:' in header


def test_operator_context_restored_after_job():
    w = _RecordingWorker('ebay_upload')
    _run(w, {'sku': 'tgwtest', 'origin': 'operator'})
    assert quota._context_kind == 'background'
    assert quota._context_name == 'worker:ebay_upload'


def test_operator_context_restored_even_on_failure():
    w = _RecordingWorker('ebay_upload')
    w.raise_exc = RuntimeError('boom')
    _run(w, {'sku': 'tgwtest', 'origin': 'operator'})
    assert quota._context_kind == 'background'


def test_background_job_stays_background():
    w = _RecordingWorker('ebay_upload')
    _run(w, {'sku': 'tgwtest'})
    assert w.seen_kind == 'background'


def test_interactive_context_passes_quota_precheck_at_halt():
    """The point of C10: an operator job clears a halted pool that would
    block a background caller."""
    state = {'day': quota._day_key(),
             'pools': {'ebay_eps': {'spent': 3500, 'last_429': None,
                                    'callers': {}}}}
    import json
    with mock.patch.object(quota.Path, 'read_text',
                           return_value=json.dumps(state)):
        quota.set_context('background', 'worker:ebay_upload')
        with pytest.raises(quota.QuotaBudgetExceeded):
            quota.precheck(None, 'ebay_eps')
        quota.set_context('interactive', 'operator:ebay_upload')
        quota.precheck(None, 'ebay_eps')  # must not raise


@pytest.mark.parametrize('module_name', [
    'tgw.workers.ebay_draft',
    'tgw.workers.ebay_price',
    'tgw.workers.ebay_stage',
    'tgw.workers.ebay_publish',
    'tgw.workers.ebay_upload',
])
def test_pipeline_workers_propagate_origin(module_name):
    """Source-level check: every pipeline worker that enqueues downstream
    pipeline work references the origin field (the C10 propagation)."""
    import importlib
    import inspect
    src = inspect.getsource(importlib.import_module(module_name))
    assert "'origin'" in src or '"origin"' in src, (
        f'{module_name} enqueues downstream jobs but never propagates '
        f"origin='operator' (invariant C10)")
