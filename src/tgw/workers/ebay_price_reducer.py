"""
tgw.workers.ebay_price_reducer — Scheduled markdown price reducer.

Works through a per-item reprice_schedule written at publish time:
  stage 0 / launch  — published at 110% of max price, rounded to .99
  stage 1 / retail  — lowered to p75 after N days
  stage 2 / move    — lowered to p25 after N more days

Distinct from the future repricer (market-aware, dynamic price adjustment).
This worker executes a pre-computed schedule only — no market queries.

Periods and percentiles are configurable via reprice_stages in config.
Items without a reprice_schedule are skipped (manual-price listings).
Items can be excluded by setting reprice_skip: true in the item JSON.

Safety rules (docs/invariants.md C4/C5/C6):
  - never raises a price — a stage at or above the current price is stamped
    done without an eBay call
  - offer PUT is full-replace, so the complete offer body is rebuilt via
    _build_offer_bodies with the new price (a partial body would strip
    policies/category/description from the live offer)
  - every applied reduction appends a price_history event to the item JSON
  - the item JSON is written only after eBay accepts the update

Self-scheduling: runs every few hours. Enqueues a startup job if idle.

Queue name: ebay_price_reducer
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import requests

import tgw.logging as tgw_logging
from tgw.apis.ebay.client import ebay_put
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.ebay.sync import _build_offer_bodies
from tgw.items import atomic_write_json
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME       = 'ebay_price_reducer'
RUN_INTERVAL_S   = 6 * 3600


class EbayPriceReducerWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_price_reducer worker started: owner=%s', self.owner)

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('ebay_price_reducer: enqueued startup job')
        except Exception as exc:
            log.warning('ebay_price_reducer: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        log.info('ebay_price_reducer: scanning for due price reductions')
        tgw_logging.log_event('ebay_price_reducer_start')

        now = datetime.now(timezone.utc)
        itemdata_root: Path = self.config['itemdata_root']

        stats = {'scanned': 0, 'reduced': 0, 'skipped': 0, 'errors': 0}

        for child in sorted(itemdata_root.iterdir()):
            jf = child / f'{child.name}.json'
            if not jf.exists():
                continue
            stats['scanned'] += 1
            try:
                self._reduce_item(jf, now, stats)
            except Exception:
                log.exception('ebay_price_reducer: unhandled error on %s', child.name)
                stats['errors'] += 1

        log.info('ebay_price_reducer complete: %s', stats)
        tgw_logging.log_event('ebay_price_reducer_complete', **stats)
        self._reschedule()

    def _reduce_item(self, jf: Path, now: datetime,
                     stats: Dict[str, int]) -> None:
        item = json.loads(jf.read_text(encoding='utf-8'))

        if item.get('reprice_skip'):
            return
        schedule: List[Dict[str, Any]] = item.get('reprice_schedule', [])
        if not schedule:
            return

        listing = item.get('ebay_listing', {})
        offer_id = item.get('ebay_offer', {}).get('offer_id')
        if not offer_id or listing.get('status') not in ('Active', 'PUBLISHED'):
            return

        pending = [
            s for s in schedule
            if s.get('done_at') is None
            and s.get('price') is not None
            and s.get('due_at') is not None
            and datetime.fromisoformat(s['due_at']) <= now
        ]
        if not pending:
            return

        entry = max(pending, key=lambda s: s['stage'])
        new_price = entry['price']
        sku = item.get('sku', jf.parent.name)

        def _stamp_due_stages() -> None:
            for s in schedule:
                if s.get('done_at') is None and s.get('due_at') is not None:
                    if datetime.fromisoformat(s['due_at']) <= now:
                        s['done_at'] = now.isoformat()

        old_price = item.get('ebay_offer', {}).get('price')
        try:
            old_price_f = float(old_price) if old_price is not None else None
        except (TypeError, ValueError):
            old_price_f = None

        # Markdown only ever moves a price down. If the current price is already
        # at or below the scheduled stage (e.g. operator cut it manually), the
        # stage is satisfied — stamp it done without touching eBay.
        if old_price_f is not None and float(new_price) >= old_price_f:
            _stamp_due_stages()
            item['reprice_schedule'] = schedule
            atomic_write_json(jf, item, pretty=self.config.get('pretty', True))
            stats['skipped'] += 1
            log.info('ebay_price_reducer: %s stage %d (%s) $%.2f >= current $%.2f'
                     ' — stamped done without applying',
                     sku, entry['stage'], entry['label'], new_price, old_price_f)
            tgw_logging.log_event('ebay_price_reducer_stage_satisfied', sku=sku,
                                  stage=entry['stage'], label=entry['label'],
                                  price=new_price, current_price=old_price_f)
            return

        draft = item.get('draft_listing')
        if not draft:
            log.error('ebay_price_reducer: %s has a reprice_schedule but no '
                      'draft_listing — cannot rebuild the full offer body', sku)
            stats['errors'] += 1
            return

        log.info('ebay_price_reducer: %s → stage %d (%s) $%.2f',
                 sku, entry['stage'], entry['label'], new_price)
        tgw_logging.log_event('ebay_price_reducer_apply', sku=sku,
                              stage=entry['stage'], label=entry['label'],
                              price=new_price)

        # Offer PUT is full-replace: an incomplete body strips live listing
        # fields. Inject the new price and rebuild the complete offer body.
        # (Disk is only written after eBay accepts, so a failure discards
        # these in-memory mutations.)
        draft['price'] = new_price
        item['ebay_offer']['price'] = new_price
        try:
            _, offer_body = _build_offer_bodies(self.config, sku, item)
        except ValueError as exc:
            log.error('ebay_price_reducer: cannot build offer body for %s: %s',
                      sku, exc)
            stats['errors'] += 1
            return

        try:
            ebay_put(self.config,
                     f'/sell/inventory/v1/offer/{offer_id}',
                     offer_body,
                     extra_headers={'Content-Language': 'en-US'})
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            log.error('ebay_price_reducer: eBay rejected price update for %s (HTTP %s): %s',
                      sku, status,
                      exc.response.text[:200] if exc.response is not None else '')
            stats['errors'] += 1
            return

        _stamp_due_stages()
        item.setdefault('price_history', []).append({
            'ts':             now.isoformat(),
            'price':          new_price,
            'previous_price': old_price,
            'stage':          entry['stage'],
            'label':          entry['label'],
            'source':         'ebay_price_reducer',
        })
        item['reprice_schedule'] = schedule
        atomic_write_json(jf, item, pretty=self.config.get('pretty', True))
        stats['reduced'] += 1

    def _reschedule(self) -> None:
        next_run = time.time() + RUN_INTERVAL_S
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'reason': 'scheduled'},
            not_before=next_run,
            max_attempts=3,
        )
        log.info('ebay_price_reducer: next run in %dh (job %s)',
                 RUN_INTERVAL_S // 3600, jid)
        tgw_logging.log_event('ebay_price_reducer_rescheduled',
                              next_run_in_hours=RUN_INTERVAL_S // 3600)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-price-reducer-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayPriceReducerWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
