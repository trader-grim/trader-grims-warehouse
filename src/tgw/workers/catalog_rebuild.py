"""
tgw.workers.catalog_rebuild — Full catalog rebuild worker.

Claims a catalog_rebuild job and rebuilds all catalog artifacts:
JSON catalog, SQLite catalog, and location symlink tree.

Jobs are enqueued with a 30s not_before and dedupe_key='catalog_rebuild:pending'
so rapid successive writes coalesce into a single rebuild.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import tgw.logging as tgw_logging
from tgw.catalog import build_all_catalogs
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'catalog_rebuild'


class CatalogRebuildWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        reason  = payload.get('reason', 'unknown')
        log.info('catalog rebuild triggered by: %s', reason)
        tgw_logging.log_event('catalog_rebuild_start', reason=reason)

        result = build_all_catalogs(self.config)

        if not result.get('ok', False):
            raise RuntimeError(f'catalog rebuild failed: {result}')

        log.info('catalog rebuild complete')
        tgw_logging.log_event('catalog_rebuild_complete', result=result)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-catalog-rebuild-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = CatalogRebuildWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
