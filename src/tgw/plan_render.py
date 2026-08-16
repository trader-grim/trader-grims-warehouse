"""
tgw.plan_render — generated taskboard renderer + plan reconciler (PP-PLANDB-001).

Renders ``TGW-Taskboard.md`` in the configured operational render root from the ``todo_items``
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

Plan reconciliation (Phase 3):
- ``tgw plan check`` — reconcile tracker ↔ master plan both directions
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

TASKBOARD_NAME = 'TGW-Taskboard.md'
DEFAULT_PLAN_ROOT = Path('/opt/TGW/library/plans')

# ---------------------------------------------------------------------------
# plan_brief — bounded, exact-source Master Plan retrieval (PP-KNOWLEDGE-001
# / todo #1439, #1520). Deterministic pure retrieval: no LLM summarization,
# no generated cache — the exact matched section of the canonical Master
# Plan, with source hashes/anchors, plus metadata-only (never inlined)
# provenance for a linked plan/pp/<PP>.md detail document.
# ---------------------------------------------------------------------------

PLAN_BRIEF_VERSION = 'tgw-plan-brief-v1'
PLAN_BRIEF_MAX_SOURCE_BYTES = 64 * 1024
PP_IDENTIFIER_RE = re.compile(r'^PP-[A-Z0-9][A-Z0-9-]*$')

_DONE_WINDOW_DAYS = 7
_DONE_MAX_ROWS = 15  # cap shown in the taskboard done section to avoid noise after big sessions

# size token (XS/S/M/L/XL) as it appears in round-style bodies, e.g.
# "Round7 p16 S: ..." or "Round7 p72 M (GATED: ...)"
_SIZE_RE = re.compile(r'(?:^|\s)(XS|S|M|L|XL)(?=[:\s(])')

# PP-* reference token — handles compound names like PP-PORTABLE-CATALOG-001.
# Pattern: PP- followed by one or more WORD- segments, ending with 3 digits.
_PP_REF_RE = re.compile(r'\bPP-(?:[A-Z][A-Z0-9]*-)+\d{3}\b')

# Round tag at start of todo body (e.g. "Round7 p58 S:" or "Round 4 #29 —")
_ROUND_RE = re.compile(r'^Round\s*(\d+)\b', re.IGNORECASE)

_HEADER = """\
# TGW Taskboard

> **GENERATED FILE — DO NOT EDIT.** Rebuilt from the `todo_items` table by
> `tgw plan render` / the `plan_render` worker (PP-PLANDB-001 Phase 2).
> Edit tasks with `tgw todo …` — manual edits here are overwritten.
"""


def taskboard_path(cfg: Dict[str, Any]) -> Path:
    return Path(cfg.get('plan_render_root') or '/opt/TGW/var/plan-render') / TASKBOARD_NAME


class PlanRenderBindingError(ValueError):
    """A rendered Plan view cannot establish its approved source identity."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f'approved Plan binding unavailable: {code}')


