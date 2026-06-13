"""
tgw.workers.sync_conflict — Syncthing conflict-file resolution worker
(PP-PORTABLE-CATALOG-001 P3).

Self-scheduling (same pattern as velocity_stats): enqueues its own startup job
when the queue is empty, then reschedules after each run at a configurable
interval (default 6h).

Each cycle calls ``run_scan(cfg)`` which:
  - discards conflict copies that are byte-for-byte identical to their canonical
  - moves divergent/orphan copies to inbox/review/ and creates operator todos

Queue name: sync_conflict
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
from tgw.sync_conflict import run_scan

log = logging.getLogger(__name__)

QUEUE_NAME     = 'sync_conflict'
RUN_INTERVAL_S = 6 * 3600


class SyncConflictWorker(QueueWorker):

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=QUEUE_NAME, owner=self.owner)
        log.info('sync_conflict worker started: owner=%s', self.owner)

        try:
            depths = state_machine.queue_depths()
            if depths.get(QUEUE_NAME, 0) == 0:
                state_machine.enqueue_job(
                    queue_name=QUEUE_NAME,
                    payload={'reason': 'startup'},
                    max_attempts=3,
                )
                log.info('sync_conflict: enqueued startup job')
        except Exception as exc:
            log.warning('sync_conflict: startup enqueue skipped: %s', exc)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=QUEUE_NAME, owner=self.owner)

    def handle(self, job: Dict[str, Any]) -> None:
        log.info('sync_conflict: scanning for conflict files')
        tgw_logging.log_event('sync_conflict_start')

        result = run_scan(self.config)

        log.info(
            'sync_conflict: complete — %d found, %d discarded, %d flagged',
            result['total'], result['discarded'], result['flagged'],
        )
        tgw_logging.log_event(
            'sync_conflict_complete',
            total=result['total'],
            discarded=result['discarded'],
            flagged=result['flagged'],
        )
        self._reschedule()

    def _reschedule(self) -> None:
        interval = int(self.config.get('sync_conflict_interval_s', RUN_INTERVAL_S))
        not_before = time.time() + interval
        try:
            state_machine.enqueue_job(
                queue_name=QUEUE_NAME,
                payload={'reason': 'scheduled'},
                not_before=not_before,
                max_attempts=3,
            )
            log.info('sync_conflict: rescheduled in %dh', interval // 3600)
        except Exception as exc:
            log.warning('sync_conflict: reschedule failed: %s', exc)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-sync-conflict-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    SyncConflictWorker(queue_name=QUEUE_NAME, config=cfg).run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
