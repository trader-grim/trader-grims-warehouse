"""
tgw.workers.agent_run_render — Agent-runs render worker (PP-AGENTTRACE-001
Phase 2).

Claims an agent_run_render job and regenerates the operational TGW-Agent-Runs
view from the agent_runs table and an approved Plan binding.

Jobs are enqueued with a 30s not_before and dedupe_key='agent_run_render:pending'
so rapid successive start_agent_run()/end_agent_run() calls coalesce into a
single render (same pattern as plan_render/catalog_rebuild).

No systemd unit is installed for this worker yet (out of scope for this
packet, PP-AGENTTRACE-001 Phase 2 — see nix-flake-maintainer follow-up):
jobs will queue but not be processed until that follow-up lands.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import tgw.logging as tgw_logging
from tgw.agent_trace_render import render_agent_runs_doc
from tgw.config import DEFAULT_CONFIG
from tgw.queue.worker_base import QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME = 'agent_run_render'


class AgentRunRenderWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        payload = job.get('payload_json') or {}
        reason  = payload.get('reason', 'unknown')
        log.info('agent runs render triggered by: %s', reason)
        tgw_logging.log_event('agent_run_render_start', reason=reason)

        result = render_agent_runs_doc(self.config)

        if not result.get('ok', False):
            raise RuntimeError(f'agent runs render failed: {result}')

        log.info('agent runs render complete: %s', result['path'])
        tgw_logging.log_event('agent_run_render_complete', result=result)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-agent-run-render-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    from tgw.config import load_operational_config
    cfg = load_operational_config(Path(args.config))
    worker = AgentRunRenderWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
