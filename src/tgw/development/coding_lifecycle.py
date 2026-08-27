"""Durable, local-only coding lifecycle supervisor.

The supervisor is deliberately a coordinator, not another queue or authority.  Its
journal records which existing operation owns each transition and makes replay of a
crashed transition observable.  Effects are completed by supplied handlers and are
recorded exactly once by their stable stage key.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "tgw-local-coding-lifecycle/v1"
STAGES = (
    "implementation", "controller", "candidate", "review", "integration",
    "materialization", "live_verification", "terminal_publication",
    "operator_notification", "operator_readback",
)
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "REMEDIATION_REQUIRED", "RESUMABLE_PARTIAL"})
FAILURE_OUTCOMES = frozenset({"failed", "remediation", "resumable_partial"})


class LifecycleError(RuntimeError):
    """A lifecycle binding or journal is unsafe."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def root_id(target: int | str, plan_commit: str, source_commit: str) -> str:
    value = {"target": str(target), "plan_commit": plan_commit, "source_commit": source_commit}
    return "coding:" + hashlib.sha256(_canonical(value)).hexdigest()


class LifecycleStore:
    """Atomic, lock-serialized JSON journals suitable for host restart recovery."""

    def __init__(self, root: Path | str):
        self.root = Path(root)

    def path(self, identity: str) -> Path:
        if not identity.startswith("coding:") or len(identity) != 71:
            raise LifecycleError("invalid coding lifecycle root ID")
        return self.root / (identity.removeprefix("coding:") + ".json")

    def _read(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleError("coding lifecycle journal is unavailable or invalid") from exc
        claimed = value.get("record_hash")
        unsigned = {k: v for k, v in value.items() if k != "record_hash"}
        if claimed != _hash(unsigned) or value.get("schema") != SCHEMA:
            raise LifecycleError("coding lifecycle journal hash/schema mismatch")
        return value

    def get(self, identity: str) -> dict[str, Any] | None:
        path = self.path(identity)
        return None if not path.exists() else self._read(path)

    def put(self, value: Mapping[str, Any]) -> dict[str, Any]:
        identity = str(value.get("root_id", ""))
        path = self.path(identity)
        self.root.mkdir(parents=True, exist_ok=True)
        unsigned = {k: v for k, v in dict(value).items() if k != "record_hash"}
        document = {**unsigned, "record_hash": _hash(unsigned)}
        fd, temporary = tempfile.mkstemp(prefix=".coding-lifecycle-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return document

    def locked(self, identity: str):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / (identity.removeprefix("coding:") + ".lock")).open("a")
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return lock

    def records(self) -> list[dict[str, Any]]:
        """Return every valid lifecycle record; one bad journal fails the scan closed."""
        if not self.root.exists():
            return []
        return [self._read(path) for path in sorted(self.root.glob("*.json"))]

    def find(self, target: int | str) -> dict[str, Any] | None:
        """Resolve a Todo/PP to its sole lifecycle, refusing ambiguous generations."""
        matches = [
            record for record in self.records()
            if record.get("binding", {}).get("target") == str(target)
        ]
        if not matches:
            return None
        if len(matches) != 1:
            raise LifecycleError(
                f"coding lifecycle target {target} has ambiguous source generations"
            )
        return matches[0]

    def supervisor_lock(self, identity: str, *, blocking: bool = False):
        """Claim the one long-lived supervisor slot for a root."""
        self.path(identity)  # validate before deriving a lock path
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / (identity.removeprefix("coding:") + ".supervisor.lock")).open("a")
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(lock.fileno(), flags)
        except BlockingIOError:
            lock.close()
            return None
        return lock


def create(store: LifecycleStore, *, target: int | str, plan_commit: str,
           solution_hash: str, source_commit: str, source_tree: str) -> dict[str, Any]:
    identity = root_id(target, plan_commit, source_commit)
    lock = store.locked(identity)
    try:
        prior = store.get(identity)
        binding = {"target": str(target), "plan_commit": plan_commit,
                   "solution_hash": solution_hash, "source_commit": source_commit,
                   "source_tree": source_tree}
        if prior is not None:
            if prior.get("binding") != binding:
                raise LifecycleError("stale lifecycle binding; zero effects")
            return prior
        now = datetime.now(timezone.utc).isoformat()
        return store.put({
            "schema": SCHEMA, "root_id": identity, "binding": binding,
            "state": "QUEUED", "stage": STAGES[0], "created_at": now,
            "updated_at": now, "stages": {}, "effects": {}, "job_ids": [],
            "publication": {"attempted": False, "retry_available": True},
            "operator_acceptance": "PENDING",
            "boundaries": {"ssh": False, "tgw_prod": False, "remote_provision": False,
                           "hidden_approval": False, "admission": False,
                           "actor_fleet": False, "memory": False, "provider_effect": False},
        })
    finally:
        lock.close()


StageHandler = Callable[[dict[str, Any]], Mapping[str, Any]]


def stage_idempotency_key(record: Mapping[str, Any], stage: str) -> str:
    """Stable effect identity shared by every replay of one bound stage."""
    return _hash({
        "root_id": record["root_id"], "stage": stage,
        "binding": record["binding"],
    })


