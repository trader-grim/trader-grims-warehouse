"""Tests for tgw.todo CRUD operations and dead_letter state_machine helpers."""
from __future__ import annotations

import subprocess
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _no_render_enqueue():
    """Keep tests hermetic: todo mutations must not enqueue real plan_render jobs."""
    with patch('tgw.todo._enqueue_plan_render') as m:
        yield m

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


def test_todo_add_with_plandb_metadata():
    from tgw.todo import todo_add
    ctx, cur = _mock_conn(fetchone_return=(43,))
    with patch('tgw.todo._conn', ctx):
        result = todo_add('claude', 'phase 2', pp_ref='PP-PLANDB-001',
                          depends_on=[109], plan_anchor='PP-PLANDB-001 — Database-Driven Plan Builder')
    assert result['ok'] is True
    assert result['pp_ref'] == 'PP-PLANDB-001'
    assert result['depends_on'] == [109]
    # the INSERT received all three new columns
    sql, params = cur.execute.call_args[0]
    assert 'pp_ref' in sql and 'depends_on' in sql and 'plan_anchor' in sql
    assert params[4] == 'PP-PLANDB-001'
    assert params[5] == [109]


def test_todo_add_enqueues_plan_render(_no_render_enqueue):
    from tgw.todo import todo_add
    ctx, cur = _mock_conn(fetchone_return=(44,))
    with patch('tgw.todo._conn', ctx):
        todo_add('claude', 'test task')
    _no_render_enqueue.assert_called_once_with('todo_add')


# ---------------------------------------------------------------------------
# todo_set_meta
# ---------------------------------------------------------------------------

def test_todo_set_meta_partial_update():
    from tgw.todo import todo_set_meta
    ctx, cur = _mock_conn(fetchone_return=(7, 'claude', 'PP-X-001', [1, 2], None, 'normal', None))
    with patch('tgw.todo._conn', ctx):
        result = todo_set_meta(7, pp_ref='PP-X-001', depends_on=[1, 2])
    assert result['ok'] is True
    sql = cur.execute.call_args[0][0]
    assert 'pp_ref = %s' in sql and 'depends_on = %s' in sql
    assert 'plan_anchor = %s' not in sql  # not passed → untouched


def test_todo_set_meta_requires_a_field():
    from tgw.todo import todo_set_meta
    result = todo_set_meta(7)
    assert result['ok'] is False


def test_todo_set_meta_status_note():
    from tgw.todo import todo_set_meta
    note = 'in-progress; worktree: /opt/TGW/var/worktrees/todo-1732-cli'
    row = (1732, 'claude', 'PP-WORKFLOW-001', [], None, 'normal', note)
    ctx, cur = _mock_conn(fetchone_return=row)
    with patch('tgw.todo._conn', ctx):
        result = todo_set_meta(1732, status_note=note)
    assert result['status_note'] == note
    assert 'status_note = %s' in cur.execute.call_args.args[0]


def test_todo_set_meta_not_found():
    from tgw.todo import todo_set_meta
    ctx, cur = _mock_conn(fetchone_return=None)
    with patch('tgw.todo._conn', ctx):
        result = todo_set_meta(999, pp_ref='PP-X-001')
    assert result['ok'] is False
    assert 'not found' in result['error']


# ---------------------------------------------------------------------------
# brief + plan-section extraction
# ---------------------------------------------------------------------------

_PLAN_MD = """\
# TGW Master Plan

### PP-FOO-001 — The Foo Project

Foo design prose.

#### Phases

Phase table here.

### PP-BAR-001 — The Bar Project

Bar prose.
"""


def test_extract_plan_section(tmp_path):
    from tgw.todo import extract_plan_section
    plan = tmp_path / 'plan.md'
    plan.write_text(_PLAN_MD, encoding='utf-8')
    text = extract_plan_section(plan, 'PP-FOO-001')
    assert text.startswith('### PP-FOO-001')
    assert 'Foo design prose' in text
    assert 'Phase table here' in text       # deeper heading stays in section
    assert 'Bar prose' not in text          # next same-level heading ends it


