"""
tgw.todo — Multi-agent TODO tracker (PP-TODO-001 + PP-PLANDB-001 Phase 1).

Storage: PostgreSQL table ``todo_items`` in ``state_machine`` DB.
CLI entry point: ``tgw todo [agent] [--add TEXT] [--done ID] [--seed]``
                 ``tgw todo brief <id>`` — self-contained per-agent task spec

Agents: claude, admin, gemini, db, tigwa  (open-ended — any string is valid)
Priority: integer, lower = higher priority (50 = default, 10 = urgent, 90 = someday)

PP-PLANDB-001 Phase 1 columns (migration, applied 2026-06-12)::

    ALTER TABLE todo_items
      ADD COLUMN pp_ref TEXT,
      ADD COLUMN depends_on INTEGER[] NOT NULL DEFAULT '{}',
      ADD COLUMN plan_anchor TEXT;

``pp_ref``      — PP-* item this todo belongs to (e.g. 'PP-PLANDB-001')
``depends_on``  — todo ids that must complete first (blocker badges on taskboard)
``plan_anchor`` — exact master-plan heading text (without leading #'s) for the
                  linked design section; resolved from pp_ref when omitted
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

_DSN = 'dbname=state_machine user=tgw'


def _ensure_reasoning_column() -> None:
    """Idempotent migration: add reasoning column to todo_items if absent."""
    sql = """
    DO $$ BEGIN
        ALTER TABLE todo_items
            ADD COLUMN reasoning TEXT NOT NULL DEFAULT 'normal'
            CHECK (reasoning IN ('high', 'normal', 'low'));
    EXCEPTION WHEN duplicate_column THEN NULL;
    END $$;
    """
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute(sql)
    except Exception as exc:
        log.warning('_ensure_reasoning_column: migration skipped — %s', exc)


def _ensure_status_note_column() -> None:
    """Idempotent migration: add status_note column to todo_items if absent.

    Separate from ``body`` so a dispatch step (e.g. tgw-coder marking
    in-progress) can record a status without destroying the original
    finding text — see todo #1384.
    """
    sql = """
    DO $$ BEGIN
        ALTER TABLE todo_items ADD COLUMN status_note TEXT;
    EXCEPTION WHEN duplicate_column THEN NULL;
    END $$;
    """
    try:
        with _conn() as con:
            with con.cursor() as cur:
                cur.execute(sql)
    except Exception as exc:
        log.warning('_ensure_status_note_column: migration skipped — %s', exc)


def _push_clipboard(text: str) -> bool:
    """Push text to the system clipboard via pyperclip."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


@contextmanager
def _conn() -> Generator:
    con = psycopg2.connect(_DSN)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


_ensure_reasoning_column()
_ensure_status_note_column()


# ---------------------------------------------------------------------------
# Seed data — Work Tracks items from master plan (session 6)
# ---------------------------------------------------------------------------

