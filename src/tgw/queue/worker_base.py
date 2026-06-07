"""
tgw.queue.worker_base — shared base class for all queue workers.

DESIGN (settled 2026-05-31):
    PostgreSQL is the single source of truth for work. systemd keeps this
    process alive. This class is the ONLY place a worker touches the state
    machine — subclasses implement business logic in handle() and never
    write SQL or construct paths.

STATE TRANSITIONS PER JOB:
    queued → leased  (claim_queue_jobs)
    leased → running (mark_running, at start of handle())
    running → succeeded  (mark_succeeded, on clean return)
    running → retry_wait (mark_failed, transient error, attempt < max)
    running → failed → dead_letter (mark_failed, attempt >= max)
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import time
from typing import Any, Dict, Optional

import tgw.logging as tgw_logging
from tgw.queue import state_machine

log = logging.getLogger(__name__)


class HardFailure(Exception):
    """
    Raise from handle() to immediately dead-letter the job with no retries.

    Use for failures where retrying cannot help: expired credentials,
    missing required resources, or any condition that needs human intervention.
    """

_RECOVER_INTERVAL_S = 60   # how often to call recover_expired_jobs

# Transient error patterns that warrant automatic requeue rather than dead-letter.
# (substring match, case-insensitive) → requeue delay in seconds.
# Order matters: first match wins.
_TRANSIENT_ERRORS: list[tuple[str, int]] = [
    ('token is expired',       900),   # eBay token lapsed; wait for token_refresh
    ('no ebay photo urls yet', 600),   # ebay_upload still running
    ('directory not empty',     30),   # transient OS race in catalog_rebuild
    ('readtimeout',            120),   # network hiccup
    ('lease_expired',          120),   # claim race; will resolve on retry
    ('connectionerror',        120),   # transient network
]


def classify_dead_letter(error_text: str) -> tuple[str, int]:
    """Classify a failure as transient-requeue or permanent dead-letter.

    Returns ('requeue', delay_seconds) or ('dead_letter', 0).
    Called when a job has exhausted normal retries (attempt_count >= max_attempts).
    """
    lower = error_text.lower()
    for pattern, delay in _TRANSIENT_ERRORS:
        if pattern in lower:
            return ('requeue', delay)
    return ('dead_letter', 0)


class QueueWorker:
    """Forever loop: claim a leased job, handle it, report the result."""

    def __init__(self, queue_name: str, config: Dict[str, Any]) -> None:
        self.queue_name   = queue_name
        self.config       = config
        self.owner        = f'{socket.gethostname()}:{os.getpid()}'
        self.poll_interval = float(
            config.get('queue', {}).get('poll_interval_s', 2.0)
        )
        self.lease_seconds = int(
            config.get('queue', {}).get('lease_seconds', 300)
        )
        self._stop           = False
        self._last_recover   = 0.0
        state_machine.init(config.get('postgres_dsn', 'dbname=state_machine user=tgw'))
        tgw_logging.setup_logging(component=f'worker.{queue_name}')

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def install_signal_handlers(self) -> None:
        import threading
        if threading.current_thread() is not threading.main_thread():
            log.debug('not main thread — skipping signal handler install')
            return
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT,  self._request_stop)

    def _request_stop(self, *_args: Any) -> None:
        log.info('shutdown requested; will exit after current job')
        self._stop = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event('worker_start', queue=self.queue_name, owner=self.owner)
        log.info('worker started: queue=%s owner=%s', self.queue_name, self.owner)

        while not self._stop:
            self._maybe_recover()
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)

        tgw_logging.log_event('worker_stop', queue=self.queue_name, owner=self.owner)
        log.info('worker stopped: queue=%s', self.queue_name)

    def _maybe_recover(self) -> None:
        now = time.time()
        if now - self._last_recover >= _RECOVER_INTERVAL_S:
            try:
                n = state_machine.recover_expired_jobs()
                if n:
                    log.info('recovered %d expired job(s)', n)
                    tgw_logging.log_event('jobs_recovered', count=n)
            except Exception:
                log.exception('recover_expired_jobs failed')
            self._last_recover = now

    def _claim_one(self) -> Optional[Dict[str, Any]]:
        try:
            jobs = state_machine.claim_queue_jobs(
                queue_name=self.queue_name,
                lease_owner=self.owner,
                lease_seconds=self.lease_seconds,
                limit=1,
            )
            return jobs[0] if jobs else None
        except Exception:
            log.exception('claim_queue_jobs failed')
            return None

    def _process(self, job: Dict[str, Any]) -> None:
        job_id = str(job['job_id'])
        tgw_logging.log_event('job_claimed', job_id=job_id, queue=self.queue_name)
        log.info('claimed job %s', job_id)

        try:
            state_machine.mark_running(job_id, self.owner)
            self.handle(job)
        except HardFailure as exc:
            log.error('job %s hard failure (dead_letter): %s', job_id, exc)
            state_machine.mark_dead_letter(job_id, self.owner, repr(exc))
            tgw_logging.log_event('job_dead_letter', job_id=job_id,
                                  error=repr(exc))
        except Exception as exc:
            error_text = repr(exc)
            log.exception('job %s failed: %s', job_id, error_text)

            # When the job has exhausted normal retries, classify the error.
            # Transient errors get rescheduled with a fresh retry window rather
            # than dying permanently — keeps dead_letter clean.
            attempt = int(job.get('attempt_count') or 0)
            max_att = int(job.get('max_attempts') or 5)
            if attempt >= max_att:
                action, delay = classify_dead_letter(error_text)
                if action == 'requeue':
                    log.warning(
                        'transient error at retry limit on %s; rescheduling in %ds: %s',
                        self.queue_name, delay, error_text[:200],
                    )
                    state_machine.requeue_with_backoff(
                        job_id, self.owner, delay, error_text
                    )
                    tgw_logging.log_event(
                        'job_transient_requeue', job_id=job_id,
                        queue=self.queue_name, delay=delay,
                    )
                    return

            state_machine.mark_failed(job_id, self.owner, error_text)
            tgw_logging.log_event('job_failed', job_id=job_id, error=error_text)
        else:
            state_machine.mark_succeeded(job_id, self.owner)
            tgw_logging.log_event('job_succeeded', job_id=job_id)
            log.info('job %s succeeded', job_id)

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    def handle(self, job: Dict[str, Any]) -> None:
        """Do the work. Raise on failure; return on success.

        Subclasses implement this and nothing else. Use tgw-api for any
        data access. Never construct ItemData paths here.
        """
        raise NotImplementedError