def validate_stage_result(record: Mapping[str, Any], stage: str,
                          value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject receipts that could belong to another root, stage, or replay key."""
    result = dict(value)
    outcome = result.get("outcome")
    allowed = {"satisfied", "waiting", "publication_unavailable", *FAILURE_OUTCOMES}
    if outcome not in allowed:
        raise LifecycleError(f"coding lifecycle stage {stage} returned an invalid outcome")
    expected = stage_idempotency_key(record, stage)
    supplied = result.get("idempotency_key")
    if supplied is not None and supplied != expected:
        raise LifecycleError(f"coding lifecycle stage {stage} receipt binding mismatch")
    result["idempotency_key"] = expected
    if outcome == "satisfied":
        result.setdefault("effect_key", stage)
    return result


def advance(store: LifecycleStore, identity: str,
            handlers: Mapping[str, StageHandler]) -> dict[str, Any]:
    """Advance until waiting or terminal; safe to call after any restart/event replay."""
    lock = store.locked(identity)
    try:
        record = store.get(identity)
        if record is None:
            raise LifecycleError("coding lifecycle does not exist")
        while record["state"] not in TERMINAL:
            stage = record["stage"]
            if stage not in STAGES:
                raise LifecycleError("coding lifecycle stage is invalid")
            prior = record["stages"].get(stage)
            if isinstance(prior, dict) and prior.get("outcome") == "satisfied":
                index = STAGES.index(stage) + 1
                if index == len(STAGES):
                    record["state"] = "SUCCEEDED"
                    break
                record["stage"] = STAGES[index]
                continue
            handler = handlers.get(stage)
            if handler is None:
                record["state"] = "REMEDIATION_REQUIRED"
                record["failure"] = {"stage": stage, "reason": "stage handler unavailable"}
                break
            # Persist the intent before invoking an effect adapter.  After a crash the
            # adapter receives the same key and must read back/reuse its immutable
            # receipt rather than repeat the effect.
            intent = prior if isinstance(prior, dict) else None
            if not intent or intent.get("status") != "executing":
                intent = {
                    "status": "executing", "outcome": "waiting",
                    "idempotency_key": stage_idempotency_key(record, stage),
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
                record["stages"][stage] = intent
                record["state"] = "RUNNING"
                record = store.put(record)
            result = validate_stage_result(record, stage, handler(dict(record)))
            outcome = result.get("outcome")
            if outcome == "waiting":
                record["state"] = "WAITING"
                record["stages"][stage] = result
                break
            if outcome == "resumable_partial":
                record["state"] = "RESUMABLE_PARTIAL"
                record["stages"][stage] = result
                break
            if outcome == "publication_unavailable":
                if stage != "terminal_publication":
                    raise LifecycleError("publication-unavailable is invalid outside publication")
                attempts = int(record["publication"].get("attempts", 0)) + 1
                record["publication"].update({
                    "attempted": True, "attempts": attempts,
                    "last_error": result.get("reason", "Context unavailable"),
                    "retry_available": attempts < 2,
                })
                if attempts < 2:
                    record["state"] = "WAITING"
                    record["stages"][stage] = result
                    break
                # Context is optional orientation.  A second compact controlled
                # failure is retained as evidence and cannot block local closure.
                result = {**result, "outcome": "satisfied", "context_published": False}
                outcome = "satisfied"
            if outcome != "satisfied":
                record["state"] = "FAILED" if outcome == "failed" else "REMEDIATION_REQUIRED"
                record["stages"][stage] = result
                record["failure"] = {"stage": stage, "reason": result.get("reason", outcome)}
                break
            effect_key = result.get("effect_key")
            if effect_key:
                old = record["effects"].get(effect_key)
                receipt = result.get("receipt")
                if old is not None and old != receipt:
                    raise LifecycleError("duplicate lifecycle effect has conflicting receipt")
                record["effects"][effect_key] = receipt
            for job_id in result.get("job_ids", []):
                if job_id not in record["job_ids"]:
                    record["job_ids"].append(job_id)
            record["stages"][stage] = result
            if stage == "terminal_publication":
                record["publication"].update({
                    "attempted": True,
                    "attempts": max(1, int(record["publication"].get("attempts", 0))),
                    "retry_available": False,
                    "published": result.get("context_published", True),
                })
            record["state"] = "RUNNING"
            index = STAGES.index(stage) + 1
            if index == len(STAGES):
                record["state"] = "SUCCEEDED"
                break
            record["stage"] = STAGES[index]
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return store.put(record)
    finally:
        lock.close()


def spawn(identity: str, *, config_path: Path | str) -> int:
    """Detach a recovery-capable local supervisor from the requesting client."""
    process = subprocess.Popen(
        [sys.executable, "-m", "tgw.development.coding_lifecycle", "--resume", identity,
         "--config", str(config_path)], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        close_fds=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return process.pid


def spawn_pending(store: LifecycleStore, *, config_path: Path | str) -> list[dict[str, Any]]:
    """Reconstruct local supervisors after a Foreman/process/host restart.

    The per-root supervisor lock is the duplicate-event fence.  A spawned process
    that loses the race exits without touching the journal.
    """
    result = []
    for record in store.records():
        if record["state"] in TERMINAL:
            continue
        result.append({"root_id": record["root_id"], "pid": spawn(
            record["root_id"], config_path=config_path,
        )})
    return result


def run_supervisor(identity: str, *, config_path: Path | str,
                   poll_interval: float = 2.0) -> dict[str, Any] | None:
    """Own one root until terminal, including durable WAITING transitions."""
    from tgw.coding_cli import supervise
    from tgw.development.local_workflow import load_config

    config = load_config(config_path)
    store = LifecycleStore(config["coding"]["lifecycle_root"])
    owner = store.supervisor_lock(identity)
    if owner is None:
        return None
    try:
        while True:
            record = supervise(identity, config_path=Path(config_path))
            if record["state"] in TERMINAL:
                return record
            time.sleep(max(0.05, poll_interval))
    finally:
        owner.close()


def main() -> int:
    # Runtime stage adapters are intentionally owned by coding_cli so the journal
    # cannot become a second dispatcher.  Importing here also keeps unit tests pure.
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run_supervisor(args.resume, config_path=Path(args.config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