_SEED_ITEMS = [
    # (agent, priority, body, source)
    ('claude', 20, 'PP-SHELL-001 Tier 2 — remove deprecated blocks + replace ARCH-VIOLATES with `tgw` wrappers (coordinate locationupdate arg-order with bash callers)', 'plan'),
    ('claude', 30, 'PP-MC-001 Phase 2 — `tgwitem` copyin + ebay/ + pipeline/ subdirs', 'plan'),
    ('claude', 40, 'PP-GLOBALS-001 — analysis only: identify offer-invariant fields; design doc', 'plan'),
    ('claude', 50, 'PP-HINT-001 — eBay Browse enrichment in ebay_draft; per-SKU hint trail', 'plan'),
    ('admin',  10, 'New eBay keyset — developer.ebay.com → Application Keys → Create keyset (TGW-Automation-v2); replace App/Cert/Dev ID in secrets_root/ebay-credentials.json', 'plan'),
    ('admin',  15, 'eBay Developer Support — contact for buy.marketplace_insights scope (limited release, no self-service); unblocks PP-REPRICER-001', 'plan'),
    ('admin',  20, 'IGDB credentials — Twitch dev account → register app → save client_id/client_secret to secrets_root/igdb-credentials.json', 'plan'),
    ('admin',  25, 'Discogs credentials — discogs.com/settings/developers → generate token → save to secrets_root/discogs-credentials.json', 'plan'),
    ('admin',  30, 'Go-UPC API key — go-upc.com/api → sign up → save to secrets_root/go-upc-credentials.json', 'plan'),
    ('admin',  35, 'Run Perplexity briefs 001–004 in docs/TGW-Plan-Vault/perplexity/ and drop results to inbox/', 'plan'),
    ('admin',  40, 'tgw ebay-sweep → physical inventory review (run after Perplexity brief results arrive)', 'plan'),
    ('admin',  45, 'Fix 9 wrong-shipping Seller Hub listings flagged in sweep', 'plan'),
    ('admin',  50, 'Tailscale install on TGW server', 'plan'),
    ('admin',  55, 'nvm + npm + markmap-cli install (for Obsidian markmap rendering)', 'plan'),
    ('admin',  60, 'Second keyboard wired up as macroboard (see etc/interfaces/keyd/tgw-macroboard.conf)', 'plan'),
    ('admin',  65, 'eBay webhook endpoint — nginx/cloudflared so PP-SOLD-001 Tier 4 webhook can receive notifications', 'plan'),
    ('db',     20, 'PP-SOLD-001 Tier 3 — physical sweep checklist after full-history CSV import; run tgw ebay-sweep', 'plan'),
    ('gemini', 30, 'PP-GLOBALS-001 — large-context pass over offer-invariant fields once design doc exists', 'plan'),
]


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------

def _enqueue_plan_render(reason: str) -> None:
    """Coalesced taskboard re-render on any todo mutation (PP-PLANDB-001 Phase 2).

    Same pattern as catalog_rebuild: dedupe_key + 30s not_before so rapid
    successive mutations collapse into one render. Never lets a queue problem
    break the todo operation itself.
    """
    try:
        from tgw.queue import state_machine as _sm
        _sm.enqueue_job(
            queue_name='plan_render',
            payload={'reason': reason},
            dedupe_key='plan_render:pending',
            not_before=time.time() + 30,
            max_attempts=3,
        )
    except Exception:
        pass


def todo_add(
    agent: str,
    body: str,
    priority: int = 50,
    source: str = 'session',
    pp_ref: Optional[str] = None,
    depends_on: Optional[List[int]] = None,
    plan_anchor: Optional[str] = None,
    reasoning: str = 'normal',
) -> Dict[str, Any]:
    # Angle-bracket placeholders (e.g. <filename>) are misread by aider as
    # file directives and can create garbage files.  Warn so the author can
    # rewrite with {curly} or [bracket] syntax before the todo reaches aider.
    warning = None
    if re.search(r'<[A-Za-z]', body):
        warning = 'body contains <angle-bracket> text — aider misreads these as filenames; use {curly} or [bracket] placeholders instead'

    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                'INSERT INTO todo_items (agent, priority, body, source, pp_ref, depends_on, plan_anchor, reasoning) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id',
                (agent, priority, body, source, pp_ref, depends_on or [], plan_anchor, reasoning),
            )
            new_id = cur.fetchone()[0]
    _enqueue_plan_render('todo_add')
    result: Dict[str, Any] = {'ok': True, 'id': new_id, 'agent': agent, 'priority': priority,
                               'body': body, 'pp_ref': pp_ref, 'depends_on': depends_on or [],
                               'plan_anchor': plan_anchor, 'reasoning': reasoning}
    if warning:
        result['warning'] = warning
    return result


def todo_done(item_id: int) -> Dict[str, Any]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE todo_items SET done_at = now() WHERE id = %s AND done_at IS NULL RETURNING id, agent, body",
                (item_id,),
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found or already done'}
    _enqueue_plan_render('todo_done')
    return {'ok': True, 'id': row[0], 'agent': row[1], 'body': row[2]}


def todo_list(agent: Optional[str] = None, show_all: bool = False) -> List[Dict[str, Any]]:
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            parts = []
            params: list = []
            if agent:
                parts.append('agent = %s')
                params.append(agent)
            if not show_all:
                parts.append('done_at IS NULL')
            where = ('WHERE ' + ' AND '.join(parts)) if parts else ''
            cur.execute(
                f'SELECT id, agent, priority, body, source, added_at, done_at, '
                f'pp_ref, depends_on, plan_anchor, reasoning, status_note '
                f'FROM todo_items {where} ORDER BY agent, priority, id',
                params,
            )
            return [dict(r) for r in cur.fetchall()]


