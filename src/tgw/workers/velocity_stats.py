"""
tgw.workers.velocity_stats — Nightly sold velocity analytics aggregator (PP-PRICE-004).

Scans ItemData, groups sold items by eBay category, computes sell-through rate
by reprice stage, days-to-sale, and sale price distributions.
Output: catalog_root/velocity-stats.json

Self-scheduling: runs nightly (24h interval).

Queue name: velocity_stats
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue import state_machine
from tgw.queue.worker_base import QueueWorker
from tgw.velocity import aggregate_velocity, save_velocity_stats

log = logging.getLogger(__name__)

QUEUE_NAME     = 'velocity_stats'
RUN_INTERVAL_S = 24 * 3600


class VelocityStatsWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('velocity_stats worker started: owner=%s', self.owner)

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('velocity_stats: enqueued startup job')
        except Exception as exc:
            log.warning('velocity_stats: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        log.info('velocity_stats: aggregating sold velocity data')
        tgw_logging.log_event('velocity_stats_start')

        itemdata_root: Path = self.config['itemdata_root']
        catalog_root:  Path = self.config['catalog_root']

        stats = aggregate_velocity(itemdata_root)
        save_velocity_stats(catalog_root, stats,
                            pretty=self.config.get('pretty', True))

        cat_count  = len(stats.get('categories', {}))
        item_count = stats.get('item_count', 0)
        log.info('velocity_stats: complete — %d categories from %d items',
                 cat_count, item_count)
        tgw_logging.log_event('velocity_stats_complete',
                              categories=cat_count, item_count=item_count)
        self._reschedule()

    def _reschedule(self) -> None:
        next_run = time.time() + RUN_INTERVAL_S
        jid = state_machine.enqueue_job(
            queue_name=QUEUE_NAME,
            payload={'reason': 'scheduled'},
            not_before=next_run,
            max_attempts=3,
        )
        log.info('velocity_stats: next run in %dh (job %s)',
                 RUN_INTERVAL_S // 3600, jid)
        tgw_logging.log_event('velocity_stats_rescheduled',
                              next_run_in_hours=RUN_INTERVAL_S // 3600)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-velocity-stats-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = VelocityStatsWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
