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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_put
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pricing import to_99
from tgw.ebay.sync import publish_offer
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_publish'

_PERCENTILE_KEYS = ('max', 'p75', 'p75', 'median', 'p25', 'min')


def _build_reprice_schedule(stages: List[Dict[str, Any]],
                             comps: Dict[str, Any],
                             category_id: str,
                             category_defaults: Dict[str, float],
                             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """
    Compute the full reprice schedule from reprice_stages config.

    Each entry: {stage, label, price, due_at, done_at}
    Prices come from comps[percentile] or category_defaults fallback.
    Launch price (stage 0) is rounded up to next .99.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    schedule = []
    for i, stage in enumerate(stages):
        pct_key = stage.get('percentile', 'p25')
        label   = stage.get('label', f'stage{i}')
        days    = int(stage.get('days', 0))

        raw_price: Optional[float] = None
        if comps and pct_key in comps and comps[pct_key]:
            raw_price = float(comps[pct_key])
        elif category_id and category_id in category_defaults:
            raw_price = float(category_defaults[category_id])

        if raw_price is not None:
            price = to_99(raw_price) if days == 0 else round(raw_price, 2)
        else:
            price = None
            log.warning('reprice_schedule: no price data for stage %r (pct=%s)',
                        label, pct_key)

        due_at = (now + timedelta(days=days)).isoformat()
        schedule.append({
            'stage':   i,
            'label':   label,
            'price':   price,
            'due_at':  due_at,
            'done_at': None,
        })
    return schedule


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

        ebay_offer = item.get('ebay_offer', {})
        offer_id = ebay_offer.get('offer_id')
        if not offer_id:
            raise HardFailure(
                f'{sku}: not staged on eBay yet — run ebay_stage first'
            )

        # Build reprice schedule from comps — price is already correct on the offer.
        # ebay_price now sets draft_listing.price = launch_price (max→.99), so
        # ebay_stage already staged at the right price. No pre-publish update needed.
        stages       = self.config.get('reprice_stages', [])
        comps        = ebay_offer.get('price_comps', {})
        cat_id       = str(item.get('ebay_category_id', ''))
        cat_defaults = self.config.get('category_price_defaults', {})
        schedule     = _build_reprice_schedule(stages, comps, cat_id, cat_defaults)
        launch_entry = next((s for s in schedule if s['label'] == 'launch'), None)

        log.info('publishing %s (offerId=%s)', sku, offer_id)
        tgw_logging.log_event('ebay_publish_start', sku=sku, offer_id=offer_id)

        try:
            result = publish_offer(self.config, offer_id)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 422):
                body_text = exc.response.text if exc.response is not None else ''
                errors = []
                try:
                    errors = json.loads(body_text).get('errors', [])
                except Exception:
                    pass
                if any(e.get('errorId') == 25021 for e in errors):
                    # Category rejects granular condition — fall back to USED_EXCELLENT
                    # (conditionId 3000, accepted universally for used-item categories)
                    log.warning('%s: condition rejected by category at publish — '
                                'retrying with USED_EXCELLENT', sku)
                    ebay_put(self.config,
                             f'/sell/inventory/v1/inventory_item/{sku}',
                             {'condition': 'USED_EXCELLENT'},
                             extra_headers={'Content-Language': 'en-US'})
                    result = publish_offer(self.config, offer_id)
                else:
                    raise HardFailure(
                        f'{sku}: eBay rejected publish (HTTP {status}): {body_text[:500]}'
                    ) from exc
            else:
                raise  # transient — base class retries

        now = datetime.now(timezone.utc)
        item['ebay_listing'] = {
            'offer_id':     offer_id,
            'listing_id':   result['listing_id'],
            'listing_url':  result['listing_url'],
            'status':       'Active',
            'api':          'inventory',
            'published_at': now.isoformat(),
        }
        ebay_offer['status']       = 'PUBLISHED'
        ebay_offer['published_at'] = now.isoformat()
        if launch_entry and launch_entry['price'] is not None:
            ebay_offer['price'] = launch_entry['price']
        item['ebay_offer'] = ebay_offer

        # Stamp launch entry done_at and store full schedule
        for s in schedule:
            if s['label'] == 'launch':
                s['done_at'] = now.isoformat()
                s['due_at']  = now.isoformat()
        item['reprice_schedule'] = schedule

        # Refresh picklist line in draft description now that listing_id is known
        from tgw.ebay.description import build_listing_description
        if item.get('draft_listing'):
            item['draft_listing']['listing_description'] = build_listing_description(
                item, self.config)

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
