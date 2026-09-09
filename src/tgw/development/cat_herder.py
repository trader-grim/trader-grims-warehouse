"""``cat_herder`` — the continual-harness orchestrator as a standing daemon.

LEAF-11-1 residual / Todo 2003. ``tgw coding start <todo>`` is synchronous: a
session dispatches one task and blocks for the whole implement -> test ->
review -> land loop. ``cat_herder`` runs that loop unattended.

It dequeues coding jobs (``harness_queue`` over the shared ``queue_jobs`` table)
and runs each through ``harness_cli.dispatch`` — the same code path as the
operator CLI. The queue lease and the ``harness_ledger`` are the only durable
state, so a restart resumes cleanly: the next herder recovers the dead lease
and the ledger cursor and continues from the recorded round.

Design:
  * one job at a time — a coding dispatch holds a worktree and runs for
    minutes; deliberate, explicit parallelism is a later choice
  * singleton — a PostgreSQL advisory lock; a second herder exits at once
  * SIGTERM / SIGINT — finish the job in flight, then exit (systemd stop)
  * a background thread heartbeats the job lease across the long dispatch; if
    the lease is lost the herder stops touching that job rather than racing a
    herder that stole it
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pwd
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from tgw.development import harness_cli, harness_orchestrator, harness_queue
from tgw.development.local_workflow import DEFAULT_CONFIG, LocalCodingWorkflowError, load_config

log = logging.getLogger("tgw.cat_herder")

_ADVISORY_LOCK_KEY = "tgw-cat-herder"
_DEFAULT_POLL_SECONDS = 5.0
_DEFAULT_LEASE_SECONDS = 2100          # 35 min — a bounded implement/review budget
_RECOVER_EVERY_SECONDS = 120.0
_PUBLISHER_USER = "tgw-harness"        # tgw.main_ref_guard._PUBLISHER_IDENTITIES

# outcomes that mean the task is finished and on main (or needed no change)
_DONE_OUTCOMES = frozenset({"landed", "already_satisfied", "already_done"})


class CatHerder:
    def __init__(self, config: dict[str, Any], *, poll_seconds: float | None = None,
                 lease_seconds: int | None = None, lock_key: str = _ADVISORY_LOCK_KEY) -> None:
        self._lock_key = lock_key
        self._dsn: str = config["postgres_dsn"]
        coding = config.get("coding", {})
        self._repository = str(coding.get("repository_root", harness_cli._REPOSITORY))
        self._worktree_root = str(coding.get("worktree_root", harness_cli._WORKTREE_ROOT))
        queue_cfg = config.get("queue", {}) if isinstance(config.get("queue"), dict) else {}
        self._poll = float(poll_seconds if poll_seconds is not None
                           else queue_cfg.get("poll_interval_s", _DEFAULT_POLL_SECONDS))
        self._lease = int(lease_seconds if lease_seconds is not None
                          else queue_cfg.get("lease_seconds", _DEFAULT_LEASE_SECONDS))
        self._actor = pwd.getpwuid(os.geteuid()).pw_name
        self._owner = f"cat_herder@{socket.gethostname()}:{os.getpid()}"
        self._stop = threading.Event()
        self._lock_conn: Any = None
        harness_queue.init(self._dsn)

    # -- lifecycle ---------------------------------------------------------- #

    def _acquire_singleton(self) -> bool:
        import psycopg2

        self._lock_conn = psycopg2.connect(self._dsn)
        self._lock_conn.autocommit = True
        with self._lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self._lock_key,))
            got = bool(cur.fetchone()[0])
        if not got:
            self._lock_conn.close()
            self._lock_conn = None
        return got

    def _install_signals(self) -> None:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, self._request_stop)
            signal.signal(signal.SIGINT, self._request_stop)

    def _request_stop(self, *_a: Any) -> None:
        log.info("shutdown requested — will exit after the current job")
        self._stop.set()

    def run(self) -> int:
        if not self._acquire_singleton():
            log.warning("another cat_herder holds the singleton lock on %s — exiting", self._dsn)
            return 0
        self._install_signals()
        log.info("cat_herder up: owner=%s repo=%s lease=%ss poll=%ss",
                 self._owner, self._repository, self._lease, self._poll)
        last_recover = 0.0
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if now - last_recover >= _RECOVER_EVERY_SECONDS:
                    self._recover()
                    last_recover = now
                job = self._claim()
                if job is None:
                    self._stop.wait(self._poll)
                    continue
                self._process(job)
        finally:
            self._release_singleton()
        log.info("cat_herder stopped: owner=%s", self._owner)
        return 0

    def run_once(self) -> int:
        """Recover, then claim and process at most one job, then exit. Used by a
        systemd oneshot/timer deployment and by the tests."""
        if not self._acquire_singleton():
            log.warning("another cat_herder holds the singleton lock — exiting")
            return 0
        try:
            self._recover()
            job = self._claim()
            if job is None:
                log.info("cat_herder --once: queue empty")
                return 0
            self._process(job)
        finally:
            self._release_singleton()
        return 0

    def _release_singleton(self) -> None:
        if self._lock_conn is not None:
            try:
                self._lock_conn.close()
            except Exception:  # pragma: no cover - best effort
                pass
            self._lock_conn = None

    # -- queue -------------------------------------------------------------- #

    def _recover(self) -> None:
        try:
            n = harness_queue.recover_expired()
            if n:
                log.info("recovered %d expired/matured coding job(s)", n)
        except Exception:
            log.exception("recover_expired failed")

    def _claim(self) -> dict[str, Any] | None:
        try:
            return harness_queue.claim(self._owner, lease_seconds=self._lease)
        except Exception:
            log.exception("claim failed")
            return None

    # -- one job ---------------------------------------------------------- #

    def _process(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        token = job["lease_token"]
        attempt = int(job.get("attempt_count") or 1)
        max_attempts = int(job.get("max_attempts") or harness_queue.DEFAULT_MAX_ATTEMPTS)
        payload = job.get("payload_json") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {}

        try:
            todo_id = int(payload["todo_id"])
            message = str(payload["message"]).strip()
            if not message:
                raise ValueError("empty message")
        except (KeyError, TypeError, ValueError) as exc:
            log.error("job %s has an unusable payload (%s) — parking", job_id, exc)
            harness_queue.park(job_id, token, f"unusable payload: {exc}")
            return

        executor_preference = tuple(payload.get("executor_preference") or ())
        max_rounds = int(payload.get("max_rounds") or harness_orchestrator.DEFAULT_MAX_ROUNDS)

        if not harness_queue.mark_running(job_id, token):
            log.warning("job %s lease already lost before start — skipping", job_id)
            return

        log.info("job %s: Todo %s (attempt %d/%d) -> %r",
                 job_id, todo_id, attempt, max_attempts, message)
        stop_beat = threading.Event()
        beat = threading.Thread(
            target=self._heartbeat_loop, args=(job_id, token, stop_beat), daemon=True)
        beat.start()
        try:
            result = self._dispatch(todo_id, message, executor_preference, max_rounds)
        except harness_orchestrator.OrchestratorBusy as exc:
            self._retry(job_id, token, attempt, max_attempts, 120,
                        f"another owner holds the task cursor: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - a dispatch failure must not kill the daemon
            log.exception("job %s: dispatch raised", job_id)
            self._retry(job_id, token, attempt, max_attempts, 300, repr(exc))
            return
        finally:
            stop_beat.set()
            beat.join(timeout=5)

        outcome = result.get("outcome")
        if outcome in _DONE_OUTCOMES:
            ok = harness_queue.succeed(job_id, token, result)
            log.info("job %s: %s%s", job_id, outcome, "" if ok else " (lease lost — not recorded)")
        elif outcome == "blocked":
            handoff = result.get("supervisor_handoff", {})
            detail = handoff.get("next_operator_action") or json.dumps(handoff)[:2000] or "blocked"
            harness_queue.park(job_id, token, f"orchestrator blocked: {detail}")
            log.info("job %s: parked (blocked) — %s", job_id, detail)
        elif outcome == "rebind_required":
            self._retry(job_id, token, attempt, max_attempts, 30,
                        f"base ref moved: {result.get('detail', '')}")
        else:
            self._retry(job_id, token, attempt, max_attempts, 300,
                        f"unexpected outcome {outcome!r}: {json.dumps(result)[:1500]}")

    def _dispatch(self, todo_id: int, message: str,
                  executor_preference: tuple[str, ...], max_rounds: int) -> dict[str, Any]:
        return harness_cli.dispatch(
            f"todo-{todo_id}",
            message=message,
            body=None,
            repository=self._repository,
            worktree_root=self._worktree_root,
            max_rounds=max_rounds,
            self_publish=(self._actor == _PUBLISHER_USER),
            publisher_user=_PUBLISHER_USER,
            coder_user="tgw-coder",
            executor_preference=executor_preference,
            postgres_dsn=self._dsn,
        )

    def _retry(self, job_id: str, token: str, attempt: int, max_attempts: int,
               delay: int, detail: str) -> None:
        state = harness_queue.retry_later(
            job_id, token, error_detail=detail, delay_seconds=delay,
            attempt_count=attempt, max_attempts=max_attempts)
        log.warning("job %s: %s (attempt %d/%d) — %s",
                    job_id, state, attempt, max_attempts, detail[:300])

    def _heartbeat_loop(self, job_id: str, token: str, stop: threading.Event) -> None:
        interval = max(15.0, self._lease / 3.0)
        while not stop.wait(interval):
            try:
                if not harness_queue.heartbeat(job_id, token, lease_seconds=self._lease):
                    log.warning("job %s: lease lost — stopping heartbeat", job_id)
                    return
            except Exception:
                log.exception("job %s: heartbeat error", job_id)


def _load(config_path: Path | str) -> dict[str, Any]:
    try:
        return load_config(config_path)
    except LocalCodingWorkflowError as exc:
        raise SystemExit(f"cat_herder: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tgw-cat-herder", description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--poll-interval", type=float, default=None,
                        help="seconds between empty-queue polls (default: config or 5)")
    parser.add_argument("--lease-seconds", type=int, default=None,
                        help="job lease length (default: config queue.lease_seconds or 2100)")
    parser.add_argument("--once", action="store_true",
                        help="process at most one job, then exit (for a systemd oneshot / tests)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    herder = CatHerder(_load(args.config),
                       poll_seconds=args.poll_interval, lease_seconds=args.lease_seconds)
    if args.once:
        return herder.run_once()
    return herder.run()


if __name__ == "__main__":
    raise SystemExit(main())