def approved_render_plan_identity(cfg: Mapping[str, Any]) -> Dict[str, str]:
    """Return the only Plan identity permitted to drive a rendered view.

    Rendered taskboard and agent-trace views are operational artifacts, but
    their links still shape operator decisions.  Never let a configured
    legacy ``plan_master_path`` substitute for the exact clean standalone
    Plan materialization approved with its solution.
    """
    from tgw.plan_graph import SourcePreconditionError, approved_plan_binding

    try:
        binding = approved_plan_binding(
            Path(cfg.get('standalone_plan_root') or DEFAULT_PLAN_ROOT),
            approved_plan_commit=cfg.get('plan_approved_commit'),
            approved_solution_hash=cfg.get('plan_approved_solution_hash'),
            git_path=str(cfg.get('plan_git_path') or 'git'),
        )
        root = Path(binding['plan_root'])
        master = root / 'plan' / 'TGW-Master-Plan.md'
        if not master.is_file():
            raise PlanRenderBindingError('canonical_plan_unavailable')
    except PlanRenderBindingError:
        raise
    except SourcePreconditionError as exc:
        raise PlanRenderBindingError(exc.code) from exc
    except (OSError, ValueError) as exc:
        raise PlanRenderBindingError('canonical_plan_unavailable') from exc
    return {
        'plan_root': str(root),
        'plan_commit': binding['plan_commit'],
        'solution_hash': binding['solution_hash'],
        'master_plan_path': str(master),
    }


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
        for ref in _PP_REF_RE.findall(heading):
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
    plan_identity: Optional[Mapping[str, str]] = None,
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
        *(
            [
                '_Bound Plan: '
                f'commit `{plan_identity["plan_commit"]}`, '
                f'solution `{plan_identity["solution_hash"]}`._',
                '',
            ]
            if plan_identity is not None else []
        ),
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

    shown = done_week[:_DONE_MAX_ROWS]
    overflow = len(done_week) - len(shown)
    done_header = f'## Done this week ({len(done_week)})'
    if overflow:
        done_header += f'  — showing {_DONE_MAX_ROWS} most recent'
    lines += [done_header, '']
    if shown:
        lines += [
            '| ID | Agent | Done | Task |',
            '|---:|-------|------|------|',
        ]
        for i in shown:
            done_str = i['done_at'].strftime('%Y-%m-%d')
            lines.append(f'| {i["id"]} | {i["agent"]} | {done_str} | {_md_escape(i["body"])} |')
        if overflow:
            lines.append(f'| … | | | _…and {overflow} more — run `tgw todo --all` to see everything_ |')
    else:
        lines.append('_Nothing completed in the window._')
    lines.append('')

    return '\n'.join(lines)


