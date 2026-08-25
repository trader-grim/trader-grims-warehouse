"""Tests for tgw.plan_render — generated taskboard + plan check (PP-PLANDB-001).

plan_brief() coverage (PP-KNOWLEDGE-001 / todo #1439, #1520 follow-up
refactor) lives here per item 5 of Tigwa's reviewed v1 submission — the
deterministic parser/retrieval logic moved out of tgw.mcp_server into this
module; tests/test_mcp_server.py retains only FastMCP-boundary coverage.
"""
from __future__ import annotations

import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tgw.plan_render import (
    PLAN_BRIEF_MAX_SOURCE_BYTES,
    _fmt_ids,
    _parse_plan_sections,
    _parse_size,
    _plan_heading_map,
    build_taskboard,
    format_plan_check,
    format_plan_status,
    plan_brief,
    plan_check,
    plan_status,
    render_taskboard,
    taskboard_path,
)

NOW = datetime(2026, 6, 12, 12, 0, tzinfo=timezone.utc)


def _item(id, agent='claude', priority=50, body='task body', done_at=None,
          pp_ref=None, depends_on=None, plan_anchor=None):
    return {'id': id, 'agent': agent, 'priority': priority, 'body': body,
            'source': 'test', 'added_at': NOW, 'done_at': done_at,
            'pp_ref': pp_ref, 'depends_on': depends_on or [],
            'plan_anchor': plan_anchor}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_parse_size():
    assert _parse_size('Round7 p16 S: do the thing') == 'S'
    assert _parse_size('Round7 p45 XS: tiny thing') == 'XS'
    assert _parse_size('Round7 p72 M (GATED: after #20): thing') == 'M'
    assert _parse_size('no size token here') == ''


def test_plan_heading_map(tmp_path):
    plan = tmp_path / 'plan.md'
    plan.write_text(
        '# Plan\n### PP-FOO-001 — The Foo Project\nprose PP-IGNORED-001 in body\n'
        '### PP-BAR-001 — Bar (design)\n'
        '### PP-COMPOUND-NAME-001 — Multi-word project\n',
        encoding='utf-8',
    )
    m = _plan_heading_map(plan)
    assert m['PP-FOO-001'] == 'PP-FOO-001 — The Foo Project'
    assert m['PP-BAR-001'] == 'PP-BAR-001 — Bar (design)'
    assert m['PP-COMPOUND-NAME-001'] == 'PP-COMPOUND-NAME-001 — Multi-word project'
    assert 'PP-IGNORED-001' not in m  # body text is not a heading


def test_plan_heading_map_missing_file(tmp_path):
    assert _plan_heading_map(tmp_path / 'nope.md') == {}


# ---------------------------------------------------------------------------
# build_taskboard
# ---------------------------------------------------------------------------

def test_build_groups_by_agent_and_sorts_by_priority():
    items = [
        _item(1, agent='claude', priority=50, body='later'),
        _item(2, agent='claude', priority=10, body='first'),
        _item(3, agent='admin', priority=20, body='admin job'),
    ]
    text = build_taskboard(items, {}, now=NOW)
    assert '## admin (1 open)' in text
    assert '## claude (2 open)' in text
    assert text.index('| 2 |') < text.index('| 1 |')  # p10 before p50


def test_build_blocker_badges():
    items = [
        _item(1, body='blocker task'),
        _item(2, body='blocked task', depends_on=[1]),
        _item(3, body='unblocked task', depends_on=[99]),  # 99 not open
    ]
    text = build_taskboard(items, {}, now=NOW)
    lines = {ln.split('|')[1].strip(): ln for ln in text.splitlines() if ln.startswith('|')}
    assert '⛔ #1' in lines['2']
    assert '✓ deps done' in lines['3']