def todo_get(item_id: int) -> Optional[Dict[str, Any]]:
    """Fetch a single todo row (open or done), or None."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT id, agent, priority, body, source, added_at, done_at, '
                'pp_ref, depends_on, plan_anchor, reasoning, status_note FROM todo_items WHERE id = %s',
                (item_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def open_ids(ids: List[int]) -> set:
    """Return the subset of `ids` that are still open (not done)."""
    if not ids:
        return set()
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                'SELECT id FROM todo_items WHERE id = ANY(%s) AND done_at IS NULL',
                (ids,),
            )
            return {r[0] for r in cur.fetchall()}


def todo_update(item_id: int, body: str) -> Dict[str, Any]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE todo_items SET body = %s WHERE id = %s AND done_at IS NULL RETURNING id, agent",
                (body, item_id),
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found or already done'}
    _enqueue_plan_render('todo_update')
    return {'ok': True, 'id': row[0], 'agent': row[1], 'body': body}


def todo_set_status_note(item_id: int, note: str) -> Dict[str, Any]:
    """Record a progress/dispatch note without touching ``body`` (todo #1384:
    the tgw-coder dispatch step was overwriting the original finding text
    with a generic 'in progress: tgw-coder' placeholder via todo_update)."""
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE todo_items SET status_note = %s WHERE id = %s AND done_at IS NULL "
                "RETURNING id, agent, body",
                (note, item_id),
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found or already done'}
    _enqueue_plan_render('todo_set_status_note')
    return {'ok': True, 'id': row[0], 'agent': row[1], 'body': row[2], 'status_note': note}


def todo_delegate(item_id: int, new_agent: str) -> Dict[str, Any]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE todo_items SET agent = %s WHERE id = %s AND done_at IS NULL RETURNING id, body",
                (new_agent, item_id),
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found or already done'}
    _enqueue_plan_render('todo_delegate')
    return {'ok': True, 'id': row[0], 'agent': new_agent, 'body': row[1]}


def todo_set_priority(item_id: int, priority: int) -> Dict[str, Any]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "UPDATE todo_items SET priority = %s WHERE id = %s AND done_at IS NULL RETURNING id, agent, body",
                (priority, item_id),
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found or already done'}
    _enqueue_plan_render('todo_set_priority')
    return {'ok': True, 'id': row[0], 'agent': row[1], 'priority': priority, 'body': row[2]}


def todo_top(agent: str) -> Optional[Dict[str, Any]]:
    """Return the highest-priority open task for *agent* (lowest priority int,
    ties broken by id), or None when none exist."""
    with _conn() as con:
        with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                'SELECT id, agent, priority, body, source, added_at, done_at, '
                'pp_ref, depends_on, plan_anchor, reasoning FROM todo_items '
                'WHERE agent = %s AND done_at IS NULL ORDER BY priority, id LIMIT 1',
                (agent,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def todo_set_meta(
    item_id: int,
    pp_ref: Optional[str] = None,
    depends_on: Optional[List[int]] = None,
    plan_anchor: Optional[str] = None,
    reasoning: Optional[str] = None,
    status_note: Optional[str] = None,
) -> Dict[str, Any]:
    """Set PP-PLANDB-001 metadata on an existing item. Only passed fields change."""
    sets, params = [], []
    if pp_ref is not None:
        sets.append('pp_ref = %s')
        params.append(pp_ref or None)          # '--pp ""' clears the field
    if depends_on is not None:
        sets.append('depends_on = %s')
        params.append(depends_on)
    if plan_anchor is not None:
        sets.append('plan_anchor = %s')
        params.append(plan_anchor or None)
    if reasoning is not None:
        sets.append('reasoning = %s')
        params.append(reasoning)
    if status_note is not None:
        sets.append('status_note = %s')
        params.append(status_note or None)
    if not sets:
        return {'ok': False, 'error': 'no metadata given — pass --pp/--depends/--anchor/--reasoning/--status-note'}
    params.append(item_id)
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                f"UPDATE todo_items SET {', '.join(sets)} WHERE id = %s "
                f"RETURNING id, agent, pp_ref, depends_on, plan_anchor, reasoning, status_note",
                params,
            )
            row = cur.fetchone()
    if row is None:
        return {'ok': False, 'error': f'item {item_id} not found'}
    _enqueue_plan_render('todo_set_meta')
    return {'ok': True, 'id': row[0], 'agent': row[1], 'pp_ref': row[2],
            'depends_on': row[3], 'plan_anchor': row[4], 'reasoning': row[5],
            'status_note': row[6]}


# ---------------------------------------------------------------------------
# Task brief — self-contained per-agent spec (PP-PLANDB-001 Phase 1)
# ---------------------------------------------------------------------------

_PLAN_EXTRACT_CAP = 6000

_BRIEF_CONSTRAINTS = """\
## Constraints

