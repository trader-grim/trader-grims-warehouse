"""Tests for tgw.agent_trace_render — Obsidian agent-runs view (PP-AGENTTRACE-001
Phase 2).

All tests are offline: build_agent_runs_doc() is a pure function (no IO,
no DB); render_agent_runs_doc()'s DB call is mocked via
tgw.queue.state_machine.list_agent_runs, same convention as
tests/test_plan_render.py's render_taskboard tests.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tgw.agent_trace_render import (
    AGENT_RUNS_DOC_NAME,
    agent_runs_doc_path,
    build_agent_runs_doc,
    render_agent_runs_doc,
)

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)


def _row(run_id='abcdef0123456789', agent_type='tgw-coder', pp_ref=None,
         todo_id=None, host='tgw-prod', status='running',
         started_at=NOW, ended_at=None, summary=None):
    return {
        'run_id': run_id, 'parent_run_id': None, 'agent_type': agent_type,
        'todo_id': todo_id, 'pp_ref': pp_ref, 'host': host,
        'git_branch': None, 'started_at': started_at, 'ended_at': ended_at,
        'status': status, 'summary': summary, 'transcript_path': None,
    }


# ---------------------------------------------------------------------------
# build_agent_runs_doc — pure
# ---------------------------------------------------------------------------

def test_build_has_generated_warning_and_truncation_note():
    text = build_agent_runs_doc([], now=NOW)
    assert 'GENERATED FILE — DO NOT EDIT' in text
    assert 'agent_run_render' in text
    assert '_Rendered 2026-07-20 12:00 UTC — 0 run(s) shown._' in text


def test_build_empty_shows_placeholder_row():
    text = build_agent_runs_doc([], now=NOW)
    assert '_no runs recorded yet_' in text


def test_build_truncates_run_id_to_12_chars():
    rows = [_row(run_id='0123456789abcdef0123456789abcdef')]
    text = build_agent_runs_doc(rows, now=NOW)
    assert '`0123456789ab`' in text
    assert '0123456789abcdef0123456789abcdef' not in text


def test_build_shows_running_status_with_no_ended_at():
    rows = [_row(status='running', ended_at=None)]
    text = build_agent_runs_doc(rows, now=NOW)
    assert '| running |' in text


def test_build_computes_duration_when_ended():
    rows = [_row(
        started_at=NOW,
        ended_at=NOW + timedelta(minutes=5, seconds=30),
        status='completed',
    )]
    text = build_agent_runs_doc(rows, now=NOW)
    assert '5m30s' in text


def test_build_duration_blank_when_no_started_at():
    rows = [_row()]
    rows[0]['started_at'] = None
    text = build_agent_runs_doc(rows, now=NOW)
    # No crash; row still rendered with empty duration cell.
    assert 'tgw-coder' in text


def test_build_pp_ref_links_via_heading_map():
    rows = [_row(pp_ref='PP-AGENTTRACE-001', todo_id=1581)]
    headings = {'PP-AGENTTRACE-001': 'PP-AGENTTRACE-001 — agent trace logging'}
    text = build_agent_runs_doc(rows, headings, now=NOW)
    assert '[[TGW-Master-Plan#PP-AGENTTRACE-001 — agent trace logging\\|PP-AGENTTRACE-001]]' in text
    assert '#1581' in text


def test_build_pp_ref_without_heading_falls_back_to_code_span():
    rows = [_row(pp_ref='PP-LOST-001')]
    text = build_agent_runs_doc(rows, {}, now=NOW)
    assert '`PP-LOST-001`' in text


def test_build_escapes_pipes_in_summary():
    rows = [_row(summary='did a | thing')]
    text = build_agent_runs_doc(rows, now=NOW)
    assert 'did a \\| thing' in text


# ---------------------------------------------------------------------------
# render_agent_runs_doc — atomic write
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    root = tmp_path / 'standalone-plan'
    master = root / 'plan' / 'TGW-Master-Plan.md'
    master.parent.mkdir(parents=True)
    master.write_text(
        '### PP-AGENTTRACE-001 — approved agent trace logging\n', encoding='utf-8',
    )
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
        'commit', '-qm', 'approved Plan',
    ], check=True)
    commit = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    vault = tmp_path / 'legacy-vault'
    legacy_master = vault / 'plan' / 'TGW-Master-Plan.md'
    legacy_master.parent.mkdir(parents=True)
    legacy_master.write_text(
        '### PP-AGENTTRACE-001 — legacy agent trace logging\n', encoding='utf-8',
    )
    return {
        'plan_vault_path': vault,
        'plan_render_root': tmp_path / 'rendered',
        'plan_master_path': legacy_master,
        'standalone_plan_root': root,
        'plan_approved_commit': commit,
        'plan_approved_solution_hash': 'sha256:' + 'b' * 64,
    }


def test_render_writes_file(tmp_path):
    cfg = _cfg(tmp_path)
    rows = [_row(pp_ref='PP-AGENTTRACE-001', todo_id=1581)]
    with patch('tgw.queue.state_machine.list_agent_runs', return_value=rows):
        result = render_agent_runs_doc(cfg)
    assert result['ok'] is True
    assert result['count'] == 1
    assert result['plan_identity']['plan_root'] == str(cfg['standalone_plan_root'])
    assert result['plan_identity']['plan_commit'] == cfg['plan_approved_commit']
    assert result['plan_identity']['solution_hash'] == cfg['plan_approved_solution_hash']
    out = agent_runs_doc_path(cfg)
    assert out.name == AGENT_RUNS_DOC_NAME
    assert out.exists()
    content = out.read_text(encoding='utf-8')
    assert 'tgw-coder' in content
    assert '[[TGW-Master-Plan#PP-AGENTTRACE-001 — approved agent trace logging\\|PP-AGENTTRACE-001]]' in content
    assert cfg['plan_approved_commit'] in content
    assert cfg['plan_approved_solution_hash'] in content
    # no temp file left behind
    assert not list(out.parent.glob('.agent-runs-*'))
    assert 'plan' not in out.relative_to(tmp_path).parts


def test_render_reports_tracker_failure(tmp_path):
    cfg = _cfg(tmp_path)
    with patch('tgw.queue.state_machine.list_agent_runs', side_effect=RuntimeError('db down')):
        result = render_agent_runs_doc(cfg)
    assert result['ok'] is False
    assert 'db down' in result['error']
    assert result['plan_identity']['plan_commit'] == cfg['plan_approved_commit']
    assert not agent_runs_doc_path(cfg).exists()


@pytest.mark.parametrize(
    ('field', 'code'),
    [
        ('plan_approved_commit', 'approved_plan_commit_required'),
        ('plan_approved_solution_hash', 'approved_solution_required'),
    ],
)
def test_agent_trace_render_refuses_unbound_plan_before_reading_tracker(tmp_path, field, code):
    cfg = _cfg(tmp_path)
    cfg.pop(field)
    with patch('tgw.queue.state_machine.list_agent_runs') as list_runs:
        result = render_agent_runs_doc(cfg)
    assert result == {
        'ok': False,
        'error': f'approved Plan binding unavailable: {code}',
        'code': code,
    }
    list_runs.assert_not_called()
    assert not agent_runs_doc_path(cfg).exists()


# ---------------------------------------------------------------------------
# AgentRunRenderWorker.handle() — PP-AGENTTRACE-001 Phase 2
# ---------------------------------------------------------------------------

def test_worker_handle_calls_render_and_logs():
    from tgw.workers.agent_run_render import AgentRunRenderWorker

    worker = object.__new__(AgentRunRenderWorker)  # skip __init__ side effects
    worker.queue_name = 'agent_run_render'
    worker.config = {'fake': 'cfg'}

    with patch(
        'tgw.workers.agent_run_render.render_agent_runs_doc',
        return_value={'ok': True, 'path': '/x/TGW-Agent-Runs.md', 'count': 3},
    ) as mock_render:
        worker.handle({'payload_json': {'reason': 'agent_run_mutation'}})

    mock_render.assert_called_once_with({'fake': 'cfg'})


def test_worker_handle_raises_on_render_failure():
    from tgw.workers.agent_run_render import AgentRunRenderWorker

    worker = object.__new__(AgentRunRenderWorker)
    worker.queue_name = 'agent_run_render'
    worker.config = {}

    with patch(
        'tgw.workers.agent_run_render.render_agent_runs_doc',
        return_value={'ok': False, 'error': 'tracker unavailable'},
    ):
        try:
            worker.handle({'payload_json': {}})
            assert False, 'expected RuntimeError'
        except RuntimeError as exc:
            assert 'tracker unavailable' in str(exc)


def test_render_is_idempotent_atomic_replace(tmp_path):
    cfg = _cfg(tmp_path)
    with patch('tgw.queue.state_machine.list_agent_runs', return_value=[_row()]):
        render_agent_runs_doc(cfg)
        first_content = agent_runs_doc_path(cfg).read_text(encoding='utf-8')
    with patch('tgw.queue.state_machine.list_agent_runs', return_value=[]):
        render_agent_runs_doc(cfg)
        second_content = agent_runs_doc_path(cfg).read_text(encoding='utf-8')
    assert first_content != second_content
    assert not list(agent_runs_doc_path(cfg).parent.glob('.agent-runs-*'))
