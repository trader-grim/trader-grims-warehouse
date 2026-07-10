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
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.repush import _repush_one
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_repush'


class EbayRepushWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_repush job missing sku in payload')

        tgw_logging.log_event('ebay_repush_start', sku=sku)
        result = _repush_one(self.config, sku)

        if result.get('skipped'):
            raise HardFailure(f'{sku}: {result.get("reason")}')
        if not result.get('ok'):
            raise RuntimeError(f'ebay_repush failed for {sku}: {result.get("reason")}')

        # Clear photo_verify so ebay_sync re-checks on next cycle
        json_path = config.sku_json(self.config, sku)
        item = json.loads(json_path.read_text(encoding='utf-8'))
        inv_body = (item.get('ebay_submitted') or {}).get('inventory_item') or {}
        photo_count = len(inv_body.get('product', {}).get('imageUrls', []))

        now_iso = datetime.now(timezone.utc).isoformat()
        fence_ebay_write(self.config, sku, ebay_listing={"repush_at": now_iso, "photo_verify": None},
                          allow_protected=["photo_verify"])

        log.info('ebay_repush: %s repushed successfully (%d photo(s))', sku, photo_count)
        tgw_logging.log_event('ebay_repush_complete', sku=sku, photo_count=photo_count)


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
