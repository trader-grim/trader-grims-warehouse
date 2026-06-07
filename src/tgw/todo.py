"""
tgw.todo — Multi-agent TODO tracker (PP-TODO-001).

Storage: PostgreSQL table ``todo_items`` in ``state_machine`` DB.
CLI entry point: ``tgw todo [agent] [--add TEXT] [--done ID] [--seed]``

Agents: claude, admin, gemini, db  (open-ended — any string is valid)
Priority: integer, lower = higher priority (50 = default, 10 = urgent, 90 = someday)
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional

import psycopg2
import psycopg2.extras

_DSN = 'dbname=state_machine user=tgw'


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

def todo_add(agent: str, body: str, priority: int = 50, source: str = 'session') -> Dict[str, Any]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                'INSERT INTO todo_items (agent, priority, body, source) VALUES (%s, %s, %s, %s) RETURNING id',
                (agent, priority, body, source),
            )
            new_id = cur.fetchone()[0]
    return {'ok': True, 'id': new_id, 'agent': agent, 'priority': priority, 'body': body}


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
                f'SELECT id, agent, priority, body, source, added_at, done_at '
                f'FROM todo_items {where} ORDER BY agent, priority, id',
                params,
            )
            return [dict(r) for r in cur.fetchall()]


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
    return {'ok': True, 'id': row[0], 'agent': row[1], 'body': body}


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
    return {'ok': True, 'id': row[0], 'agent': row[1], 'priority': priority, 'body': row[2]}


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

def cmd_todo(cfg: Dict[str, Any], args: argparse.Namespace) -> Dict[str, Any]:
    if args.seed:
        result = todo_seed()
        print(f"Seeded {result['seeded']} items ({result['skipped']} already existed).")
        return result

    if args.add:
        agent = args.agent or 'claude'
        result = todo_add(agent, args.add, priority=args.priority, source=args.source)
        print(f"Added #{result['id']} [{agent} p{args.priority}]: {args.add}")
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

    current_agent = None
    for item in items:
        ag = item['agent']
        if ag != current_agent:
            print(f'\n── {ag} ──')
            current_agent = ag
        done_mark = '✓' if item['done_at'] else ' '
        body_preview = item['body'][:80] + ('…' if len(item['body']) > 80 else '')
        print(f'  [{done_mark}] #{item["id"]:3d} p{item["priority"]:2d}  {body_preview}')

    print(f'\n{len(items)} item(s).')
    return {'ok': True, 'count': len(items)}
