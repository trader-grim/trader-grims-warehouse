"""
tgw.workers.ebay_price — Suggest and auto-fill a price for a draft listing.

Enqueued by ebay_draft after the draft_listing block is written.  Queries
eBay Browse API for active listing comps, computes the 25th-percentile price,
and writes:
  - ebay_offer.price        (the suggested price)
  - ebay_offer.price_source (how it was derived)
  - ebay_offer.price_comps  (count, min, p25, median, max)
  - ebay_offer.priced_at    (ISO timestamp)
  - draft_listing.price     (same value, so ebay_publish can read it)

Skips items that already have ebay_offer.price set (idempotent).
If comps are insufficient (< 3 results) price is left null and flagged.

Queue name: ebay_price
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

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pricing import suggest_price
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_price'


class EbayPriceWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_price job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        draft = item.get('draft_listing')
        if not draft:
            raise HardFailure(f'{sku}: no draft_listing — run ebay_draft first')

        # Idempotent: skip if already priced
        existing = item.get('ebay_offer', {})
        if existing.get('price') is not None:
            log.info('ebay_price: %s already priced at $%.2f — skipping',
                     sku, existing['price'])
            tgw_logging.log_event('ebay_price_skipped', sku=sku,
                                  reason='already_priced',
                                  price=existing['price'])
            return

        title         = draft.get('title') or item.get('title', '')
        category_name = draft.get('category_name') or item.get('ebay_category_name', '')

        if not title or title == sku:
            raise HardFailure(f'{sku}: no title — run ai_identify first')

        log.info('ebay_price: querying comps for %r', title[:60])
        tgw_logging.log_event('ebay_price_start', sku=sku, title=title[:60])

        result = suggest_price(self.config, title, category_name)

        ebay_offer = dict(existing)
        ebay_offer['price_source'] = result['source']
        ebay_offer['price_comps']  = result['comps']
        ebay_offer['priced_at']    = result['queried_at']

        suggested = result['price']
        if suggested is not None:
            ebay_offer['price'] = suggested
            draft['price']      = suggested
            log.info('ebay_price: %s → $%.2f (%d comps, %s)',
                     sku, suggested, result['comps'].get('count', 0), result['source'])
            tgw_logging.log_event('ebay_price_set', sku=sku,
                                  price=suggested,
                                  source=result['source'],
                                  comps=result['comps'])
        else:
            ebay_offer['price'] = None
            log.warning('ebay_price: %s — insufficient comps, price left null', sku)
            tgw_logging.log_event('ebay_price_no_comps', sku=sku, title=title[:60])

        item['ebay_offer']    = ebay_offer
        item['draft_listing'] = draft

        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_price:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-price-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPriceWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
