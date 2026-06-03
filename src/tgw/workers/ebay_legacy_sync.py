"""
tgw.workers.ebay_legacy_sync — Sync all active eBay listings back to ItemData.

Uses GetMyeBaySelling (Trading API) to fetch every active listing regardless
of whether it was created via the Inventory API, Trading API, or manually
in a browser.  Matches listings to local items by custom label (= SKU).

For each matched item:
  - Writes/updates ebay_listing block (listing_id, live_price, status, url, api=trading)
  - Does NOT overwrite an existing Inventory API listing (api=inventory)

For unmatched listings (no custom label or unknown SKU):
  - Logs as orphan — listed on eBay but no local TGW record

Self-scheduling: runs daily. Enqueues a startup job if queue is idle.
Manual trigger: insert an ebay_legacy_sync job into the queue.

Queue name: ebay_legacy_sync
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import psycopg2.errors
import requests

from tgw.apis.ebay.trading import get_my_ebay_selling
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME      = 'ebay_legacy_sync'
SYNC_INTERVAL_S = 24 * 3600   # daily


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

        log.info('ebay_legacy_sync complete: %s', stats)
        tgw_logging.log_event('ebay_legacy_sync_complete', **stats,
                              orphan_count=len(orphans))

        if orphans:
            log.warning('ebay_legacy_sync: %d orphaned listings (on eBay, no local item):',
                        len(orphans))
            for o in orphans:
                log.warning('  ItemID=%s label=%r title=%s',
                            o['listing_id'], o['custom_label'], o['title'][:60])

        if stats['updated']:
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
