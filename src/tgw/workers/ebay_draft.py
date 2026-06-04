"""
tgw.workers.ebay_draft — eBay draft listing worker.

Fetches item specifics (aspects) for the item's eBay category, uses
Qwen2.5 (text) to suggest values based on item data, then writes a
draft_listing block to the item JSON ready for human review before upload.

Enqueued by ai_identify after category resolution. Safe to re-run —
will overwrite draft_listing but never touch title/description/condition.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors
import requests

from tgw.apis.ebay.conditions import best_condition
from tgw.apis.ebay.specifics import get_aspects
from tgw.ebay.description import build_listing_description
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


_OFFLINE_CSV_FIELDS = ['sku', 'title', 'category_id', 'category_name',
                       'condition', 'format', 'quantity', 'price', 'description']


def _is_ebay_offline(exc: Exception) -> bool:
    """True if exc indicates eBay is unreachable (not an auth or client error)."""
    if isinstance(exc, (requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout)):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        return status >= 500
    return False


def _write_offline_csv_row(cfg: Dict[str, Any], sku: str,
                           item: Dict[str, Any]) -> None:
    """Append a row to the offline draft CSV for later manual upload."""
    csv_path: Path = cfg['ebay_draft_csv_path']
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not csv_path.exists()
    with csv_path.open('a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_OFFLINE_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            'sku':           sku,
            'title':         item.get('title', ''),
            'category_id':   item.get('ebay_category_id', ''),
            'category_name': item.get('ebay_category_name', ''),
            'condition':     item.get('condition', ''),
            'format':        'FixedPrice',
            'quantity':      1,
            'price':         '',
            'description':   item.get('description', ''),
        })


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
            av = a['allowed_values']
            if len(av) <= 30:
                vals = ', '.join(av)
            else:
                vals = ', '.join(av[:30]) + f' ... ({len(av)} total)'
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

        title = item.get('title', '')
        if not title or title == sku:
            raise HardFailure(f'no title on {sku} — run ai_identify first')

        category_id   = item.get('ebay_category_id')
        category_name = item.get('ebay_category_name', '')

        # If taxonomy lookup failed during ai_identify, retry it here
        if not category_id:
            log.info('no ebay_category_id for %s — retrying taxonomy lookup', sku)
            try:
                from tgw.apis.ebay.taxonomy import best_category
                category_id, category_name = best_category(
                    self.config, title, item.get('category', ''))
                if category_id:
                    item['ebay_category_id']   = category_id
                    item['ebay_category_name'] = category_name
                    log.info('taxonomy retry succeeded for %s: %s %s',
                             sku, category_id, category_name)
            except Exception as exc:
                log.warning('taxonomy retry failed for %s: %s', sku, exc)

        if not category_id:
            # No category at all — use a broad fallback so eBay prompts the
            # operator to select the correct leaf category when they open the draft
            category_id   = '99'   # eBay "Everything Else" — non-leaf, eBay will prompt
            category_name = 'Everything Else'
            log.warning('%s: no category found — staging with fallback category 99', sku)

        # Fetch aspects — category 99 is a non-leaf catch-all; eBay returns 400
        # for it so skip the call and let the operator set specifics in Seller Hub.
        if category_id == '99':
            aspects: List[Dict[str, Any]] = []
            log.warning('%s: fallback category 99 — skipping aspects (set in Seller Hub)', sku)
        else:
            try:
                aspects = get_aspects(self.config, category_id)
            except Exception as exc:
                if _is_ebay_offline(exc):
                    _write_offline_csv_row(self.config, sku, item)
                    item['offline_draft'] = True
                    atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))
                    log.warning('eBay unreachable for %s (%s) — wrote offline CSV row', sku, exc)
                    tgw_logging.log_event('ebay_draft_offline', sku=sku,
                                          reason=type(exc).__name__)
                    return
                raise
            log.info('fetched %d aspects for category %s', len(aspects), category_id)

        # Use text model to fill aspect values
        prompt  = _build_prompt(item, aspects)
        log.info('asking %s to fill %d aspects for %s', TEXT_MODEL, len(aspects), sku)
        tgw_logging.log_event('ebay_draft_aspects_call', sku=sku,
                              category_id=category_id, aspect_count=len(aspects))

        from tgw.queue.ollama_lock import acquire_ollama_lock
        with acquire_ollama_lock(self.config):
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

        # Backfill required aspects the AI left blank — eBay rejects at staging
        # if any required aspect is missing.
        _UNBRANDED_FALLBACKS = ('Unbranded', 'Does Not Apply', 'N/A')
        for aspect in aspects:
            if not aspect['required'] or aspect['name'] in item_specifics:
                continue
            av = aspect['allowed_values']
            fallback: Optional[str] = None
            for candidate in _UNBRANDED_FALLBACKS:
                if not av or candidate in av:
                    fallback = candidate
                    break
            if fallback:
                item_specifics[aspect['name']] = fallback
                log.info('required aspect %r not filled by AI — defaulting to %r',
                         aspect['name'], fallback)

        # Resolve condition — look up the best allowed conditionId for this category.
        # Never upgrades condition; falls back same-or-worse. Stores both the
        # conditionId (what eBay validates) and the buyer-facing label.
        raw_condition = item.get('condition', '')
        cond_result = None
        if category_id != '99':
            try:
                cond_result = best_condition(self.config, category_id, raw_condition)
            except Exception as exc:
                log.warning('%s: condition lookup failed (%s) — will use enum fallback',
                            sku, exc)
        if cond_result:
            log.info('%s: condition %r → %s (%s)',
                     sku, raw_condition,
                     cond_result['condition_id'], cond_result['condition_label'])
        else:
            log.warning('%s: no valid condition found for %r in category %s — '
                        'needs manual review', sku, raw_condition, category_id)

        # Build draft listing block
        draft: Dict[str, Any] = {
            'title':           title,
            'category_id':     category_id,
            'category_name':   item.get('ebay_category_name', ''),
            'condition':       raw_condition,
            'condition_id':    cond_result['condition_id']    if cond_result else None,
            'condition_label': cond_result['condition_label'] if cond_result else None,
            'condition_enum':  cond_result['condition_enum']  if cond_result else None,
            'format':          'FixedPrice',
            'quantity':        1,
            'price':           None,
            'item_specifics':  item_specifics,
            'description':     item.get('description', ''),
        }

        # Build full eBay listing description: AI text + boilerplate footer + picklist line
        item['draft_listing'] = draft   # temporary — needed by build_listing_description
        draft['listing_description'] = build_listing_description(item, self.config)

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

        try:
            state_machine.enqueue_job(
                queue_name='ebay_price',
                payload={'sku': sku},
                dedupe_key=f'ebay_price:{sku}',
                max_attempts=5,
            )
        except psycopg2.errors.UniqueViolation:
            pass

        try:
            state_machine.enqueue_job(
                queue_name='ebay_upload',
                payload={'sku': sku},
                dedupe_key=f'ebay_upload:{sku}',
                max_attempts=5,
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
