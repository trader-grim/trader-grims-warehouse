"""
tgw.workers.ebay_legacy_sync — Sync active eBay listings and sold orders to ItemData.

Active sync:
  Uses GetMyeBaySelling (Trading API) to fetch every active listing regardless
  of whether it was created via Inventory API, Trading API, or manually.
  Matches by custom label (= SKU).  Writes/updates ebay_listing block.
  Does NOT overwrite an existing Inventory API listing (api=inventory).
  Orphaned listings (no local item) are logged.

Sold sync (PP-SOLD-001):
  Uses GetOrders (Trading API, OrderStatus=Completed) to fetch completed sales.
  Builds a listing_id → item_path index from ItemData, then for each order
  transaction marks the matched item status=sold and writes ebay_sale block.
  Tracks last sync timestamp in SOLD_STATE_FILE; initial run looks back
  SOLD_INITIAL_LOOKBACK_DAYS days in 90-day windows (GetOrders limit).

Self-scheduling: runs daily. Queue name: ebay_legacy_sync
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2.errors
import requests

from tgw.apis.ebay.trading import get_my_ebay_selling, get_orders
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME                 = 'ebay_legacy_sync'
SYNC_INTERVAL_S            = 24 * 3600
SOLD_INITIAL_LOOKBACK_DAYS = 365
SOLD_ORDERS_WINDOW_DAYS    = 90    # GetOrders API limit per call


def _sold_state_path(cfg: Dict[str, Any]) -> Path:
    return Path(cfg['raw'].get('runtime_root', '/opt/TGW/runtime')) / 'state' / 'ebay-sold-sync-state.json'


def _mark_item_sold(json_path: Path, order_id: str, buyer: str,
                    sale_price: Any, quantity: int, sale_date: str,
                    synced_at: str, cfg: Dict[str, Any]) -> bool:
    """
    Mark an item sold in-place.  Idempotent: returns False if already sold.
    Writes status=sold + ebay_sale block and logs the event.
    """
    item = json.loads(json_path.read_text(encoding='utf-8'))
    if item.get('status') == 'sold':
        return False
    sku = json_path.parent.name
    item['status'] = 'sold'
    item.setdefault('ebay_listing', {})['status'] = 'Sold'
    item['ebay_sale'] = {
        'order_id':   order_id,
        'buyer':      buyer,
        'sale_price': sale_price,
        'quantity':   quantity,
        'sale_date':  sale_date,
        'synced_at':  synced_at,
    }
    atomic_write_json(json_path, item, pretty=cfg.get('pretty', True))
    log.info('ebay_legacy_sync: sold %s order=%s price=$%s', sku, order_id, sale_price)
    tgw_logging.log_event('ebay_item_sold', sku=sku,
                          order_id=order_id, sale_price=sale_price)
    return True


def _build_listing_index(itemdata_root: Path) -> Dict[str, Path]:
    """Scan ItemData and return {listing_id: json_path} for all items that have one."""
    index: Dict[str, Path] = {}
    for json_path in itemdata_root.glob('*/*.json'):
        try:
            text = json_path.read_text(encoding='utf-8')
            if '"listing_id"' not in text:
                continue
            item = json.loads(text)
            lid = item.get('ebay_listing', {}).get('listing_id', '')
            if lid:
                index[str(lid)] = json_path
        except Exception:
            pass
    return index


class EbayLegacySyncWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_legacy_sync worker started: owner=%s', self.owner)

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('ebay_legacy_sync: enqueued startup sync job')
        except Exception as exc:
            log.warning('ebay_legacy_sync: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        log.info('ebay_legacy_sync: fetching all active listings via GetMyeBaySelling')
        tgw_logging.log_event('ebay_legacy_sync_start')

        synced_at = datetime.now(timezone.utc).isoformat()
        itemdata_root: Path = self.config['itemdata_root']

        stats = {'fetched': 0, 'matched': 0, 'updated': 0,
                 'skipped_inventory': 0, 'orphaned': 0, 'errors': 0}
        orphans: List[Dict[str, Any]] = []

        try:
            listings = list(get_my_ebay_selling(self.config))
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            log.warning('ebay_legacy_sync: eBay unreachable: %s', exc)
            tgw_logging.log_event('ebay_legacy_sync_offline', reason=str(exc))
            self._reschedule()
            return

        stats['fetched'] = len(listings)
        log.info('ebay_legacy_sync: %d active listings fetched', len(listings))

        for listing in listings:
            try:
                self._sync_one(listing, itemdata_root, synced_at, stats, orphans)
            except Exception:
                log.exception('ebay_legacy_sync: error on listing %s',
                              listing.get('listing_id'))
                stats['errors'] += 1

        log.info('ebay_legacy_sync active: %s', stats)

        if orphans:
            log.warning('ebay_legacy_sync: %d orphaned listings (on eBay, no local item):',
                        len(orphans))
            for o in orphans:
                log.warning('  ItemID=%s label=%r title=%s',
                            o['listing_id'], o['custom_label'], o['title'][:60])

        # --- Sold sync ---
        try:
            listing_index = _build_listing_index(itemdata_root)
            log.info('ebay_legacy_sync: listing index built (%d entries)', len(listing_index))
            self._sync_sold(listing_index, synced_at, stats)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            log.warning('ebay_legacy_sync: sold sync skipped (eBay unreachable): %s', exc)
        except Exception:
            log.exception('ebay_legacy_sync: sold sync failed')

        log.info('ebay_legacy_sync final stats: %s', stats)
        tgw_logging.log_event('ebay_legacy_sync_complete', **stats,
                              orphan_count=len(orphans))

        if orphans:
            log.warning('ebay_legacy_sync: %d orphaned listings (on eBay, no local item):',
                        len(orphans))
            for o in orphans:
                log.warning('  ItemID=%s label=%r title=%s',
                            o['listing_id'], o['custom_label'], o['title'][:60])

        if stats['updated'] or stats.get('sold_marked', 0):
            try:
                state_machine.enqueue_job(
                    queue_name='catalog_rebuild',
                    payload={'reason': 'ebay_legacy_sync'},
                    dedupe_key='catalog_rebuild:pending',
                    not_before=time.time() + 30,
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass

        self._reschedule()

    def _sync_one(self, listing: Dict[str, Any], itemdata_root: Path,
                  synced_at: str, stats: Dict[str, int],
                  orphans: List[Dict[str, Any]]) -> None:
        sku = listing.get('custom_label', '').strip()

        if not sku:
            orphans.append(listing)
            stats['orphaned'] += 1
            return

        json_path = itemdata_root / sku / f'{sku}.json'
        if not json_path.exists():
            log.warning('ebay_legacy_sync: listing %s has custom_label %r '
                        'but no local item — orphan', listing['listing_id'], sku)
            orphans.append(listing)
            stats['orphaned'] += 1
            return

        stats['matched'] += 1
        item = json.loads(json_path.read_text(encoding='utf-8'))
        existing = item.get('ebay_listing', {})

        # Don't overwrite a listing managed by the Inventory API
        if existing.get('api') == 'inventory' and existing.get('listing_id'):
            log.debug('ebay_legacy_sync: %s already has Inventory API listing — skip', sku)
            stats['skipped_inventory'] += 1
            return

        new_listing = {
            'listing_id':   listing['listing_id'],
            'listing_url':  listing['listing_url'],
            'status':       listing['status'],
            'live_price':   listing['live_price'],
            'api':          'trading',
            'synced_at':    synced_at,
        }

        # Preserve publish/offer data if already there
        for k in ('offer_id', 'published_at'):
            if existing.get(k):
                new_listing[k] = existing[k]

        if new_listing == {k: existing.get(k) for k in new_listing}:
            return  # nothing changed

        item['ebay_listing'] = new_listing
        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))
        stats['updated'] += 1
        log.debug('ebay_legacy_sync: synced %s ItemID=%s price=$%s',
                  sku, listing['listing_id'], listing['live_price'])

    def _sync_sold(self, listing_index: Dict[str, Path],
                   synced_at: str, stats: Dict[str, Any]) -> None:
        """Pull completed orders and mark matched items sold."""
        state_path = _sold_state_path(self.config)
        now = datetime.now(timezone.utc)

        if state_path.exists():
            state = json.loads(state_path.read_text())
            scan_from = datetime.fromisoformat(state['last_synced_at']) - timedelta(hours=2)
        else:
            scan_from = now - timedelta(days=SOLD_INITIAL_LOOKBACK_DAYS)
            log.info('ebay_legacy_sync: first sold sync — looking back %d days',
                     SOLD_INITIAL_LOOKBACK_DAYS)

        # GetOrders is limited to 90-day windows; iterate if span is larger
        orders: List[Dict[str, Any]] = []
        window_start = scan_from
        while window_start < now:
            window_end = min(window_start + timedelta(days=SOLD_ORDERS_WINDOW_DAYS), now)
            chunk = list(get_orders(self.config, window_start, window_end))
            log.info('ebay_legacy_sync sold: %s–%s → %d orders',
                     window_start.strftime('%Y-%m-%d'), window_end.strftime('%Y-%m-%d'),
                     len(chunk))
            orders.extend(chunk)
            window_start = window_end

        marked = 0
        for order in orders:
            for tx in order['transactions']:
                listing_id = tx.get('listing_id', '')
                json_path = listing_index.get(listing_id)
                if not json_path or not json_path.exists():
                    continue
                try:
                    did_mark = _mark_item_sold(
                        json_path,
                        order_id=order['order_id'],
                        buyer=order['buyer'],
                        sale_price=tx['sale_price'],
                        quantity=tx['quantity'],
                        sale_date=tx['sale_date'],
                        synced_at=synced_at,
                        cfg=self.config,
                    )
                    if did_mark:
                        marked += 1
                except Exception as exc:
                    log.error('ebay_legacy_sync: sold mark failed listing %s: %s',
                              listing_id, exc)
                    stats['errors'] += 1

        stats['sold_marked'] = marked
        log.info('ebay_legacy_sync: %d items marked sold', marked)
        tgw_logging.log_event('ebay_sold_sync_complete', marked=marked,
                              orders_fetched=len(orders))

        # Save state so next run only scans the new window
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(
            {'last_synced_at': now.isoformat()}, indent=2))

    def _reschedule(self) -> None:
        next_run = time.time() + SYNC_INTERVAL_S
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'reason': 'scheduled'},
            not_before=next_run,
            max_attempts=3,
        )
        log.info('ebay_legacy_sync: next run in %dh (job %s)',
                 SYNC_INTERVAL_S // 3600, jid)
        tgw_logging.log_event('ebay_legacy_sync_rescheduled',
                              next_run_in_hours=SYNC_INTERVAL_S // 3600)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-legacy-sync-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayLegacySyncWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