def test_extract_plan_section_no_match(tmp_path):
    from tgw.todo import extract_plan_section
    plan = tmp_path / 'plan.md'
    plan.write_text(_PLAN_MD, encoding='utf-8')
    assert extract_plan_section(plan, 'PP-NOPE-999') == ''


def test_todo_brief_includes_plan_extract_and_deps(tmp_path):
    from tgw import todo as todo_mod
    plan = tmp_path / 'plan.md'
    plan.write_text(_PLAN_MD, encoding='utf-8')

    rows = {
        9: {'id': 9, 'agent': 'claude', 'priority': 16, 'body': 'build the foo',
            'source': 'round7', 'added_at': None, 'done_at': None,
            'pp_ref': 'PP-FOO-001', 'depends_on': [8], 'plan_anchor': None},
        8: {'id': 8, 'agent': 'claude', 'priority': 10, 'body': 'prereq task',
            'source': 'round7', 'added_at': None, 'done_at': 'sometime',
            'pp_ref': None, 'depends_on': [], 'plan_anchor': None},
    }
    with patch.object(todo_mod, 'todo_get', side_effect=lambda i: rows.get(i)):
        result = todo_mod.todo_brief(9, plan)
    assert result['ok'] is True
    brief = result['brief']
    assert 'todo #9' in brief
    assert 'build the foo' in brief
    assert 'Foo design prose' in brief          # plan extract present
    assert '#8 [done] prereq task' in brief     # dependency status
    assert 'You are Claude Code' in brief       # actor-specific contract
    assert 'read `CLAUDE.md`' in brief
    assert '/opt/TGW/src/trader-grims-warehouse' not in brief


def test_todo_brief_routes_codex_to_agents_not_claude(tmp_path):
    from tgw import todo as todo_mod
    plan = tmp_path / 'plan.md'
    plan.write_text(_PLAN_MD, encoding='utf-8')
    item = {
        'id': 10, 'agent': 'codex', 'priority': 10, 'body': 'safe task',
        'source': 'operator-plan', 'added_at': None, 'done_at': None,
        'pp_ref': None, 'depends_on': [], 'plan_anchor': None,
    }
    with patch.object(todo_mod, 'todo_get', return_value=item):
        brief = todo_mod.todo_brief(10, plan)['brief']
    assert 'Read repository `AGENTS.md`' in brief
    assert '`CLAUDE.md` is context only and does not govern you' in brief
    assert 'You are Claude Code' not in brief
    assert 'exact TGW worktree bound by the workflow' in brief


def test_todo_brief_not_found():
    from tgw import todo as todo_mod
    with patch.object(todo_mod, 'todo_get', return_value=None):
        result = todo_mod.todo_brief(999, None)
    assert result['ok'] is False


# ---------------------------------------------------------------------------
# _parse_depends
# ---------------------------------------------------------------------------

def test_parse_depends():
    from tgw.todo import _parse_depends
    assert _parse_depends(None) is None
    assert _parse_depends('') == []
    assert _parse_depends('12,14') == [12, 14]
    assert _parse_depends('12, 14') == [12, 14]


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


# ---------------------------------------------------------------------------
# --clip and --next flags (PP-TODO-001 extension, todo #122)
# ---------------------------------------------------------------------------

def _make_brief_args(**overrides):
    """Build a minimal argparse.Namespace for `tgw todo brief` tests."""
    import argparse
    defaults = dict(
        agent='brief', brief_id=None, seed=False, add=None, done=None,
        update=None, delegate=None, set_priority=None, set_meta=None,
        show_all=False, priority=50, source='session',
        pp=None, depends=None, anchor=None,
        clip=False, next_task=False, next_agent=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


_ROW_9 = {
    'id': 9, 'agent': 'claude', 'priority': 20, 'body': 'do the work',
    'source': 'test', 'added_at': None, 'done_at': None,
    'pp_ref': None, 'depends_on': [], 'plan_anchor': None,
}


def _approved_cfg(tmp_path):
    """Return a clean, pinned standalone Plan for CLI-facing todo tests."""
    root = tmp_path / 'standalone-plan'
    master = root / 'plan' / 'TGW-Master-Plan.md'
    master.parent.mkdir(parents=True)
    master.write_text('# TGW Master Plan\n', encoding='utf-8')
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test',
        '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'approved Plan',
    ], check=True)
    commit = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    return {
        'standalone_plan_root': root,
        'plan_approved_commit': commit,
        'plan_approved_solution_hash': 'sha256:' + 'a' * 64,
    }


