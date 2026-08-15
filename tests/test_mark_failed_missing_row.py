"""audit#1143 #1234/#1243 follow-up — mark_failed() used to return
'retry_wait' when the job_id row was missing from queue_jobs entirely, even
though nothing will ever retry a row that isn't there. That misclassified a
terminal condition as non-terminal, silently suppressing the dead-letter
alert/reschedule worker_base.py only fires on 'dead_letter'.
"""

from contextlib import contextmanager

import pytest

import tgw.queue.state_machine as state_machine


class _FakeCursor:
    def execute(self, *a, **k):
        pass

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def cursor(self, *a, **k):
        return _FakeCursor()


def test_mark_failed_rejects_missing_running_lease(monkeypatch):
    @contextmanager
    def _fake_conn():
        yield _FakeConn()

    monkeypatch.setattr(state_machine, "_conn", _fake_conn)

    with pytest.raises(RuntimeError, match="lost running lease"):
        state_machine.mark_failed(
            "gone-job-id", "owner:1",
            "55555555-5555-4555-8555-555555555555", "some error",
        )
