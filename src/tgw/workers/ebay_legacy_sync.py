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

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.pull import build_listing_index, sync_active_listings, sync_sold_orders
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME      = 'ebay_legacy_sync'
SYNC_INTERVAL_S = 24 * 3600


def _sold_state_path(cfg: Dict[str, Any]) -> Path:
    return Path(cfg['raw'].get('runtime_root', '/opt/TGW/runtime')) / 'state' / 'ebay-sold-sync-state.json'


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

        synced_at     = datetime.now(timezone.utc).isoformat()
        itemdata_root = self.config['itemdata_root']

        # --- Active listings ---
        try:
            active_stats = sync_active_listings(self.config, itemdata_root, synced_at)
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            log.warning('ebay_legacy_sync: eBay unreachable: %s', exc)
            tgw_logging.log_event('ebay_legacy_sync_offline', reason=str(exc))
            self._reschedule()
            return

        log.info('ebay_legacy_sync active: %s', active_stats)
        for o in active_stats.get('orphans', []):
            log.warning('ebay_legacy_sync orphan: ItemID=%s label=%r title=%s',
                        o['listing_id'], o.get('custom_label', ''), o.get('title', '')[:60])

        # --- Sold orders ---
        sold_stats: Dict[str, Any] = {'orders_fetched': 0, 'sold_marked': 0, 'errors': 0}
        try:
            listing_index = build_listing_index(itemdata_root)
            log.info('ebay_legacy_sync: listing index built (%d entries)', len(listing_index))
            sold_stats = sync_sold_orders(
                self.config, listing_index, synced_at, _sold_state_path(self.config))
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as exc:
            log.warning('ebay_legacy_sync: sold sync skipped (eBay unreachable): %s', exc)
        except Exception:
            log.exception('ebay_legacy_sync: sold sync failed')

        combined = {**active_stats, **sold_stats}
        combined.pop('orphans', None)
        log.info('ebay_legacy_sync final: %s', combined)
        tgw_legacy_stats = {k: v for k, v in combined.items() if isinstance(v, (int, float, str))}
        tgw_logging.log_event('ebay_legacy_sync_complete', **tgw_legacy_stats)

        if active_stats.get('updated', 0) or sold_stats.get('sold_marked', 0):
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

    # _on_terminal_failure: no override needed — worker_base.QueueWorker's
    # default detects _reschedule() (no-arg) and calls it automatically on
    # dead_letter (audit#1143 #1244).

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
