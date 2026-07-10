"""
tgw.suggestions — SUGGESTIONS.md parsing and LLM batch classification.

PP-DOCFLOW-001 Phase 2: pre-classify unprocessed suggestion entries before a
planning session so Claude reviews dispositions rather than raw text.

Usage:
    entries = parse_pending(suggestions_path)
    classified = classify_batch(entries, plan_headings, cfg)
    result = apply_classifications(suggestions_path, entries, classified, cfg, write=True)
    print(format_report(entries, result, applied=True))
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from tgw.apis.llm import call_model
from tgw.apis.ollama import extract_json
from tgw.todo import todo_add

_PENDING_RE = re.compile(r'^- \[ \] (\S+) :: (.+)$')
_PP_REF_RE = re.compile(r'^PP-[A-Z0-9]+-\d+$')

_SYSTEM_PROMPT = """\
You are the TGW project manager assistant. TGW is Trader Grim's Warehouse, a \
Python-based inventory and eBay listing management system.

Classify each unprocessed user suggestion into one disposition:
- "already_done": suggestion is fully implemented or already captured in the plan/codebase
- "todo": suggestion describes new work that should become a tracked task
- "plan_append": suggestion adds context/notes to an existing plan section (not new work)
- "review_flag": suggestion is ambiguous, too broad, or requires human judgement

Rules:
- Only mark "already_done" when you are confident it is fully reflected.
- "todo" agent: "claude" for technical/code work, "admin" for operator/business decisions.
- Keep todo_body concise and actionable (one line).
- For todos, set "pp_ref" to the PP-* item id (e.g. "PP-PHOTO-001") ONLY when the
  suggestion clearly belongs to one PP item visible in the plan headings; omit it
  when unsure — a wrong link is worse than no link.
- EXCEPTION: if the suggestion is a purely operator action with no code to write —
  e.g. obtaining an API key or credential, purchasing hardware, setting up an external
  account, OS/service installation, or configuring secrets/permissions — set
  todo_agent="admin" and pp_ref="PP-OPS-001" (the catch-all anchor for operator gates).
- For plan_append, section_heading must exactly match a heading from the plan structure.
- Set "reasoning" based on task complexity:
  "high" for architectural decisions, multi-file refactors, novel design;
  "low" for mechanical edits, renaming, formatting, simple migrations;
  "normal" for everything else (default — omit if normal).
- Respond with a JSON array only — one object per suggestion, in the same index order.
"""

_USER_TEMPLATE = """\
## Plan headings (for plan_append section matching)
{plan_headings}

---

## Suggestions to classify ({count} pending)

{entries}

---

Respond with a JSON array, one object per suggestion:
[
  {{
    "index": 0,
    "action": "already_done|todo|plan_append|review_flag",
    "rationale": "1-2 sentences",
    "todo_agent": "claude|admin",
    "todo_body": "actionable one-line text (todo action only)",
    "pp_ref": "PP-XXXX-NNN (todo action only; omit unless confident)",
    "reasoning": "high|normal|low (todo action only; omit if normal)",
    "section_heading": "## exact heading (plan_append only)",
    "content": "markdown lines to append (plan_append only)",
    "review_agent": "claude|admin",
    "review_body": "what needs review (review_flag only)"
  }}
]
"""

_PLAN_HEADINGS_CAP = 4000
_TODO_SOURCE = 'suggestions_classify'


def parse_pending(suggestions_path: Path) -> List[Dict[str, Any]]:
    """Return list of unprocessed entries: {index, timestamp, text, line_no, raw}."""
    if not suggestions_path.exists():
        return []
    entries = []
    for line_no, line in enumerate(suggestions_path.read_text(encoding='utf-8').splitlines()):
        m = _PENDING_RE.match(line.strip())
        if m:
            entries.append({
                'index': len(entries),
                'timestamp': m.group(1),
                'text': m.group(2).strip(),
                'line_no': line_no,
                'raw': line,
            })
    return entries


def classify_batch(
    entries: List[Dict[str, Any]],
    plan_headings: str,
    cfg: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Send all pending entries to the LLM; return list of classified objects."""
    if not entries:
        return []
    entry_lines = '\n'.join(
        f'{e["index"]}. [{e["timestamp"]}] {e["text"]}'
        for e in entries
    )
    user_prompt = _USER_TEMPLATE.format(
        plan_headings=plan_headings[:_PLAN_HEADINGS_CAP],
        count=len(entries),
        entries=entry_lines,
    )
    raw = call_model('suggestions_classify', _SYSTEM_PROMPT, user_prompt, cfg)
    parsed = extract_json(raw)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for v in parsed.values():
            if isinstance(v, list):
                return v
    return []


