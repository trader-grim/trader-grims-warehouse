"""
tgw.workers.ebay_stage — Push a priced draft to eBay as an UNPUBLISHED offer.

Creates the eBay inventory item and offer without publishing.  The draft
appears immediately in Seller Hub where the operator can review, edit, and
list it.  This is the stopgap publishing interface until the full revision
system (PP-REVISION-001) is built.

Enqueued automatically by ebay_price when a price is successfully set.
Skipped (idempotent) if ebay_offer.offer_id already exists.

Guards:
  - draft_listing.price or ebay_offer.price must be set
  - ebay_photos must be populated (photos on eBay EPS)
  If photos aren't uploaded yet the job retries — ebay_upload runs concurrently.

Queue name: ebay_stage
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
from tgw.ebay.sync import stage_draft
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_stage'


class EbayStageWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku = payload.get('sku', '')
        if not sku:
            raise HardFailure('ebay_stage job missing sku in payload')

        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            raise HardFailure(f'item JSON not found for {sku}')

        item = json.loads(json_path.read_text(encoding='utf-8'))

        # Guard: item was previously listed via Trading API — must not create a
        # duplicate Inventory API offer until the legacy listing is resolved.
        legacy_item_number = item.get('Item number') or item.get('item_number')
        if legacy_item_number and not item.get('legacy_listing_resolved'):
            log.warning(
                'ebay_stage: %s has legacy eBay Item# %s — skipping to avoid '
                'duplicate listing; resolve via relist workflow first',
                sku, legacy_item_number,
            )
            tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                  reason='legacy_trading_api_listing',
                                  item_number=str(legacy_item_number))
            return

        # Idempotent: already staged
        existing_offer_id = item.get('ebay_offer', {}).get('offer_id')
        if existing_offer_id:
            log.info('ebay_stage: %s already staged (offerId=%s) — skipping',
                     sku, existing_offer_id)
            tgw_logging.log_event('ebay_stage_skipped', sku=sku,
                                  offer_id=existing_offer_id)
            return

        draft = item.get('draft_listing', {})
        if not draft:
            raise HardFailure(f'{sku}: no draft_listing — run ebay_draft first')

        # Price must be set (either by ebay_price or manually)
        price = draft.get('price') or item.get('ebay_offer', {}).get('price')
        if price is None:
            raise HardFailure(
                f'{sku}: no price set — run ebay_price or set draft_listing.price manually'
            )

        # Photos must be uploaded — retryable if ebay_upload hasn't finished yet
        image_urls = draft.get('imageUrls') or [e['url'] for e in item.get('ebay_photos', [])]
        if not image_urls:
            raise RuntimeError(
                f'{sku}: no eBay photo URLs yet — waiting for ebay_upload (will retry)'
            )

        log.info('ebay_stage: staging %s as UNPUBLISHED offer (price=$%s)', sku, price)
        tgw_logging.log_event('ebay_stage_start', sku=sku, price=price)

        try:
            result = stage_draft(self.config, sku, item)
        except ValueError as exc:
            raise HardFailure(str(exc)) from exc
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                body = exc.response.text[:500] if exc.response is not None else ''
                raise HardFailure(
                    f'{sku}: eBay rejected staging (HTTP {status}): {body}'
                ) from exc
            raise  # transient — base class retries

        # Merge into ebay_offer block (preserves price_comps etc from ebay_price)
        ebay_offer = dict(item.get('ebay_offer', {}))
        ebay_offer['offer_id']   = result['offer_id']
        ebay_offer['status']     = 'UNPUBLISHED'
        ebay_offer['staged_at']  = datetime.now(timezone.utc).isoformat()

        item['ebay_offer'] = ebay_offer
        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))

        log.info('ebay_stage: %s staged → offerId=%s (visible in Seller Hub)',
                 sku, result['offer_id'])
        tgw_logging.log_event('ebay_stage_complete', sku=sku,
                              offer_id=result['offer_id'])

        try:
            state_machine.enqueue_job(
                queue_name='catalog_rebuild',
                payload={'reason': f'ebay_stage:{sku}'},
                dedupe_key='catalog_rebuild:pending',
                not_before=time.time() + 30,
                max_attempts=3,
            )
        except psycopg2.errors.UniqueViolation:
            pass


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-stage-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayStageWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
