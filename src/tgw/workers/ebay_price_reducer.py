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
from tgw.apis.fence import ebay_write as fence_ebay_write
from tgw.apis.fence import patch_item as fence_patch_item
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.draft_sync import baseline_fields
from tgw.ebay.sync import _build_offer_bodies
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
        # Skip repricing while item is in an active markdown promotion (R2 risk in PP-PROMO-001)
        from tgw.promo import has_active_promo
        if has_active_promo(item):
            log.debug('ebay_price_reducer: %s in active promo — skipping reprice',
                      jf.parent.name)
            stats['skipped'] += 1
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
            fence_patch_item(self.config, sku, {'reprice_schedule': schedule})
            stats['skipped'] += 1
            log.info('ebay_price_reducer: %s stage %d (%s) $%.2f >= current $%.2f'
                     ' — stamped done without applying',
                     sku, entry['stage'], entry['label'], new_price, old_price_f)
            tgw_logging.log_event('ebay_price_reducer_stage_satisfied', sku=sku,
                                  stage=entry['stage'], label=entry['label'],
                                  price=new_price, current_price=old_price_f)
            return

        # Cliff guard (session 42): schedules minted from asking-price "comps"
        # produced fire-sale floors (one was literally $0.00, due in 3 days).
        # A stage may not cut more than half off its PREDECESSOR stage's price
        # (measured against the schedule's own shape, so a legitimate multi-
        # stage catch-up after worker downtime isn't refused), nor go below a
        # hard floor. A refused stage is stamped 'refused_at' so it never
        # retries silently — a broken schedule needs a human, not a retry.
        _HARD_FLOOR = 2.99
        _prior = next((float(s.get('price') or 0) for s in reversed(schedule)
                       if s.get('stage', 0) < entry.get('stage', 0)
                       and s.get('price') is not None), old_price_f)
        if new_price < max(_prior * 0.5, _HARD_FLOOR):
            entry['refused_at'] = now.isoformat()
            entry['done_at'] = now.isoformat()
            item['reprice_schedule'] = schedule
            fence_patch_item(self.config, sku, {'reprice_schedule': schedule})
            log.error('ebay_price_reducer: %s stage %d (%s) REFUSED — $%.2f is a '
                      'cliff-drop from current $%.2f (>50%% or below $%.2f floor); '
                      'schedule needs operator review',
                      sku, entry['stage'], entry['label'], new_price, old_price_f,
                      _HARD_FLOOR)
            tgw_logging.log_event('ebay_price_reducer_stage_refused', sku=sku,
                                  stage=entry['stage'], label=entry['label'],
                                  price=new_price, current_price=old_price_f)
            stats['errors'] += 1
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
                     offer_body)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            log.error('ebay_price_reducer: eBay rejected price update for %s (HTTP %s): %s',
                      sku, status,
                      exc.response.text[:200] if exc.response is not None else '')
            stats['errors'] += 1
            return

        _stamp_due_stages()
        price_entry = {
            'ts':             now.isoformat(),
            'price':          new_price,
            'previous_price': old_price,
            'stage':          entry['stage'],
            'label':          entry['label'],
            'source':         'ebay_price_reducer',
        }
        item.setdefault('price_history', []).append(price_entry)
        item['reprice_schedule'] = schedule

        # Session 41 fix: draft_listing.price was never persisted here — only
        # mutated in memory (see draft['price'] = new_price above) and then
        # silently dropped, every run, success or failure. ebay_stage.py reads
        # draft_listing.price as its FIRST price source, so the next time
        # ebay_stage ran for any reason it would push the stale pre-reduction
        # price back live, silently reverting the markdown eBay had already
        # accepted (confirmed live on tgw202605051933258: draft_listing.price
        # stuck at the original $82.99 while ebay_offer/price_history correctly
        # tracked the reduction schedule down to $32.92 — the drift was
        # invisible until an ebay_stage re-run pushed $82.99 back live).
        #
        # patch_item persists this — the more critical, authoritative write —
        # before the ebay_write() deep-merge below, so a failure in the latter
        # (as happened here, a transient KeyError('api_key') crash) can no
        # longer discard the bookkeeping for a price change eBay already
        # accepted live.
        # Draft lifecycle (broker B1a): this write makes draft == offer by
        # construction (the reduced price just went live AND into the draft),
        # so it MAINTAINS the baseline rather than breaking it. Without the
        # explicit state, the fence PATCH hook would flip the item to
        # 'editing' every 6h and the fleet baseline would erode within days.
        # An in-flight operator edit (state 'editing') is preserved as-is.
        _state = ({'draft_listing_state': 'editing'}
                  if item.get('draft_listing_state') == 'editing'
                  else baseline_fields())
        fence_patch_item(self.config, sku, {
            'reprice_schedule': schedule,
            'price_history':    item['price_history'],
            'draft_listing':    draft,
            **_state,
        })
        try:
            fence_ebay_write(self.config, sku, ebay_offer={'price': new_price})
        except Exception:
            log.exception(
                'ebay_price_reducer: ebay_offer.price merge failed for %s after '
                'the live eBay PUT and local bookkeeping already succeeded — '
                'ebay_offer.price may lag draft_listing.price until the next sync',
                sku,
            )
        stats['reduced'] += 1

    # _on_terminal_failure: no override needed — worker_base.QueueWorker's
    # default detects _reschedule() (no-arg) and calls it automatically on
    # dead_letter (audit#1143 #1244).

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
