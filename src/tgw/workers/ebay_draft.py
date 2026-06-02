"""
tgw.workers.ebay_draft — eBay draft listing worker.

Fetches item specifics (aspects) for the item's eBay category, uses
Qwen2.5 (text) to suggest values based on item data, then writes a
draft_listing block to the item JSON ready for human review before upload.

Enqueued by ai_identify after category resolution. Safe to re-run —
will overwrite draft_listing but never touch title/description/condition.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors

from tgw.apis.ebay.specifics import get_aspects
from tgw.apis.ollama import chat, extract_json
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME  = 'ebay_draft'
TEXT_MODEL  = 'Qwen2.5:latest'

_SYSTEM = """\
You are an eBay listing assistant. Given item details and a list of eBay item
specifics (aspects), suggest the best value for each aspect.
For SELECTION_ONLY aspects, you MUST choose from the allowed values listed.
For FREE_TEXT aspects, suggest a concise, accurate value.
If an aspect does not apply, use null.
Respond with valid JSON only — an object mapping aspect name to suggested value.
"""


def _build_prompt(item: Dict[str, Any], aspects: List[Dict[str, Any]]) -> str:
    lines = [
        f'Title: {item.get("title", "")}',
        f'Category: {item.get("ebay_category_name", "")}',
        f'Description: {item.get("description", "")}',
        f'Condition: {item.get("condition", "")}',
        '',
        'Aspects to fill:',
    ]
    for a in aspects:
        req = ' (REQUIRED)' if a['required'] else ''
        if a['allowed_values']:
            vals = ', '.join(a['allowed_values'][:10])
            lines.append(f'  {a["name"]}{req}: choose from [{vals}]')
        else:
            lines.append(f'  {a["name"]}{req}: free text')
    lines.append('')
    lines.append('Respond with JSON: {"Brand": "...", "Theme": "...", ...}')
    return '\n'.join(lines)


class EbayDraftWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_draft job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        category_id = item.get('ebay_category_id')
        if not category_id:
            raise HardFailure(f'no ebay_category_id on {sku} — run ai_identify first')

        title = item.get('title', '')
        if not title or title == sku:
            raise HardFailure(f'no title on {sku} — run ai_identify first')

        # Fetch aspects for the category
        aspects = get_aspects(self.config, category_id)
        log.info('fetched %d aspects for category %s', len(aspects), category_id)

        # Use text model to fill aspect values
        prompt  = _build_prompt(item, aspects)
        log.info('asking %s to fill %d aspects for %s', TEXT_MODEL, len(aspects), sku)
        tgw_logging.log_event('ebay_draft_aspects_call', sku=sku,
                              category_id=category_id, aspect_count=len(aspects))

        raw = chat(
            model=TEXT_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            system=_SYSTEM,
        )

        try:
            suggested = extract_json(raw)
        except Exception as exc:
            raise HardFailure(
                f'ebay_draft: model returned non-JSON for {sku}: {raw[:200]}'
            ) from exc

        # Filter nulls and validate SELECTION_ONLY values
        item_specifics: Dict[str, str] = {}
        for aspect in aspects:
            name = aspect['name']
            val  = suggested.get(name)
            if not val:
                continue
            val = str(val).strip()
            if aspect['mode'] == 'SELECTION_ONLY' and aspect['allowed_values']:
                if val not in aspect['allowed_values']:
                    log.warning('invalid value %r for %r — skipping', val, name)
                    continue
            item_specifics[name] = val

        # Build draft listing block
        draft: Dict[str, Any] = {
            'title':          title,
            'category_id':    category_id,
            'category_name':  item.get('ebay_category_name', ''),
            'condition':      item.get('condition', ''),
            'format':         'FixedPrice',
            'quantity':       1,
            'price':          None,   # human to set before upload
            'item_specifics': item_specifics,
            'description':    item.get('description', ''),
        }

        item['draft_listing'] = draft
        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('ebay_draft complete for %s: %d specifics filled', sku, len(item_specifics))
        tgw_logging.log_event('ebay_draft_complete', sku=sku,
                              specifics_filled=len(item_specifics),
                              item_specifics=item_specifics)

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_draft:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-draft-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayDraftWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
