"""
tgw.workers.ebay_sync — sync eBay listing status back to ItemData.

Self-scheduling: on startup enqueues a sync job if the queue is idle, then
reschedules every SYNC_INTERVAL_S after each run.

Each run fetches all offers from the eBay Inventory API and updates the
ebay_listing.status field in any matching local item JSON.  If any items
changed, a coalesced catalog_rebuild job is enqueued.

Queue name: ebay_sync
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List

import psycopg2.errors
import requests

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import fetch_all_offers
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME       = 'ebay_sync'
SYNC_INTERVAL_S  = 6 * 3600   # check eBay every 6 hours


class EbaySyncWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_sync worker started: owner=%s', self.owner)

        # Enqueue a startup sync job only if the queue is completely idle
        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('ebay_sync: enqueued startup sync job')
        except Exception as exc:
            log.warning('ebay_sync: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_sync worker stopped')

    def handle(self, job: Dict[str, Any]) -> None:
        log.info('ebay_sync: fetching all eBay offers')
        tgw_logging.log_event('ebay_sync_start')

        try:
            offers = fetch_all_offers(self.config)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            log.warning('ebay_sync: eBay unreachable (%s) — will retry next cycle', exc)
            tgw_logging.log_event('ebay_sync_offline', reason=type(exc).__name__)
            self._reschedule()
            return

        log.info('ebay_sync: received %d offer(s) from eBay', len(offers))
        updated = 0

        for offer in offers:
            sku = offer.get('sku', '')
            if not sku:
                continue
            try:
                updated += self._sync_one(offer, sku)
            except Exception:
                log.exception('ebay_sync: error syncing %s', sku)

        log.info('ebay_sync: updated %d item(s)', updated)
        tgw_logging.log_event('ebay_sync_complete',
                              offers_fetched=len(offers), items_updated=updated)

        if updated:
            try:
                state_machine.enqueue_job(
                    queue_name='catalog_rebuild',
                    payload={'reason': 'ebay_sync'},
                    dedupe_key='catalog_rebuild:pending',
                    not_before=time.time() + 30,
                    max_attempts=3,
                )
            except psycopg2.errors.UniqueViolation:
                pass

        self._reschedule()

    def _sync_one(self, offer: Dict[str, Any], sku: str) -> int:
        """Update local item JSON from one eBay offer. Returns 1 if item was changed."""
        json_path = self.config['itemdata_root'] / sku / f'{sku}.json'
        if not json_path.exists():
            return 0

        item = json.loads(json_path.read_text(encoding='utf-8'))
        ebay_listing = item.get('ebay_listing')
        if not ebay_listing:
            return 0  # not published by us — skip

        ebay_status  = offer.get('status', '')
        listing_info = offer.get('listing', {})
        listing_id   = listing_info.get('listingId') or ebay_listing.get('listing_id', '')

        changed = False
        if ebay_listing.get('status') != ebay_status and ebay_status:
            ebay_listing['status'] = ebay_status
            changed = True
        if listing_id and ebay_listing.get('listing_id') != listing_id:
            ebay_listing['listing_id']  = listing_id
            ebay_listing['listing_url'] = f'https://www.ebay.com/itm/{listing_id}'
            changed = True

        if not changed:
            return 0

        item['ebay_listing'] = ebay_listing
        atomic_write_json(json_path, item, pretty=self.config.get('pretty', True))
        log.info('ebay_sync: %s → status=%s', sku, ebay_status)
        tgw_logging.log_event('ebay_listing_status_updated', sku=sku,
                              status=ebay_status, listing_id=listing_id)
        return 1

    def _reschedule(self) -> None:
        next_run = time.time() + SYNC_INTERVAL_S
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'reason': 'scheduled'},
            not_before=next_run,
            max_attempts=3,
        )
        log.info('ebay_sync: next sync in %dh (job %s)',
                 SYNC_INTERVAL_S // 3600, jid)
        tgw_logging.log_event('ebay_sync_rescheduled',
                              next_run_in_hours=SYNC_INTERVAL_S // 3600,
                              next_job_id=jid)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-sync-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbaySyncWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
