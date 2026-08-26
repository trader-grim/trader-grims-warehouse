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
import math
import os
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import tgw.logging as tgw_logging
from tgw.errors import HardFailure
from tgw.queue import state_machine

log = logging.getLogger(__name__)

_TREATMENT_TIMER_MIN_DELAY_S = 1.0
_TREATMENT_TIMER_MAX_DELAY_S = 7 * 24 * 3600.0


def _is_treatment_receipt_candidate(value: Any) -> bool:
    return isinstance(value, dict) and (
        value.get("receipt_schema_id") == "treatment-receipt/v1"
        or any(key in value for key in ("treatment_id", "treatment_version", "graph_id"))
    )


def _treatment_receipt_error(value: Any, job: Dict[str, Any]) -> str | None:
    """Return why a candidate receipt is not fully contract-bound."""
    if not _is_treatment_receipt_candidate(value):
        return "NOT_A_TREATMENT_RECEIPT"
    payload = job.get("payload_json") or {}
    if not isinstance(payload, dict):
        return "INVALID_JOB_PAYLOAD"
    required_receipt = (
        "treatment_id", "treatment_version", "graph_id",
        "goal_profile_id", "goal_profile_version", "object_generation",
        "condition_hash", "entity_id",
    )
    if any(not isinstance(value.get(key), str) or not value[key].strip()
           for key in required_receipt):
        return "INVALID_RECEIPT_IDENTITY"
    if value.get("receipt_schema_id") != "treatment-receipt/v1":
        return "INVALID_RECEIPT_SCHEMA"
    if value.get("outcome") != "satisfied":
        return "INVALID_RECEIPT_OUTCOME"
    for key in (
        "goal_profile_id", "goal_profile_version", "object_generation",
        "condition_hash",
    ):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            return f"INVALID_{key.upper()}"
    if job.get("entity_type") != "item":
        return "INVALID_ENTITY_TYPE"
    entity_id = job.get("entity_id")
    if not isinstance(entity_id, str) or not entity_id.strip():
        return "INVALID_ENTITY_ID"
    if payload.get("entity_id") != entity_id:
        return "ENTITY_ID_MISMATCH"
    if value.get("entity_id") != entity_id:
        return "RECEIPT_ENTITY_ID_MISMATCH"
    if payload.get("object_id") not in (None, entity_id):
        return "OBJECT_ID_MISMATCH"
    for key in (
        "treatment_id", "treatment_version", "graph_id",
        "goal_profile_id", "goal_profile_version", "object_generation",
        "condition_hash",
    ):
        if value.get(key) != payload.get(key):
            return f"{key.upper()}_MISMATCH"
    return None


