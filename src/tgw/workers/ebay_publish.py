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
from tgw.ebay.sync import publish_offer
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

        offer_id = item.get('ebay_offer', {}).get('offer_id')
        if not offer_id:
            raise HardFailure(
                f'{sku}: not staged on eBay yet — run ebay_stage first '
                '(or drop a symlink to re-process)'
            )

        log.info('publishing %s (offerId=%s)', sku, offer_id)
        tgw_logging.log_event('ebay_publish_start', sku=sku, offer_id=offer_id)

        try:
            result = publish_offer(self.config, offer_id)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                body = exc.response.text[:500] if exc.response is not None else ''
                raise HardFailure(
                    f'{sku}: eBay rejected publish (HTTP {status}): {body}'
                ) from exc
            raise  # transient — base class retries

        item['ebay_listing'] = {
            'offer_id':     offer_id,
            'listing_id':   result['listing_id'],
            'listing_url':  result['listing_url'],
            'status':       'PUBLISHED',
            'published_at': datetime.now(timezone.utc).isoformat(),
        }
        # Update ebay_offer status to match
        item.setdefault('ebay_offer', {})['status'] = 'PUBLISHED'

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('published %s → %s', sku, result['listing_url'])
        tgw_logging.log_event('ebay_listing_published', sku=sku,
                              listing_id=result['listing_id'],
                              offer_id=offer_id,
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