- Read `CLAUDE.md` first; settled-architecture rules apply (tgw-api fence,
  `{ok, ...}` output contract, secrets from `secrets_root`, workers stay thin,
  catalog rebuild always a job).
- Never touch config files, secrets, or eBay OAuth scopes.
- Acceptance: `pytest -q` must pass offline; new behavior gets tests.
- If a requirement is impossible as specified, stop and explain instead of
  improvising."""


def extract_plan_section(plan_path: Path, anchor: str) -> str:
    """Extract one master-plan section: the heading whose text contains `anchor`
    through to the next heading of the same or higher level. Capped."""
    if not plan_path.exists():
        return ''
    lines = plan_path.read_text(encoding='utf-8').splitlines()
    start = level = None
    for i, line in enumerate(lines):
        m = re.match(r'^(#{1,6})\s+(.*)$', line)
        if m and anchor.lower() in m.group(2).lower():
            start, level = i, len(m.group(1))
            break
    if start is None:
        return ''
    out = [lines[start]]
    for line in lines[start + 1:]:
        m = re.match(r'^(#{1,6})\s', line)
        if m and len(m.group(1)) <= level:
            break
        out.append(line)
    text = '\n'.join(out).strip()
    if len(text) > _PLAN_EXTRACT_CAP:
        text = text[:_PLAN_EXTRACT_CAP] + '\n\n[... section truncated — read the master plan for the rest]'
    return text


def todo_brief(item_id: int, plan_path: Path) -> Dict[str, Any]:
    """Build a self-contained task spec for one todo (Aider message-file
    pattern, next-process.md): todo body + linked plan-section extract +
    dependency status + standing constraints. Minimal context, link out for more."""
    item = todo_get(item_id)
    if item is None:
        return {'ok': False, 'error': f'item {item_id} not found'}

    anchor = item.get('plan_anchor') or item.get('pp_ref') or ''
    extract = extract_plan_section(plan_path, anchor) if anchor else ''

    dep_lines = []
    for dep_id in item.get('depends_on') or []:
        dep = todo_get(dep_id)
        if dep is None:
            dep_lines.append(f'- #{dep_id} — MISSING (deleted?)')
        else:
            state = 'done' if dep['done_at'] else 'OPEN — blocks this task'
            dep_lines.append(f'- #{dep_id} [{state}] {dep["body"][:100]}')

    status = 'done' if item['done_at'] else 'open'
    parts = [
        f'# Task brief — todo #{item["id"]} [{item["agent"]}] '
        f'(p{item["priority"]}, source: {item["source"]}, {status})',
        '',
        'You are working in the Trader Grim\'s Warehouse (TGW) repo at',
        '`/opt/TGW/src/trader-grims-warehouse`. This brief is self-contained;',
        'consult the linked plan section before deviating from it.',
        '',
        '## Task',
        '',
        item['body'],
        '',
    ]
    reasoning = item.get('reasoning', 'normal')
    if reasoning and reasoning != 'normal':
        parts += [f'**Reasoning:** {reasoning}', '']
    if item.get('status_note'):
        parts += [f'**Status:** {item["status_note"]}', '']
    if item.get('pp_ref'):
        parts += [f'**Plan item:** {item["pp_ref"]}'
                  + (f' — see master-plan section "{item["plan_anchor"]}"' if item.get('plan_anchor') else ''),
                  '']
    if dep_lines:
        parts += ['## Dependencies', ''] + dep_lines + ['']
    if extract:
        parts += [f'## Linked plan section ({anchor})', '', extract, '']
    elif anchor:
        parts += ['## Linked plan section', '',
                  f'(no master-plan heading matched "{anchor}" — read '
                  f'`docs/TGW-Plan-Vault/plan/TGW-Master-Plan.md` directly)', '']
    parts.append(_BRIEF_CONSTRAINTS)

    return {'ok': True, 'id': item['id'], 'agent': item['agent'],
            'brief': '\n'.join(parts)}


def todo_seed() -> Dict[str, Any]:
    """Seed Work Tracks items; skip if body already exists for that agent."""
    added = 0
    skipped = 0
    with _conn() as con:
        with con.cursor() as cur:
            for agent, priority, body, source in _SEED_ITEMS:
                cur.execute(
                    'SELECT 1 FROM todo_items WHERE agent = %s AND body = %s LIMIT 1',
                    (agent, body),
                )
                if cur.fetchone():
                    skipped += 1
                    continue
                cur.execute(
                    'INSERT INTO todo_items (agent, priority, body, source) VALUES (%s, %s, %s, %s)',
                    (agent, priority, body, source),
                )
                added += 1
    return {'ok': True, 'seeded': added, 'skipped': skipped}


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------

def _parse_depends(raw: Optional[str]) -> Optional[List[int]]:
    """'12,14' → [12, 14]; '' → [] (clears); None → None (untouched)."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return []
    return [int(p) for p in re.split(r'[,\s]+', raw) if p]


