"""Native queue-backed, receipt-addressed coding provision requests.

The queue state machine is the execution authority.  This module maps the
HTTP/CLI request shape onto a generation-bound queue job of request-safe data
only — the canonical service never inspects Git or tgw-lib paths.  The
tgw-lib worker validates/creates its local worktree envelope after claim, the
service records that envelope under the exact lease, and the durable receipt
authors from that same immutable envelope.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path
from typing import Any

from tgw.coding_execution import execution_envelope
from tgw.config import validate_service_request_config
from tgw.queue import state_machine
from tgw.queue.worker_base import HardFailure
from tgw.workers.coding import DEFAULT_REPOSITORY_ROOT, validated_coding_worktree
from tgw.workflow.coding_snapshot import deserialize_snapshot
from tgw.workflow.evaluator import evaluate
from tgw.workflow.foreman import EVALUATOR_VERSION
from tgw.workflow.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow.scheduler import select_treatment
from tgw.workflow.treatments import CODING_TREATMENTS

QUEUE_NAME = "coding-provision"
UNKNOWN = "unknown"


def _todo_lookup(todo_id: int) -> dict[str, Any] | None:
    """Read the canonical Todo model only at the service authority boundary."""
    from tgw.todo import todo_get

    return todo_get(todo_id)


todo_lookup = _todo_lookup


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
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree,
            check=False,
            text=True,
            capture_output=True,
        )
        head_probe = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=worktree,
            check=False,
            text=True,
            capture_output=True,
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
        "repository_root": str(repository),
        "worktree": str(worktree),
        "todo_id": todo_id,
        "branch": branch,
        "head": head,
        "worker_identity": worker_identity,
    }


def _location_envelope(todo_id: int, worktree: str, coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    location = location_identity(todo_id, worktree, coding, worker_identity)
    return {"location": location, "envelope_hash": _hash(location)}


def _resolve_local_envelope(todo_id: int, coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    """Resolve and validate the local tgw-lib worktree envelope for one todo.

    The worker derives the worktree from its configured ``worktree_root`` and
    ``todo_id``, then ``location_identity`` validates it as a real Git worktree
    of the configured repository with a real branch/head.  This runs only on
    tgw-lib, never on the canonical service.
    """
    root_value = coding.get("worktree_root")
    if not isinstance(root_value, (str, Path)) or not str(root_value):
        raise HardFailure("coding.worktree_root must be a path")
    worktree = Path(root_value).resolve() / f"todo-{todo_id}"
    return _location_envelope(todo_id, worktree, coding, worker_identity)


def _document(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload_json")
    if not isinstance(payload, dict) or payload.get("kind") != "coding-provision/v1":
        raise HardFailure("coding provision job payload is invalid")
    document = dict(payload)
    document["request_id"] = str(job["job_id"])
    document["state"] = str(job.get("state", UNKNOWN))
    if isinstance(job.get("error_detail"), str) and job["error_detail"]:
        document["error"] = job["error_detail"]
    result = job.get("result")
    if result is None and isinstance(payload.get("result"), dict):
        result = payload["result"]
    document["receipt"] = result.get("receipt") if isinstance(result, dict) else None
    return document


def _authorize_execution(document: dict[str, Any], location: dict[str, Any], snapshot_claim: object) -> dict[str, Any]:
    """Bind a verified local observation to the canonical Todo/evaluator path."""
    todo_id = document.get("todo_id")
    todo = todo_lookup(todo_id) if isinstance(todo_id, int) else None
    if not isinstance(todo, dict) or todo.get("id") != todo_id or todo.get("done_at") is not None:
        raise HardFailure("canonical Todo is unavailable or no longer open")
    try:
        snapshot = deserialize_snapshot(snapshot_claim)
    except ValueError as exc:
        raise HardFailure(f"verified local coding snapshot is invalid: {exc}") from exc
    if snapshot.object_id != location.get("worktree"):
        raise HardFailure("verified local coding snapshot worktree does not match claim")
    if snapshot.generation != document.get("object_generation"):
        raise HardFailure("verified local coding snapshot generation does not match request")
    graph = evaluate(
        snapshot=snapshot,
        goal=CODING_READY_FOR_IMPLEMENTATION,
        treatments=CODING_TREATMENTS,
        evaluator_version=EVALUATOR_VERSION,
    )
    treatment = select_treatment(graph, CODING_TREATMENTS)
    if treatment is None:
        raise HardFailure("canonical evaluator/scheduler found no dispatchable coding treatment")
    return {
        "todo_id": todo_id,
        "treatment_id": treatment.identity,
        "treatment_version": treatment.version,
        "graph_id": graph.graph_id,
        "object_generation": graph.object_generation,
        "evaluator_version": graph.evaluator_version,
        "evidence_set_hash": graph.evidence_set_hash,
        "treatment_registry_hash": graph.treatment_registry_hash,
    }


def _validate_treatment_result(document: dict[str, Any], result: dict[str, Any]) -> None:
    """Require success evidence to be bound to the evaluated treatment."""
    execution = execution_envelope(document)
    treatment = next(item for item in CODING_TREATMENTS if item.identity == execution["treatment_id"])
    for field in ("treatment_id", "treatment_version", "graph_id", "object_generation"):
        if result.get(field) != execution[field]:
            raise HardFailure(f"coding treatment receipt {field} does not match execution envelope")
    if result.get("receipt_schema_id") != treatment.receipt_schema_id:
        raise HardFailure("coding treatment receipt schema does not match treatment contract")
    if result.get("outcome") != "satisfied":
        raise HardFailure("coding treatment receipt is not a satisfied outcome")
    established = result.get("established_conditions")
    artifacts = result.get("artifacts")
    if (
        not isinstance(established, list)
        or not established
        or not all(isinstance(item, str) for item in established)
        or not set(established).issubset(treatment.may_establish)
        or not isinstance(artifacts, list)
        or not artifacts
    ):
        raise HardFailure("coding treatment receipt lacks required contract evidence")
    if result.get("execution_identity") != document.get("location"):
        raise HardFailure("coding treatment receipt execution identity does not match claim")


def create_request(config: dict[str, Any], *, todo_id: int, object_generation: str) -> dict[str, Any]:
    """Enqueue exactly one generation-bound queue job of request-safe data.

    The canonical service does not know (and must not probe) any tgw-lib-local
    worktree or repository; it records only ``todo_id``, the generation, and the
    target host/worker identity.  The tgw-lib worker resolves and validates its
    local worktree envelope after claim, and the durable receipt authors from
    that envelope.
    """
    coding = _coding(config)
    validate_service_request_config(coding)
    host, worker_identity = coding["host"], coding["worker_identity"]
    if todo_id <= 0:
        raise HardFailure("coding provision request needs a positive todo_id")
    if not isinstance(object_generation, str) or not object_generation:
        raise HardFailure("coding provision request needs object_generation")
    payload = {
        "kind": "coding-provision/v1",
        "todo_id": todo_id,
        "object_generation": object_generation,
        "host": host,
        "worker_identity": worker_identity,
    }
    job_id = state_machine.enqueue_job(
        QUEUE_NAME,
        payload,
        entity_type="coding_provision",
        entity_id=str(todo_id),
        handler_family=QUEUE_NAME,
        dedupe_key=f"coding-provision:{todo_id}:{object_generation}",
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
    if document["state"] == "cancelled":
        document["receipt"] = document["receipt"] or {"receipt_source": f"queue-job:{request_id}", "outcome": "stopped"}
    return document


def access_status(config: dict[str, Any], request_id: str | None = None) -> dict[str, str]:
    coding = _coding(config)
    receipt_source = UNKNOWN
    if request_id:
        receipt = get_request(config, request_id).get("receipt")
        if isinstance(receipt, dict) and isinstance(receipt.get("receipt_source"), str):
            receipt_source = receipt["receipt_source"]
    return {
        "endpoint": coding.get("api_endpoint") if isinstance(coding.get("api_endpoint"), str) else UNKNOWN,
        "role": coding.get("role") if isinstance(coding.get("role"), str) else UNKNOWN,
        "coding_host": coding.get("host") if isinstance(coding.get("host"), str) else UNKNOWN,
        "worker_identity": coding.get("worker_identity") if isinstance(coding.get("worker_identity"), str) else UNKNOWN,
        "receipt_source": receipt_source,
        "provider_status": UNKNOWN,
    }


def _validate_before_claim(document: dict[str, Any], coding: dict[str, Any], local_host: str, worker_identity: str) -> dict[str, Any]:
    """Fence the local worker and compute its immutable local worktree envelope.

    Returns the ``{location, envelope_hash}`` envelope the worker echoes when it
    claims; the canonical service cannot probe the tgw-lib worktree itself.
    """
    if local_host != coding.get("host") or worker_identity != coding.get("worker_identity"):
        raise HardFailure("local coding worker identity does not match configured envelope")
    if document.get("host") != local_host or document.get("worker_identity") != worker_identity:
        raise HardFailure("coding provision request envelope does not match local worker")
    return _resolve_local_envelope(int(document.get("todo_id", 0)), coding, worker_identity)


def _validate_service_worker(document: dict[str, Any], coding: dict[str, Any], local_host: str, worker_identity: str, envelope_hash: str, location: dict[str, Any]) -> None:
    """Validate the worker-echoed envelope against request-safe facts only.

    The service never probes (or mounts) the tgw-lib worktree; it proves the
    envelope is self-consistent and bound to this exact request, todo, and
    worker.  The worker is the party that validated the local Git identity.
    """
    if local_host != coding.get("host") or worker_identity != coding.get("worker_identity"):
        raise HardFailure("coding worker does not match configured service identity")
    if document.get("host") != local_host or document.get("worker_identity") != worker_identity:
        raise HardFailure("coding provision request envelope does not match worker")
    if not isinstance(envelope_hash, str) or not envelope_hash or not isinstance(location, dict):
        raise HardFailure("coding provision envelope is invalid")
    if _hash(location) != envelope_hash:
        raise HardFailure("coding provision envelope hash is invalid")
    if location.get("todo_id") != document.get("todo_id"):
        raise HardFailure("coding provision envelope does not match request todo_id")
    if location.get("worker_identity") != worker_identity:
        raise HardFailure("coding provision envelope worker identity is invalid")
    head = location.get("head")
    branch = location.get("branch")
    if not isinstance(head, str) or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise HardFailure("coding provision envelope head is invalid")
    if not isinstance(branch, str) or not branch or branch == "HEAD":
        raise HardFailure("coding provision envelope branch is invalid")


def claim_request(config: dict[str, Any], *, request_id: str, local_host: str, worker_identity: str, envelope_hash: str, location: dict[str, Any], snapshot: object) -> dict[str, Any]:
    """Lease a request and record the worker-validated local envelope.

    The worker validates its local tgw-lib worktree and echoes the immutable
    envelope; the service records it under the exact lease so the durable
    receipt later authors from that same envelope.
    """
    coding = _coding(config)
    document = get_request(config, request_id)
    if document["state"] != "queued":
        raise HardFailure("coding provision request is not claimable")
    _validate_service_worker(document, coding, local_host, worker_identity, envelope_hash, location)
    job = state_machine.claim_job(request_id, worker_identity)
    if job is None:
        raise HardFailure("coding provision request is not claimable")
    token = str(job.get("lease_token") or "")
    if not token:
        raise HardFailure("canonical queue claim returned no lease token")
    try:
        execution = _authorize_execution(document, location, snapshot)
        envelope = {
            "location": location,
            "envelope_hash": envelope_hash,
            "snapshot": snapshot,
            "execution": execution,
        }
        if state_machine.record_claim_envelope(request_id, worker_identity, token, envelope) is None:
            raise HardFailure("canonical coding execution envelope was not recorded under the lease")
    except Exception as exc:
        state_machine.fail_claimed_job(request_id, worker_identity, token, str(exc))
        raise
    return {"lease_token": token, "request": get_request(config, request_id)}


def start_request(config: dict[str, Any], *, request_id: str, worker_identity: str, lease_token: str) -> dict[str, Any]:
    """Start an exact canonical lease; token validation occurs in the queue."""
    if worker_identity != _coding(config).get("worker_identity") or not lease_token:
        raise HardFailure("coding worker identity or lease token is invalid")
    execution_envelope(get_request(config, request_id))
    state_machine.start_claimed_job(request_id, worker_identity, lease_token)
    return get_request(config, request_id)


def complete_request(config: dict[str, Any], *, request_id: str, worker_identity: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
    """Persist a service-authored receipt under an exact canonical lease."""
    coding = _coding(config)
    document = get_request(config, request_id)
    if worker_identity != coding.get("worker_identity") or not lease_token:
        raise HardFailure("coding worker identity or lease token is invalid")
    if not isinstance(result, dict):
        raise HardFailure("coding provision returned no structured result")
    _validate_treatment_result(document, result)
    receipt = {
        "receipt_id": str(uuid.uuid4()),
        "receipt_source": f"queue-job:{request_id}",
        "worker_identity": worker_identity,
        "location": document["location"],
        "envelope_hash": document["envelope_hash"],
        "object_generation": document["object_generation"],
        "execution": execution_envelope(document),
        "outcome": "succeeded",
        "result": result,
    }
    state_machine.succeed_claimed_job(request_id, worker_identity, lease_token, {"receipt": receipt})
    return get_request(config, request_id)


def fail_request(config: dict[str, Any], *, request_id: str, worker_identity: str, lease_token: str, error: str) -> dict[str, Any]:
    """Fail a canonical lease without giving the worker database access."""
    if worker_identity != _coding(config).get("worker_identity") or not lease_token:
        raise HardFailure("coding worker identity or lease token is invalid")
    state_machine.fail_claimed_job(request_id, worker_identity, lease_token, str(error))
    return get_request(config, request_id)