def test_brief_clip_calls_push_clipboard(tmp_path):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(brief_id='9', clip=True)

    with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
        with patch.object(todo_mod, '_push_clipboard', return_value=True) as mock_clip:
            result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    mock_clip.assert_called_once()
    pushed = mock_clip.call_args[0][0]
    assert 'do the work' in pushed


def test_brief_clip_failure_prints_warning(tmp_path, capsys):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(brief_id='9', clip=True)

    with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
        with patch.object(todo_mod, '_push_clipboard', return_value=False):
            todo_mod.cmd_todo(cfg, args)

    out = capsys.readouterr().out
    assert 'clipboard' in out


def test_brief_no_clip_does_not_call_push_clipboard(tmp_path):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(brief_id='9', clip=False)

    with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
        with patch.object(todo_mod, '_push_clipboard', return_value=True) as mock_clip:
            todo_mod.cmd_todo(cfg, args)

    mock_clip.assert_not_called()


def test_brief_next_agent_gets_top_task(tmp_path):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(next_task=True, next_agent='gemini')

    gemini_row = dict(_ROW_9, id=7, agent='gemini', body='gemini top task')

    with patch.object(todo_mod, 'todo_top', return_value=gemini_row) as mock_top:
        with patch.object(todo_mod, 'todo_get', return_value=gemini_row):
            result = todo_mod.cmd_todo(cfg, args)

    mock_top.assert_called_once_with('gemini')
    assert result['ok'] is True
    assert result['id'] == 7


def test_brief_next_default_agent_is_claude(tmp_path):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(next_task=True, next_agent=None)  # no --agent given

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9) as mock_top:
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            todo_mod.cmd_todo(cfg, args)

    mock_top.assert_called_once_with('claude')


def test_brief_next_no_tasks_returns_error(tmp_path):
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_brief_args(next_task=True, next_agent='gemini')

    with patch.object(todo_mod, 'todo_top', return_value=None):
        result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is False
    assert 'gemini' in result['error']


def test_todo_top_queries_db():
    from tgw.todo import todo_top
    row_data = dict(_ROW_9, agent='gemini')
    ctx, cur = _mock_conn(fetchone_return=row_data)
    cur.fetchone.return_value = row_data

    # Use a RealDictCursor-like mock
    from unittest.mock import MagicMock
    cur2 = MagicMock()
    cur2.__enter__ = lambda s: s
    cur2.__exit__ = MagicMock(return_value=False)
    cur2.fetchone.return_value = row_data

    con = MagicMock()
    con.cursor.return_value = cur2
    con.__enter__ = lambda s: s
    con.__exit__ = MagicMock(return_value=False)

    from contextlib import contextmanager

    @contextmanager
    def ctx2():
        yield con

    with patch('tgw.todo._conn', ctx2):
        result = todo_top('gemini')

    assert result is not None
    sql = cur2.execute.call_args[0][0]
    assert 'agent = %s' in sql
    assert 'done_at IS NULL' in sql
    assert 'ORDER BY priority, id LIMIT 1' in sql


def test_todo_top_no_tasks_returns_none():
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from tgw.todo import todo_top

    cur = MagicMock()
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = None

    con = MagicMock()
    con.cursor.return_value = cur
    con.__enter__ = lambda s: s
    con.__exit__ = MagicMock(return_value=False)

    @contextmanager
    def ctx():
        yield con

    with patch('tgw.todo._conn', ctx):
        result = todo_top('claude')

    assert result is None


# ---------------------------------------------------------------------------
# _push_clipboard — pyperclip wrapper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# --next AGENT shorthand (todo #860)
# Replaces: tgw todo brief --next --agent AGENT --clip
# New form:  tgw todo --next [AGENT]   (clipboard always on)
# ---------------------------------------------------------------------------

