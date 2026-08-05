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
    ('503 server error',       300),   # eBay / EPS service temporarily unavailable
    ('service unavailable',    300),   # generic upstream 503
    # Quota walls are transient by definition — they clear at the daily reset
    # (00:00 America/Los_Angeles). Requeue-with-delay instead of dead-lettering
    # so exhaustion never piles up thousands of dead letters again (session 42).
    ('pipeline steps still running', 60),   # ordering guard: stage/publish wait for upstream (s42)
    ('quota budget exhausted', 1800),  # tgw.quota background halt (pre-call)
    ('too many requests',      1800),  # HTTP 429 from any metered API
    ('usage limit',            1800),  # Trading/EPS Ack=Failure quota message
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

        # PP-QUOTA-001: workers are background callers — the quota layer may
        # halt them at the budget threshold to protect the operator's reserve.
        try:
            from tgw import quota
            quota.set_context('background', f'worker:{queue_name}')
        except Exception as exc:  # pragma: no cover - defensive
            log.debug('quota context skipped: %s', exc)

        # PP-AIOPS-001: set mutation attribution context for this worker process
        try:
            from tgw.apis.nats_client import init_nats
            from tgw.items import set_mutation_context
            set_mutation_context(f'worker:{queue_name}')
            init_nats(config)
        except Exception as exc:
            log.debug('nats init skipped: %s', exc)

        # PP-WM-001: activate config-driven notifications (desktop/webhook/smtp).
        # An absent 'notifications' block falls back to log+file backends, so this
        # is behavior-neutral until the operator opts in. Wrapped so a notify
        # misconfig can never prevent a (load-bearing) worker from starting.
        try:
            from tgw.notify import configure
            configure(config.get('notifications')
                      or config.get('raw', {}).get('notifications', {}))
        except Exception as exc:  # pragma: no cover - defensive
            log.debug('notify configure skipped: %s', exc)

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

        # Invariant C10 (Dave, session 43): an operator action stays an operator
        # action end-to-end. Jobs carrying origin='operator' (stamped by every
        # operator surface, propagated worker-to-worker) run in the interactive
        # quota context — counted, never background-halted — so background
        # debris can never starve the operator's reserve out from under a
        # button press. Restored in the finally: the worker process itself
        # remains a background caller between operator jobs.
        # The context NAME keeps the worker: prefix — the fence's X-TGW-Caller
        # header is built from it, and the PATCH auto-redraft guard must still
        # recognize this as a machine write (a worker acting FOR the operator
        # is not the operator editing in the UI). Dropping the prefix here
        # resurrected the s42 redraft loop within two cycles (2026-07-03).
        _operator_job = (job.get('payload_json') or {}).get('origin') == 'operator'
        if _operator_job:
            try:
                from tgw import quota
                quota.set_context('interactive', f'worker:{self.queue_name}:operator')
            except Exception as exc:  # pragma: no cover - defensive
                log.debug('quota context switch skipped: %s', exc)

        try:
            state_machine.mark_running(job_id, self.owner)
            _handle_result = self.handle(job)
        except HardFailure as exc:
            log.error('job %s hard failure (dead_letter): %s', job_id, exc)
            state_machine.mark_dead_letter(job_id, self.owner, repr(exc))
            tgw_logging.log_event('job_dead_letter', job_id=job_id,
                                  error=repr(exc))
            from tgw.notify import notify
            notify(
                f'Dead letter: {self.queue_name}',
                f'{repr(exc)[:140]}',
                level='error',
            )
            self._on_terminal_failure(job, repr(exc))
        except Exception as exc:
            error_text = repr(exc)
            log.exception('job %s failed: %s', job_id, error_text)

            # Classify the error on every attempt, not just the last one.
            # Transient errors (expired token, quota wall, 429) get the tuned
            # backoff immediately — retrying sooner with generic 30-240s
            # exponential backoff just re-hammers an already-broken dependency
            # several times before the real backoff kicks in (same failure
            # class as the 3-day EPS quota exhaustion incident, PP-QUOTA-001).
            attempt = int(job.get('attempt_count') or 0)
            max_att = int(job.get('max_attempts') or 5)
            action, delay = classify_dead_letter(error_text)
            if action == 'requeue':
                log.warning(
                    'transient error on %s (attempt %d/%d); rescheduling in %ds: %s',
                    self.queue_name, attempt, max_att, delay, error_text[:200],
                )
                from tgw.notify import notify
                notify(
                    f'Transient requeue: {self.queue_name}',
                    f'Rescheduling in {delay}s — {error_text[:120]}',
                    level='warning',
                )
                state_machine.requeue_with_backoff(
                    job_id, self.owner, delay, error_text
                )
                tgw_logging.log_event(
                    'job_transient_requeue', job_id=job_id,
                    queue=self.queue_name, delay=delay,
                )
                return

            result_state = state_machine.mark_failed(job_id, self.owner, error_text)
            tgw_logging.log_event('job_failed', job_id=job_id, error=error_text)
            if result_state == 'dead_letter':
                log.error('job %s dead_letter after %d/%d attempts: %s',
                          job_id, attempt, max_att, error_text[:200])
                from tgw.notify import notify
                notify(
                    f'Dead letter: {self.queue_name}',
                    f'{error_text[:140]}',
                    level='error',
                )
                self._on_terminal_failure(job, error_text)
        else:
            receipt = _handle_result if isinstance(_handle_result, dict) else None
            state_machine.mark_succeeded(job_id, self.owner, result=receipt)
            tgw_logging.log_event('job_succeeded', job_id=job_id)
            log.info('job %s succeeded', job_id)
        finally:
            if _operator_job:
                try:
                    from tgw import quota
                    quota.set_context('background', f'worker:{self.queue_name}')
                except Exception as exc:  # pragma: no cover - defensive
                    log.debug('quota context restore skipped: %s', exc)

    # ------------------------------------------------------------------
    # Subclass contract
    # ------------------------------------------------------------------

    def handle(self, job: Dict[str, Any]) -> None:
        """Do the work. Raise on failure; return on success.

        Subclasses implement this and nothing else. Use tgw-api for any
        data access. Never construct ItemData paths here.
        """
        raise NotImplementedError

    def _on_terminal_failure(self, job: Dict[str, Any], error_text: str) -> None:
        """Called once a job reaches dead_letter (HardFailure or exhausted
        retries) — after the dead-letter notify has already fired.

        Self-rescheduling workers (handle() calls self._reschedule() only on
        success — token_refresh, velocity_stats, ebay_sync, ebay_dole,
        ebay_price_reducer, ebay_legacy_sync, sync_conflict) need their next
        job enqueued here too, or a single dead-lettered job ends the chain
        forever with no future job ever enqueued: the dead-letter notify
        tells Dave a job failed, but nothing tells him the recurring check
        itself stopped (audit#1143 #1165+#1166).

        audit#1143 #1244 follow-up (code review): originally fixed by having
        each self-rescheduling worker hand-write an identical 4-line
        override (log + call self._reschedule()), guarded only by a test
        that scanned for the omission. Generalized here instead — any
        subclass whose _reschedule() takes no required arguments gets it
        called automatically, so the omission this audit already caught
        once (todo #1234 fixed 2 of 8 workers; review caught the other 6 as
        todo #1242) can't recur for a future worker of this shape.

        Workers whose _reschedule() needs an argument (ebay_sku_migrate,
        which needs interval_hours recomputed from config since handle()
        never reached its own call) must still override this explicitly —
        the override takes precedence over this default.
        """
        reschedule = getattr(self, '_reschedule', None)
        if reschedule is None:
            return
        try:
            import inspect
            sig = inspect.signature(reschedule)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        if any(
            p.default is inspect.Parameter.empty
            and p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD,
                          inspect.Parameter.POSITIONAL_ONLY)
            for p in sig.parameters.values()
        ):
            # _reschedule needs an argument this base class can't supply —
            # the subclass must override _on_terminal_failure itself.
            return
        log.warning('%s dead-lettered; rescheduling next run anyway',
                   self.queue_name)
        reschedule()
