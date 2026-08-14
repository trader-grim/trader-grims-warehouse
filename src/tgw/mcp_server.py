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
from typing import Annotated, Any, Dict, List, Optional

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
# alias_field — shared MCP parameter-alias helper (PP-KNOWLEDGE-001, todo
# #1528). Agents/clients sometimes present a title-cased parameter label
# (e.g. `Agent`, `Text`) even though the canonical, stable, public MCP
# JSON-schema property is lowercase (`agent`, `text`) — Tigwa hit two live
# cases of this (tgw_get_todo's `Agent` silently ignored, tgw_add_suggest's
# `Text` failing validation outright) and fixed both with Pydantic
# AliasChoices. This helper generalizes that established pattern: the
# canonical lowercase key never changes (requirement 1), the title-cased
# form (`name.capitalize()` — first letter up, rest as authored, matching
# Tigwa's own `to_actor` -> `To_actor` precedent, NOT per-word Title_Case)
# is accepted as an alias (requirement 2), and any genuinely-useful extra
# shorthand alias (e.g. mailbox_send's `To`/`Type`/`Todo`) can still be
# passed through explicitly. See
# docs/TGW-Plan-Vault/inbox/claude/TIGWA-REQUEST-mcp-parameter-alias-pattern-2026-07-18.md
# for the full spec this implements.
# ---------------------------------------------------------------------------

def alias_field(name: str, *extra_aliases: str) -> Any:
    """Return a pydantic Field accepting `name` plus its title-cased form
    (and any explicit extra_aliases) as validation aliases, while the
    canonical schema property stays `name`.
    """
    return Field(validation_alias=AliasChoices(name, name.capitalize(), *extra_aliases))


# ---------------------------------------------------------------------------
# tgw_get_item — fetch full item JSON for a SKU
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_get_item(sku: Annotated[str, alias_field('sku')]) -> str:
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
    search: Annotated[str, alias_field('search')] = '',
    location: Annotated[str, alias_field('location')] = '',
    status: Annotated[str, alias_field('status')] = '',
    limit: Annotated[int, alias_field('limit')] = 20,
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
# tgw_search_full — full-text search over the recoll knowledge index
# (PP-KNOWLEDGE-001 Track R2, todo #1147). Distinct from tgw_search_items:
# that tool searches the structured item DB (title/location/status fields);
# this one searches everything recoll has indexed (ItemData/ItemArchive/
# ItemCatalog/plan vault + mounted drives, 441K+ docs) — the "front door"
# every agent should use for recovery/audit-style lookups instead of ad-hoc
# grep/find over mounted paths.
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_search_full(
    query: Annotated[str, alias_field('query')],
    limit: Annotated[int, alias_field('limit')] = 20,
) -> str:
    """Full-text search over the entire recoll knowledge index (files,
    ItemData/ItemArchive/ItemCatalog, plan vault, mounted drives — NOT just
    the structured item DB; use tgw_search_items for that).

    Args:
        query: recoll query-language string (implicit AND, -exclude,
            field:term, "phrase", OR). Passed through verbatim.
        limit: Maximum results to return (default 20, max 200)

    Returns JSON {ok, query, count, elapsed_ms, results:[{url, title,
    mtype, fbytes, abstract}, ...]}.
    """
    from tgw.search_full import run_full_text_search
    try:
        result = run_full_text_search(query, limit=limit)
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

def tgw_enqueue(
    sku: Annotated[str, alias_field('sku')],
    action: Annotated[str, alias_field('action')],
) -> str:
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
    agent: Annotated[str, alias_field('agent')] = '',
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
    text: Annotated[str, alias_field('text')],
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
# tgw_clip_deliver — deliver agent-prepared content into Dave's local clip
# history (PP-CLIP-001 clipboard-agent-delivery Phase 0)
# ---------------------------------------------------------------------------

def tgw_clip_deliver(
    content: Annotated[str, alias_field('content')],
    label: Annotated[str, alias_field('label')] = '',
) -> str:
    """Deliver agent-prepared content into Dave's local clipboard history.

    Request-initiated only — call this only when Dave has actually asked for
    something to be delivered (never unsolicited). The content lands as a
    discrete, addressable entry in the existing `tgw clip` history, tagged
    [AGENT], for Dave to select via the rofi picker when he's ready — this
    never writes directly onto the live OS clipboard.

    Args:
        content: The prepared text to deliver.
        label: Optional short human-readable description (e.g. "eBay support
            ticket text + attachments").

    Returns ok/id on success.
    """
    from tgw.clip import deliver_clip
    try:
        result = deliver_clip(content, label=label or None)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})