def _make_shorthand_args(**overrides):
    """Build args for the --next AGENT shorthand (agent != 'brief')."""
    import argparse
    defaults = dict(
        agent=None, brief_id=None, seed=False, add=None, done=None,
        update=None, delegate=None, set_priority=None, set_meta=None,
        show_all=False, priority=50, source='session',
        pp=None, depends=None, anchor=None,
        clip=False, next_task=True, next_agent=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_shorthand_next_prints_and_clips_top_task(tmp_path):
    """tgw todo --next claude: prints brief and always copies to clipboard."""
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_shorthand_args(agent='claude')

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9) as mock_top:
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True) as mock_clip:
                result = todo_mod.cmd_todo(cfg, args)

    mock_top.assert_called_once_with('claude')
    assert result['ok'] is True
    mock_clip.assert_called_once()
    pushed = mock_clip.call_args[0][0]
    assert 'do the work' in pushed


def test_shorthand_next_default_agent_is_claude(tmp_path):
    """tgw todo --next (no positional) defaults agent to claude."""
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_shorthand_args(agent=None)  # no positional, no --agent

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9) as mock_top:
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                todo_mod.cmd_todo(cfg, args)

    mock_top.assert_called_once_with('claude')


def test_shorthand_next_agent_flag_overrides_positional(tmp_path):
    """tgw todo --next --agent gemini: --agent flag wins over absent positional."""
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    # --agent flag fills next_agent; no positional agent
    args = _make_shorthand_args(agent=None, next_agent='gemini')

    gemini_row = dict(_ROW_9, id=11, agent='gemini', body='gemini top task')

    with patch.object(todo_mod, 'todo_top', return_value=gemini_row) as mock_top:
        with patch.object(todo_mod, 'todo_get', return_value=gemini_row):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                result = todo_mod.cmd_todo(cfg, args)

    mock_top.assert_called_once_with('gemini')
    assert result['id'] == 11


def test_shorthand_next_no_tasks_returns_error(tmp_path, capsys):
    """tgw todo --next admin when admin queue is empty: prints message, returns error."""
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_shorthand_args(agent='admin')

    with patch.object(todo_mod, 'todo_top', return_value=None):
        result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is False
    assert 'admin' in result['error']
    out = capsys.readouterr().out
    assert 'admin' in out


def test_shorthand_next_clipboard_fail_prints_warning(tmp_path, capsys):
    """tgw todo --next: clipboard failure prints warning but still returns ok."""
    from tgw import todo as todo_mod
    cfg = _approved_cfg(tmp_path)
    args = _make_shorthand_args(agent='claude')

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=False):
                result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    out = capsys.readouterr().out
    assert 'clipboard' in out


def test_push_clipboard_returns_true_on_success():
    from tgw.todo import _push_clipboard

    with patch('pyperclip.copy') as mock_copy:
        result = _push_clipboard('hello')

    mock_copy.assert_called_once_with('hello')
    assert result is True


def test_push_clipboard_returns_false_on_exception():
    from tgw.todo import _push_clipboard

    with patch('pyperclip.copy', side_effect=Exception('no clipboard')):
        result = _push_clipboard('hello')

    assert result is False


# ---------------------------------------------------------------------------
# reasoning column
# ---------------------------------------------------------------------------

def test_todo_add_reasoning_high():
    from tgw.todo import todo_add
    ctx, cur = _mock_conn(fetchone_return=(50,))
    with patch('tgw.todo._conn', ctx):
        result = todo_add('claude', 'hard task', reasoning='high')
    assert result['ok'] is True
    assert result['reasoning'] == 'high'
    sql, params = cur.execute.call_args[0]
    assert 'reasoning' in sql
    assert 'high' in params


def test_todo_set_meta_reasoning():
    from tgw.todo import todo_set_meta
    ctx, cur = _mock_conn(fetchone_return=(7, 'claude', None, [], None, 'low', None))
    with patch('tgw.todo._conn', ctx):
        result = todo_set_meta(7, reasoning='low')
    assert result['ok'] is True
    assert result['reasoning'] == 'low'
    sql = cur.execute.call_args[0][0]
    assert 'reasoning = %s' in sql


