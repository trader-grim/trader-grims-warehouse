"""
tgw.workers.ebay_repush — Re-PUT the eBay inventory item from ebay_submitted.

Enqueued by ebay_sync when a periodic photo integrity check detects that
eBay has silently dropped photos from a live listing.  Restores the full
inventory_item body (including imageUrls) to what was originally submitted.

Queue name: ebay_repush
Payload:    {sku: "<SKU>"}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import tgw.config as config
import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_put
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_repush'


class EbayRepushWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_repush job missing sku in payload')

        json_path = config.sku_json(self.config, sku)
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        inv_body = item.get('ebay_submitted', {}).get('inventory_item')
        if not inv_body:
            raise HardFailure(
                f'{sku}: no ebay_submitted.inventory_item — item must be staged '
                f'after PP-EBAY-SNAPSHOT-001 was deployed'
            )

        image_urls = inv_body.get('product', {}).get('imageUrls', [])
        log.info('ebay_repush: re-PUTting inventory_item for %s (%d photo(s))',
                 sku, len(image_urls))
        tgw_logging.log_event('ebay_repush_start', sku=sku, photo_count=len(image_urls))

        ebay_put(self.config, f'/sell/inventory/v1/inventory_item/{sku}', inv_body)

        # Clear photo_verify so ebay_sync re-checks on next cycle
        now_iso = datetime.now(timezone.utc).isoformat()
        ebay_listing = item.get('ebay_listing', {})
        ebay_listing['repush_at'] = now_iso
        ebay_listing.pop('photo_verify', None)
        item['ebay_listing'] = ebay_listing

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('ebay_repush: %s repushed successfully (%d photo(s))', sku, len(image_urls))
        tgw_logging.log_event('ebay_repush_complete', sku=sku, photo_count=len(image_urls))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-repush-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayRepushWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
