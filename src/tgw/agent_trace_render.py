"""
tgw.agent_trace_render — generated agent-runs Obsidian view (PP-AGENTTRACE-001
Phase 2).

Renders ``TGW-Agent-Runs.md`` in the configured operational render root from the ``agent_runs``
table (Phase 1): one row per recorded agent run (Claude sessions/subagents,
tgw-coder, aider, etc.), most-recently-started first.

Parallel to ``tgw.plan_render`` — a separate module on purpose (different
source table, no shared logic beyond the atomic-write pattern) but copies
its exact render shape: pure ``build_agent_runs_doc()`` + impure
``render_agent_runs_doc()`` atomic-write, queue-triggered via the
``agent_run_render`` worker, coalesced the same way ``plan_render`` is
(dedupe_key + 30s not_before — see ``state_machine._enqueue_agent_run_render()``).

The file is a **wholly-generated companion file** — one writer, never
hand-edited, same Option C rationale as ``TGW-Taskboard.md``.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tgw.plan_render import _plan_heading_map

AGENT_RUNS_DOC_NAME = 'TGW-Agent-Runs.md'

_RUN_ID_DISPLAY_CHARS = 12

_HEADER = """\
# TGW Agent Runs

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `agent_runs` table by
> `tgw trace` start/end / the `agent_run_render` worker (PP-AGENTTRACE-001
> Phase 2). Run IDs are truncated to the first {n} hex characters for display;
> use `tgw trace show <run_id>` (or query `agent_runs` directly) for the
> full id.
"""


def agent_runs_doc_path(cfg: Dict[str, Any]) -> Path:
    return Path(cfg.get('plan_render_root') or '/opt/TGW/var/plan-render') / AGENT_RUNS_DOC_NAME


def _md_escape(text: str) -> str:
    """Make a value safe inside a Markdown table cell."""
    return (text or '').replace('|', '\\|').replace('\n', ' ')


def _short_run_id(run_id: str) -> str:
    run_id = run_id or ''
    return run_id[:_RUN_ID_DISPLAY_CHARS]


def _ref_cell(row: Dict[str, Any], headings: Dict[str, str]) -> str:
    """PP/Todo cell: combines pp_ref/todo_id, with an Obsidian link to the
    PP's master-plan heading where pp_ref is set (reuses plan_render's
    heading-lookup helper — same lookup taskboard rows use)."""
    pp_ref = row.get('pp_ref') or ''
    todo_id = row.get('todo_id')
    parts: List[str] = []
    if pp_ref:
        heading = headings.get(pp_ref)
        if heading:
            parts.append(f'[[TGW-Master-Plan#{_md_escape(heading)}\\|{pp_ref}]]')
        else:
            parts.append(f'`{pp_ref}`')
    if todo_id is not None:
        parts.append(f'#{todo_id}')
    return ' '.join(parts)


def _duration_cell(row: Dict[str, Any], now: datetime) -> str:
    started = row.get('started_at')
    ended = row.get('ended_at')
    if started is None:
        return ''
    if ended is None:
        return 'running'
    delta = ended - started
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f'{hours}h{minutes}m'
    if minutes:
        return f'{minutes}m{seconds}s'
    return f'{seconds}s'


def build_agent_runs_doc(
    rows: List[Dict[str, Any]],
    headings: Optional[Dict[str, str]] = None,
    now: Optional[datetime] = None,
) -> str:
    """Pure renderer: agent_runs rows -> Obsidian markdown table.

    No IO — unit-testable directly with synthetic row dicts, exactly like
    plan_render.build_taskboard().
    """
    now = now or datetime.now(tz=timezone.utc)
    headings = headings or {}

    lines = [
        _HEADER.format(n=_RUN_ID_DISPLAY_CHARS),
        f'_Rendered {now.strftime("%Y-%m-%d %H:%M UTC")} — {len(rows)} run(s) shown._',
        '',
        '| Run ID | Agent Type | PP/Todo | Host | Status | Started | Duration | Summary |',
        '|--------|------------|---------|------|--------|---------|----------|---------|',
    ]

    for row in rows:
        started = row.get('started_at')
        started_str = started.strftime('%Y-%m-%d %H:%M UTC') if started else ''
        lines.append(
            f'| `{_short_run_id(row.get("run_id", ""))}` '
            f'| {_md_escape(row.get("agent_type", ""))} '
            f'| {_ref_cell(row, headings)} '
            f'| {_md_escape(row.get("host", ""))} '
            f'| {_md_escape(row.get("status", ""))} '
            f'| {started_str} '
            f'| {_duration_cell(row, now)} '
            f'| {_md_escape(row.get("summary", ""))} |'
        )

    if not rows:
        lines.append('| _no runs recorded yet_ | | | | | | | |')

    lines.append('')
    return '\n'.join(lines)


def render_agent_runs_doc(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Query agent_runs and atomically write a non-authoritative runtime view."""
    from tgw.queue import state_machine

    try:
        rows = state_machine.list_agent_runs()
    except Exception as exc:
        return {'ok': False, 'error': f'agent_runs tracker unavailable: {exc}'}

    headings = _plan_heading_map(cfg['plan_master_path'])
    text = build_agent_runs_doc(rows, headings)

    out_path = agent_runs_doc_path(cfg)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix='.agent-runs-', suffix='.tmp')
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

    return {'ok': True, 'path': str(out_path), 'count': len(rows)}