def test_listing_shows_high_badge(tmp_path, capsys):
    import argparse

    from tgw import todo as todo_mod

    args = argparse.Namespace(
        agent='claude', brief_id=None, seed=False, add=None, done=None,
        update=None, delegate=None, set_priority=None, set_meta=None,
        show_all=False, priority=50, source='session',
        pp=None, depends=None, anchor=None,
        clip=False, next_task=False, next_agent=None,
        nextloop=False, reasoning='normal',
    )
    items = [{'id': 1, 'agent': 'claude', 'priority': 50, 'body': 'do hard thing',
              'done_at': None, 'pp_ref': None, 'depends_on': [], 'reasoning': 'high'}]
    with patch.object(todo_mod, 'todo_list', return_value=items):
        with patch.object(todo_mod, 'open_ids', return_value=set()):
            todo_mod.cmd_todo({}, args)
    out = capsys.readouterr().out
    assert '[high]' in out


def test_listing_no_badge_for_normal(tmp_path, capsys):
    import argparse

    from tgw import todo as todo_mod

    args = argparse.Namespace(
        agent='claude', brief_id=None, seed=False, add=None, done=None,
        update=None, delegate=None, set_priority=None, set_meta=None,
        show_all=False, priority=50, source='session',
        pp=None, depends=None, anchor=None,
        clip=False, next_task=False, next_agent=None,
        nextloop=False, reasoning='normal',
    )
    items = [{'id': 2, 'agent': 'claude', 'priority': 50, 'body': 'normal task',
              'done_at': None, 'pp_ref': None, 'depends_on': [], 'reasoning': 'normal'}]
    with patch.object(todo_mod, 'todo_list', return_value=items):
        with patch.object(todo_mod, 'open_ids', return_value=set()):
            todo_mod.cmd_todo({}, args)
    out = capsys.readouterr().out
    assert '[normal]' not in out
    assert '[high]' not in out
    assert '[low]' not in out


def test_brief_includes_reasoning_when_high(tmp_path):
    from tgw.todo import todo_brief
    item = {
        'id': 5, 'agent': 'claude', 'priority': 20, 'body': 'hard work',
        'source': 'test', 'added_at': None, 'done_at': None,
        'pp_ref': None, 'depends_on': [], 'plan_anchor': None,
        'reasoning': 'high',
    }
    plan = tmp_path / 'plan.md'
    plan.write_text('', encoding='utf-8')
    with patch('tgw.todo.todo_get', return_value=item):
        result = todo_brief(5, plan)
    assert result['ok'] is True
    assert '**Reasoning:** high' in result['brief']


def test_brief_omits_reasoning_when_normal(tmp_path):
    from tgw.todo import todo_brief
    item = {
        'id': 6, 'agent': 'claude', 'priority': 20, 'body': 'easy work',
        'source': 'test', 'added_at': None, 'done_at': None,
        'pp_ref': None, 'depends_on': [], 'plan_anchor': None,
        'reasoning': 'normal',
    }
    plan = tmp_path / 'plan.md'
    plan.write_text('', encoding='utf-8')
    with patch('tgw.todo.todo_get', return_value=item):
        result = todo_brief(6, plan)
    assert result['ok'] is True
    assert '**Reasoning:**' not in result['brief']


# ---------------------------------------------------------------------------
# _next_interactive: less pager + done/skip prompt (todo #865)
# ---------------------------------------------------------------------------

def _interactive_cfg(tmp_path):
    return _approved_cfg(tmp_path)


def test_next_interactive_y_marks_done(tmp_path):
    """Y answer marks the task done and returns action='done'."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                with patch.object(todo_mod, '_tty_prompt', return_value='y') as mock_prompt:
                    with patch.object(todo_mod, '_pager_run'):
                        with patch.object(todo_mod, 'todo_done', return_value={'ok': True, 'id': 9}) as mock_done:
                            result = todo_mod._next_interactive(cfg, 'claude')

    assert result['ok'] is True
    assert result['action'] == 'done'
    assert result['id'] == 9
    mock_done.assert_called_once_with(9)
    assert 'complete' in mock_prompt.call_args[0][0].lower()


def test_next_interactive_empty_answer_marks_done(tmp_path):
    """Empty answer (just Enter) is treated as Y — mark done."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                with patch.object(todo_mod, '_tty_prompt', return_value=''):
                    with patch.object(todo_mod, '_pager_run'):
                        with patch.object(todo_mod, 'todo_done', return_value={'ok': True, 'id': 9}):
                            result = todo_mod._next_interactive(cfg, 'claude')

    assert result['action'] == 'done'


