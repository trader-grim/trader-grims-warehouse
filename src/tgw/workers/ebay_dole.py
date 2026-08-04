"""
tgw.workers.ebay_dole — Rate-limited listing dole-out (PP-EDITOR-001).

Each cycle, publishes a slice of the ready pool (items reviewed and marked
ready via ``tgw ready set``) by enqueuing ebay_publish jobs: 1/``dole_divisor``
of the pool per cycle (default 1/60), at least one when the pool is non-empty.
Published items leave the pool automatically (offer status → PUBLISHED).
``tgw publish <sku>`` remains the List-Now bypass.

Self-scheduling on a ``dole_interval_s`` cycle (default 1h) — same pattern as
velocity_stats: enqueues its own startup job when the queue is empty, then
reschedules after each run.

Queue name: ebay_dole
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.ready import dole_batch_size, ready_pool

log = logging.getLogger(__name__)

QUEUE_NAME = 'ebay_dole'


class EbayDoleWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('ebay_dole worker started: owner=%s', self.owner)

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                    dedupe_key=f'{QUEUE_NAME}:pending',
                    debounce=True,
                )
                log.info('ebay_dole: enqueued startup job')
        except Exception as exc:
            log.warning('ebay_dole: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        from tgw.api import cmd_publish

        pool = ready_pool(self.config)
        divisor = int(self.config.get('dole_divisor', 60))
        n = dole_batch_size(len(pool), divisor)

        if n == 0:
            log.info('ebay_dole: ready pool empty — nothing to publish')
            tgw_logging.log_event('ebay_dole_cycle', pool=0, published=0)
        else:
            batch = [item['sku'] for item in pool[:n]]
            result = cmd_publish(self.config, batch)
            log.info('ebay_dole: pool=%d → enqueued %d publish job(s)%s',
                     len(pool), len(result['enqueued']),
                     f' ({len(result["errors"])} errors)' if result['errors'] else '')
            for line in result['skipped']:
                log.info('ebay_dole: skipped %s', line)
            for line in result['errors']:
                log.warning('ebay_dole: error %s', line)
            tgw_logging.log_event('ebay_dole_cycle', pool=len(pool),
                                  published=len(result['enqueued']),
                                  skipped=len(result['skipped']),
                                  errors=len(result['errors']))

        self._reschedule()

    # _on_terminal_failure: no override needed — worker_base.QueueWorker's
    # default detects _reschedule() (no-arg) and calls it automatically on
    # dead_letter (audit#1143 #1244).

    def _reschedule(self) -> None:
        interval_s = int(self.config.get('dole_interval_s', 3600))
        try:
            jid = state_machine.enqueue_job(
                queue_name=QUEUE_NAME,
                payload={'reason': 'scheduled'},
                not_before=time.time() + interval_s,
                max_attempts=3,
                dedupe_key=f'{QUEUE_NAME}:pending',
                debounce=True,
            )
        except psycopg2.errors.UniqueViolation:
            jid = None
        log.info('ebay_dole: next cycle in %dmin (job %s)', interval_s // 60, jid)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-ebay-dole-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EbayDoleWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
