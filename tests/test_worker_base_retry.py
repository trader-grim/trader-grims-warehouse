"""audit#1143 / todo #1201 — transient errors must get the tuned backoff on
EVERY attempt, not just the final one. Before the fix, an expired token or a
quota wall re-hammered the broken dependency 4x with generic 30-240s backoff
before requeue_with_backoff's 900s/1800s delay kicked in on the last attempt.
"""

import tgw.queue.worker_base as worker_base
from tgw.queue import state_machine


class _FakeWorker(worker_base.QueueWorker):
    def __init__(self):
        # Skip QueueWorker.__init__ (DB connect, quota, nats, notify side effects).
        self.queue_name = "ebay_stage"
        self.owner = "test:1"

    def handle(self, job):
        raise RuntimeError("token is expired")


def _job(attempt_count):
    return {
        "job_id": "j1",
        "lease_token": "22222222-2222-4222-8222-222222222222",
        "attempt_count": attempt_count,
        "max_attempts": 5,
        "payload_json": {},
    }


def test_transient_error_requeues_on_first_attempt(monkeypatch):
    """attempt_count=1 (far from max_attempts=5) must still use the tuned
    900s token-expired backoff, not the generic 30s exponential one."""
    calls = {"requeue": None, "mark_failed": False}
    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine, "requeue_with_backoff",
        lambda job_id, owner, token, delay, detail: calls.__setitem__("requeue", delay),
    )
    monkeypatch.setattr(
        state_machine, "mark_failed",
        lambda *a, **k: calls.__setitem__("mark_failed", True),
    )

    w = _FakeWorker()
    w._process(_job(attempt_count=1))

    assert calls["requeue"] == 900
    assert calls["mark_failed"] is False


def test_transient_error_requeues_on_final_attempt_too(monkeypatch):
    calls = {"requeue": None}
    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine, "requeue_with_backoff",
        lambda job_id, owner, token, delay, detail: calls.__setitem__("requeue", delay),
    )
    monkeypatch.setattr(state_machine, "mark_failed", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("mark_failed should not be called for a transient error")
    ))

    w = _FakeWorker()
    w._process(_job(attempt_count=5))

    assert calls["requeue"] == 900


def test_non_transient_error_still_falls_through_to_mark_failed(monkeypatch):
    class _HardWorker(_FakeWorker):
        def handle(self, job):
            raise RuntimeError("some unclassified bug")

    calls = {"mark_failed": False, "requeue": False}
    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine, "requeue_with_backoff",
        lambda *a, **k: calls.__setitem__("requeue", True),
    )
    monkeypatch.setattr(
        state_machine, "mark_failed",
        lambda *a, **k: calls.__setitem__("mark_failed", True),
    )

    w = _HardWorker()
    w._process(_job(attempt_count=1))

    assert calls["mark_failed"] is True
    assert calls["requeue"] is False


def test_bounded_job_timeout_requeues_instead_of_stranding_worker(monkeypatch):
    class _TimedWorker(_FakeWorker):
        job_timeout_s = 0.01

        def handle(self, job):
            import time
            time.sleep(1)

    calls = {"delay": None}
    monkeypatch.setattr(state_machine, "mark_running", lambda *a, **k: None)
    monkeypatch.setattr(
        state_machine,
        "requeue_with_backoff",
        lambda _job, _owner, _token, delay, _detail: calls.__setitem__("delay", delay),
    )
    monkeypatch.setattr(
        state_machine, "mark_failed",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must requeue")),
    )

    _TimedWorker()._process(_job(attempt_count=1))

    assert calls["delay"] == 120


def test_external_price_and_sync_workers_have_bounded_job_deadlines():
    """Live consumers must not remain process-alive while a provider wedges."""
    from tgw.workers.ebay_price import EbayPriceWorker
    from tgw.workers.ebay_sync import EbaySyncWorker

    assert EbayPriceWorker.job_timeout_s == 180
    assert EbaySyncWorker.job_timeout_s == 900
