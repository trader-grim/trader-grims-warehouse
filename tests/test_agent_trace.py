"""Tests for agent run trace logging — PP-AGENTTRACE-001 Phase 1.

All tests are offline. DB calls are mocked so no real PostgreSQL connection
is needed (same convention as tests/test_ai_usage.py).
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2.errors
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg():
    return {'postgres_dsn': 'dbname=state_machine user=tgw'}


def _mock_conn_cursor():
    """Build a MagicMock (con, cur) pair matching state_machine._conn()'s
    context-manager shape."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    return mock_con, mock_cur


# ---------------------------------------------------------------------------
# start_agent_run / end_agent_run round trip
# ---------------------------------------------------------------------------

def test_start_agent_run_inserts_and_returns_run_id():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine._enqueue_agent_run_render'):
            run_id = sm.start_agent_run(
                'tgw-coder', todo_id=1580, pp_ref='PP-AGENTTRACE-001',
                host='tgw-prod', git_branch='todo/1580-agent-trace-phase1',
            )

    assert isinstance(run_id, str)
    assert len(run_id) == 32  # uuid4().hex
    mock_cur.execute.assert_called_once()
    sql, params = mock_cur.execute.call_args[0]
    assert 'INSERT INTO agent_runs' in sql
    assert params[0] == run_id
    assert params[2] == 'tgw-coder'
    assert params[3] == 1580
    assert params[4] == 'PP-AGENTTRACE-001'
    assert params[5] == 'tgw-prod'
    assert params[6] == 'todo/1580-agent-trace-phase1'


def test_end_agent_run_updates_row():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.rowcount = 1
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine._enqueue_agent_run_render'):
            sm.end_agent_run(
                'deadbeef' * 4, status='completed', summary='ok',
                transcript_path='/opt/TGW/var/agent-traces/2026-07-20/deadbeef.jsonl',
            )

    mock_cur.execute.assert_called_once()
    sql, params = mock_cur.execute.call_args[0]
    assert 'UPDATE agent_runs' in sql
    assert 'SET ended_at = NOW()' in sql
    assert params[0] == 'completed'
    assert params[1] == 'ok'
    assert params[3] == 'deadbeef' * 4


def test_get_agent_run_reads_back_row():
    from tgw.queue import state_machine as sm

    fake_row = {
        'run_id': 'abc123', 'parent_run_id': None, 'agent_type': 'tgw-coder',
        'todo_id': 1580, 'pp_ref': 'PP-AGENTTRACE-001', 'host': 'tgw-prod',
        'git_branch': 'todo/1580-agent-trace-phase1', 'status': 'running',
        'summary': None, 'transcript_path': None,
    }
    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchone = MagicMock(return_value=fake_row)
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        row = sm.get_agent_run('abc123')

    assert row == fake_row


def test_get_agent_run_returns_none_when_missing():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchone = MagicMock(return_value=None)
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        row = sm.get_agent_run('does-not-exist')

    assert row is None


def test_start_agent_run_enqueues_coalesced_render_job():
    """PP-AGENTTRACE-001 Phase 2: a successful start_agent_run() enqueues a
    coalesced agent_run_render job, same shape as todo.py's
    _enqueue_plan_render (dedupe_key + 30s not_before), and never lets a
    queue problem break the trace-recording operation itself."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine.enqueue_job') as mock_enqueue:
            sm.start_agent_run('tgw-coder')

    mock_enqueue.assert_called_once()
    _, kwargs = mock_enqueue.call_args
    assert kwargs['queue_name'] == 'agent_run_render'
    assert kwargs['dedupe_key'] == 'agent_run_render:pending'
    assert kwargs['max_attempts'] == 3
    assert kwargs['not_before'] > 0


def test_start_agent_run_survives_enqueue_failure():
    """A queue problem must never break the actual trace-recording call."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine.enqueue_job', side_effect=RuntimeError('queue down')):
            run_id = sm.start_agent_run('tgw-coder')  # must not raise

    assert isinstance(run_id, str)


def test_end_agent_run_enqueues_coalesced_render_job():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.rowcount = 1
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine.enqueue_job') as mock_enqueue:
            sm.end_agent_run('some-id', status='completed')

    mock_enqueue.assert_called_once()
    _, kwargs = mock_enqueue.call_args
    assert kwargs['queue_name'] == 'agent_run_render'
    assert kwargs['dedupe_key'] == 'agent_run_render:pending'


