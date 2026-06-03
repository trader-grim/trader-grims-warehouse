"""
tgw.workers.ebay_publish — publish a priced draft listing to eBay.

Triggered manually after a human reviews draft_listing and sets price.
Guards: draft_listing must exist, price must be non-null, photos must be uploaded.

On success: writes ebay_listing block to item JSON and enqueues catalog_rebuild.

Queue name: ebay_publish
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors
import requests

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import publish_draft
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_publish'


class EbayPublishWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_publish job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        draft = item.get('draft_listing')
        if not draft:
            raise HardFailure(f'{sku}: no draft_listing — run ebay_draft first')

        price = draft.get('price')
        if price is None:
            raise HardFailure(
                f'{sku}: draft_listing.price is null — set a price before publishing'
            )

        if not item.get('ebay_photos') and not draft.get('imageUrls'):
            raise HardFailure(f'{sku}: no eBay photos — run ebay_upload first')

        log.info('publishing %s (price=%s)', sku, price)
        tgw_logging.log_event('ebay_publish_start', sku=sku,
                              category_id=draft.get('category_id'), price=price)

        try:
            result = publish_draft(self.config, sku, item)
        except ValueError as exc:
            raise HardFailure(str(exc)) from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                body = exc.response.text[:500] if exc.response is not None else ''
                raise HardFailure(
                    f'{sku}: eBay rejected listing (HTTP {status}): {body}'
                ) from exc
            raise  # transient (5xx, 401, etc.) — base class will retry

        item['ebay_listing'] = {
            'offer_id':     result['offer_id'],
            'listing_id':   result['listing_id'],
            'listing_url':  result['listing_url'],
            'status':       result['status'],
            'published_at': datetime.now(timezone.utc).isoformat(),
        }

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('published %s → %s', sku, result['listing_url'])
        tgw_logging.log_event('ebay_listing_published', sku=sku,
                              listing_id=result['listing_id'],
                              offer_id=result['offer_id'],
                              listing_url=result['listing_url'])

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_publish:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-publish-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPublishWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