if not _READONLY:
    mcp.tool()(tgw_clip_deliver)


# ---------------------------------------------------------------------------
# tgw_dead_letter — inspect dead_letter jobs
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_dead_letter(
    queue: Annotated[str, alias_field('queue')] = '',
    limit: Annotated[int, alias_field('limit')] = 50,
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
def tgw_hint_trail(sku: Annotated[str, alias_field('sku')]) -> str:
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
    location: Annotated[str, alias_field('location')] = '',
    limit: Annotated[int, alias_field('limit')] = 100,
    severity: Annotated[str, alias_field('severity')] = 'warning',
    mark_verified: Annotated[bool, alias_field('mark_verified')] = False,
    force: Annotated[bool, alias_field('force')] = False,
    skip_verified: Annotated[bool, alias_field('skip_verified')] = False,
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
    to_actor: Annotated[str, alias_field('to_actor', 'To')],
    text: Annotated[str, alias_field('text')],
    from_actor: Annotated[str, alias_field('from_actor', 'From')] = 'tigwa',
    msg_type: Annotated[str, alias_field('msg_type', 'Type')] = 'NOTE',
    subject: Annotated[str, alias_field('subject')] = '',
    todo_id: Annotated[int, alias_field('todo_id', 'Todo')] = 0,
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
#
# Deterministic parser/retrieval logic lives in tgw.plan_render.plan_brief()
# (PP-KNOWLEDGE-001 / todo #1439, #1520 follow-up refactor, Tigwa's v1
# reviewed submission) — this tool is a thin delegate, not a second parser.
# Paths come from cfg['plan_master_path'] / cfg['plan_detail_root']; no Plan
# root is hard-coded in this module.  Mutable inbox/docs state remains under
# cfg['plan_vault_path'] and is deliberately not a canonical read source.
# ---------------------------------------------------------------------------

@mcp.tool()
def tgw_get_plan_brief(pp: Annotated[str, alias_field('pp', 'PP')]) -> str:
    """Retrieve one bounded, exact-source Master Plan packet for a PP identifier.

    The Master Plan remains canonical. This read-only tool returns source hashes,
    exact heading/line/byte anchors, and the exact matched section. A linked
    canonical `plan/pp/<PP>.md` detail document, if present, is reported
    metadata-only (path/status/hash/bytes) — its content is never inlined.
    It never produces a model-written summary and refuses missing or
    ambiguous PP matches.

    Args:
        pp: Exact PP identifier, for example `PP-KNOWLEDGE-001`.

    Returns JSON packet with canonical-source provenance and retrieval warnings.
    """
    from tgw.plan_render import plan_brief
    cfg = _get_cfg()
    return json.dumps(plan_brief(cfg, pp), ensure_ascii=False)


@mcp.tool()
def tgw_get_plan_graph(
    task: Annotated[str, alias_field('task')],
    receiver: Annotated[str, alias_field('receiver')] = 'codex',
    operation: Annotated[str, alias_field('operation')] = 'brief',
    limit: Annotated[int, alias_field('limit')] = 12,
) -> str:
    """Retrieve a source-envelope-bound graph from the standalone Plan.

    The graph is read-only derived navigation. Canonical Markdown remains
    authoritative and the result grants no approval or effect authority.
    """
    from tgw.plan_graph import live_plan_graph

    cfg = _get_cfg()
    root = Path(cfg.get('standalone_plan_root') or '/opt/TGW/library/plans')
    try:
        return json.dumps(live_plan_graph(
            root, task, receiver=receiver, operation=operation, limit=limit,
            git_path=str(cfg.get('plan_git_path') or 'git'),
        ), ensure_ascii=False)
    except Exception as exc:
        code = getattr(exc, 'code', None)
        return json.dumps({
            'ok': False,
            'error': {'code': code, 'message': str(exc)} if code else str(exc),
            'derived': True,
            'canonical_authority': 'Standalone Plan Markdown remains canonical.',
        }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# tgw_simple_llm_jobs — generic DeepSeek V4-Flash cheap text-transform tool
# (PP-SIMPLEJOBS-001, todo #1574). Backed by the existing tgw.apis.llm
# facility (get_task_model()/call_model() — same 'simple_llm_jobs' task-key
# pattern as pm_intake/suggestions_classify/pricing_comp_filter, all
# deepseek_direct in tgw-models.json). Read-only/no side effects (pure text
# transform, no ItemData/eBay/queue writes) — registered unconditionally,
# same as tgw_search_full, not gated by TGW_MCP_READONLY.
# ---------------------------------------------------------------------------

_SIMPLE_LLM_JOBS_OPERATIONS = {
    'summarize', 'compress_context', 'extract_fields', 'classify',
    'rewrite', 'rank_snippets', 'log_summary',
}


def _simple_llm_jobs_system_prompt(
    operation: str,
    schema: Optional[Dict[str, Any]],
    label_set: Optional[List[str]],
) -> str:
    """Per-operation system prompt — always demands a single JSON object,
    with a worked example so DeepSeek's JSON mode has a concrete shape to
    match (research doc's build_system_prompt sketch)."""
    if operation == 'summarize':
        return (
            "You are a fast, low-cost summarization engine for Trader Grim's Warehouse.\n"
            "Always respond with a single JSON object, no other text.\n"
            'Example: {"summary": "short summary here", "key_points": ["point 1", "point 2"]}'
        )
    if operation == 'compress_context':
        return (
            "You compress long context into a compact representation that preserves "
            "key facts, entities, and relationships.\n"
            "Respond as a single JSON object, no other text.\n"
            'Example: {"entities": [{"name": "...", "type": "..."}], '
            '"facts": ["...", "..."], "summary": "..."}'
        )
    if operation == 'extract_fields':
        schema_json = json.dumps(schema or {}, ensure_ascii=False)
        return (
            "You extract structured fields from unstructured text.\n"
            "Respond with a single JSON object matching this schema/example, no other text:\n"
            f"{schema_json}\n"
            "Do not add extra fields. Do not include explanations."
        )
    if operation == 'classify':
        labels_str = ', '.join(label_set or [])
        return (
            "You perform text classification into exactly one of the allowed labels.\n"
            f"Allowed labels: {labels_str}\n"
            "Respond as a single JSON object, no other text.\n"
            'Example: {"label": "USED", "confidence": 0.92, "reason": "short rationale"}'
        )
    if operation == 'rewrite':
        return (
            "You rewrite text according to instructions.\n"
            "Respond as a single JSON object, no other text.\n"
            'Example: {"rewritten": "new text", "notes": "optional notes"}'
        )
    if operation == 'rank_snippets':
        return (
            "You rank candidate snippets by relevance to a query.\n"
            "Respond as a single JSON object, no other text.\n"
            'Example: {"ranked": [{"index": 0, "score": 0.93}, {"index": 2, "score": 0.71}]}'
        )
    if operation == 'log_summary':
        return (
            "You summarize logs into a compact diagnostic view.\n"
            "Respond as a single JSON object, no other text.\n"
            'Example: {"summary": "high-level description", "errors": ["error 1"], '
            '"suggested_actions": ["action 1"]}'
        )
    raise ValueError(f'unknown operation: {operation!r}')  # pragma: no cover — validated by caller


def _simple_llm_jobs_user_prompt(
    operation: str,
    text: str,
    instructions: str,
    items: Optional[List[str]],
) -> str:
    if operation == 'rank_snippets':
        items_block = '\n'.join(f'{i}: {s}' for i, s in enumerate(items or []))
        return (
            f"Operation: rank_snippets\n"
            f"Additional instructions: {instructions}\n\n"
            "Query text:\n```text\n" + text + "\n```\n\n"
            "Candidate snippets (indexed):\n```text\n" + items_block + "\n```"
        )
    return (
        f"Operation: {operation}\n"
        f"Additional instructions: {instructions}\n\n"
        "Input text:\n```text\n" + text + "\n```"
    )


@mcp.tool()
def tgw_simple_llm_jobs(
    operation: Annotated[str, alias_field('operation')],
    text: Annotated[str, alias_field('text')],
    instructions: Annotated[str, alias_field('instructions')] = '',
    schema: Annotated[Optional[Dict[str, Any]], alias_field('schema')] = None,
    label_set: Annotated[Optional[List[str]], alias_field('label_set')] = None,
    items: Annotated[Optional[List[str]], alias_field('items')] = None,
    max_output_tokens: Annotated[Optional[int], alias_field('max_output_tokens')] = None,
) -> str:
    """Fast, low-cost DeepSeek V4-Flash text-transform tool: summarize,
    compress_context, extract_fields, classify, rewrite, rank_snippets,
    log_summary. Read-only — no ItemData/eBay/queue writes.

    Args:
        operation: One of summarize, compress_context, extract_fields,
            classify, rewrite, rank_snippets, log_summary
        text: Input text to transform (for rank_snippets, the query text)
        instructions: Optional extra guidance for the operation
        schema: Optional field spec (JSON object) for extract_fields
        label_set: Optional allowed labels for classify
        items: Optional candidate snippets for rank_snippets
        max_output_tokens: Optional cap on model output length (currently
            advisory only — not yet wired into DeepSeek's request payload;
            see get_task_generation_config for the config-level equivalent)

    Returns a JSON string: {ok, operation, result} on success (result is the
    parsed JSON object the model returned), or {ok: False, error, raw} if
    the model's response wasn't valid JSON.
    """
    if operation not in _SIMPLE_LLM_JOBS_OPERATIONS:
        return json.dumps({
            'ok': False,
            'error': f'invalid operation {operation!r}; valid: {sorted(_SIMPLE_LLM_JOBS_OPERATIONS)}',
        })

    if operation == 'classify' and label_set is not None and len(label_set) == 0:
        return json.dumps({
            'ok': False,
            'error': 'label_set is empty — no valid classification is possible',
        })

    cfg = _get_cfg()
    from tgw.apis.llm import call_model
    from tgw.apis.ollama import extract_json

    system_prompt = _simple_llm_jobs_system_prompt(operation, schema, label_set)
    user_prompt = _simple_llm_jobs_user_prompt(operation, text, instructions, items)

    try:
        raw = call_model('simple_llm_jobs', system_prompt, user_prompt, cfg)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': str(exc)})

    try:
        result = extract_json(raw)
    except Exception as exc:
        return json.dumps({'ok': False, 'error': f'model response was not valid JSON: {exc}',
                            'raw': raw})

    # Output-contract validation (todo #1576, PP-SIMPLEJOBS-001 follow-up):
    # a JSON-shaped response isn't automatically a response that honors what
    # the caller actually asked for. Only the two operations with an
    # explicit caller-supplied contract (label_set / schema) are checked;
    # the rest have no equivalent contract to validate against.
    #
    # label_set uses explicit None/length checks, not truthiness (todo
    # #1577, Tigwa peer review of #1576): label_set is None means
    # open-ended classification (no check); label_set == [] is rejected
    # fail-loud above, before the model call; non-empty label_set is
    # validated here. `if label_set:` would have silently treated an
    # explicit empty list the same as "not supplied".
    if operation == 'classify' and label_set is not None and len(label_set) > 0:
        label = result.get('label') if isinstance(result, dict) else None
        if label not in label_set:
            return json.dumps({
                'ok': False,
                'error': f'model returned label {label!r} not in label_set',
                'raw': result,
            }, default=str)

    if operation == 'extract_fields' and schema:
        missing = [k for k in schema if not isinstance(result, dict) or k not in result]
        if missing:
            return json.dumps({
                'ok': False,
                'error': f'model response missing requested field(s): {sorted(missing)}',
                'raw': result,
            }, default=str)

    return json.dumps({'ok': True, 'operation': operation, 'result': result}, default=str)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _sse_binding() -> tuple[str, int]:
    """Return the explicit production SSE bind configured by the service unit."""
    host = os.environ.get('TGW_MCP_HOST', '127.0.0.1').strip()
    if not host:
        raise ValueError('TGW_MCP_HOST must not be empty')
    raw_port = os.environ.get('TGW_MCP_PORT', '8000').strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError('TGW_MCP_PORT must be an integer') from exc
    if not 1 <= port <= 65535:
        raise ValueError('TGW_MCP_PORT must be between 1 and 65535')
    return host, port


def main() -> None:
    import sys
    sse = '--sse' in sys.argv
    if sse:
        host, port = _sse_binding()
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport='sse')
    else:
        mcp.run(transport='stdio')


if __name__ == '__main__':
    main()