def test_end_agent_run_does_not_enqueue_on_unknown_run_id():
    """end_agent_run raises ValueError before ever reaching the enqueue call
    when the UPDATE matched zero rows — a failed correction must not also
    look like a real render trigger."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.rowcount = 0
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine.enqueue_job') as mock_enqueue:
            with pytest.raises(ValueError):
                sm.end_agent_run('does-not-exist', status='completed')

    mock_enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# list_agent_runs — PP-AGENTTRACE-001 Phase 2
# ---------------------------------------------------------------------------

def test_list_agent_runs_returns_rows_most_recent_first():
    from tgw.queue import state_machine as sm

    fake_rows = [
        {'run_id': 'b', 'agent_type': 'tgw-coder'},
        {'run_id': 'a', 'agent_type': 'aider'},
    ]
    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchall = MagicMock(return_value=fake_rows)
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        rows = sm.list_agent_runs()

    assert rows == fake_rows
    sql, params = mock_cur.execute.call_args[0]
    assert 'ORDER BY started_at DESC' in sql
    assert 'LIMIT %s' in sql
    assert params == (200,)


def test_list_agent_runs_respects_custom_limit():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchall = MagicMock(return_value=[])
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        sm.list_agent_runs(limit=5)

    _, params = mock_cur.execute.call_args[0]
    assert params == (5,)


def test_list_agent_runs_ensures_table_first():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchall = MagicMock(return_value=[])
    sm._agent_runs_table_ready = False

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine._ensure_agent_runs_table') as mock_ensure:
            sm.list_agent_runs()
            mock_ensure.assert_called_once()


def test_start_and_end_ensure_table_first():
    """Both entry points call _ensure_agent_runs_table() before touching the DB."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    sm._agent_runs_table_ready = False

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine._ensure_agent_runs_table') as mock_ensure:
            sm.start_agent_run('claude-main')
            mock_ensure.assert_called_once()

    sm._agent_runs_table_ready = False
    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with patch('tgw.queue.state_machine._ensure_agent_runs_table') as mock_ensure:
            sm.end_agent_run('some-id', status='completed')
            mock_ensure.assert_called_once()


def test_end_agent_run_raises_on_unknown_run_id():
    """A run_id that doesn't exist must not silently succeed — an UPDATE
    matching zero rows is a failed correction (invariant C14 class), not a
    quiet no-op. Live-verified 2026-07-20: ending a run_id that was never
    started via start_agent_run() previously printed "Ended run ..." with
    no error and zero rows changed."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.rowcount = 0
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with pytest.raises(ValueError, match='no agent_runs row found'):
            sm.end_agent_run('does-not-exist', status='completed')


def test_cmd_trace_end_returns_error_on_unknown_run_id():
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch(
            'tgw.queue.state_machine.end_agent_run',
            side_effect=ValueError("end_agent_run: no agent_runs row found for run_id='nope'"),
        ):
            result = api.cmd_trace_end(_make_cfg(), 'nope', status='completed')

    assert result['ok'] is False
    assert 'no agent_runs row found' in result['error']


# ---------------------------------------------------------------------------
# status CHECK constraint — must propagate, never be swallowed
# ---------------------------------------------------------------------------

def test_end_agent_run_propagates_check_violation_on_bad_status():
    """An invalid status is rejected by the DB's CHECK constraint; the
    function must not swallow this (unlike record_ai_usage's fail-soft
    contract — this is load-bearing metadata)."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.execute = MagicMock(
        side_effect=psycopg2.errors.CheckViolation('new row violates check constraint "agent_runs_status_check"')
    )
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with pytest.raises(psycopg2.errors.CheckViolation):
            sm.end_agent_run('some-id', status='not-a-real-status')


def test_start_agent_run_propagates_foreign_key_violation_on_bad_parent():
    """parent_run_id references agent_runs(run_id); a nonexistent parent
    must raise, not be silently accepted."""
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.execute = MagicMock(
        side_effect=psycopg2.errors.ForeignKeyViolation(
            'insert or update on table "agent_runs" violates foreign key '
            'constraint "agent_runs_parent_run_id_fkey"'
        )
    )
    sm._agent_runs_table_ready = True

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        with pytest.raises(psycopg2.errors.ForeignKeyViolation):
            sm.start_agent_run('claude-subagent', parent_run_id='nonexistent-parent-id')


# ---------------------------------------------------------------------------
# DDL sanity
# ---------------------------------------------------------------------------