def test_next_interactive_n_leaves_open(tmp_path):
    """n answer leaves the task open and returns action='left_open'."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                with patch.object(todo_mod, '_tty_prompt', return_value='n'):
                    with patch.object(todo_mod, '_pager_run'):
                        with patch.object(todo_mod, 'todo_done') as mock_done:
                            result = todo_mod._next_interactive(cfg, 'claude')

    assert result['ok'] is True
    assert result['action'] == 'left_open'
    mock_done.assert_not_called()


def test_next_interactive_s_skips_to_next(tmp_path):
    """s answer skips the current task and shows the next one (loops once)."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)

    row_a = dict(_ROW_9, id=9, body='first task')
    row_b = dict(_ROW_9, id=10, body='second task')
    top_returns = [row_a, row_b]

    with patch.object(todo_mod, 'todo_top', side_effect=top_returns):
        with patch.object(todo_mod, 'todo_get', side_effect=[row_a, row_b]):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                # First call: 's'; second call: 'n' to stop
                with patch.object(todo_mod, '_tty_prompt', side_effect=['s', 'n']):
                    with patch.object(todo_mod, '_pager_run'):
                        with patch.object(todo_mod, 'todo_done') as mock_done:
                            result = todo_mod._next_interactive(cfg, 'claude')

    assert result['action'] == 'left_open'
    assert result['id'] == 10
    mock_done.assert_not_called()


def test_next_interactive_less_not_found_falls_back(tmp_path, capsys):
    """If less is not installed, falls back to plain print."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)

    with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
        with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
            with patch.object(todo_mod, '_push_clipboard', return_value=True):
                with patch.object(todo_mod, '_tty_prompt', return_value='n'):
                    with patch.object(todo_mod, '_pager_run', side_effect=FileNotFoundError):
                        with patch.object(todo_mod, 'todo_done'):
                            todo_mod._next_interactive(cfg, 'claude')

    out = capsys.readouterr().out
    assert 'do the work' in out  # brief printed to console instead


def test_next_noninteractive_path_used_when_not_tty(tmp_path):
    """When stdout is not a TTY, cmd_todo uses the non-interactive path (no less, no prompt)."""
    from tgw import todo as todo_mod
    cfg = _interactive_cfg(tmp_path)
    args = _make_shorthand_args(agent='claude')

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = False
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = False
            with patch.object(todo_mod, 'todo_top', return_value=_ROW_9):
                with patch.object(todo_mod, 'todo_get', return_value=_ROW_9):
                    with patch.object(todo_mod, '_push_clipboard', return_value=True):
                        with patch.object(todo_mod, '_next_interactive') as mock_interactive:
                            result = todo_mod.cmd_todo(cfg, args)

    mock_interactive.assert_not_called()
    assert result['ok'] is True


def test_tty_prompt_returns_empty_on_oserror():
    """_tty_prompt returns '' if /dev/tty is unavailable."""
    from tgw.todo import _tty_prompt

    with patch('builtins.open', side_effect=OSError('no tty')):
        result = _tty_prompt('prompt: ')

    assert result == ''


# ---------------------------------------------------------------------------
# --nextloop AGENT (loop --next until exhausted or user quits)
# ---------------------------------------------------------------------------

def _make_nextloop_args(**overrides):
    import argparse
    defaults = dict(
        agent=None, brief_id=None, seed=False, add=None, done=None,
        update=None, delegate=None, set_priority=None, set_meta=None,
        show_all=False, priority=50, source='session',
        pp=None, depends=None, anchor=None,
        clip=False, next_task=False, nextloop=True, next_agent=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


_ROW_A = {'id': 20, 'agent': 'claude', 'body': 'task alpha', 'priority': 50,
          'source': 'test', 'added_at': None, 'done_at': None,
          'pp_ref': None, 'depends_on': [], 'plan_anchor': None}
_ROW_B = {'id': 21, 'agent': 'claude', 'body': 'task beta', 'priority': 50,
          'source': 'test', 'added_at': None, 'done_at': None,
          'pp_ref': None, 'depends_on': [], 'plan_anchor': None}


def test_nextloop_marks_done_and_continues(tmp_path):
    """y answer marks task done and fetches the next one."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent='claude')

    top_seq = [_ROW_A, _ROW_B, None]
    top_iter = iter(top_seq)

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch.object(todo_mod, 'todo_top', side_effect=lambda _: next(top_iter)):
                with patch.object(todo_mod, 'todo_get', side_effect=lambda tid: dict(_ROW_A, id=tid)):
                    with patch.object(todo_mod, '_push_clipboard', return_value=True):
                        with patch.object(todo_mod, '_tty_prompt', return_value='y'):
                            with patch.object(todo_mod, 'todo_done') as mock_done:
                                with patch.object(todo_mod, '_pager_run'):
                                    result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    assert result['done_count'] == 2
    assert result['skipped_count'] == 0
    assert mock_done.call_count == 2