def test_build_plan_links():
    items = [
        _item(1, body='anchored', pp_ref='PP-FOO-001',
              plan_anchor='PP-FOO-001 — The Foo Project'),
        _item(2, body='ref via heading map', pp_ref='PP-BAR-001'),
        _item(3, body='ref without anchor', pp_ref='PP-LOST-001'),
    ]
    headings = {'PP-BAR-001': 'PP-BAR-001 — Bar (design)'}
    text = build_taskboard(items, headings, now=NOW)
    assert '[[TGW-Master-Plan#PP-FOO-001 — The Foo Project\\|PP-FOO-001]]' in text
    assert '[[TGW-Master-Plan#PP-BAR-001 — Bar (design)\\|PP-BAR-001]]' in text
    assert '`PP-LOST-001`' in text


def test_build_done_this_week_window():
    items = [
        _item(1, body='open task'),
        _item(2, body='recent done', done_at=NOW - timedelta(days=2)),
        _item(3, body='old done', done_at=NOW - timedelta(days=30)),
    ]
    text = build_taskboard(items, {}, now=NOW)
    assert '## Done this week (1)' in text
    assert 'recent done' in text
    assert 'old done' not in text


def test_build_escapes_pipes_in_body():
    items = [_item(1, body='uses a | pipe')]
    text = build_taskboard(items, {}, now=NOW)
    assert 'uses a \\| pipe' in text


def test_build_has_generated_warning():
    text = build_taskboard([], {}, now=NOW)
    assert 'GENERATED FILE — DO NOT EDIT' in text
    assert '_Rendered 2026-06-12 12:00 UTC' in text


# ---------------------------------------------------------------------------
# render_taskboard (file write)
# ---------------------------------------------------------------------------

def _cfg(tmp_path):
    root = tmp_path / 'standalone-plan'
    master = root / 'plan' / 'TGW-Master-Plan.md'
    master.parent.mkdir(parents=True)
    master.write_text('### PP-FOO-001 — The Approved Project\n', encoding='utf-8')
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
    legacy_master.write_text('### PP-FOO-001 — Legacy Vault Plan\n', encoding='utf-8')
    return {
        'plan_vault_path': vault,
        'plan_render_root': tmp_path / 'rendered',
        # Prove rendering ignores this stale compatibility field.
        'plan_master_path': legacy_master,
        'standalone_plan_root': root,
        'plan_approved_commit': commit,
        'plan_approved_solution_hash': 'sha256:' + 'a' * 64,
    }


def test_render_writes_file(tmp_path):
    cfg = _cfg(tmp_path)
    items = [_item(1, body='open task', pp_ref='PP-FOO-001'),
             _item(2, body='done task', done_at=datetime.now(tz=timezone.utc))]
    with patch('tgw.todo.todo_list', return_value=items):
        result = render_taskboard(cfg)
    assert result['ok'] is True
    assert result['open'] == 1
    assert result['done_week'] == 1
    assert result['plan_identity'] == {
        'plan_root': str(cfg['standalone_plan_root']),
        'plan_commit': cfg['plan_approved_commit'],
        'solution_hash': cfg['plan_approved_solution_hash'],
        'master_plan_path': str(cfg['standalone_plan_root'] / 'plan' / 'TGW-Master-Plan.md'),
    }
    board = taskboard_path(cfg)
    assert board.exists()
    content = board.read_text(encoding='utf-8')
    assert 'open task' in content
    assert '[[TGW-Master-Plan#PP-FOO-001 — The Approved Project\\|PP-FOO-001]]' in content
    assert cfg['plan_approved_commit'] in content
    assert cfg['plan_approved_solution_hash'] in content
    # no temp file left behind
    assert not list(board.parent.glob('.taskboard-*'))
    assert 'plan' not in board.relative_to(tmp_path).parts


def test_render_uses_explicit_todo_database_without_global_rebind(tmp_path):
    cfg = _cfg(tmp_path)
    cfg['postgres_dsn'] = 'dbname=tgw_lib_dev_state_machine'

    with patch('tgw.todo.todo_list', return_value=[]) as todo_list:
        result = render_taskboard(cfg)

    assert result['ok'] is True
    todo_list.assert_called_once_with(
        show_all=True,
        dsn='dbname=tgw_lib_dev_state_machine',
    )