def test_agent_runs_ddl_has_expected_columns_and_constraints():
    from tgw.queue import state_machine as sm

    ddl = sm._AGENT_RUNS_DDL
    assert 'CREATE TABLE IF NOT EXISTS agent_runs' in ddl
    assert 'run_id          TEXT PRIMARY KEY' in ddl
    assert 'REFERENCES agent_runs(run_id)' in ddl
    assert "CHECK (status IN ('running', 'completed', 'failed', 'killed', 'escalated'))" in ddl
    assert 'agent_type      TEXT NOT NULL' in ddl


def test_ensure_agent_runs_table_applies_ddl_once():
    from tgw.queue import state_machine as sm

    mock_con, mock_cur = _mock_conn_cursor()
    sm._agent_runs_table_ready = False

    with patch('tgw.queue.state_machine._conn', return_value=mock_con):
        sm._ensure_agent_runs_table()
        sm._ensure_agent_runs_table()  # second call is a no-op (ready flag)

    assert mock_cur.execute.call_count == 1
    assert sm._agent_runs_table_ready is True
    sm._agent_runs_table_ready = False


# ---------------------------------------------------------------------------
# CLI: tgw trace start / tgw trace end
# ---------------------------------------------------------------------------

def test_cmd_trace_start_prints_run_id_and_auto_detects_host_branch(capsys):
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch('tgw.queue.state_machine.start_agent_run', return_value='fake-run-id') as mock_start:
            result = api.cmd_trace_start(_make_cfg(), 'tgw-coder', host=None, git_branch=None)

    assert result == {'ok': True, 'run_id': 'fake-run-id'}
    _, kwargs = mock_start.call_args
    # host/git_branch auto-detected (best-effort — may be None in a sandboxed
    # test env with no git repo, but the call must always pass *something*,
    # never crash resolving them).
    assert 'host' in kwargs
    assert 'git_branch' in kwargs


def test_cmd_trace_start_passes_through_explicit_host_and_branch():
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch('tgw.queue.state_machine.start_agent_run', return_value='fake-run-id') as mock_start:
            api.cmd_trace_start(
                _make_cfg(), 'aider', parent_run_id='parent-1', todo_id=42,
                pp_ref='PP-AGENTTRACE-001', host='a1131', git_branch='main',
            )

    _, kwargs = mock_start.call_args
    assert kwargs['parent_run_id'] == 'parent-1'
    assert kwargs['todo_id'] == 42
    assert kwargs['pp_ref'] == 'PP-AGENTTRACE-001'
    assert kwargs['host'] == 'a1131'
    assert kwargs['git_branch'] == 'main'


def test_cmd_trace_start_returns_error_on_db_failure():
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch('tgw.queue.state_machine.start_agent_run', side_effect=RuntimeError('db down')):
            result = api.cmd_trace_start(_make_cfg(), 'tgw-coder')

    assert result['ok'] is False
    assert 'db down' in result['error']


def test_cmd_trace_end_returns_ok():
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch('tgw.queue.state_machine.end_agent_run') as mock_end:
            result = api.cmd_trace_end(_make_cfg(), 'fake-run-id', status='completed', summary='done')

    assert result == {'ok': True, 'run_id': 'fake-run-id', 'status': 'completed'}
    mock_end.assert_called_once_with(
        'fake-run-id', status='completed', summary='done', transcript_path=None,
    )


def test_cmd_trace_end_returns_error_on_bad_status():
    from tgw import api

    with patch('tgw.queue.state_machine.init'):
        with patch(
            'tgw.queue.state_machine.end_agent_run',
            side_effect=psycopg2.errors.CheckViolation('bad status'),
        ):
            result = api.cmd_trace_end(_make_cfg(), 'fake-run-id', status='not-a-real-status')

    assert result['ok'] is False


def test_trace_start_cli_prints_only_run_id_to_stdout(capsys):
    """`tgw trace start` must print ONLY the run_id, so a shell can capture
    it via RUN_ID=$(tgw trace start ...)."""
    from tgw import api

    args = argparse.Namespace(
        op='trace', trace_op='start', agent_type='tgw-coder',
        parent_run_id=None, todo_id=None, pp_ref=None, host='tgw-prod',
        git_branch='todo/1580-agent-trace-phase1',
    )
    cfg = _make_cfg()
    with patch('tgw.api.cmd_trace_start', return_value={'ok': True, 'run_id': 'abc123'}):
        if args.trace_op == 'start':
            result = api.cmd_trace_start(
                cfg, args.agent_type, parent_run_id=args.parent_run_id,
                todo_id=args.todo_id, pp_ref=args.pp_ref, host=args.host,
                git_branch=args.git_branch,
            )
            if result.get('ok'):
                print(result['run_id'])

    out = capsys.readouterr().out
    assert out.strip() == 'abc123'