def _waiting_treatment_receipt_error(value: Any, job: Dict[str, Any]) -> str | None:
    """Validate a worker request for an atomic, scheduler-owned timer."""
    if not isinstance(value, dict) or value.get("receipt_schema_id") != (
        "treatment-wait-receipt/v1"
    ):
        return "NOT_A_WAITING_TREATMENT_RECEIPT"
    if value.get("outcome") != "transient_backoff":
        return "INVALID_WAITING_OUTCOME"
    payload = job.get("payload_json") or {}
    if not isinstance(payload, dict):
        return "INVALID_JOB_PAYLOAD"
    for key in ("treatment_id", "treatment_version", "graph_id"):
        if not isinstance(value.get(key), str) or value.get(key) != payload.get(key):
            return f"{key.upper()}_MISMATCH"
    timer = value.get("timer")
    if not isinstance(timer, dict):
        return "INVALID_TIMER"
    if timer.get("queue_name") != job.get("queue_name"):
        return "TIMER_QUEUE_MISMATCH"
    not_before = timer.get("not_before")
    if (isinstance(not_before, bool)
            or not isinstance(not_before, (int, float))
            or not math.isfinite(float(not_before))):
        return "INVALID_TIMER_NOT_BEFORE"
    delay = float(not_before) - time.time()
    if delay < _TREATMENT_TIMER_MIN_DELAY_S:
        return "TIMER_NOT_IN_FUTURE"
    if delay > _TREATMENT_TIMER_MAX_DELAY_S:
        return "TIMER_WINDOW_EXCEEDED"
    timer_payload = timer.get("payload")
    if not isinstance(timer_payload, dict):
        return "INVALID_TIMER_PAYLOAD"
    for key in ("graph_id", "object_generation", "condition_hash"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            return f"INVALID_{key.upper()}"
    for key in ("treatment_id", "treatment_version"):
        if timer_payload.get(key) != payload.get(key):
            return f"TIMER_{key.upper()}_MISMATCH"
    continuation_bindings = (
        "treatment_id", "treatment_version", "graph_id",
        "goal_profile_id", "goal_profile_version", "object_generation",
        "condition_hash", "entity_id", "object_id", "sku",
        "provider_effect_id", "provider_identity", "expected_offer_id",
        "source_operation",
    )
    for key in continuation_bindings:
        if timer_payload.get(key) != payload.get(key):
            return f"TIMER_{key.upper()}_MISMATCH"
    if "observation_checkpoint" in timer_payload:
        return "TIMER_RESERVED_CHECKPOINT"
    if timer_payload.get("sku") != job.get("entity_id"):
        return "TIMER_ENTITY_MISMATCH"
    if not isinstance(timer.get("dedupe_key"), str) or not timer["dedupe_key"]:
        return "INVALID_TIMER_DEDUPE"
    max_attempts = timer.get("max_attempts", 3)
    if (isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 10):
        return "INVALID_TIMER_MAX_ATTEMPTS"
    return None


_RECOVER_INTERVAL_S = 60   # how often to call recover_expired_jobs


class JobTimeoutError(RuntimeError):
    """A worker's bounded execution window elapsed."""


class JobCancelled(RuntimeError):
    """The exact leased job was durably cancelled while its child was active."""

    def __init__(self, message: str, *, reason: str, reaped: bool,
                 runner: Dict[str, Any]) -> None:
        super().__init__(message)
        self.reason = reason
        self.reaped = reaped
        self.runner = runner

# Transient error patterns that warrant automatic requeue rather than dead-letter.
# (substring match, case-insensitive) → requeue delay in seconds.
# Order matters: first match wins.
_TRANSIENT_ERRORS: list[tuple[str, int]] = [
    ('job execution timed out', 120),  # bounded call; retry instead of wedging
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
        configured_lease_seconds = int(
            config.get('queue', {}).get('lease_seconds', 300)
        )
        # Most jobs should use the short fleet-wide lease.  A bounded full
        # reconciliation can legitimately outlive it; subclasses declare a
        # minimum rather than silently losing their lease mid-run.
        self.lease_seconds = max(
            configured_lease_seconds,
            int(getattr(self, "min_lease_seconds", 0) or 0),
        )
        self._stop           = False
        self._last_recover   = 0.0
        state_machine.init(config.get('postgres_dsn', 'dbname=state_machine user=tgw'))
        # File logging is a resolved configuration binding.  Directly
        # constructed workers (tests and embedded callers) stay console-only
        # instead of implicitly opening the production log directory.
        tgw_logging.setup_logging(
            component=f'worker.{queue_name}',
            log_root=config.get('log_root'),
        )

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
        try:
            lease_token = str(uuid.UUID(str(job.get("lease_token"))))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError(f"claimed job {job_id} lacks lease_token") from exc
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
            state_machine.mark_running(job_id, self.owner, lease_token)
            _handle_result = self._handle_with_deadline(job)
        except JobCancelled as exc:
            runner_identity = {
                "schema": "tgw-coding-runner/v2",
                "job_id": job_id,
                "queue_name": self.queue_name,
                "lease_owner": self.owner,
                "lease_token": lease_token,
            }
            if exc.reason == "no_runner":
                runner_identity["kind"] = "no_runner"
            acknowledgement = {
                "schema": "tgw-coding-stop-ack/v1",
                "job_id": job_id,
                "ack_id": str(uuid.uuid4()),
                "worker": self.owner,
                # Persist only the exact immutable request identity. Process
                # diagnostics remain local and are not cancellation authority.
                "runner": runner_identity,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "reason": exc.reason,
                "reaped": exc.reaped,
            }
            acknowledged = state_machine.acknowledge_cancellation(job_id, acknowledgement)
            if acknowledged is None:
                raise RuntimeError(
                    f"job {job_id} cancellation acknowledgement was not durably accepted"
                )
            log.info("job %s cancellation acknowledged: %s", job_id, exc)
            tgw_logging.log_event("job_cancelled", job_id=job_id)
        except HardFailure as exc:
            log.error('job %s hard failure (dead_letter): %s', job_id, exc)
            state_machine.mark_dead_letter(
                job_id, self.owner, lease_token, repr(exc),
                result=getattr(exc, "result", None),
            )
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
                    job_id, self.owner, lease_token, delay, error_text
                )
                tgw_logging.log_event(
                    'job_transient_requeue', job_id=job_id,
                    queue=self.queue_name, delay=delay,
                )
                return

            result_state = state_machine.mark_failed(
                job_id, self.owner, lease_token, error_text,
            )
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
            if getattr(self, "direct_local_receipts", False):
                # Development coding receipts are consumed directly from the
                # request worktree by the local Foreman.  They do not enter
                # the item-workflow evaluation queue.
                success_hook = getattr(self, "_on_direct_local_success", None)
                closed = state_machine.close_local_success(
                    job_id,
                    self.owner,
                    lease_token,
                    receipt or {},
                    lambda register: success_hook(job, receipt, register)
                    if success_hook is not None else None,
                )
                if not closed:
                    log.info("job %s cancellation won local success closure", job_id)
                    tgw_logging.log_event("job_cancelled", job_id=job_id)
                    return
                tgw_logging.log_event('job_succeeded', job_id=job_id)
                log.info('job %s succeeded', job_id)
                return
            waiting_error = _waiting_treatment_receipt_error(receipt, job)
            if waiting_error is None:
                timer_job_id = state_machine.complete_treatment_and_schedule_timer(
                    job_id, self.owner, lease_token, receipt,
                )
                if not timer_job_id:
                    raise RuntimeError(
                        f"lost lease scheduling treatment timer for job {job_id}"
                    )
                tgw_logging.log_event(
                    "job_waiting", job_id=job_id, timer_job_id=timer_job_id,
                    outcome=receipt["outcome"],
                )
                log.info("job %s waiting on timer %s", job_id, timer_job_id)
                return
            receipt_error = _treatment_receipt_error(receipt, job)
            if receipt_error is None:
                event_id = state_machine.complete_treatment_and_enqueue_evaluation(
                    job_id, self.owner, lease_token, receipt,
                )
                if not event_id:
                    raise RuntimeError(
                        f"lost lease completing treatment job {job_id}"
                    )
            elif _is_treatment_receipt_candidate(receipt):
                failed_receipt = dict(receipt)
                failed_receipt["outcome"] = "failed"
                failed_receipt["established_conditions"] = []
                failed_receipt["error_detail"] = (
                    f"receipt binding rejected: {receipt_error}"
                )
                evidence = dict(failed_receipt.get("evidence") or {})
                evidence["reason_code"] = receipt_error
                failed_receipt["evidence"] = evidence
                state_machine.mark_dead_letter(
                    job_id, self.owner, lease_token, failed_receipt["error_detail"],
                    result=failed_receipt,
                )
                tgw_logging.log_event(
                    'job_dead_letter', job_id=job_id,
                    error=failed_receipt["error_detail"],
                )
                log.error("job %s emitted invalid treatment receipt: %s", job_id, receipt_error)
                return
            else:
                state_machine.mark_succeeded(
                    job_id, self.owner, lease_token, result=receipt,
                )
            tgw_logging.log_event('job_succeeded', job_id=job_id)
            log.info('job %s succeeded', job_id)
        finally:
            if _operator_job:
                try:
                    from tgw import quota
                    quota.set_context('background', f'worker:{self.queue_name}')
                except Exception as exc:  # pragma: no cover - defensive
                    log.debug('quota context restore skipped: %s', exc)

    def _handle_with_deadline(self, job: Dict[str, Any]) -> Any:
        """Run opt-in remote work with a hard process-level deadline.

        Queue leases protect the row, not a provider SDK that remains blocked.
        A worker may set ``job_timeout_s``; timeout then follows the ordinary
        transient-retry path rather than leaving a live process stuck forever.
        """
        timeout_s = float(getattr(self, "job_timeout_s", 0) or 0)
        if timeout_s <= 0 or not hasattr(signal, "setitimer"):
            return self.handle(job)

        import threading

        if threading.current_thread() is not threading.main_thread():
            return self.handle(job)

        def _expired(_signum: int, _frame: Any) -> None:
            raise JobTimeoutError(
                f"job execution timed out after {timeout_s:g}s "
                f"(queue={self.queue_name}, job={job.get('job_id')})"
            )

        previous_handler = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, _expired)
        signal.setitimer(signal.ITIMER_REAL, timeout_s)
        try:
            return self.handle(job)
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)

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
