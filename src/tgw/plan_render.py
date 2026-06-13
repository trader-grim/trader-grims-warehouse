"""
tgw.plan_render — generated taskboard renderer (PP-PLANDB-001 Phase 2).

Renders ``plan/TGW-Taskboard.md`` in the plan vault from the ``todo_items``
table: per-agent sections (ID / pri / size / task), blocker badges from
``depends_on``, Obsidian links to master-plan sections via ``pp_ref`` /
``plan_anchor``, and a done-this-week section.

The taskboard is a **wholly-generated companion file** — one writer, never
hand-edited, so Syncthing mixed-edit conflicts cannot occur (Option C decision,
2026-06-12). Edit tasks via ``tgw todo``; the file regenerates.

Render paths:
- ``tgw plan render`` — immediate, in-process
- ``plan_render`` queue worker — coalesced job enqueued on every todo mutation
  (dedupe_key ``plan_render:pending`` + 30s not_before, catalog-rebuild pattern)
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

TASKBOARD_NAME = 'TGW-Taskboard.md'

_DONE_WINDOW_DAYS = 7

# size token (XS/S/M/L/XL) as it appears in round-style bodies, e.g.
# "Round7 p16 S: ..." or "Round7 p72 M (GATED: ...)"
_SIZE_RE = re.compile(r'(?:^|\s)(XS|S|M|L|XL)(?=[:\s(])')

_HEADER = """\
# TGW Taskboard

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `todo_items` table by
> `tgw plan render` / the `plan_render` worker (PP-PLANDB-001 Phase 2).
> Edit tasks with `tgw todo …` — manual edits here are overwritten.
"""


def taskboard_path(cfg: Dict[str, Any]) -> Path:
    return cfg['plan_vault_path'] / 'plan' / TASKBOARD_NAME


def _parse_size(body: str) -> str:
    m = _SIZE_RE.search(body[:80])
    return m.group(1) if m else ''


def _plan_heading_map(plan_path: Path) -> Dict[str, str]:
    """Map PP-* ref → exact master-plan heading text (for Obsidian heading links)."""
    if not plan_path.exists():
        return {}
    mapping: Dict[str, str] = {}
    for line in plan_path.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^#{1,6}\s+(.*)$', line)
        if not m:
            continue
        heading = m.group(1).strip()
        for ref in re.findall(r'PP-[A-Z0-9]+-\d+', heading):
            mapping.setdefault(ref, heading)
    return mapping


def _md_escape(text: str) -> str:
    """Make a body safe inside a Markdown table cell."""
    return text.replace('|', '\\|').replace('\n', ' ')


def _ref_cell(item: Dict[str, Any], headings: Dict[str, str]) -> str:
    pp_ref = item.get('pp_ref') or ''
    anchor = item.get('plan_anchor') or (headings.get(pp_ref, '') if pp_ref else '')
    if anchor:
        label = pp_ref or 'plan'
        return f'[[TGW-Master-Plan#{_md_escape(anchor)}\\|{label}]]'
    return f'`{pp_ref}`' if pp_ref else ''


def _blocker_cell(item: Dict[str, Any], open_set: set) -> str:
    deps = item.get('depends_on') or []
    if not deps:
        return ''
    blockers = [d for d in deps if d in open_set]
    if blockers:
        return '⛔ ' + ' '.join(f'#{d}' for d in blockers)
    return '✓ deps done'


def build_taskboard(
    items: List[Dict[str, Any]],
    headings: Dict[str, str],
    now: Optional[datetime] = None,
) -> str:
    """Pure renderer: todo rows (open + recently done) → taskboard markdown."""
    now = now or datetime.now(tz=timezone.utc)
    open_items = [i for i in items if not i.get('done_at')]
    cutoff = now - timedelta(days=_DONE_WINDOW_DAYS)
    done_week = sorted(
        (i for i in items if i.get('done_at') and i['done_at'] >= cutoff),
        key=lambda i: i['done_at'], reverse=True,
    )
    open_set = {i['id'] for i in open_items}

    lines = [
        _HEADER,
        f'_Rendered {now.strftime("%Y-%m-%d %H:%M UTC")} — '
        f'{len(open_items)} open, {len(done_week)} done in the last {_DONE_WINDOW_DAYS} days._',
        '',
    ]

    agents = sorted({i['agent'] for i in open_items})
    for agent in agents:
        rows = sorted((i for i in open_items if i['agent'] == agent),
                      key=lambda i: (i['priority'], i['id']))
        lines += [
            f'## {agent} ({len(rows)} open)',
            '',
            '| ID | Pri | Size | Task | Plan | Blockers |',
            '|---:|----:|:----:|------|------|----------|',
        ]
        for i in rows:
            lines.append(
                f'| {i["id"]} | {i["priority"]} | {_parse_size(i["body"])} '
                f'| {_md_escape(i["body"])} | {_ref_cell(i, headings)} '
                f'| {_blocker_cell(i, open_set)} |'
            )
        lines.append('')

    lines += [f'## Done this week ({len(done_week)})', '']
    if done_week:
        lines += [
            '| ID | Agent | Done | Task |',
            '|---:|-------|------|------|',
        ]
        for i in done_week:
            done_str = i['done_at'].strftime('%Y-%m-%d')
            lines.append(f'| {i["id"]} | {i["agent"]} | {done_str} | {_md_escape(i["body"])} |')
    else:
        lines.append('_Nothing completed in the window._')
    lines.append('')

    return '\n'.join(lines)


def render_taskboard(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Query the tracker and atomically (re)write plan/TGW-Taskboard.md."""
    from tgw.todo import todo_list

    try:
        items = todo_list(show_all=True)
    except Exception as exc:
        return {'ok': False, 'error': f'todo tracker unavailable: {exc}'}

    headings = _plan_heading_map(cfg['plan_master_path'])
    text = build_taskboard(items, headings)

    out_path = taskboard_path(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix='.taskboard-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(tmp, 0o664)  # mkstemp gives 600; vault files are group-shared
        os.replace(tmp, out_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    open_count = sum(1 for i in items if not i.get('done_at'))
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=_DONE_WINDOW_DAYS)
    done_week = sum(1 for i in items if i.get('done_at') and i['done_at'] >= cutoff)
    return {'ok': True, 'path': str(out_path), 'open': open_count, 'done_week': done_week}
