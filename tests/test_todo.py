"""Tests for tgw.todo CRUD operations and dead_letter state_machine helpers."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: build a mock connection + cursor that returns canned data
# ---------------------------------------------------------------------------

def _mock_conn(fetchone_return=None):
    """Return a context-manager that yields a mock psycopg2 connection."""
    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = fetchone_return

    con = MagicMock()
    con.cursor.return_value = cur
    con.__enter__ = lambda s: s
    con.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def _ctx():
        yield con

    return _ctx, cur


# ---------------------------------------------------------------------------
# todo_add
# ---------------------------------------------------------------------------

def test_todo_add_returns_new_id():
    from tgw.todo import todo_add
    ctx, cur = _mock_conn(fetchone_return=(42,))
    with patch('tgw.todo._conn', ctx):
        result = todo_add('claude', 'test task', priority=30, source='test')
    assert result['ok'] is True
    assert result['id'] == 42
    assert result['agent'] == 'claude'
    assert result['priority'] == 30


# ---------------------------------------------------------------------------
# todo_done
# ---------------------------------------------------------------------------

def test_todo_done_success():
    from tgw.todo import todo_done
    ctx, cur = _mock_conn(fetchone_return=(7, 'claude', 'do the thing'))
    with patch('tgw.todo._conn', ctx):
        result = todo_done(7)
    assert result['ok'] is True
    assert result['id'] == 7
    assert result['agent'] == 'claude'


def test_todo_done_not_found():
    from tgw.todo import todo_done
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.todo._conn', ctx):
        result = todo_done(999)
    assert result['ok'] is False
    assert 'not found' in result['error']


# ---------------------------------------------------------------------------
# todo_update
# ---------------------------------------------------------------------------

def test_todo_update_success():
    from tgw.todo import todo_update
    ctx, cur = _mock_conn(fetchone_return=(5, 'gemini'))
    with patch('tgw.todo._conn', ctx):
        result = todo_update(5, 'revised task text')
    assert result['ok'] is True
    assert result['id'] == 5
    assert result['agent'] == 'gemini'
    assert result['body'] == 'revised task text'


def test_todo_update_not_found():
    from tgw.todo import todo_update
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.todo._conn', ctx):
        result = todo_update(999, 'new text')
    assert result['ok'] is False
    assert 'not found' in result['error']


# ---------------------------------------------------------------------------
# todo_delegate
# ---------------------------------------------------------------------------

def test_todo_delegate_success():
    from tgw.todo import todo_delegate
    ctx, cur = _mock_conn(fetchone_return=(3, 'reassign this task'))
    with patch('tgw.todo._conn', ctx):
        result = todo_delegate(3, 'admin')
    assert result['ok'] is True
    assert result['id'] == 3
    assert result['agent'] == 'admin'
    assert result['body'] == 'reassign this task'


def test_todo_delegate_not_found():
    from tgw.todo import todo_delegate
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.todo._conn', ctx):
        result = todo_delegate(999, 'admin')
    assert result['ok'] is False
    assert 'not found' in result['error']


# ---------------------------------------------------------------------------
# todo_set_priority
# ---------------------------------------------------------------------------

def test_todo_set_priority_success():
    from tgw.todo import todo_set_priority
    ctx, cur = _mock_conn(fetchone_return=(12, 'db', 'some db task'))
    with patch('tgw.todo._conn', ctx):
        result = todo_set_priority(12, 10)
    assert result['ok'] is True
    assert result['id'] == 12
    assert result['priority'] == 10
    assert result['agent'] == 'db'


def test_todo_set_priority_not_found():
    from tgw.todo import todo_set_priority
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.todo._conn', ctx):
        result = todo_set_priority(999, 10)
    assert result['ok'] is False
    assert 'not found' in result['error']


# ---------------------------------------------------------------------------
# dead_letter_jobs (state_machine)
# ---------------------------------------------------------------------------

def test_dead_letter_jobs_returns_list():
    from tgw.queue import state_machine
    ctx, cur = _mock_conn()
    cur.fetchall.return_value = [
        {'job_id': 'aaa', 'queue_name': 'ebay_draft', 'payload_json': {'sku': 'tgw20260101000001'},
         'error_detail': 'HardFailure: no category', 'attempt_count': 3,
         'max_attempts': 3, 'created_at': None, 'finished_at': None},
    ]
    with patch('tgw.queue.state_machine._conn', ctx):
        jobs = state_machine.dead_letter_jobs(queue_name='ebay_draft')
    assert len(jobs) == 1
    assert jobs[0]['queue_name'] == 'ebay_draft'


def test_dead_letter_jobs_empty():
    from tgw.queue import state_machine
    ctx, cur = _mock_conn()
    cur.fetchall.return_value = []
    with patch('tgw.queue.state_machine._conn', ctx):
        jobs = state_machine.dead_letter_jobs()
    assert jobs == []


def test_requeue_dead_letter_job_not_found():
    from tgw.queue import state_machine
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.queue.state_machine._conn', ctx):
        try:
            state_machine.requeue_dead_letter_job('nonexistent-id')
            assert False, 'should have raised ValueError'
        except ValueError as exc:
            assert 'not found' in str(exc)