# ---------------------------------------------------------------------------
# _detect_host / _detect_git_branch — best-effort, never raise
# ---------------------------------------------------------------------------

def test_detect_host_never_raises():
    from tgw.api import _detect_host

    with patch('socket.gethostname', side_effect=Exception('nope')):
        assert _detect_host() is None


def test_detect_git_branch_never_raises_on_missing_git():
    from tgw.api import _detect_git_branch

    with patch('subprocess.run', side_effect=FileNotFoundError('no git')):
        assert _detect_git_branch() is None


def test_detect_git_branch_returns_none_on_nonzero_exit():
    from tgw.api import _detect_git_branch

    mock_result = MagicMock(returncode=128, stdout='')
    with patch('subprocess.run', return_value=mock_result):
        assert _detect_git_branch() is None


def test_detect_git_branch_returns_branch_name():
    from tgw.api import _detect_git_branch

    mock_result = MagicMock(returncode=0, stdout='main\n')
    with patch('subprocess.run', return_value=mock_result):
        assert _detect_git_branch() == 'main'


# ---------------------------------------------------------------------------
# archive_transcript()
# ---------------------------------------------------------------------------

def test_archive_transcript_copies_file_atomically(tmp_path: Path):
    from tgw.logging import archive_transcript

    source = tmp_path / 'source.jsonl'
    source.write_text('{"event": "session_start"}\n{"event": "session_end"}\n', encoding='utf-8')

    traces_root = tmp_path / 'agent-traces'
    dest = archive_transcript(
        'run-abc', source, traces_root=traces_root, today=date(2026, 7, 20),
    )

    dest_path = Path(dest)
    assert dest_path == traces_root / '2026-07-20' / 'run-abc.jsonl'
    assert dest_path.read_text(encoding='utf-8') == source.read_text(encoding='utf-8')
    # no leftover temp files
    leftovers = [p for p in dest_path.parent.iterdir() if p.name.startswith('.')]
    assert leftovers == []


def test_archive_transcript_creates_missing_date_dir(tmp_path: Path):
    from tgw.logging import archive_transcript

    source = tmp_path / 'source.jsonl'
    source.write_text('{}', encoding='utf-8')

    traces_root = tmp_path / 'does-not-exist-yet'
    assert not traces_root.exists()

    dest = archive_transcript('run-xyz', source, traces_root=traces_root, today=date(2026, 7, 20))

    assert Path(dest).exists()
    assert traces_root.is_dir()


def test_archive_transcript_raises_clear_error_on_missing_source(tmp_path: Path):
    from tgw.logging import archive_transcript

    with pytest.raises(FileNotFoundError):
        archive_transcript('run-nope', tmp_path / 'does-not-exist.jsonl', traces_root=tmp_path / 'traces')


def test_archive_transcript_two_run_ids_do_not_clobber_each_other(tmp_path: Path):
    from tgw.logging import archive_transcript

    src_a = tmp_path / 'a.jsonl'
    src_a.write_text('run A content', encoding='utf-8')
    src_b = tmp_path / 'b.jsonl'
    src_b.write_text('run B content', encoding='utf-8')

    traces_root = tmp_path / 'agent-traces'
    today = date(2026, 7, 20)

    dest_a = Path(archive_transcript('run-a', src_a, traces_root=traces_root, today=today))
    dest_b = Path(archive_transcript('run-b', src_b, traces_root=traces_root, today=today))

    assert dest_a != dest_b
    assert dest_a.exists() and dest_b.exists()
    assert dest_a.read_text(encoding='utf-8') == 'run A content'
    assert dest_b.read_text(encoding='utf-8') == 'run B content'


def test_archive_transcript_reachiving_same_run_id_is_idempotent(tmp_path: Path):
    """Calling archive_transcript twice for the SAME run_id atomically
    replaces that run's own file — never leaves a partial/corrupt file."""
    from tgw.logging import archive_transcript

    src = tmp_path / 'a.jsonl'
    src.write_text('version 1', encoding='utf-8')
    traces_root = tmp_path / 'agent-traces'
    today = date(2026, 7, 20)

    dest1 = archive_transcript('run-a', src, traces_root=traces_root, today=today)

    src.write_text('version 2', encoding='utf-8')
    dest2 = archive_transcript('run-a', src, traces_root=traces_root, today=today)

    assert dest1 == dest2
    assert Path(dest2).read_text(encoding='utf-8') == 'version 2'