def render_taskboard(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Query the tracker and atomically write a non-authoritative runtime view."""
    from tgw.todo import todo_list

    try:
        plan_identity = approved_render_plan_identity(cfg)
    except PlanRenderBindingError as exc:
        return {'ok': False, 'error': str(exc), 'code': exc.code}

    try:
        items = todo_list(show_all=True)
    except Exception as exc:
        return {
            'ok': False,
            'error': f'todo tracker unavailable: {exc}',
            'plan_identity': plan_identity,
        }

    headings = _plan_heading_map(Path(plan_identity['master_plan_path']))
    text = build_taskboard(items, headings, plan_identity=plan_identity)

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
    return {
        'ok': True,
        'path': str(out_path),
        'open': open_count,
        'done_week': done_week,
        'plan_identity': plan_identity,
    }


# ---------------------------------------------------------------------------
# plan_check — PP-PLANDB-001 Phase 3
# ---------------------------------------------------------------------------

def _parse_plan_sections(plan_path: Path) -> Tuple[Dict[str, str], Set[str], Set[str]]:
    """
    Parse master plan headings and return:
    - pp_in_headings: PP-* ref → first heading text containing that ref
    - done_in_headings: PP-* refs whose heading line also contains ✅
    - all_headings: every heading text (for plan_anchor validation)
    """
    if not plan_path.exists():
        return {}, set(), set()

    pp_in_headings: Dict[str, str] = {}
    done_in_headings: Set[str] = set()
    all_headings: Set[str] = set()

    for line in plan_path.read_text(encoding='utf-8').splitlines():
        m = re.match(r'^#{1,6}\s+(.*)', line)
        if not m:
            continue
        heading = m.group(1).strip()
        all_headings.add(heading)
        for ref in _PP_REF_RE.findall(heading):
            pp_in_headings.setdefault(ref, heading)
            if '✅' in heading:
                done_in_headings.add(ref)

    return pp_in_headings, done_in_headings, all_headings


def plan_check(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reconcile tracker ↔ master plan both directions (PP-PLANDB-001 Phase 3).

    Checks:
    1. Tracker→Plan orphaned pp_refs: open todo pp_ref not in any plan heading
    2. Tracker→Plan orphaned plan_anchors: todo plan_anchor text not in any plan heading
    3. Plan→Tracker done mismatch: PP section is ✅ in plan but has open todos
    4. Round-tag stale: open todos from rounds earlier than the current (max) round

    Issues are grouped per pp_ref / per round to avoid one-issue-per-todo noise.
    Returns {ok, issues: [...], counts: {warnings, infos}}.
    """
    from tgw.todo import todo_list

    try:
        plan_identity = approved_render_plan_identity(cfg)
    except PlanRenderBindingError as exc:
        return {
            'ok': False, 'error': str(exc), 'code': exc.code,
            'issues': [], 'counts': {},
        }
    plan_path = Path(plan_identity['master_plan_path'])

    pp_in_headings, done_in_headings, all_headings = _parse_plan_sections(Path(plan_path))

    try:
        items = todo_list(show_all=True)
    except Exception as exc:
        return {
            'ok': False, 'error': f'todo tracker unavailable: {exc}',
            'issues': [], 'counts': {}, 'plan_identity': plan_identity,
        }

    open_items = [i for i in items if not i.get('done_at')]
    issues: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # 1. Orphaned pp_refs: open todos whose pp_ref has no plan heading     #
    # ------------------------------------------------------------------ #
    orphan_refs: Dict[str, List[int]] = {}
    for item in open_items:
        pp = item.get('pp_ref') or ''
        if pp and pp not in pp_in_headings:
            orphan_refs.setdefault(pp, []).append(item['id'])

    for pp, ids in sorted(orphan_refs.items()):
        issues.append({
            'kind': 'orphaned_pp_ref',
            'severity': 'warning',
            'message': (
                f'pp_ref {pp!r} has no plan section heading '
                f'({len(ids)} open todo(s): {_fmt_ids(ids)})'
            ),
        })

    # ------------------------------------------------------------------ #
    # 2. Orphaned plan_anchors: anchor text not found in any plan heading  #
    # ------------------------------------------------------------------ #
    seen_anchor_issues: Set[str] = set()
    for item in open_items:
        anchor = item.get('plan_anchor') or ''
        if anchor and anchor not in all_headings and anchor not in seen_anchor_issues:
            seen_anchor_issues.add(anchor)
            issues.append({
                'kind': 'orphaned_plan_anchor',
                'severity': 'warning',
                'message': (
                    f'plan_anchor {anchor!r} (todo #{item["id"]}) '
                    f'not found as a plan heading'
                ),
            })

    # ------------------------------------------------------------------ #
    # 3. Done-in-plan / open-in-tracker mismatch                          #
    # ------------------------------------------------------------------ #
    done_mismatch: Dict[str, List[int]] = {}
    for item in open_items:
        pp = item.get('pp_ref') or ''
        if pp and pp in done_in_headings:
            done_mismatch.setdefault(pp, []).append(item['id'])

    for pp, ids in sorted(done_mismatch.items()):
        issues.append({
            'kind': 'done_mismatch',
            'severity': 'warning',
            'message': (
                f'{pp} is ✅ DONE in plan but has {len(ids)} open todo(s): {_fmt_ids(ids)}'
            ),
        })

    # ------------------------------------------------------------------ #
    # 4. Stale round tags: open todos from rounds < max observed round    #
    # ------------------------------------------------------------------ #
    # Collect round numbers from ALL todos (open + done) to find max round.
    all_round_nums: List[int] = []
    for item in items:
        m = _ROUND_RE.match(item.get('body') or '')
        if m:
            all_round_nums.append(int(m.group(1)))

    if all_round_nums:
        max_round = max(all_round_nums)
        stale_open: Dict[int, List[int]] = {}
        for item in open_items:
            m = _ROUND_RE.match(item.get('body') or '')
            if m:
                rn = int(m.group(1))
                if rn < max_round:
                    stale_open.setdefault(rn, []).append(item['id'])

        for rn in sorted(stale_open):
            ids = stale_open[rn]
            issues.append({
                'kind': 'stale_round_tag',
                'severity': 'info',
                'message': (
                    f'Round{rn} has {len(ids)} open todo(s) '
                    f'(current round is Round{max_round}): {_fmt_ids(ids)}'
                ),
            })

    # ------------------------------------------------------------------ #
    # 5. Missing pp_ref on todos added on/after the standing-requirement   #
    #    cutoff (CLAUDE.md, Dave 2026-07-11): every new todo gets a PP.    #
    #    Pre-cutoff backlog is explicitly grandfathered — no backtracking. #
    # ------------------------------------------------------------------ #
    pp_ref_cutoff = datetime(2026, 7, 11, tzinfo=timezone.utc)
    missing_pp_ids = [
        item['id'] for item in open_items
        if not item.get('pp_ref') and item.get('added_at') and item['added_at'] >= pp_ref_cutoff
    ]
    if missing_pp_ids:
        issues.append({
            'kind': 'missing_pp_ref',
            'severity': 'warning',
            'message': (
                f'{len(missing_pp_ids)} open todo(s) added on/after 2026-07-11 '
                f'have no pp_ref (standing requirement, CLAUDE.md): {_fmt_ids(missing_pp_ids)}'
            ),
        })

    warnings = sum(1 for i in issues if i['severity'] == 'warning')
    infos = sum(1 for i in issues if i['severity'] == 'info')
    return {
        'ok': True,
        'plan_identity': plan_identity,
        'issues': issues,
        'counts': {'warnings': warnings, 'infos': infos},
        'summary': (
            f'{warnings} warning(s), {infos} info(s)' if issues
            else 'all clear'
        ),
    }


def _fmt_ids(ids: List[int]) -> str:
    """Format a list of todo IDs, capping display at 5."""
    shown = ids[:5]
    tail = f' +{len(ids) - 5} more' if len(ids) > 5 else ''
    return '#' + ', #'.join(str(i) for i in shown) + tail


def format_plan_check(result: Dict[str, Any]) -> str:
    """Format plan_check() result as human-readable text for the CLI."""
    if not result.get('ok'):
        return f"Error: {result.get('error', 'unknown error')}"

    issues = result.get('issues', [])
    counts = result.get('counts', {})
    lines = [f"tgw plan check — {result['summary']}"]
    identity = result.get('plan_identity') or {}
    if identity:
        lines.append(
            f"Bound Plan: {identity.get('plan_commit', 'unknown')} "
            f"{identity.get('solution_hash', 'unknown')}"
        )

    if not issues:
        return '\n'.join(lines)

    lines.append('')
    for issue in issues:
        sev = '⚠' if issue['severity'] == 'warning' else 'ℹ'
        lines.append(f"  {sev}  [{issue['kind']}] {issue['message']}")

    lines.append('')
    lines.append(
        f"  {counts.get('warnings', 0)} warning(s) — review with `tgw todo --pp <ref>` / "
        f"`tgw todo set-meta <id> --pp <correct-ref>`"
    )
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# plan_status — PP-PLANDB-001 Phase 4
# ---------------------------------------------------------------------------

def _item_activity_ts(item: Dict[str, Any]) -> Optional[datetime]:
    """Latest timestamp for a todo: done_at when done, added_at otherwise."""
    return item.get('done_at') or item.get('added_at')


def plan_status(cfg: Dict[str, Any], pp_ref: Optional[str] = None) -> Dict[str, Any]:
    """
    One-line status summary per PP-* item from the todo tracker (PP-PLANDB-001 Phase 4).

    For each PP-* item with tracked todos, returns:
      open count, done count, blocked count (open todos with open deps),
      and the timestamp + body of the most-recently-active todo.

    pp_ref: if given, restrict output to that single PP-* item.
    Returns {ok, rows: [{pp_ref, open, done, blocked, latest, latest_body}]}.
    """
    from tgw.todo import todo_list

    try:
        all_items = todo_list(show_all=True)
    except Exception as exc:
        return {'ok': False, 'error': f'todo tracker unavailable: {exc}', 'rows': []}

    open_set = {i['id'] for i in all_items if not i.get('done_at')}

    ref_items = [i for i in all_items if i.get('pp_ref')]
    if pp_ref:
        ref_items = [i for i in ref_items if i['pp_ref'] == pp_ref]

    by_pp: Dict[str, List[Dict[str, Any]]] = {}
    for item in ref_items:
        by_pp.setdefault(item['pp_ref'], []).append(item)

    rows = []
    for pp in sorted(by_pp):
        pp_items = by_pp[pp]
        open_todos = [i for i in pp_items if not i.get('done_at')]
        done_todos = [i for i in pp_items if i.get('done_at')]
        blocked = [
            i for i in open_todos
            if any(d in open_set for d in (i.get('depends_on') or []))
        ]

        timestamps = [t for i in pp_items if (t := _item_activity_ts(i))]
        latest = max(timestamps) if timestamps else None

        ts_pairs = [(i, _item_activity_ts(i)) for i in pp_items]
        ts_pairs = [(i, t) for i, t in ts_pairs if t is not None]
        latest_item = max(ts_pairs, key=lambda x: x[1])[0] if ts_pairs else pp_items[0]

        rows.append({
            'pp_ref': pp,
            'open': len(open_todos),
            'done': len(done_todos),
            'blocked': len(blocked),
            'latest': latest,
            'latest_body': latest_item.get('body', ''),
        })

    return {'ok': True, 'rows': rows}


def format_plan_status(result: Dict[str, Any]) -> str:
    """Format plan_status() result as human-readable text for the CLI."""
    if not result.get('ok'):
        return f"Error: {result.get('error', 'unknown error')}"

    rows = result.get('rows', [])
    if not rows:
        return 'tgw plan status — no PP-* items with tracked todos'

    lines = [f'tgw plan status — {len(rows)} PP-* item(s) tracked']
    for row in rows:
        counts = []
        if row['open']:
            blocked_note = f' ({row["blocked"]} blocked)' if row['blocked'] else ''
            counts.append(f'{row["open"]} open{blocked_note}')
        if row['done']:
            counts.append(f'{row["done"]} done')
        if not counts:
            counts.append('no open todos')

        latest_str = ''
        if row['latest']:
            date = row['latest'].strftime('%Y-%m-%d')
            body = row['latest_body'][:60].replace('\n', ' ')
            if len(row['latest_body']) > 60:
                body += '…'
            latest_str = f' · {date} ({body})'

        lines.append(f'  {row["pp_ref"]}: {", ".join(counts)}{latest_str}')

    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# plan_brief internals
# ---------------------------------------------------------------------------

def _canonical_plan_source(plan_master_path: Path) -> Tuple[bytes, Dict[str, Any]]:
    """Return canonical Master Plan bytes with reproducible source metadata."""
    raw = plan_master_path.read_bytes()
    stat = plan_master_path.stat()
    return raw, {
        'path': str(plan_master_path),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'bytes': len(raw),
        'mtime_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _plan_heading_sections(text: str, pp: str) -> List[Dict[str, Any]]:
    """Find exact PP-bearing headings and their structural source ranges.

    Matches only headings whose *heading text starts with* the exact PP
    identifier — a heading that merely cross-references or says it was
    folded into the requested PP (e.g. "PP-OLD-001 Folded into
    PP-ALPHA-001") must never compete with the canonical section.
    """
    lines = text.splitlines(keepends=True)
    headings: List[Tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r'^(#{2,6})\s+(.+?)\s*$', line)
        heading = match.group(2).strip() if match else ''
        if match and re.match(rf'^{re.escape(pp)}(?:\s|:|—|$)', heading.upper()):
            headings.append((index, len(match.group(1)), heading))

    sections: List[Dict[str, Any]] = []
    for start, level, heading in headings:
        end = len(lines)
        for index in range(start + 1, len(lines)):
            next_heading = re.match(r'^(#{1,6})\s+', lines[index])
            if next_heading and len(next_heading.group(1)) <= level:
                end = index
                break
        content = ''.join(lines[start:end])
        byte_start = len(''.join(lines[:start]).encode('utf-8'))
        content_bytes = content.encode('utf-8')
        sections.append({
            'heading': heading,
            'line_start': start + 1,
            'line_end': end,
            'byte_start': byte_start,
            'byte_end': byte_start + len(content_bytes),
            'sha256': hashlib.sha256(content_bytes).hexdigest(),
            'content': content,
        })
    return sections


def plan_brief(cfg: Dict[str, Any], pp_ref: str) -> Dict[str, Any]:
    """Retrieve one bounded, exact-source Master Plan packet for a PP identifier
    (PP-KNOWLEDGE-001 / todo #1439, #1520).

    Pure, read-only, deterministic retrieval — never writes anything, never
    produces a model-written summary, and refuses missing or ambiguous PP
    matches. Paths are derived only from the approved standalone Plan binding
    and its canonical siblings — no mutable Plan Vault/config-path fallback
    exists, and this is the single implementation shared by the MCP tool (and
    any future CLI surface, per Tigwa's item 7 — not built here).

    A linked ``plan/pp/<PP>.md`` detail document, if present, is reported
    metadata-only (path/status/sha256/bytes) — its content is never inlined,
    even when it would fit the packet's byte cap. A future explicit detail
    retrieval tool can carry its own bound/provenance contract for that.

    Returns a JSON-serializable dict; never raises for a well-formed
    ``pp_ref`` string — I/O and validation failures are reported as
    ``{'ok': False, 'code': ..., ...}``.
    """
    query_pp = (pp_ref or '').strip().upper()
    if not PP_IDENTIFIER_RE.fullmatch(query_pp):
        return {
            'ok': False,
            'error': 'pp must be an exact identifier like PP-KNOWLEDGE-001',
            'code': 'invalid_pp_identifier',
        }

    try:
        plan_identity = approved_render_plan_identity(cfg)
    except PlanRenderBindingError as exc:
        return {'ok': False, 'error': str(exc), 'code': exc.code}

    plan_master_path = Path(plan_identity['master_plan_path'])
    try:
        raw, source = _canonical_plan_source(plan_master_path)
    except OSError as exc:
        return {
            'ok': False,
            'error': str(exc),
            'code': 'canonical_plan_unavailable',
            'plan_identity': plan_identity,
        }

    text = raw.decode('utf-8')
    sections = _plan_heading_sections(text, query_pp)
    if not sections:
        return {
            'ok': False,
            'code': 'pp_not_found',
            'query': {'pp': query_pp},
            'plan_identity': plan_identity,
            'canonical_source': source,
            'warning': 'Read the full canonical Master Plan or select another PP; no heading was guessed.',
        }
    if len(sections) > 1:
        return {
            'ok': False,
            'code': 'ambiguous_pp',
            'query': {'pp': query_pp},
            'plan_identity': plan_identity,
            'canonical_source': source,
            'matches': [{key: section[key] for key in ('heading', 'line_start', 'line_end')}
                        for section in sections],
            'warning': 'Multiple exact PP headings found; no section was selected.',
        }

    section = sections[0]
    if len(section['content'].encode('utf-8')) > PLAN_BRIEF_MAX_SOURCE_BYTES:
        return {
            'ok': False,
            'code': 'section_too_large',
            'query': {'pp': query_pp},
            'plan_identity': plan_identity,
            'canonical_source': source,
            'section': {key: section[key] for key in section if key != 'content'},
            'warning': 'Exact section exceeds the packet limit; use the canonical source path and anchors.',
        }

    # Linked PP details are canonical Plan material too.  The master Plan's
    # own parent is the only fallback; a source-vault sibling is never valid.
    plan_root = Path(plan_identity['plan_root'])
    detail_roots = (plan_root / 'plan' / 'pp', plan_root / 'pp')
    detail_candidates = tuple(root / f'{query_pp}.md' for root in detail_roots)
    existing_details = tuple(path for path in detail_candidates if path.is_file())
    detail_path = existing_details[0] if len(existing_details) == 1 else detail_candidates[0]
    detail: Dict[str, Any] = {'path': str(detail_path), 'status': 'absent'}
    warnings: List[str] = []
    if len(existing_details) > 1:
        detail.update({
            'status': 'ambiguous',
            'matches': [str(path) for path in existing_details],
        })
        warnings.append(
            'Multiple canonical PP detail documents have the same identifier; '
            'no detail was selected.'
        )
    elif existing_details:
        detail_stat = detail_path.stat()
        detail.update({
            'sha256': hashlib.sha256(detail_path.read_bytes()).hexdigest(),
            'bytes': detail_stat.st_size,
            'status': 'present',
        })
        warnings.append(
            'Linked PP detail exists and is reported metadata-only (path/status/hash/'
            'bytes); its content is never inlined by this tool — read the path directly.'
        )
    else:
        warnings.append('No linked canonical PP detail document exists at the expected path.')

    return {
        'ok': True,
        'generator_version': PLAN_BRIEF_VERSION,
        'plan_identity': plan_identity,
        'query': {'pp': query_pp},
        'canonical_source': source,
        'section': section,
        'linked_pp_detail': detail,
        'warnings': warnings,
    }
