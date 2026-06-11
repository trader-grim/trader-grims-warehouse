"""
tgw.workers.alt_text — vision alt-text generation worker.

Processes 'alt_text' queue jobs. Each job payload: {"sku": "<SKU>"}.

Calls cmd_alt_text() with the configured provider (default: openrouter /
Gemini 2.5 Flash). Falls back to Ollama if OpenRouter key is unavailable.

Enqueued by:
  - ai_identify worker (automatically, after each identification)
  - tgw alt-text-batch (bulk backfill of existing catalog)
  - tgw enqueue-sku --queue alt_text <sku> (manual)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from tgw.alt_text import cmd_alt_text
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = "alt_text"


class AltTextWorker(QueueWorker):
    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get("payload_json") or {}
        sku = payload.get("sku", "")
        if not sku:
            raise HardFailure("alt_text job missing sku in payload")

        result = cmd_alt_text(self.config, sku=sku)

        if not result.get("ok"):
            if result.get("skipped"):
                log.info("alt_text: %s already processed, skipping", sku)
                return
            raise HardFailure(f"alt_text failed for {sku}: {result.get('error')}")

        log.info(
            "alt_text complete for %s via %s: %r",
            sku,
            result.get("provider"),
            result.get("alt_text", "")[:60],
        )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="tgw-alt-text-worker")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = AltTextWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
