"""
tgw.workers.echo — Reference echo worker.

The no-op reference implementation. Proves the full claim → run → succeed
loop with zero business risk. Template for every subsequent worker.

Usage:
    python -m tgw.workers.echo
    tgw-echo-worker  (console script, once registered in pyproject.toml)
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict

from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)


class EchoWorker(QueueWorker):
    """Logs the job payload and marks succeeded. Does nothing else."""

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        log.info('echo: %s', json.dumps(payload, ensure_ascii=False))


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-echo-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--queue',  default='echo')
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = EchoWorker(queue_name=args.queue, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