def test_nextloop_skip_continues_without_marking_done(tmp_path):
    """s answer skips the task and fetches the next one without marking done."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent='claude')

    top_seq = [_ROW_A, None]
    top_iter = iter(top_seq)

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch.object(todo_mod, 'todo_top', side_effect=lambda _: next(top_iter)):
                with patch.object(todo_mod, 'todo_get', return_value=_ROW_A):
                    with patch.object(todo_mod, '_push_clipboard', return_value=True):
                        with patch.object(todo_mod, '_tty_prompt', return_value='s'):
                            with patch.object(todo_mod, 'todo_done') as mock_done:
                                with patch.object(todo_mod, '_pager_run'):
                                    result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    assert result['done_count'] == 0
    assert result['skipped_count'] == 1
    mock_done.assert_not_called()


def test_nextloop_quit_exits_immediately(tmp_path):
    """q answer leaves task open and exits the loop."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent='claude')

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch.object(todo_mod, 'todo_top', return_value=_ROW_A):
                with patch.object(todo_mod, 'todo_get', return_value=_ROW_A):
                    with patch.object(todo_mod, '_push_clipboard', return_value=True):
                        with patch.object(todo_mod, '_tty_prompt', return_value='q'):
                            with patch.object(todo_mod, 'todo_done') as mock_done:
                                with patch.object(todo_mod, '_pager_run'):
                                    result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    assert result['done_count'] == 0
    assert result['action'] == 'loop_exit'
    mock_done.assert_not_called()


def test_nextloop_empty_queue_returns_ok(tmp_path):
    """--nextloop with no tasks returns ok immediately."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent='claude')

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch.object(todo_mod, 'todo_top', return_value=None):
                with patch.object(todo_mod, '_pager_run'):
                    result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is True
    assert result['done_count'] == 0


def test_nextloop_requires_tty(tmp_path, capsys):
    """--nextloop in non-interactive context returns error."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent='claude')

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = False
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = todo_mod.cmd_todo(cfg, args)

    assert result['ok'] is False
    assert 'TTY' in result['error']


def test_nextloop_default_agent_is_claude(tmp_path):
    """--nextloop with no agent arg defaults to claude."""
    from tgw import todo as todo_mod

    cfg = _approved_cfg(tmp_path)
    args = _make_nextloop_args(agent=None, next_agent=None)

    with patch('sys.stdout') as mock_stdout:
        mock_stdout.isatty.return_value = True
        with patch('sys.stdin') as mock_stdin:
            mock_stdin.isatty.return_value = True
            with patch.object(todo_mod, '_nextloop_interactive') as mock_loop:
                mock_loop.return_value = {'ok': True, 'done_count': 0, 'skipped_count': 0, 'action': 'loop_exit'}
                todo_mod.cmd_todo(cfg, args)

    mock_loop.assert_called_once_with(cfg, 'claude')
