"""
tgw.workers.token_refresh — eBay token refresh worker.

First real state-machine worker. Demonstrates the full pattern:
    claim → check → act → self-reschedule → succeed
                        ↘ retry_wait (transient)
                        ↘ dead_letter + notify (hard failure)

Runs alongside the existing eBay cron until one full expiry+refresh
cycle is observed. Retire the cron only after that gate is cleared.

Queue name: token_refresh
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict

import psycopg2.errors
import requests

import tgw.logging as tgw_logging
from tgw.config import DEFAULT_CONFIG, load_config
from tgw.notify import notify
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure, QueueWorker

log = logging.getLogger(__name__)

QUEUE_NAME        = 'token_refresh'
REFRESH_BUFFER_S  = 1800   # refresh when less than 30 min remaining
RESCHEDULE_INTERVAL_S = 3600   # check every hour


class TokenRefreshWorker(QueueWorker):

    def handle(self, job: Dict[str, Any]) -> None:
        token_path: Path = self.config['ebay_token_path']

        # ── 1. Token file must exist ────────────────────────────────────────
        if not token_path.exists():
            msg = f'token state file missing: {token_path} — run initial OAuth'
            notify('eBay token missing', msg, level='error')
            tgw_logging.log_event('ebay_token_missing', path=str(token_path))
            raise HardFailure(msg)  # → dead_letter immediately

        state = json.loads(token_path.read_text(encoding='utf-8'))
        expiry    = float(state.get('expiry', 0))
        remaining = expiry - time.time()

        # ── 2. Check if refresh is needed ───────────────────────────────────
        if remaining > REFRESH_BUFFER_S:
            log.info('token valid for %dm — no refresh needed', int(remaining // 60))
            tgw_logging.log_event('ebay_token_ok',
                                  remaining_minutes=int(remaining // 60))
            self._reschedule()
            return

        # ── 3. Attempt refresh ──────────────────────────────────────────────
        log.info('token expires in %dm — refreshing', max(0, int(remaining // 60)))
        tgw_logging.log_event('ebay_token_refreshing',
                              remaining_minutes=int(remaining // 60))
        try:
            from tgw.apis.ebay.refresh_access_token import refresh_access_token
            refresh_access_token(force=True)  # worker owns timing; bypass internal guard
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status in (400, 401):
                # Hard failure — refresh token dead, browser re-consent needed
                msg = (f'eBay refresh token rejected (HTTP {status}). '
                       'Browser re-consent required: run get_access_token.')
                notify('eBay token DEAD', msg, level='critical')
                tgw_logging.log_event('ebay_token_dead', status=status,
                                      error=str(exc))
                raise HardFailure(msg) from exc  # → dead_letter, no retries
            # Transient HTTP error — let base class put into retry_wait
            tgw_logging.log_event('ebay_token_refresh_transient',
                                  status=status, error=str(exc))
            raise
        except Exception as exc:
            # Network error, timeout, etc. — transient, retry_wait
            tgw_logging.log_event('ebay_token_refresh_transient', error=str(exc))
            raise

        log.info('eBay token refreshed successfully')
        tgw_logging.log_event('ebay_token_refreshed')
        notify('eBay token refreshed', 'Token renewed successfully', level='info')

        # ── 4. Self-reschedule ──────────────────────────────────────────────
        self._reschedule()

    # _on_terminal_failure: no override needed — worker_base.QueueWorker's
    # default detects _reschedule() (no-arg) and calls it automatically on
    # dead_letter (audit#1143 #1244).

    def _reschedule(self) -> None:
        """Enqueue the next token-check run, timed to arrive before refresh is needed."""
        token_path: Path = self.config['ebay_token_path']
        expiry = 0.0
        if token_path.exists():
            try:
                expiry = float(json.loads(
                    token_path.read_text(encoding='utf-8')
                ).get('expiry', 0))
            except Exception:
                pass

        if expiry > 0:
            # Wake up 5 minutes before the refresh buffer kicks in
            next_run = expiry - REFRESH_BUFFER_S - 300
        else:
            next_run = time.time() + RESCHEDULE_INTERVAL_S

        # Never sooner than 5 minutes from now
        next_run = max(next_run, time.time() + 300)
        wait_min = int((next_run - time.time()) // 60)

        try:
            jid = state_machine.enqueue_job(
                queue_name=QUEUE_NAME,
                payload={'reason': 'scheduled'},
                not_before=next_run,
                max_attempts=3,
                dedupe_key=f'{QUEUE_NAME}:pending',
                debounce=True,
            )
        except psycopg2.errors.UniqueViolation:
            jid = None
        log.info('next token check in %dm (job %s)', wait_min, jid)
        tgw_logging.log_event('ebay_token_rescheduled',
                              next_run_in_minutes=wait_min,
                              next_job_id=jid)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='tgw-token-worker')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    worker = TokenRefreshWorker(queue_name=QUEUE_NAME, config=cfg)
    worker.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