def test_render_reports_tracker_failure(tmp_path):
    cfg = _cfg(tmp_path)
    with patch('tgw.todo.todo_list', side_effect=RuntimeError('db down')):
        result = render_taskboard(cfg)
    assert result['ok'] is False
    assert 'db down' in result['error']
    assert result['plan_identity']['plan_commit'] == cfg['plan_approved_commit']
    assert not taskboard_path(cfg).exists()


@pytest.mark.parametrize(
    ('field', 'code'),
    [
        ('plan_approved_commit', 'approved_plan_commit_required'),
        ('plan_approved_solution_hash', 'approved_solution_required'),
    ],
)
def test_render_refuses_unbound_plan_before_reading_tracker(tmp_path, field, code):
    cfg = _cfg(tmp_path)
    cfg.pop(field)
    with patch('tgw.todo.todo_list') as todo_list:
        result = render_taskboard(cfg)
    assert result == {
        'ok': False,
        'error': f'approved Plan binding unavailable: {code}',
        'code': code,
    }
    todo_list.assert_not_called()
    assert not taskboard_path(cfg).exists()


def test_render_refuses_dirty_or_mismatched_approved_plan_before_reading_tracker(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg['standalone_plan_root'] / 'plan' / 'TGW-Master-Plan.md').write_text('dirty\n', encoding='utf-8')
    with patch('tgw.todo.todo_list') as todo_list:
        result = render_taskboard(cfg)
    assert result['ok'] is False
    assert result['code'] == 'source_changed'
    todo_list.assert_not_called()


# ---------------------------------------------------------------------------
# _parse_plan_sections
# ---------------------------------------------------------------------------

def _plan_with(text: str, tmp_path):
    p = tmp_path / 'TGW-Master-Plan.md'
    p.write_text(text, encoding='utf-8')
    return p


def test_parse_plan_sections_extracts_pp_refs(tmp_path):
    plan = _plan_with(
        '### PP-FOO-001 — The Foo Project\n'
        '### PP-BAR-001 ✅ DONE 2026-01-01 — Bar done\n'
        '### PP-COMPOUND-NAME-001 — Multi-word project\n'
        'body text mentioning PP-BODY-001 not a heading\n',
        tmp_path,
    )
    pp_map, done_set, headings = _parse_plan_sections(plan)
    assert 'PP-FOO-001' in pp_map
    assert 'PP-BAR-001' in pp_map
    assert 'PP-COMPOUND-NAME-001' in pp_map
    assert 'PP-BODY-001' not in pp_map  # body mention, not heading
    assert 'PP-BAR-001' in done_set
    assert 'PP-FOO-001' not in done_set
    assert 'PP-BAR-001 ✅ DONE 2026-01-01 — Bar done' in headings


def test_parse_plan_sections_missing_file(tmp_path):
    pp_map, done_set, headings = _parse_plan_sections(tmp_path / 'missing.md')
    assert pp_map == {}
    assert done_set == set()
    assert headings == set()


# ---------------------------------------------------------------------------
# _fmt_ids
# ---------------------------------------------------------------------------

def test_fmt_ids_short():
    assert _fmt_ids([1, 2, 3]) == '#1, #2, #3'


def test_fmt_ids_capped():
    result = _fmt_ids([1, 2, 3, 4, 5, 6, 7])
    assert result.startswith('#1, #2')
    assert '+2 more' in result


# ---------------------------------------------------------------------------
# plan_check
# ---------------------------------------------------------------------------

PLAN_TEXT = """\
# TGW Master Plan
### PP-FOO-001 — Foo project
### PP-DONE-001 ✅ DONE 2026-01-01 — Done project
### PP-BAR-001 — Bar project (design)
"""


def _approved_plan_cfg(tmp_path, *, name='standalone-plan'):
    root = tmp_path / name
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test',
        '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty',
        '-qm', 'initial approved Plan',
    ], check=True)
    commit = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()
    return {
        'standalone_plan_root': root,
        'plan_approved_commit': commit,
        'plan_approved_solution_hash': 'sha256:' + 'a' * 64,
    }