def apply_classifications(
    suggestions_path: Path,
    entries: List[Dict[str, Any]],
    classified: List[Dict[str, Any]],
    write: bool = False,
) -> Dict[str, Any]:
    """
    Apply classified dispositions.

    already_done: mark entry [x] in SUGGESTIONS.md (write=True)
    todo: create a tracked todo (write=True)
    plan_append / review_flag: listed in report only (never auto-applied)

    Returns summary dict with counts and details.
    """
    buckets: Dict[str, List] = {
        'already_done': [],
        'todo': [],
        'plan_append': [],
        'review_flag': [],
        'unmatched': [],
    }

    cls_map = {c.get('index', i): c for i, c in enumerate(classified)}
    line_patches: Dict[int, str] = {}

    for entry in entries:
        c = cls_map.get(entry['index'])
        if c is None:
            buckets['unmatched'].append(entry)
            continue
        action = c.get('action', 'review_flag')
        merged = {**entry, **c}
        buckets.get(action, buckets['review_flag']).append(merged)

        if action == 'already_done':
            new_line = entry['raw'].replace('- [ ]', '- [x]', 1)
            rationale = (c.get('rationale') or '').split('.')[0].strip()
            if rationale and rationale not in new_line:
                new_line = new_line.rstrip() + f' — {rationale}'
            line_patches[entry['line_no']] = new_line

        elif action == 'todo' and write:
            agent = c.get('todo_agent', 'claude')
            body = (c.get('todo_body') or entry['text'])[:200]
            # link to the PP item only when the LLM was confident AND the ref
            # is well-formed — a hallucinated link is worse than none
            pp_ref = (c.get('pp_ref') or '').strip().upper()
            if not _PP_REF_RE.match(pp_ref):
                pp_ref = ''
            reasoning = (c.get('reasoning') or 'normal').strip().lower()
            if reasoning not in ('high', 'normal', 'low'):
                reasoning = 'normal'
            todo_add(agent, body, source=_TODO_SOURCE, pp_ref=pp_ref or None,
                     reasoning=reasoning)

    if write and line_patches:
        lines = suggestions_path.read_text(encoding='utf-8').splitlines(keepends=True)
        for line_no, new_line in line_patches.items():
            if line_no < len(lines):
                lines[line_no] = new_line.rstrip('\n') + '\n'
        suggestions_path.write_text(''.join(lines), encoding='utf-8')

    return {
        'ok': True,
        'total': len(entries),
        'already_done': len(buckets['already_done']),
        'todo': len(buckets['todo']),
        'plan_append': len(buckets['plan_append']),
        'review_flag': len(buckets['review_flag']),
        'unmatched': len(buckets['unmatched']),
        'details': buckets,
    }


def format_report(
    result: Dict[str, Any],
    applied: bool,
) -> str:
    """Format a human-readable markdown report of classification results."""
    ts = datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    total = result['total']
    lines = [
        f'## Suggestions Classification Report — {ts}',
        f'{total} pending entr{"y" if total == 1 else "ies"} reviewed. '
        f'Applied: {"yes" if applied else "no (dry-run — re-run with --apply to write)"}',
        '',
    ]
    details = result.get('details', {})

    if details.get('already_done'):
        lines.append(f'### Already reflected ({len(details["already_done"])})')
        for e in details['already_done']:
            lines.append(f'- [x] `{e["timestamp"]}` :: {e["text"]}')
            if e.get('rationale'):
                lines.append(f'  > {e["rationale"]}')
        lines.append('')

    if details.get('todo'):
        label = 'created' if applied else 'to create'
        lines.append(f'### New todos ({label}: {len(details["todo"])})')
        for e in details['todo']:
            agent = e.get('todo_agent', 'claude')
            body = e.get('todo_body') or e['text']
            pp = f' ({e["pp_ref"]})' if e.get('pp_ref') else ''
            lines.append(f'- [ ] `{e["timestamp"]}` :: {e["text"]}')
            lines.append(f'  → **todo [{agent}]{pp}:** {body}')
        lines.append('')

    if details.get('plan_append'):
        lines.append(f'### Plan appends ({len(details["plan_append"])})')
        for e in details['plan_append']:
            section = e.get('section_heading', '?')
            lines.append(f'- [ ] `{e["timestamp"]}` :: {e["text"]}')
            lines.append(f'  → **append to:** `{section}`')
            if e.get('content'):
                lines.append(f'  > {e["content"][:120]}')
        lines.append('')

    if details.get('review_flag'):
        lines.append(f'### Needs review ({len(details["review_flag"])})')
        for e in details['review_flag']:
            agent = e.get('review_agent', 'claude')
            body = e.get('review_body') or e['text']
            lines.append(f'- [ ] `{e["timestamp"]}` :: {e["text"]}')
            lines.append(f'  → **review [{agent}]:** {body}')
        lines.append('')

    if details.get('unmatched'):
        lines.append(f'### Unmatched — LLM did not classify ({len(details["unmatched"])})')
        for e in details['unmatched']:
            lines.append(f'- [ ] `{e["timestamp"]}` :: {e["text"]}')
        lines.append('')

    return '\n'.join(lines)
