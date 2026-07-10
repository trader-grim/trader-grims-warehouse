"""audit#1143 #1246 (deferred #1245 finding) — mark_failed() must check
cur.rowcount on its lease-guarded UPDATE instead of blindly returning the
state transition it *attempted*. A lease race (e.g. recover_expired_jobs()
reclaiming the lease between mark_failed's own SELECT and its UPDATE) can
make the WHERE clause (state='running' AND lease_owner=%s) match zero rows
— without a rowcount check, mark_failed would still report a transition
that never actually happened (a phantom transition), misleading the
caller's terminal-failure handling (alerting / restarting a self-
rescheduling chain).
"""

from __future__ import annotations

from contextlib import contextmanager

import tgw.queue.state_machine as state_machine


class _FakeCursor:
    """Replays canned fetchone() results per execute() call and lets the
    test control rowcount per statement, keyed by a recognizable substring
    of the SQL so scenarios can target the exact statement they care about.
    """

    def __init__(self, fetchone_results, rowcount_by_sql_substring):
        self._fetchone_results = list(fetchone_results)
        self._rowcount_by_sql = rowcount_by_sql_substring
        self.rowcount = 0
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        for substr, rc in self._rowcount_by_sql.items():
            if substr in sql:
                self.rowcount = rc
                return

    def fetchone(self):
        if not self._fetchone_results:
            return None
        return self._fetchone_results.pop(0)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *a, **k):
        return self._cursor


def _patch_conn(monkeypatch, cursor):
    @contextmanager
    def _fake_conn():
        yield _FakeConn(cursor)

    monkeypatch.setattr(state_machine, "_conn", _fake_conn)


def test_normal_retry_wait_transition_no_race(monkeypatch):
    cur = _FakeCursor(
        fetchone_results=[{'attempt_count': 1, 'max_attempts': 3}],
        rowcount_by_sql_substring={"SET state = 'retry_wait'": 1},
    )
    _patch_conn(monkeypatch, cur)

    result = state_machine.mark_failed("job-1", "owner:1", "boom")

    assert result == "retry_wait"
    # No re-query for actual state needed on the happy path.
    assert not any('SELECT state FROM' in q for q in cur.executed)


def test_normal_dead_letter_transition_no_race(monkeypatch):
    cur = _FakeCursor(
        fetchone_results=[{'attempt_count': 3, 'max_attempts': 3}],
        rowcount_by_sql_substring={
            "SET state = 'failed'": 1,
            "SET state = 'dead_letter'": 1,
        },
    )
    _patch_conn(monkeypatch, cur)

    result = state_machine.mark_failed("job-2", "owner:1", "boom")

    assert result == "dead_letter"
    assert any("SET state = 'dead_letter'" in q for q in cur.executed)


def test_retry_wait_lease_race_reports_actual_state_not_phantom(monkeypatch):
    # The retry_wait UPDATE matched 0 rows (lost the lease race) — someone
    # else already reset this job to 'queued'. mark_failed must not claim
    # 'retry_wait' as if its own UPDATE had succeeded; it must reflect the
    # real current state.
    cur = _FakeCursor(
        fetchone_results=[
            {'attempt_count': 1, 'max_attempts': 3},
            {'state': 'queued'},
        ],
        rowcount_by_sql_substring={"SET state = 'retry_wait'": 0},
    )
    _patch_conn(monkeypatch, cur)

    result = state_machine.mark_failed("job-3", "owner:1", "boom")

    assert result == "retry_wait"  # queued is non-terminal
    assert any('SELECT state FROM' in q for q in cur.executed)


def test_dead_letter_lease_race_skips_promotion_and_reports_actual_state(monkeypatch):
    # The running->failed UPDATE matched 0 rows — must NOT run the
    # unconditional failed->dead_letter promotion (that could wrongly
    # fast-forward some OTHER owner's legitimately-'failed' row), and must
    # report the row's real current state instead of a phantom 'dead_letter'.
    cur = _FakeCursor(
        fetchone_results=[
            {'attempt_count': 3, 'max_attempts': 3},
            {'state': 'dead_letter'},
        ],
        rowcount_by_sql_substring={"SET state = 'failed'": 0},
    )
    _patch_conn(monkeypatch, cur)

    result = state_machine.mark_failed("job-4", "owner:1", "boom")

    assert result == "dead_letter"
    assert not any("SET state = 'dead_letter'" in q for q in cur.executed)


def test_lease_race_row_now_gone_reports_dead_letter(monkeypatch):
    cur = _FakeCursor(
        fetchone_results=[
            {'attempt_count': 1, 'max_attempts': 3},
            None,
        ],
        rowcount_by_sql_substring={"SET state = 'retry_wait'": 0},
    )
    _patch_conn(monkeypatch, cur)

    result = state_machine.mark_failed("job-5", "owner:1", "boom")

    assert result == "dead_letter"