def _commit_approved_plan(cfg, message='update approved Plan'):
    root = cfg['standalone_plan_root']
    subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
    subprocess.run([
        'git', '-C', str(root), '-c', 'user.name=Test',
        '-c', 'user.email=test@example.invalid', 'commit', '-qm', message,
    ], check=True)
    cfg['plan_approved_commit'] = subprocess.check_output(
        ['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True,
    ).strip()


def _check_cfg(tmp_path):
    cfg = _approved_plan_cfg(tmp_path)
    plan = cfg['standalone_plan_root'] / 'plan' / 'TGW-Master-Plan.md'
    plan.parent.mkdir(parents=True)
    plan.write_text(PLAN_TEXT, encoding='utf-8')
    _commit_approved_plan(cfg)
    return cfg


def _open_item(id, body='task', pp_ref=None, plan_anchor=None):
    return {
        'id': id, 'agent': 'claude', 'priority': 50, 'body': body,
        'source': 'test', 'added_at': NOW, 'done_at': None,
        'pp_ref': pp_ref, 'depends_on': [], 'plan_anchor': plan_anchor,
    }


def _done_item(id, body='done task', pp_ref=None):
    return {
        'id': id, 'agent': 'claude', 'priority': 50, 'body': body,
        'source': 'test', 'added_at': NOW,
        'done_at': NOW,
        'pp_ref': pp_ref, 'depends_on': [], 'plan_anchor': None,
    }


def test_plan_check_all_clear(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [
        _open_item(1, pp_ref='PP-FOO-001'),
        _done_item(2, pp_ref='PP-DONE-001'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    assert result['ok'] is True
    assert result['issues'] == []
    assert result['summary'] == 'all clear'


def test_plan_check_orphaned_pp_ref(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [_open_item(1, pp_ref='PP-MISSING-001')]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    issues = result['issues']
    assert any(i['kind'] == 'orphaned_pp_ref' and 'PP-MISSING-001' in i['message'] for i in issues)
    assert result['counts']['warnings'] >= 1


def test_plan_check_done_mismatch(tmp_path):
    cfg = _check_cfg(tmp_path)
    # PP-DONE-001 is ✅ in plan but todo is still open
    items = [_open_item(1, pp_ref='PP-DONE-001')]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    issues = result['issues']
    assert any(i['kind'] == 'done_mismatch' and 'PP-DONE-001' in i['message'] for i in issues)


def test_plan_check_orphaned_plan_anchor(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [_open_item(1, pp_ref='PP-FOO-001', plan_anchor='No Such Heading')]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    issues = result['issues']
    assert any(i['kind'] == 'orphaned_plan_anchor' and 'No Such Heading' in i['message'] for i in issues)


def test_plan_check_stale_round_tag(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [
        _open_item(1, body='Round3 p10 S: old task'),
        _done_item(2, body='Round5 p20 S: new done task'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    issues = result['issues']
    assert any(i['kind'] == 'stale_round_tag' and 'Round3' in i['message'] for i in issues)
    assert result['counts']['infos'] >= 1


def test_plan_check_no_stale_flag_for_current_round(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [
        _open_item(1, body='Round7 p10 S: current round task'),
        _done_item(2, body='Round7 p20 S: done task same round'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    stale = [i for i in result['issues'] if i['kind'] == 'stale_round_tag']
    assert stale == []


def test_plan_check_deduplicates_anchor_issues(tmp_path):
    cfg = _check_cfg(tmp_path)
    # Two todos with the same bad anchor → only one issue
    items = [
        _open_item(1, plan_anchor='Bad Anchor'),
        _open_item(2, plan_anchor='Bad Anchor'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    anchor_issues = [i for i in result['issues'] if i['kind'] == 'orphaned_plan_anchor']
    assert len(anchor_issues) == 1


def test_plan_check_groups_orphaned_refs(tmp_path):
    cfg = _check_cfg(tmp_path)
    items = [
        _open_item(1, pp_ref='PP-GHOST-001'),
        _open_item(2, pp_ref='PP-GHOST-001'),
        _open_item(3, pp_ref='PP-GHOST-001'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_check(cfg)
    orphan_issues = [i for i in result['issues'] if i['kind'] == 'orphaned_pp_ref']
    assert len(orphan_issues) == 1
    assert '3 open todo(s)' in orphan_issues[0]['message']


def test_plan_check_tracker_failure(tmp_path):
    cfg = _check_cfg(tmp_path)
    with patch('tgw.todo.todo_list', side_effect=RuntimeError('db down')):
        result = plan_check(cfg)
    assert result['ok'] is False
    assert 'db down' in result['error']


def test_plan_check_missing_plan(tmp_path):
    cfg = _approved_plan_cfg(tmp_path)
    result = plan_check(cfg)
    assert result['ok'] is False


def test_format_plan_check_all_clear(tmp_path):
    result = {'ok': True, 'issues': [], 'counts': {'warnings': 0, 'infos': 0}, 'summary': 'all clear'}
    text = format_plan_check(result)
    assert 'all clear' in text


def test_format_plan_check_with_issues(tmp_path):
    result = {
        'ok': True,
        'issues': [
            {'kind': 'orphaned_pp_ref', 'severity': 'warning',
             'message': "pp_ref 'PP-X-001' has no plan section heading (1 open todo(s): #5)"},
        ],
        'counts': {'warnings': 1, 'infos': 0},
        'summary': '1 warning(s), 0 info(s)',
    }
    text = format_plan_check(result)
    assert 'PP-X-001' in text
    assert '⚠' in text


# ---------------------------------------------------------------------------
# plan_status — PP-PLANDB-001 Phase 4
# ---------------------------------------------------------------------------

def _ps_item(id, agent='claude', priority=50, body='task', done_at=None,
             pp_ref=None, depends_on=None, added_at=NOW):
    return {
        'id': id, 'agent': agent, 'priority': priority, 'body': body,
        'source': 'test', 'added_at': added_at, 'done_at': done_at,
        'pp_ref': pp_ref, 'depends_on': depends_on or [],
        'plan_anchor': None,
    }


_PS_CFG = {'plan_master_path': None, 'plan_vault_path': None}


def test_plan_status_no_tracked_todos():
    items = [_ps_item(1, body='no pp_ref')]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG)
    assert result['ok'] is True
    assert result['rows'] == []


def test_plan_status_basic_open_and_done_counts():
    items = [
        _ps_item(1, pp_ref='PP-FOO-001', body='open A'),
        _ps_item(2, pp_ref='PP-FOO-001', body='open B'),
        _ps_item(3, pp_ref='PP-FOO-001', body='done task', done_at=NOW),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG)
    assert result['ok'] is True
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['pp_ref'] == 'PP-FOO-001'
    assert row['open'] == 2
    assert row['done'] == 1
    assert row['blocked'] == 0


def test_plan_status_blocked_detection():
    items = [
        _ps_item(1, pp_ref='PP-FOO-001', body='blocker'),
        _ps_item(2, pp_ref='PP-FOO-001', body='blocked', depends_on=[1]),
        _ps_item(3, pp_ref='PP-FOO-001', body='unblocked', depends_on=[99]),  # 99 not in open set
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG)
    row = result['rows'][0]
    assert row['blocked'] == 1  # only #2 is blocked; #99 is not in open set


def test_plan_status_groups_multiple_pp_items():
    items = [
        _ps_item(1, pp_ref='PP-AAA-001', body='aaa task'),
        _ps_item(2, pp_ref='PP-BBB-001', body='bbb task'),
        _ps_item(3, pp_ref='PP-BBB-001', body='bbb done', done_at=NOW),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG)
    refs = [r['pp_ref'] for r in result['rows']]
    assert refs == ['PP-AAA-001', 'PP-BBB-001']
    bbb = next(r for r in result['rows'] if r['pp_ref'] == 'PP-BBB-001')
    assert bbb['open'] == 1
    assert bbb['done'] == 1


def test_plan_status_filter_by_pp_ref():
    items = [
        _ps_item(1, pp_ref='PP-AAA-001', body='aaa task'),
        _ps_item(2, pp_ref='PP-BBB-001', body='bbb task'),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG, pp_ref='PP-BBB-001')
    assert len(result['rows']) == 1
    assert result['rows'][0]['pp_ref'] == 'PP-BBB-001'


def test_plan_status_latest_prefers_done_at():
    earlier = NOW - timedelta(days=5)
    later = NOW - timedelta(days=1)
    items = [
        _ps_item(1, pp_ref='PP-FOO-001', body='old open', added_at=earlier),
        _ps_item(2, pp_ref='PP-FOO-001', body='recently done', done_at=later, added_at=earlier),
    ]
    with patch('tgw.todo.todo_list', return_value=items):
        result = plan_status(_PS_CFG)
    row = result['rows'][0]
    assert row['latest'] == later
    assert 'recently done' in row['latest_body']


def test_plan_status_tracker_failure():
    with patch('tgw.todo.todo_list', side_effect=RuntimeError('db down')):
        result = plan_status(_PS_CFG)
    assert result['ok'] is False
    assert 'db down' in result['error']
    assert result['rows'] == []


def test_format_plan_status_no_rows():
    text = format_plan_status({'ok': True, 'rows': []})
    assert 'no PP-* items' in text


def test_format_plan_status_with_rows():
    rows = [
        {'pp_ref': 'PP-FOO-001', 'open': 2, 'done': 1, 'blocked': 0,
         'latest': NOW, 'latest_body': 'fix the thing'},
    ]
    text = format_plan_status({'ok': True, 'rows': rows})
    assert 'PP-FOO-001' in text
    assert '2 open' in text
    assert '1 done' in text
    assert '2026-06-12' in text
    assert 'fix the thing' in text


def test_format_plan_status_blocked_note():
    rows = [
        {'pp_ref': 'PP-FOO-001', 'open': 3, 'done': 0, 'blocked': 1,
         'latest': NOW, 'latest_body': 'blocked task'},
    ]
    text = format_plan_status({'ok': True, 'rows': rows})
    assert '3 open (1 blocked)' in text


def test_format_plan_status_error():
    text = format_plan_status({'ok': False, 'error': 'db down', 'rows': []})
    assert 'Error' in text
    assert 'db down' in text


def test_format_plan_status_truncates_long_body():
    long_body = 'x' * 80
    rows = [
        {'pp_ref': 'PP-FOO-001', 'open': 1, 'done': 0, 'blocked': 0,
         'latest': NOW, 'latest_body': long_body},
    ]
    text = format_plan_status({'ok': True, 'rows': rows})
    assert '…' in text


# ---------------------------------------------------------------------------
# plan_brief — bounded, exact-source Master Plan retrieval
# (PP-KNOWLEDGE-001 / todo #1439, #1520 follow-up refactor)
# ---------------------------------------------------------------------------

def _brief_cfg(tmp_path):
    return _approved_plan_cfg(tmp_path)


def _write_plan(cfg, text):
    path = cfg['standalone_plan_root'] / 'plan' / 'TGW-Master-Plan.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    _commit_approved_plan(cfg)
    return path


def test_plan_brief_invalid_pp_identifier(tmp_path):
    cfg = _brief_cfg(tmp_path)
    out = plan_brief(cfg, 'not-a-pp')
    assert out['ok'] is False
    assert out['code'] == 'invalid_pp_identifier'


def test_plan_brief_canonical_plan_unavailable(tmp_path):
    cfg = _brief_cfg(tmp_path)
    # No canonical Master Plan file is committed.
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is False
    assert out['code'] == 'canonical_plan_unavailable'


def test_plan_brief_pp_not_found(tmp_path):
    cfg = _brief_cfg(tmp_path)
    _write_plan(cfg, '## PP-BETA-002 Beta work\nbeta source\n')
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is False
    assert out['code'] == 'pp_not_found'
    assert out['canonical_source']['path'] == str(
        cfg['standalone_plan_root'] / 'plan' / 'TGW-Master-Plan.md'
    )


def test_plan_brief_ambiguous_pp(tmp_path):
    cfg = _brief_cfg(tmp_path)
    _write_plan(
        cfg,
        '## PP-ALPHA-001 First section\none\n\n## PP-ALPHA-001 Second section\ntwo\n',
    )
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is False
    assert out['code'] == 'ambiguous_pp'
    assert len(out['matches']) == 2


def test_plan_brief_exact_section_boundaries_and_hashes(tmp_path):
    cfg = _brief_cfg(tmp_path)
    text = (
        '# TGW Master Plan\n\n'
        '## PP-ALPHA-001 Alpha work\n'
        'alpha line 1\n'
        'alpha line 2\n\n'
        '## PP-BETA-002 Beta work\n'
        'beta source\n'
    )
    _write_plan(cfg, text)

    out = plan_brief(cfg, 'PP-ALPHA-001')

    assert out['ok'] is True
    section = out['section']
    expected_content = '## PP-ALPHA-001 Alpha work\nalpha line 1\nalpha line 2\n\n'
    assert section['content'] == expected_content
    assert section['heading'] == 'PP-ALPHA-001 Alpha work'
    # line_start/line_end are 1-indexed, half-open at end (excludes next heading)
    assert section['line_start'] == 3
    assert section['line_end'] == 6
    assert section['sha256'] == hashlib.sha256(expected_content.encode('utf-8')).hexdigest()
    assert out['canonical_source']['sha256'] == hashlib.sha256(text.encode('utf-8')).hexdigest()
    assert out['canonical_source']['bytes'] == len(text.encode('utf-8'))
    # section byte range is a slice of the canonical source bytes
    raw = text.encode('utf-8')
    assert raw[section['byte_start']:section['byte_end']] == expected_content.encode('utf-8')


def test_plan_brief_lowercase_pp_normalizes_to_uppercase(tmp_path):
    cfg = _brief_cfg(tmp_path)
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')
    out = plan_brief(cfg, 'pp-alpha-001')
    assert out['ok'] is True
    assert out['query']['pp'] == 'PP-ALPHA-001'


def test_plan_brief_cross_reference_heading_is_not_a_second_match(tmp_path):
    # Regression: a heading that merely says another PP was folded into the
    # requested PP must never compete with the canonical section (the
    # false-ambiguity case from Tigwa's original v1 submission).
    cfg = _brief_cfg(tmp_path)
    _write_plan(
        cfg,
        '## PP-ALPHA-001 Canonical work\nalpha source\n\n'
        '## PP-OLD-001 Folded into PP-ALPHA-001\nold source\n',
    )
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is True
    assert out['section']['heading'] == 'PP-ALPHA-001 Canonical work'


def test_plan_brief_section_too_large(tmp_path):
    cfg = _brief_cfg(tmp_path)
    huge = 'x' * (PLAN_BRIEF_MAX_SOURCE_BYTES + 100)
    _write_plan(cfg, f'## PP-ALPHA-001 Alpha work\n{huge}\n')
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is False
    assert out['code'] == 'section_too_large'
    assert 'content' not in out['section']


def test_plan_brief_linked_detail_absent(tmp_path):
    cfg = _brief_cfg(tmp_path)
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')
    out = plan_brief(cfg, 'PP-ALPHA-001')
    assert out['ok'] is True
    assert out['linked_pp_detail']['status'] == 'absent'
    assert 'content' not in out['linked_pp_detail']


def test_plan_brief_linked_detail_is_metadata_only_never_inlined(tmp_path):
    # Item 4: linked PP documents are metadata-only (path/status/hash/size);
    # content is never inlined even when well under the packet cap.
    cfg = _brief_cfg(tmp_path)
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')
    detail_path = cfg['standalone_plan_root'] / 'plan' / 'pp' / 'PP-ALPHA-001.md'
    detail_path.parent.mkdir(parents=True)
    detail_bytes = b'# PP-ALPHA-001\nSmall detail doc, well under the cap.\n'
    detail_path.write_bytes(detail_bytes)
    _commit_approved_plan(cfg)

    out = plan_brief(cfg, 'PP-ALPHA-001')

    assert out['ok'] is True
    detail = out['linked_pp_detail']
    assert detail['status'] == 'present'
    assert detail['path'] == str(detail_path)
    assert detail['sha256'] == hashlib.sha256(detail_bytes).hexdigest()
    assert detail['bytes'] == len(detail_bytes)
    assert 'content' not in detail


def test_plan_brief_ignores_configured_detail_root(tmp_path):
    cfg = _brief_cfg(tmp_path)
    mutable_detail = tmp_path / 'mutable-vault' / 'plan' / 'pp' / 'PP-ALPHA-001.md'
    mutable_detail.parent.mkdir(parents=True)
    mutable_detail.write_text('stale mutable detail', encoding='utf-8')
    standalone_detail_root = cfg['standalone_plan_root'] / 'plan' / 'pp'
    standalone_detail_root.mkdir(parents=True)
    canonical_detail = standalone_detail_root / 'PP-ALPHA-001.md'
    canonical_detail.write_text('canonical detail', encoding='utf-8')
    cfg['plan_detail_root'] = mutable_detail.parent
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')

    out = plan_brief(cfg, 'PP-ALPHA-001')

    assert out['ok'] is True
    assert out['linked_pp_detail']['path'] == str(canonical_detail)
    assert out['linked_pp_detail']['sha256'] == hashlib.sha256(
        b'canonical detail'
    ).hexdigest()


def test_plan_brief_uses_secondary_canonical_detail_root(tmp_path):
    cfg = _brief_cfg(tmp_path)
    second = cfg['standalone_plan_root'] / 'pp'
    second.mkdir(parents=True)
    canonical_detail = second / 'PP-ALPHA-001.md'
    canonical_detail.write_text('root detail', encoding='utf-8')
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')

    out = plan_brief(cfg, 'PP-ALPHA-001')

    assert out['linked_pp_detail']['status'] == 'present'
    assert out['linked_pp_detail']['path'] == str(canonical_detail)


def test_plan_brief_refuses_duplicate_canonical_details(tmp_path):
    cfg = _brief_cfg(tmp_path)
    first = cfg['standalone_plan_root'] / 'plan' / 'pp'
    second = cfg['standalone_plan_root'] / 'pp'
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    for root in (first, second):
        (root / 'PP-ALPHA-001.md').write_text(str(root), encoding='utf-8')
    _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')

    out = plan_brief(cfg, 'PP-ALPHA-001')

    assert out['linked_pp_detail']['status'] == 'ambiguous'
    assert out['linked_pp_detail']['matches'] == [
        str(first / 'PP-ALPHA-001.md'),
        str(second / 'PP-ALPHA-001.md'),
    ]


def test_plan_brief_never_writes_anything(tmp_path):
    cfg = _brief_cfg(tmp_path)
    plan_path = _write_plan(cfg, '## PP-ALPHA-001 Alpha work\nalpha source\n')
    before = plan_path.read_bytes()
    before_mtime = plan_path.stat().st_mtime

    plan_brief(cfg, 'PP-ALPHA-001')
    plan_brief(cfg, 'PP-MISSING-999')  # not_found path
    plan_brief(cfg, 'not-a-pp')  # invalid path

    # No new files anywhere under the approved Plan (read-only guarantee).
    vault = cfg['standalone_plan_root']
    all_files = sorted(
        p for p in vault.rglob('*') if p.is_file() and '.git' not in p.relative_to(vault).parts
    )
    assert all_files == [plan_path]
    assert plan_path.read_bytes() == before
    assert plan_path.stat().st_mtime == before_mtime
