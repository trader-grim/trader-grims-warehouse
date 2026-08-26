"""Actual Python subprocess proofs for coding worker cancellation behavior."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _python(source: str, *, timeout: float = 10):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)], env=env,
        text=True, capture_output=True, timeout=timeout, check=False,
    )


def test_worker_subprocess_reports_truthful_no_runner_then_advances_serial_job():
    completed = _python(r'''
        import json, uuid
        from tgw.queue import worker_base
        from tgw.queue.worker_base import JobCancelled, QueueWorker

        token = str(uuid.uuid4())
        acknowledgements = []
        worker_base.state_machine.mark_running = lambda *a: None
        worker_base.state_machine.acknowledge_cancellation = lambda j, a: (acknowledgements.append(a) or {'job_id': j})
        worker_base.state_machine.close_local_success = lambda *a: True
        worker_base.tgw_logging.log_event = lambda *a, **k: None

        class Worker(QueueWorker):
            direct_local_receipts = True
            def __init__(self):
                self.owner = 'worker:subprocess'
                self.queue_name = 'codex-implement'
                self.config = {}
                self.calls = 0
            def _handle_with_deadline(self, job):
                self.calls += 1
                if self.calls == 1:
                    raise JobCancelled('cancelled', reason='no_runner', reaped=True,
                        runner={'schema':'tgw-coding-runner/v2','kind':'no_runner',
                          'job_id':job['job_id'],'queue_name':self.queue_name,
                          'lease_owner':self.owner,'lease_token':job['lease_token']})
                return {'next_job_advanced': True}

            def _on_direct_local_success(self, job, receipt, register):
                register(lambda: None)

        worker = Worker()
        first = {'job_id':str(uuid.uuid4()), 'lease_token':token, 'payload_json':{}}
        worker._process(first)
        # The serial worker remains usable after acknowledging cancellation.
        worker._process({'job_id':str(uuid.uuid4()), 'lease_token':str(uuid.uuid4()),
                         'payload_json':{}})
        print(json.dumps({'ack': acknowledgements[0], 'calls_before_next': worker.calls}))
    ''')
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout.strip().splitlines()[-1])
    assert value["ack"]["reason"] == "no_runner"
    assert value["ack"]["reaped"] is True
    assert value["calls_before_next"] == 2


def test_runner_subprocess_cancellation_reaps_scope_and_bounds_output():
    completed = _python(r'''
        import json, pathlib, sys, threading
        from tgw.queue.worker_base import JobCancelled
        from tgw.workers.coding import _run_bounded_process_group
        cancelled = threading.Event()
        threading.Timer(.15, cancelled.set).start()
        try:
            _run_bounded_process_group(
                [sys.executable, '-c', "print('started', flush=True); import time; time.sleep(30)"],
                cwd=pathlib.Path.cwd(), env={}, timeout=5,
                cancellation_check=cancelled.is_set,
                runner_identity={'job_id':'11111111-1111-4111-8111-111111111111',
                  'queue_name':'codex-implement','lease_owner':'worker:subprocess',
                  'lease_token':'22222222-2222-4222-8222-222222222222'})
        except JobCancelled as exc:
            print(json.dumps({'reason':exc.reason,'reaped':exc.reaped,
                              'returncode':exc.runner.get('returncode')}))
    ''', timeout=8)
    assert completed.returncode == 0, completed.stderr
    value = json.loads(completed.stdout.strip().splitlines()[-1])
    assert value == {"reason": "stopped", "reaped": True, "returncode": None}


def test_runner_subprocess_timeout_is_bounded_and_truthful():
    completed = _python(r'''
        import pathlib, subprocess, sys
        from tgw.workers.coding import _run_bounded_process_group
        try:
            _run_bounded_process_group([sys.executable, '-c', 'import time; time.sleep(30)'],
                cwd=pathlib.Path.cwd(), env={}, timeout=.05)
        except subprocess.TimeoutExpired as exc:
            print(f'timeout:{exc.timeout}')
    ''', timeout=8)
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "timeout:0.05"
