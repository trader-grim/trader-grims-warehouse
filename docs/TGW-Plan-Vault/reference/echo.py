"""
tgw.workers.echo — the reference worker.

Purpose: prove the queue plumbing end to end with zero business risk, and
serve as the template every future worker is copied from. It does nothing
but log its payload and succeed.

When a real worker (PM-intake, camera-intake) misbehaves, you debug ITS
handle() logic — never the queue mechanics, because this worker already
proved those work.

Run:
    python -m tgw.workers.echo --config /opt/TGW/config/tgw-api-config.json
"""

from __future__ import annotations

import argparse

from tgw.config import load_config
from tgw.queue.worker_base import QueueWorker
from tgw import logging as tgw_logging

log = tgw_logging.get_logger(__name__)

QUEUE_NAME = "echo"


class EchoWorker(QueueWorker):
    def handle(self, job) -> None:
        payload = job.get("payload") or {}
        msg = payload.get("msg", "<no msg>")
        log.info("echo job %s: %s", job.get("job_id"), msg)
        # A real worker would do work here via tgw-api. Echo just succeeds.


def main() -> None:
    parser = argparse.ArgumentParser(description="TGW echo reference worker")
    parser.add_argument("--config", required=True, help="path to tgw-api-config.json")
    args = parser.parse_args()
    config = load_config(args.config)
    EchoWorker(QUEUE_NAME, config).run()


if __name__ == "__main__":
    main()