def _tty_prompt(prompt: str) -> str:
    """Write prompt to /dev/tty and read one line. Returns '' on OSError."""
    try:
        with open('/dev/tty', 'r+') as tty:
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip().lower()
    except OSError:
        return ''


def _next_interactive(cfg: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    """Interactive --next loop: less pager + done/skip prompt (TTY only)."""
    import subprocess

    while True:
        top = todo_top(agent_name)
        if top is None:
            print(f'No open tasks for agent: {agent_name}')
            return {'ok': False, 'error': f'no open tasks for {agent_name}'}

        result = todo_brief(top['id'], cfg['plan_master_path'])
        if not result['ok']:
            print(f"Error: {result['error']}")
            return result

        brief = result['brief']

        # Copy to clipboard first so it's ready before less opens.
        if not _push_clipboard(brief):
            print('[clipboard] copy failed — wl-copy and xclip not found')

        # Pipe brief through less. -F quits if output fits on one screen;
        # -X skips the termcap init/deinit flash.
        try:
            subprocess.run(['less', '-FX'], input=brief, text=True)
        except FileNotFoundError:
            print(brief)

        answer = _tty_prompt(f'\nTask #{top["id"]} complete? [Y/n/s=skip] ')

        if answer in ('y', 'yes', ''):
            todo_done(top['id'])
            print(f'Done: #{top["id"]}')
            return {'ok': True, 'id': top['id'], 'action': 'done'}
        elif answer in ('s', 'skip'):
            print(f'Skipped #{top["id"]} — showing next.')
            continue
        else:
            print(f'Left open: #{top["id"]}')
            return {'ok': True, 'id': top['id'], 'action': 'left_open'}


def _nextloop_interactive(cfg: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    """Loop --next until the user quits or tasks are exhausted."""
    import subprocess

    done_count = 0
    skipped_count = 0

    while True:
        top = todo_top(agent_name)
        if top is None:
            print(f'No more open tasks for agent: {agent_name}')
            break

        result = todo_brief(top['id'], cfg['plan_master_path'])
        if not result['ok']:
            print(f"Error: {result['error']}")
            return result

        brief = result['brief']

        if not _push_clipboard(brief):
            print('[clipboard] copy failed — wl-copy and xclip not found')

        try:
            subprocess.run(['less', '-FX'], input=brief, text=True)
        except FileNotFoundError:
            print(brief)

        answer = _tty_prompt(f'\nTask #{top["id"]}: [y=done/s=skip/q=quit] ')

        if answer in ('y', 'yes', ''):
            todo_done(top['id'])
            done_count += 1
            print(f'Done: #{top["id"]} — next…')
        elif answer in ('s', 'skip'):
            skipped_count += 1
            print(f'Skipped #{top["id"]} — next…')
        else:
            print(f'Left open: #{top["id"]} — exiting loop.')
            break

    return {'ok': True, 'done_count': done_count, 'skipped_count': skipped_count, 'action': 'loop_exit'}


def cmd_todo(cfg: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    # Shorthand: tgw todo --nextloop [AGENT]
    # Loops --next until no tasks remain or user quits.
    if getattr(args, 'nextloop', False) and args.agent != 'brief':
        import sys
        agent_name = getattr(args, 'next_agent', None) or args.agent or 'claude'
        if sys.stdout.isatty() and sys.stdin.isatty():
            return _nextloop_interactive(cfg, agent_name)
        print('--nextloop requires an interactive terminal')
        return {'ok': False, 'error': '--nextloop requires a TTY'}

    # Shorthand: tgw todo --next [AGENT]
    # Equivalent to: tgw todo brief --next --agent AGENT --clip
    # AGENT comes from the positional, or --agent flag, defaulting to 'claude'.
    if getattr(args, 'next_task', False) and args.agent != 'brief':
        import sys
        agent_name = getattr(args, 'next_agent', None) or args.agent or 'claude'
        if sys.stdout.isatty() and sys.stdin.isatty():
            return _next_interactive(cfg, agent_name)
        # Non-interactive fallback: plain print + clipboard, no loop.
        top = todo_top(agent_name)
        if top is None:
            print(f'No open tasks for agent: {agent_name}')
            return {'ok': False, 'error': f'no open tasks for {agent_name}'}
        result = todo_brief(top['id'], cfg['plan_master_path'])
        if result['ok']:
            print(result['brief'])
            if not _push_clipboard(result['brief']):
                print('[clipboard] copy failed — wl-copy and xclip not found')
        else:
            print(f"Error: {result['error']}")
        return result

    # `tgw todo brief <id> [--clip]`
    # `tgw todo brief --next --agent <agent> [--clip]`
    if args.agent == 'brief':
        use_next = getattr(args, 'next_task', False)
        if use_next:
            agent_name = getattr(args, 'next_agent', None) or 'claude'
            top = todo_top(agent_name)
            if top is None:
                print(f'No open tasks for agent: {agent_name}')
                return {'ok': False, 'error': f'no open tasks for {agent_name}'}
            target_id = top['id']
        else:
            if args.brief_id is None:
                print('Usage: tgw todo brief <id> [--clip]\n'
                      '       tgw todo brief --next --agent <agent> [--clip]')
                return {'ok': False, 'error': 'missing id'}
            target_id = int(args.brief_id)
        result = todo_brief(target_id, cfg['plan_master_path'])
        if result['ok']:
            print(result['brief'])
            if getattr(args, 'clip', False):
                if not _push_clipboard(result['brief']):
                    print('[clipboard] copy failed — wl-copy and xclip not found')
        else:
            print(f"Error: {result['error']}")
        return result

    if args.seed:
        result = todo_seed()
        print(f"Seeded {result['seeded']} items ({result['skipped']} already existed).")
        return result

    if args.add:
        agent = args.agent or 'claude'
        result = todo_add(agent, args.add, priority=args.priority, source=args.source,
                          pp_ref=args.pp or None,
                          depends_on=_parse_depends(args.depends),
                          plan_anchor=args.anchor or None,
                          reasoning=getattr(args, 'reasoning', 'normal'))
        extras = f" pp_ref={args.pp}" if args.pp else ''
        print(f"Added #{result['id']} [{agent} p{args.priority}]{extras}: {args.add}")
        if result.get('warning'):
            print(f"WARNING: {result['warning']}")
        return result

    if args.set_meta is not None:
        result = todo_set_meta(args.set_meta,
                               pp_ref=args.pp,
                               depends_on=_parse_depends(args.depends),
                               plan_anchor=args.anchor,
                               reasoning=getattr(args, 'reasoning', None),
                               status_note=getattr(args, 'status_note', None))
        if result['ok']:
            print(f"Meta #{result['id']} [{result['agent']}]: pp_ref={result['pp_ref']} "
                  f"depends_on={result['depends_on']} plan_anchor={result['plan_anchor']}")
        else:
            print(f"Error: {result['error']}")
        return result

    if args.done is not None:
        result = todo_done(args.done)
        if result['ok']:
            print(f"Done: #{result['id']} [{result['agent']}] {result['body'][:60]}")
        else:
            print(f"Error: {result['error']}")
        return result

    if args.update is not None:
        item_id, body = args.update[0], ' '.join(args.update[1:])
        if not body:
            print('Error: --update requires ID and new text')
            return {'ok': False, 'error': 'missing text'}
        result = todo_update(int(item_id), body)
        if result['ok']:
            print(f"Updated #{result['id']} [{result['agent']}]: {body[:60]}")
        else:
            print(f"Error: {result['error']}")
        return result

    if getattr(args, 'note', None) is not None:
        item_id, note = args.note[0], ' '.join(args.note[1:])
        if not note:
            print('Error: --note requires ID and text')
            return {'ok': False, 'error': 'missing text'}
        result = todo_set_status_note(int(item_id), note)
        if result['ok']:
            print(f"Noted #{result['id']} [{result['agent']}]: {note[:60]}")
        else:
            print(f"Error: {result['error']}")
        return result

    if args.delegate is not None:
        item_id, new_agent = args.delegate
        result = todo_delegate(int(item_id), new_agent)
        if result['ok']:
            print(f"Delegated #{result['id']} → {new_agent}: {result['body'][:60]}")
        else:
            print(f"Error: {result['error']}")
        return result

    if args.set_priority is not None:
        item_id, priority = args.set_priority
        result = todo_set_priority(int(item_id), int(priority))
        if result['ok']:
            print(f"Priority #{result['id']} [{result['agent']}] → p{priority}: {result['body'][:50]}")
        else:
            print(f"Error: {result['error']}")
        return result

    # Default: list
    items = todo_list(agent=args.agent, show_all=args.show_all)
    if not items:
        label = f'[{args.agent}]' if args.agent else '[all agents]'
        status = 'all' if args.show_all else 'open'
        print(f'No {status} TODO items {label}.')
        return {'ok': True, 'count': 0}

    # blocker badges: which of the referenced dependency ids are still open?
    all_deps = sorted({d for item in items for d in (item.get('depends_on') or [])})
    still_open = open_ids(all_deps)

    if getattr(args, 'by_pp', False):
        by_pp: Dict[str, List[Dict[str, Any]]] = {}
        for item in items:
            by_pp.setdefault(item.get('pp_ref') or '(no pp_ref)', []).append(item)
        # tagged PP groups first (the useful part), untagged noise bucket last
        group_order = sorted(k for k in by_pp if k != '(no pp_ref)')
        if '(no pp_ref)' in by_pp:
            group_order.append('(no pp_ref)')
        grouped_items = [(pp, sorted(by_pp[pp], key=lambda i: (i['priority'], i['id']))) for pp in group_order]
    else:
        grouped_items = []
        current_agent = None
        bucket: List[Dict[str, Any]] = []
        for item in items:
            ag = item['agent']
            if ag != current_agent:
                if bucket:
                    grouped_items.append((current_agent, bucket))
                bucket = []
                current_agent = ag
            bucket.append(item)
        if bucket:
            grouped_items.append((current_agent, bucket))

    for label, group in grouped_items:
        print(f'\n── {label} ──')
        for item in group:
            done_mark = '✓' if item['done_at'] else ' '
            badges = ''
            if item.get('pp_ref') and not getattr(args, 'by_pp', False):
                badges += f' [{item["pp_ref"]}]'
            blockers = [d for d in (item.get('depends_on') or []) if d in still_open]
            if blockers:
                badges += ' ⛔' + ','.join(f'#{d}' for d in blockers)
            r = item.get('reasoning', 'normal')
            if r and r != 'normal':
                badges += f' [{r}]'
            if item.get('status_note'):
                badges += f' ({item["status_note"][:30]})'
            body_preview = item['body'][:80] + ('…' if len(item['body']) > 80 else '')
            print(f'  [{done_mark}] #{item["id"]:3d} p{item["priority"]:2d} {badges} {body_preview}')

    print(f'\n{len(items)} item(s).')
    return {'ok': True, 'count': len(items)}
