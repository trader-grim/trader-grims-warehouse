"""
tgw.workers.plan_render — Taskboard render worker (PP-PLANDB-001 Phase 2).

Claims a plan_render job and regenerates the operational TGW-Taskboard.md
view from the todo_items table and an approved Plan binding.

Jobs are enqueued with a 30s not_before and dedupe_key='plan_render:pending'
so rapid successive todo mutations coalesce into a single render
(same pattern as catalog_rebuild).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.plan_render import render_taskboard
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'plan_render'


class PlanRenderWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        reason  = payload.get('reason', 'unknown')
        log.info('taskboard render triggered by: %s', reason)
        tgw_logging.log_event('plan_render_start', reason=reason)

        result = render_taskboard(self.config)

        if not result.get('ok', False):
            raise RuntimeError(f'taskboard render failed: {result}')

        log.info('taskboard render complete: %s', result['path'])
        tgw_logging.log_event('plan_render_complete', result=result)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-plan-render-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = PlanRenderWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
