"""Tests for tgw.plan_render — generated taskboard (PP-PLANDB-001 Phase 2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tgw.plan_render import (
    _parse_size,
    _plan_heading_map,
    build_taskboard,
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
        '### PP-BAR-001 — Bar (design)\n',
        encoding='utf-8',
    )
    m = _plan_heading_map(plan)
    assert m['PP-FOO-001'] == 'PP-FOO-001 — The Foo Project'
    assert m['PP-BAR-001'] == 'PP-BAR-001 — Bar (design)'
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
    vault = tmp_path / 'vault'
    (vault / 'plan').mkdir(parents=True)
    (vault / 'plan' / 'TGW-Master-Plan.md').write_text(
        '### PP-FOO-001 — The Foo Project\n', encoding='utf-8')
    return {'plan_vault_path': vault,
            'plan_master_path': vault / 'plan' / 'TGW-Master-Plan.md'}


def test_render_writes_file(tmp_path):
    cfg = _cfg(tmp_path)
    items = [_item(1, body='open task', pp_ref='PP-FOO-001'),
             _item(2, body='done task', done_at=datetime.now(tz=timezone.utc))]
    with patch('tgw.todo.todo_list', return_value=items):
        result = render_taskboard(cfg)
    assert result['ok'] is True
    assert result['open'] == 1
    assert result['done_week'] == 1
    board = taskboard_path(cfg)
    assert board.exists()
    content = board.read_text(encoding='utf-8')
    assert 'open task' in content
    assert '[[TGW-Master-Plan#PP-FOO-001 — The Foo Project\\|PP-FOO-001]]' in content
    # no temp file left behind
    assert not list(board.parent.glob('.taskboard-*'))


def test_render_reports_tracker_failure(tmp_path):
    cfg = _cfg(tmp_path)
    with patch('tgw.todo.todo_list', side_effect=RuntimeError('db down')):
        result = render_taskboard(cfg)
    assert result['ok'] is False
    assert 'db down' in result['error']
    assert not taskboard_path(cfg).exists()
