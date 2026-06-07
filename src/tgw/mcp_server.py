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

import json
import os
from pathlib import Path
from typing import Any, Dict

from mcp.server import FastMCP

# ---------------------------------------------------------------------------
# Bootstrap: load TGW config once at server startup
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(
    os.environ.get('TGW_CONFIG', '/opt/TGW/config/tgw-api-config.json')
)

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
    jf = cfg['itemdata_root'] / sku / f'{sku}.json'
    if not jf.exists():
        return json.dumps({'ok': False, 'error': f'item not found: {sku}'})
    try:
        doc = json.loads(jf.read_text(encoding='utf-8'))
        return json.dumps({'ok': True, 'sku': sku, 'item': doc}, default=str)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


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
    Also returns total dead_letter count for quick health assessment.
    """
    cfg = _get_cfg()
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
        return json.dumps({
            'ok': True,
            'queues': rows,
            'dead_letter_total': dead_letter_total,
        })
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

@mcp.tool()
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

    jf = cfg['itemdata_root'] / sku / f'{sku}.json'
    if not jf.exists():
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


# ---------------------------------------------------------------------------
# tgw_get_todo — list open TODO items
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_get_todo(agent: str = '') -> str:
    """List open TODO items from the TGW multi-agent tracker.

    Args:
        agent: Filter by agent ('claude', 'admin', 'gemini', 'db', or '' for all)

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

@mcp.tool()
def tgw_add_suggest(text: str) -> str:
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
) -> str:
    """Scan ItemData for assumption violations and return a violation summary.

    Args:
        location: Limit scan to items at this location (empty = all)
        limit: Maximum items to scan (default 100)
        severity: Minimum severity to include ('critical', 'warning', 'info')

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
        )
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


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
