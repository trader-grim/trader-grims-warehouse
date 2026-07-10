"""
tgw.queue.worker_base — shared base class for all queue workers.

DESIGN (settled, Opus session 2026-05-31):
    PostgreSQL is the single source of truth for work. systemd keeps this
    process alive. This class is the ONLY place a worker touches the state
    machine — subclasses implement business logic in handle() and never
    write SQL or construct paths.

INTEGRATION NOTE FOR THE EXECUTOR:
    The function names below (claim_queue_jobs, mark_succeeded, mark_failed,
    recover_expired_jobs) are taken from the project summaries. Open the real
    src/tgw/queue/state_machine.py and reconcile signatures before running.
    If a name differs, fix the call here — do NOT add SQL to this file or to
    subclasses. This module is the seam; keep all DB specifics behind it.
"""

from __future__ import annotations

import os
import signal
import socket
import time
from typing import Any

from tgw import logging as tgw_logging
from tgw.queue import state_machine

log = tgw_logging.get_logger(__name__)


class QueueWorker:
    """Forever loop: claim a leased job, handle it, report the result."""

    def __init__(self, queue_name: str, config: dict[str, Any]):
        self.queue_name = queue_name
        self.config = config
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.poll_interval = config.get("queue", {}).get("poll_interval_s", 2.0)
        self.lease_seconds = config.get("queue", {}).get("lease_seconds", 300)
        self._stop = False
        tgw_logging.setup_logging(component=f"worker.{queue_name}")

    # -- lifecycle ---------------------------------------------------------

    def install_signal_handlers(self) -> None:
        """Finish the current job on SIGTERM, then exit cleanly."""
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

    def _request_stop(self, *_args) -> None:
        log.info("shutdown requested; will exit after current job")
        self._stop = True

    # -- the loop ----------------------------------------------------------

    def run(self) -> None:
        self.install_signal_handlers()
        tgw_logging.log_event("worker_start", queue=self.queue_name, owner=self.owner)
        while not self._stop:
            job = self._claim_one()
            if job is None:
                time.sleep(self.poll_interval)
                continue
            self._process(job)
        tgw_logging.log_event("worker_stop", queue=self.queue_name, owner=self.owner)

    def _claim_one(self):
        """Lease at most one job for this queue. Returns the job or None."""
        jobs = state_machine.claim_queue_jobs(
            queue_name=self.queue_name,
            lease_owner=self.owner,
            lease_seconds=self.lease_seconds,
            limit=1,
        )
        return jobs[0] if jobs else None

    def _process(self, job) -> None:
        job_id = job["job_id"]
        tgw_logging.log_event("job_claimed", job_id=job_id, queue=self.queue_name)
        try:
            self.handle(job)
        except Exception as exc:  # noqa: BLE001 — we want to catch everything
            log.exception("job %s failed", job_id)
            state_machine.mark_failed(job_id=job_id, error=repr(exc))
            tgw_logging.log_event("job_failed", job_id=job_id, error=repr(exc))
        else:
            state_machine.mark_succeeded(job_id=job_id)
            tgw_logging.log_event("job_succeeded", job_id=job_id)

    # -- subclass contract -------------------------------------------------

    def handle(self, job) -> None:
        """Do the work. Raise on failure; return on success.

        Subclasses implement this and nothing else. Use tgw-api for any data
        access. Never construct ItemData paths here.
        """
        raise NotImplementedError
