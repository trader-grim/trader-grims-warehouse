"""audit#1143 / todo #1234 (merged #1165+#1166) — self-rescheduling workers
only re-enqueued their next check on success. An unexpected error that
survived to dead_letter (HardFailure, or retries exhausted) silently ended
the chain forever: token_refresh never checked eBay token expiry again,
velocity_stats never ran nightly analytics again — with no distinct alert
that the *chain*, not just one job, had stopped.
"""

import tgw.queue.worker_base as worker_base
from tgw.queue import state_machine


def _job(attempt_count=1, max_attempts=5):
    return {
        "job_id": "j1",
        "lease_token": "33333333-3333-4333-8333-333333333333",
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "payload_json": {},
    }


class _RecordingWorker(worker_base.QueueWorker):
    def __init__(self):
        self.queue_name = "token_refresh"
        self.owner = "test:1"
        self.terminal_calls = []

    def _on_terminal_failure(self, job, error_text):
        self.terminal_calls.append((job["job_id"], error_text))


def test_hard_failure_triggers_terminal_failure_hook(monkeypatch):
    class _HardWorker(_RecordingWorker):
        def handle(self, job):
            raise worker_base.HardFailure("token dead")

    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(state_machine, "mark_dead_letter", lambda *a, **k: None)

    w = _HardWorker()
    w._process(_job())

    assert len(w.terminal_calls) == 1
    assert w.terminal_calls[0][0] == "j1"


def test_exhausted_retries_trigger_terminal_failure_hook(monkeypatch):
    class _WornOutWorker(_RecordingWorker):
        def handle(self, job):
            raise RuntimeError("some unclassified bug")

    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(state_machine, "mark_failed", lambda *a, **k: "dead_letter")

    w = _WornOutWorker()
    w._process(_job(attempt_count=5, max_attempts=5))

    assert len(w.terminal_calls) == 1


def test_retry_wait_does_not_trigger_terminal_failure_hook(monkeypatch):
    class _StillRetryingWorker(_RecordingWorker):
        def handle(self, job):
            raise RuntimeError("some unclassified bug")

    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(state_machine, "mark_failed", lambda *a, **k: "retry_wait")

    w = _StillRetryingWorker()
    w._process(_job(attempt_count=1, max_attempts=5))

    assert w.terminal_calls == []


def test_default_on_terminal_failure_is_a_noop(monkeypatch):
    """Ordinary (non-self-scheduling) workers get a no-op default — this
    must not raise even though QueueWorker.__init__ was never called."""
    class _PlainWorker(worker_base.QueueWorker):
        def __init__(self):
            self.queue_name = "ebay_stage"
            self.owner = "test:1"

        def handle(self, job):
            raise worker_base.HardFailure("boom")

    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(state_machine, "mark_dead_letter", lambda *a, **k: None)

    w = _PlainWorker()
    w._process(_job())  # must not raise


def test_stop_after_claim_before_mark_running_persists_no_runner_and_stays_healthy(
    monkeypatch,
):
    class _NeverRuns(_RecordingWorker):
        def handle(self, job):
            raise AssertionError("cancelled leased job reached handle")

    identity = {
        "job_id": "j1", "queue_name": "token_refresh",
        "lease_owner": "test:1",
        "lease_token": "33333333-3333-4333-8333-333333333333",
    }
    cancelled = {
        "state": "cancelled", "payload_json": {"result": {"stop_control": {
            "kind": "runner_cancel_requested", "request_identity": identity,
        }}},
    }
    acknowledgements = []
    monkeypatch.setattr(
        state_machine, "mark_running",
        lambda *_a: (_ for _ in ()).throw(RuntimeError("lost leased job")),
    )
    monkeypatch.setattr(state_machine, "get_job", lambda *_a: cancelled)
    monkeypatch.setattr(
        state_machine, "acknowledge_cancellation",
        lambda _job_id, acknowledgement: acknowledgements.append(acknowledgement)
        or cancelled,
    )

    worker = _NeverRuns()
    worker._process(_job())
    assert len(acknowledgements) == 1
    assert all(item["reason"] == "no_runner" for item in acknowledgements)
    assert all(item["runner"] == {"schema": "tgw-coding-runner/v2",
                                  "kind": "no_runner", **identity}
               for item in acknowledgements)
