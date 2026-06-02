"""
tgw.workers.thumbnail_gen — Per-SKU thumbnail generation worker.

Generates the thumbnail for a single SKU. Enqueued by bundle_intake
immediately after a new item is written to ItemData.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue.worker_base import HardFailure, QueueWorker
from tgw.thumbnail import build_thumbnail_for_sku
import tgw.logging as tgw_logging

log = logging.getLogger(__name__)

QUEUE_NAME = 'thumbnail_gen'


class ThumbnailGenWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        sku     = payload.get('sku', '')
        if not sku:
            raise HardFailure('thumbnail_gen job missing sku in payload')

        tgw_logging.log_event('thumbnail_gen_start', sku=sku)
        result = build_thumbnail_for_sku(self.config, sku)

        if not result.get('ok', False):
            raise RuntimeError(f'thumbnail_gen failed for {sku}: {result}')

        action = result.get('action', 'unknown')
        log.info('thumbnail_gen %s: %s', sku, action)
        tgw_logging.log_event('thumbnail_gen_complete', sku=sku, action=action)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-thumbnail-gen-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = ThumbnailGenWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
