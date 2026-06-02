"""
tgw.workers.ai_identify — Vision-model item identification worker.

Sends the primary photo to qwen2.5vl:7b, asks for a structured JSON
response with title, category, and description, then writes the result
back into the item JSON and enqueues catalog-rebuild + thumbnail-gen.

One photo only (primary image) to keep the prompt lean on CPU-only hardware.
Results are written only if the item still has an empty title — safe to
re-run; will not overwrite a title that has already been set by a human.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import psycopg2.errors

from tgw.apis.ollama import extract_json, is_available
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME   = 'ai_identify'
VISION_MODEL = 'qwen2.5vl:7b'

_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.JPG', '.JPEG', '.PNG'}

_SYSTEM_PROMPT = """\
You are an eBay listing assistant. You will be shown a photo of an item for sale.
Respond with valid JSON only — no prose, no markdown fences.
"""

_USER_PROMPT = """\
Look at this item photo and provide:
- A concise, descriptive eBay-style title (under 80 characters)
- The most likely eBay category name (plain English, e.g. "Board Games", "Action Figures")
- A 1-2 sentence description of what the item appears to be
- Your best guess at condition: "New", "Like New", "Very Good", "Good", "Acceptable"

Respond with JSON:
{
  "title": "...",
  "category": "...",
  "description": "...",
  "condition": "..."
}
"""


def _primary_image(sku_dir: Path) -> Optional[Path]:
    candidates = sorted(
        p for p in sku_dir.iterdir()
        if p.is_file() and p.suffix in _IMAGE_SUFFIXES
    )
    return candidates[0] if candidates else None


class AIIdentifyWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        if not sku:
            raise HardFailure('ai_identify job missing sku in payload')

        sku_dir   = self.config['itemdata_root'] / sku
        json_path = sku_dir / f'{sku}.json'

        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        # Skip if a human has already set a real title
        existing_title = str(item.get('title', '')).strip()
        if existing_title and existing_title != sku:
            log.info('skipping ai_identify for %s — title already set: %r',
                     sku, existing_title)
            tgw_logging.log_event('ai_identify_skipped', sku=sku,
                                  reason='title already set')
            return

        img_path = _primary_image(sku_dir)
        if img_path is None:
            raise HardFailure(f'no images found for {sku}')

        if not is_available(VISION_MODEL):
            raise RuntimeError(f'Ollama unavailable or model {VISION_MODEL!r} not found')

        img_b64 = base64.b64encode(img_path.read_bytes()).decode()

        log.info('calling %s for %s (image: %s, %d KB)',
                 VISION_MODEL, sku, img_path.name, img_path.stat().st_size // 1024)
        tgw_logging.log_event('ai_identify_call', sku=sku, model=VISION_MODEL,
                              image=img_path.name)

        import requests
        resp = requests.post(
            'http://localhost:11434/api/generate',
            json={
                'model':  VISION_MODEL,
                'prompt': _USER_PROMPT,
                'system': _SYSTEM_PROMPT,
                'images': [img_b64],
                'stream': False,
            },
            timeout=600,
        )
        resp.raise_for_status()
        raw = resp.json()['response']

        try:
            result = extract_json(raw)
        except Exception as exc:
            raise HardFailure(
                f'ai_identify: model returned non-JSON for {sku}: {raw[:200]}'
            ) from exc

        title       = str(result.get('title', '')).strip()
        category    = str(result.get('category', '')).strip()
        description = str(result.get('description', '')).strip()
        condition   = str(result.get('condition', '')).strip()

        if not title:
            raise HardFailure(f'ai_identify: empty title in model response for {sku}')

        # Write results back — only fields that are still empty
        item['title']       = title
        if not item.get('category'):
            item['category'] = category
        if not item.get('description'):
            item['description'] = description
        if not item.get('condition'):
            item['condition'] = condition
        item['ai_identified'] = True

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('ai_identify complete for %s: %r', sku, title)
        tgw_logging.log_event('ai_identify_complete', sku=sku, title=title,
                              category=category, condition=condition)

        # Enqueue downstream rebuild
        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ai_identify:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ai-identify-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = AIIdentifyWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
