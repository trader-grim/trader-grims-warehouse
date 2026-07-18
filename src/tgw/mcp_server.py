"""
TGW MCP Server (PP-MCP-001)

Exposes TGW capabilities as Model Context Protocol tools so Claude Code can
query item data, trigger pipeline actions, and inspect system health natively.

Run:
    python -m tgw.mcp_server          (stdio transport — for Claude Code)
    python -m tgw.mcp_server --sse    (SSE transport — for remote clients)

Register in Claude Code:
    ~/.claude/mcp_servers.json:
    {
      "tgw": {
        "command": "sudo",
        "args": ["-u", "tgw", "python", "-m", "tgw.mcp_server"],
        "env": {}
      }
    }
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Dict

from mcp.server import FastMCP
from pydantic import AliasChoices, Field

# ---------------------------------------------------------------------------
# Read-only mode: TGW_MCP_READONLY=1 drops write-capable tools (tgw_enqueue,
# tgw_add_suggest) from registration entirely — used for Tigwa/Hermes MCP
# access while she is IN TRAINING (PP-HERMES-EA-001), not just hidden from
# a client's tool list.
# ---------------------------------------------------------------------------

_READONLY = os.environ.get('TGW_MCP_READONLY', '') in ('1', 'true', 'yes')

# ---------------------------------------------------------------------------
# Bootstrap: load TGW config once at server startup
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(
    os.environ.get('TGW_CONFIG', '/opt/TGW/config/tgw-api-config.json')
)
_PLAN_VAULT_ROOT = Path('/opt/TGW/src/trader-grims-warehouse/docs/TGW-Plan-Vault')
_PLAN_PACKET_VERSION = 'tgw-plan-brief-v1'
_PLAN_PACKET_MAX_SOURCE_BYTES = 64 * 1024
_PP_IDENTIFIER_RE = re.compile(r'^PP-[A-Z0-9][A-Z0-9-]*$')

_cfg: Dict[str, Any] = {}


def _get_cfg() -> Dict[str, Any]:
    global _cfg
    if not _cfg:
        from tgw.config import load_config
        _cfg = load_config(_CONFIG_PATH)
    return _cfg


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name='tgw',
    instructions=(
        'TGW (Trader Grim\'s Warehouse) inventory management platform. '
        'Use these tools to query item data, inspect queue state, and '
        'trigger pipeline actions without shell escapes.'
    ),
)


# ---------------------------------------------------------------------------
# tgw_get_item — fetch full item JSON for a SKU
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_get_item(sku: str) -> str:
    """Fetch the full item JSON record for a given SKU.

    Args:
        sku: The TGW SKU (18-char format, e.g. tgw202601010000001)

    Returns JSON string with item data, or an error object.
    """
    cfg = _get_cfg()
    from tgw import items
    try:
        doc = items.get_item(cfg, sku)
    except FileNotFoundError:
        return json.dumps({'ok': False, 'error': f'item not found: {sku}'})
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})
    return json.dumps({'ok': True, 'sku': sku, 'item': doc}, default=str)


# ---------------------------------------------------------------------------
# tgw_search_items — search catalog by text, location, or status
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_search_items(
    search: str = '',
    location: str = '',
    status: str = '',
    limit: int = 20,
) -> str:
    """Search the TGW catalog and return matching item summaries.

    Args:
        search: Text search across title and SKU fields
        location: Filter by exact location string
        status: Filter by #STATUS value (e.g. 'In Stock', 'sold')
        limit: Maximum items to return (default 20, max 100)

    Returns JSON list of matching items with sku, title, location, status, price.
    """
    cfg = _get_cfg()
    from tgw.api import list_items
    limit = min(max(1, limit), 100)
    try:
        result = list_items(cfg, search=search, location=location,
                            status=status, limit=limit)
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_queue_status — job counts per queue + state
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_queue_status() -> str:
    """Return current job counts per queue and state from PostgreSQL.

    Returns a JSON object with 'queued', 'running', 'dead_letter' counts per queue.
    Dead-letter counts are also split TRANSIENT vs HARD_FAILURE per queue
    ('dead_letter_classified') so real failures stand out from requeue-able noise,
    and 'zero_work_stalls' flags queues where a live worker has eligible jobs
    waiting but completed nothing in the watchdog window (PP-DEADLETTER-001).
    """
    cfg = _get_cfg()
    from tgw.health import classify_dead_letter_errors
    from tgw.queue import state_machine
    state_machine.init(cfg['postgres_dsn'])
    try:
        with state_machine._conn() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT queue_name, state, COUNT(*) as n
                      FROM queue_jobs
                     WHERE state NOT IN ('succeeded', 'cancelled')
                     GROUP BY queue_name, state
                     ORDER BY queue_name, state
                    """
                )
                rows = [
                    {'queue': r[0], 'state': r[1], 'count': r[2]}
                    for r in cur.fetchall()
                ]

        dead_letter_total = sum(
            r['count'] for r in rows if r['state'] == 'dead_letter'
        )
        dead_letter_by_queue = {
            r['queue']: r['count'] for r in rows if r['state'] == 'dead_letter'
        }
        dl_classified = classify_dead_letter_errors(state_machine.dead_letter_errors())
        stall_hours = float(cfg.get('zero_work_stall_hours', 4.0))
        stalls = state_machine.zero_work_queues(stall_hours)
        return json.dumps({
            'ok': True,
            'queues': rows,
            'dead_letter_total': dead_letter_total,
            'dead_letter_by_queue': dead_letter_by_queue,
            'dead_letter_classified': dl_classified,
            'dead_letter_transient': sum(c['transient'] for c in dl_classified.values()),
            'dead_letter_hard': sum(c['hard'] for c in dl_classified.values()),
            'zero_work_stalls': stalls,
        }, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_health — platform health summary
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_health() -> str:
    """Run TGW platform health checks and return a summary.

    Checks: PostgreSQL, SQLite catalog, eBay token, Ollama, worker services.
    Returns a JSON summary with an 'all_ok' boolean and per-check results.
    """
    cfg = _get_cfg()
    from tgw.health import check_all
    try:
        result = check_all(cfg, include_ollama=False, include_ebay=False)
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_enqueue — enqueue a pipeline action for a SKU
# ---------------------------------------------------------------------------

def tgw_enqueue(sku: str, action: str) -> str:
    """Enqueue a pipeline action for a given item SKU.

    Args:
        sku: Target item SKU
        action: Pipeline stage to enqueue. Valid values:
            ai_identify   — (re-)run AI identification
            ebay_draft    — generate eBay draft listing
            ebay_price    — run pricing worker
            ebay_stage    — create eBay unpublished offer
            ebay_upload   — upload photos to eBay EPS
            catalog_rebuild — rebuild catalog for this item

    Returns ok/job_id or error.
    """
    cfg = _get_cfg()
    import psycopg2.errors

    from tgw.queue import state_machine

    _VALID_ACTIONS = {
        'ai_identify', 'ebay_draft', 'ebay_price', 'ebay_stage',
        'ebay_upload', 'catalog_rebuild',
    }
    if action not in _VALID_ACTIONS:
        return json.dumps({
            'ok': False,
            'error': f'invalid action {action!r}; valid: {sorted(_VALID_ACTIONS)}',
        })

    from tgw import items
    from tgw.resolver import find_current_sku

    jf = items.sku_json(cfg, sku)
    if not jf.exists():
        current = find_current_sku(cfg, sku)
        if current:
            jf = items.sku_json(cfg, current)
        else:
            return json.dumps({'ok': False, 'error': f'item not found: {sku}'})

    state_machine.init(cfg['postgres_dsn'])
    try:
        jid = state_machine.enqueue_job(
            queue_name=action,
            payload={'sku': sku},
            entity_type='item',
            entity_id=sku,
            operation='run',
            dedupe_key=f'{action}:{sku}',
            max_attempts=3,
        )
        return json.dumps({'ok': True, 'job_id': jid, 'queue': action, 'sku': sku})
    except psycopg2.errors.UniqueViolation:
        return json.dumps({'ok': True, 'note': 'job already queued', 'sku': sku})
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


if not _READONLY:
    mcp.tool()(tgw_enqueue)


# ---------------------------------------------------------------------------
# tgw_get_todo — list open TODO items
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_get_todo(
    agent: Annotated[
        str,
        Field(validation_alias=AliasChoices('agent', 'Agent')),
    ] = '',
) -> str:
    """List open TODO items from the TGW multi-agent tracker.

    Args:
        agent: Filter by agent ('claude', 'admin', 'gemini', 'db', 'tigwa', or '' for all)

    Returns JSON list of open TODO items with id, agent, priority, body.
    """
    cfg = _get_cfg()
    from tgw.queue import state_machine
    state_machine.init(cfg['postgres_dsn'])
    try:
        with state_machine._conn() as con:
            import psycopg2.extras
            with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if agent:
                    cur.execute(
                        """SELECT id, agent, priority, body, source, added_at
                             FROM todo_items
                            WHERE done_at IS NULL AND agent = %s
                            ORDER BY priority, added_at""",
                        (agent,),
                    )
                else:
                    cur.execute(
                        """SELECT id, agent, priority, body, source, added_at
                             FROM todo_items
                            WHERE done_at IS NULL
                            ORDER BY priority, added_at"""
                    )
                rows = [dict(r) for r in cur.fetchall()]
        return json.dumps({'ok': True, 'agent': agent or 'all', 'items': rows},
                          default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_add_suggest — append to SUGGESTIONS.md
# ---------------------------------------------------------------------------

def tgw_add_suggest(
    text: Annotated[
        str,
        Field(validation_alias=AliasChoices('text', 'Text')),
    ],
) -> str:
    """Append a suggestion or note to SUGGESTIONS.md for the next planning session.

    This is the same as running `tgw suggest "..."` from the shell.
    Use it to capture ideas, task requests, or observations mid-session.

    Args:
        text: The suggestion text to append (will be timestamped automatically)

    Returns ok/path on success.
    """
    cfg = _get_cfg()
    from tgw.api import cmd_suggest
    try:
        result = cmd_suggest(cfg, text)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


if not _READONLY:
    mcp.tool()(tgw_add_suggest)


# ---------------------------------------------------------------------------
# tgw_dead_letter — inspect dead_letter jobs
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_dead_letter(
    queue: str = '',
    limit: int = 50,
) -> str:
    """List dead_letter jobs with their classify verdict (transient vs permanent).

    Args:
        queue: Filter by queue name (empty = all queues)
        limit: Max jobs to return (default 50)

    Returns JSON list of dead_letter jobs with queue, sku, error, verdict fields.
    """
    cfg = _get_cfg()
    from tgw.queue import state_machine
    from tgw.queue.worker_base import classify_dead_letter
    state_machine.init(cfg['postgres_dsn'])
    try:
        jobs = state_machine.dead_letter_jobs(queue_name=queue, limit=limit)
        result = []
        for j in jobs:
            payload = dict(j['payload_json'] or {})
            verdict, delay = classify_dead_letter(j.get('error_detail') or '')
            result.append({
                'job_id': j['job_id'],
                'queue': j['queue_name'],
                'sku': payload.get('sku', payload.get('entity_id', '')),
                'error': (j.get('error_detail') or '')[:200],
                'verdict': verdict,
                'requeue_delay': delay,
                'attempt_count': j['attempt_count'],
                'finished_at': str(j['finished_at']) if j['finished_at'] else None,
            })
        return json.dumps({'ok': True, 'count': len(result), 'jobs': result}, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_hint_trail — show identification history for an item
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_hint_trail(sku: str) -> str:
    """Return the AI identification history for a given item.

    Args:
        sku: Target item SKU

    Returns JSON list of history events (ai_identify and hint_set events).
    """
    cfg = _get_cfg()
    from tgw.api import cmd_hint_trail
    try:
        result = cmd_hint_trail(cfg, sku)
        return json.dumps(result, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_catalog_verify — run assumption verification on a subset of items
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_catalog_verify(
    location: str = '',
    limit: int = 100,
    severity: str = 'warning',
    mark_verified: bool = False,
    force: bool = False,
    skip_verified: bool = False,
) -> str:
    """Scan ItemData for assumption violations and return a violation summary.

    Args:
        location: Limit scan to items at this location (empty = all)
        limit: Maximum items to scan (default 100)
        severity: Minimum severity to include ('critical', 'warning', 'info')
        mark_verified: Write catalog_verified hall pass to items with no violations
        force: With mark_verified=True: write hall pass even to items with violations
        skip_verified: Skip items that already have a catalog_verified hall pass

    Returns JSON with scanned count, violation count, and by_rule breakdown.
    """
    cfg = _get_cfg()
    from tgw.api import cmd_catalog_verify
    try:
        result = cmd_catalog_verify(
            cfg,
            location=location,
            limit=limit,
            output=None,
            min_severity=severity,
            mark_verified=mark_verified,
            force=force,
            skip_verified=skip_verified,
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


# ---------------------------------------------------------------------------
# tgw_mailbox_send — write a message into another actor's Plan Vault inbox
# (PP-RUNNERCOMMS-001)
# ---------------------------------------------------------------------------

def tgw_mailbox_send(
    to_actor: Annotated[
        str,
        Field(validation_alias=AliasChoices('to_actor', 'To_actor', 'To')),
    ],
    text: Annotated[
        str,
        Field(validation_alias=AliasChoices('text', 'Text')),
    ],
    from_actor: Annotated[
        str,
        Field(validation_alias=AliasChoices('from_actor', 'From_actor', 'From')),
    ] = 'tigwa',
    msg_type: Annotated[
        str,
        Field(validation_alias=AliasChoices('msg_type', 'Msg_type', 'Type')),
    ] = 'NOTE',
    subject: Annotated[
        str,
        Field(validation_alias=AliasChoices('subject', 'Subject')),
    ] = '',
    todo_id: Annotated[
        int,
        Field(validation_alias=AliasChoices('todo_id', 'Todo_id', 'Todo')),
    ] = 0,
) -> str:
    """Send a message to another actor's Plan Vault inbox mailbox.

    Same mechanism as `tgw mailbox send <actor> "<message>"` from the shell
    and the `tgw-mailbox-send` Claude Code skill (PP-RUNNERCOMMS-001) — this
    is the MCP front door for agents (e.g. Tigwa/Hermes) that don't have
    shell access. Writes a file into docs/TGW-Plan-Vault/inbox/<to_actor>/
    following the existing per-actor inbox naming/header convention.

    Args:
        to_actor: Target actor mailbox, e.g. 'claude', 'tigwa', 'dave'
        text: Message body
        from_actor: Sending actor (default: 'tigwa' — this tool is normally
            called by Tigwa/Hermes-based actors, unlike the CLI's 'claude'
            default)
        msg_type: Message type, e.g. NOTE, REQUEST, RESPONSE, REVIEW
        subject: Short subject/title; also used to derive the filename slug
        todo_id: Related todo id, recorded in the message header (0 = none)

    Returns ok/file path on success.
    """
    cfg = _get_cfg()
    from tgw.api import cmd_mailbox_send
    try:
        result = cmd_mailbox_send(
            cfg,
            to_actor,
            text,
            from_actor=from_actor,
            msg_type=msg_type,
            subject=subject or None,
            todo_id=todo_id or None,
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


if not _READONLY:
    mcp.tool()(tgw_mailbox_send)


# ---------------------------------------------------------------------------
# tgw_get_plan_brief — bounded, exact-source Master Plan retrieval
# ---------------------------------------------------------------------------

def _canonical_plan_source() -> tuple[Path, bytes, dict[str, Any]]:
    """Return canonical Master Plan bytes with reproducible source metadata."""
    path = _PLAN_VAULT_ROOT / 'plan' / 'TGW-Master-Plan.md'
    raw = path.read_bytes()
    stat = path.stat()
    return path, raw, {
        'path': str(path),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'bytes': len(raw),
        'mtime_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
    }


def _plan_heading_sections(text: str, pp: str) -> list[dict[str, Any]]:
    """Find exact PP-bearing headings and their structural source ranges."""
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r'^(#{2,6})\s+(.+?)\s*$', line)
        heading = match.group(2).strip() if match else ''
        if match and re.match(rf'^{re.escape(pp)}(?:\s|:|—|$)', heading.upper()):
            headings.append((index, len(match.group(1)), heading))

    sections: list[dict[str, Any]] = []
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


@mcp.tool()
def tgw_get_plan_brief(pp: str) -> str:
    """Retrieve one bounded, exact-source Master Plan packet for a PP identifier.

    The Master Plan remains canonical. This read-only tool returns source hashes,
    exact heading/line/byte anchors, the exact matched section, and the matching
    canonical PP detail document when it is small enough to include. It never
    produces a model-written summary and refuses missing or ambiguous PP matches.

    Args:
        pp: Exact PP identifier, for example `PP-KNOWLEDGE-001`.

    Returns JSON packet with canonical-source provenance and retrieval warnings.
    """
    query_pp = pp.strip().upper()
    if not _PP_IDENTIFIER_RE.fullmatch(query_pp):
        return json.dumps({
            'ok': False,
            'error': 'pp must be an exact identifier like PP-KNOWLEDGE-001',
            'code': 'invalid_pp_identifier',
        })

    try:
        _path, raw, source = _canonical_plan_source()
    except OSError as exc:
        return json.dumps({
            'ok': False,
            'error': str(exc),
            'code': 'canonical_plan_unavailable',
        })

    text = raw.decode('utf-8')
    sections = _plan_heading_sections(text, query_pp)
    if not sections:
        return json.dumps({
            'ok': False,
            'code': 'pp_not_found',
            'query': {'pp': query_pp},
            'canonical_source': source,
            'warning': 'Read the full canonical Master Plan or select another PP; no heading was guessed.',
        })
    if len(sections) > 1:
        return json.dumps({
            'ok': False,
            'code': 'ambiguous_pp',
            'query': {'pp': query_pp},
            'canonical_source': source,
            'matches': [{key: section[key] for key in ('heading', 'line_start', 'line_end')}
                        for section in sections],
            'warning': 'Multiple exact PP headings found; no section was selected.',
        })

    section = sections[0]
    if len(section['content'].encode('utf-8')) > _PLAN_PACKET_MAX_SOURCE_BYTES:
        return json.dumps({
            'ok': False,
            'code': 'section_too_large',
            'query': {'pp': query_pp},
            'canonical_source': source,
            'section': {key: section[key] for key in section if key != 'content'},
            'warning': 'Exact section exceeds the packet limit; use the canonical source path and anchors.',
        })

    detail_path = _PLAN_VAULT_ROOT / 'plan' / 'pp' / f'{query_pp}.md'
    detail: dict[str, Any] = {'path': str(detail_path), 'status': 'absent'}
    warnings: list[str] = []
    if detail_path.is_file():
        detail_raw = detail_path.read_bytes()
        detail.update({
            'sha256': hashlib.sha256(detail_raw).hexdigest(),
            'bytes': len(detail_raw),
        })
        if len(detail_raw) <= _PLAN_PACKET_MAX_SOURCE_BYTES:
            detail.update({'status': 'present', 'content': detail_raw.decode('utf-8')})
        else:
            detail.update({'status': 'too_large'})
            warnings.append('Linked PP detail exceeds the packet limit and was not partially quoted.')
    else:
        warnings.append('No linked canonical PP detail document exists at the expected path.')

    return json.dumps({
        'ok': True,
        'generator_version': _PLAN_PACKET_VERSION,
        'query': {'pp': query_pp},
        'canonical_source': source,
        'section': section,
        'linked_pp_detail': detail,
        'warnings': warnings,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import sys
    sse = '--sse' in sys.argv
    if sse:
        mcp.run(transport='sse')
    else:
        mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
