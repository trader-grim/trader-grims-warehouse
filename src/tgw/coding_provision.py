"""Native queue-backed, receipt-addressed coding provision requests.

The queue state machine is the execution authority.  This module only maps
the HTTP/CLI request shape onto a generation-bound queue job and validates the
location identity again before a local worker can obtain its lease.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any, Callable

from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure
from tgw.workers.coding import DEFAULT_REPOSITORY_ROOT, validated_coding_worktree
from tgw.workflow.foreman import ForemanConfig, tick

QUEUE_NAME = "coding-provision"
UNKNOWN = "unknown"


def _coding(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("coding", {})
    if not isinstance(value, dict):
        raise HardFailure("coding configuration must be an object")
    return value


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def location_identity(todo_id: int, worktree_value: str, coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    """Produce the complete immutable location envelope for one request."""
    worktree = validated_coding_worktree(worktree_value, worktree_value, coding)
    repository_value = coding.get("repository_root", DEFAULT_REPOSITORY_ROOT)
    if not isinstance(repository_value, (str, Path)):
        raise HardFailure("coding.repository_root must be a path")
    repository = Path(repository_value).resolve()
    try:
        branch_probe = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree,
            check=False, text=True, capture_output=True,
        )
        head_probe = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=worktree,
            check=False, text=True, capture_output=True,
        )
    except OSError as exc:
        raise HardFailure("coding location Git identity is unavailable") from exc
    branch = branch_probe.stdout.strip()
    head = head_probe.stdout.strip()
    if branch_probe.returncode or head_probe.returncode or not branch or len(head) != 40:
        raise HardFailure("coding location Git identity is invalid")
    if branch == "HEAD":
        raise HardFailure("coding location envelope rejects detached worktree")
    return {
        "repository_root": str(repository), "worktree": str(worktree), "todo_id": todo_id,
        "branch": branch, "head": head, "worker_identity": worker_identity,
    }


def _location_envelope(todo_id: int, worktree: str, coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    location = location_identity(todo_id, worktree, coding, worker_identity)
    return {"location": location, "envelope_hash": _hash(location)}


def _document(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload_json")
    if not isinstance(payload, dict) or payload.get("kind") != "coding-provision/v1":
        raise HardFailure("coding provision job payload is invalid")
    document = dict(payload)
    document["request_id"] = str(job["job_id"])
    document["state"] = str(job.get("state", UNKNOWN))
    result = job.get("result")
    if result is None and isinstance(payload.get("result"), dict):
        result = payload["result"]
    document["receipt"] = result.get("receipt") if isinstance(result, dict) else None
    return document


def create_request(config: dict[str, Any], *, todo_id: int, worktree: str, object_generation: str) -> dict[str, Any]:
    """Enqueue exactly one generation/location-bound native queue job."""
    coding = _coding(config)
    host, worker_identity = coding.get("host"), coding.get("worker_identity")
    if todo_id <= 0 or not isinstance(worktree, str) or not worktree:
        raise HardFailure("coding provision request needs a positive todo_id and worktree")
    if not isinstance(object_generation, str) or not object_generation:
        raise HardFailure("coding provision request needs object_generation")
    if not isinstance(host, str) or not host or not isinstance(worker_identity, str) or not worker_identity:
        raise HardFailure("coding.host and coding.worker_identity must be configured")
    envelope = _location_envelope(todo_id, worktree, coding, worker_identity)
    payload = {
        "kind": "coding-provision/v1", "todo_id": todo_id, "object_generation": object_generation,
        "host": host, "worker_identity": worker_identity, **envelope,
    }
    job_id = state_machine.enqueue_job(
        QUEUE_NAME, payload, entity_type="coding_provision", entity_id=str(todo_id),
        handler_family=QUEUE_NAME,
        dedupe_key=f"coding-provision:{todo_id}:{object_generation}:{envelope['envelope_hash']}",
        max_attempts=1,
    )
    return get_request(config, job_id)


def get_request(_config: dict[str, Any], request_id: str) -> dict[str, Any]:
    job = state_machine.get_job(request_id)
    if job is None or job.get("queue_name", QUEUE_NAME) != QUEUE_NAME:
        raise HardFailure("coding provision request does not exist")
    return _document(job)


def stop_request(config: dict[str, Any], request_id: str) -> dict[str, Any]:
    document = get_request(config, request_id)
    if document["state"] not in {"succeeded", "failed", "dead_letter", "cancelled"}:
        receipt = {"receipt_id": str(uuid.uuid4()), "receipt_source": f"queue-job:{request_id}", "outcome": "stopped"}
        state_machine.cancel_job(request_id, "coding provision stopped", {"receipt": receipt})
        document = get_request(config, request_id)
    document["receipt"] = document["receipt"] or {"receipt_source": f"queue-job:{request_id}", "outcome": "stopped"}
    return document


def access_status(config: dict[str, Any], request_id: str | None = None) -> dict[str, str]:
    coding = _coding(config)
    receipt_source = UNKNOWN
    if request_id:
        receipt = get_request(config, request_id).get("receipt")
        if isinstance(receipt, dict) and isinstance(receipt.get("receipt_source"), str):
            receipt_source = receipt["receipt_source"]
    return {"endpoint": coding.get("api_endpoint") if isinstance(coding.get("api_endpoint"), str) else UNKNOWN,
            "role": coding.get("role") if isinstance(coding.get("role"), str) else UNKNOWN,
            "coding_host": coding.get("host") if isinstance(coding.get("host"), str) else UNKNOWN,
            "worker_identity": coding.get("worker_identity") if isinstance(coding.get("worker_identity"), str) else UNKNOWN,
            "receipt_source": receipt_source, "provider_status": UNKNOWN}


def _validate_before_claim(document: dict[str, Any], coding: dict[str, Any], local_host: str, worker_identity: str) -> None:
    if local_host != coding.get("host") or worker_identity != coding.get("worker_identity"):
        raise HardFailure("local coding worker identity does not match configured envelope")
    if document.get("host") != local_host or document.get("worker_identity") != worker_identity:
        raise HardFailure("coding provision request envelope does not match local worker")
    location = document.get("location")
    if not isinstance(location, dict) or document.get("envelope_hash") != _hash(location):
        raise HardFailure("coding provision envelope hash is invalid")
    expected = _location_envelope(int(document.get("todo_id", 0)), str(location.get("worktree", "")), coding, worker_identity)
    if expected != {"location": location, "envelope_hash": document.get("envelope_hash")}:
        raise HardFailure("coding provision location envelope does not match local worktree")


def claim_and_run(config: dict[str, Any], *, request_id: str, local_host: str, worker_identity: str,
                  provision: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """Validate before atomically leasing, then finish under the lease token."""
    coding = _coding(config)
    document = get_request(config, request_id)
    if document["state"] != "queued":
        raise HardFailure("coding provision request is not claimable")
    _validate_before_claim(document, coding, local_host, worker_identity)
    job = state_machine.claim_job(request_id, worker_identity)
    if job is None:
        raise HardFailure("coding provision request is not claimable")
    token = str(job.get("lease_token") or "")
    if not token:
        raise HardFailure("native queue claim returned no lease token")
    state_machine.start_claimed_job(request_id, worker_identity, token)
    try:
        result = provision(document) if provision else {"foreman": tick(ForemanConfig(coding_config=coding), todo_ids={int(document["todo_id"])}).__dict__}
        if not isinstance(result, dict):
            raise HardFailure("coding provision returned no structured result")
        receipt = {"receipt_id": str(uuid.uuid4()), "receipt_source": f"queue-job:{request_id}",
                   "worker_identity": worker_identity, "location": document["location"],
                   "envelope_hash": document["envelope_hash"], "object_generation": document["object_generation"],
                   "outcome": "succeeded", "result": result}
        state_machine.succeed_claimed_job(request_id, worker_identity, token, {"receipt": receipt})
        return get_request(config, request_id)
    except Exception as exc:
        state_machine.fail_claimed_job(request_id, worker_identity, token, str(exc))
        raise
