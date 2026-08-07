"""One-shot HTTP worker for a canonical coding provision request.

This process deliberately has neither a PostgreSQL dependency nor an import of
the queue state machine.  tgw-prod owns every durable transition.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tgw.coding_execution import DEFAULT_REPOSITORY_ROOT, execute_authorized_treatment, execution_envelope, validated_coding_worktree
from tgw.config import DEFAULT_CONFIG, load_coding_worker_config, validate_worker_execution_config
from tgw.errors import HardFailure
from tgw.workflow.coding_snapshot import build_coding_snapshot, serialize_snapshot
from tgw.workflow.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.workflow.treatments import CODING_TREATMENTS


def _coding(config: dict[str, Any]) -> dict[str, Any]:
    coding = config.get("coding", {})
    if not isinstance(coding, dict):
        raise HardFailure("coding configuration must be an object")
    return coding


def _hash(value: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def local_location_identity(todo_id: int, worktree_value: str, coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    worktree = validated_coding_worktree(worktree_value, worktree_value, coding)
    repository = Path(coding.get("repository_root", DEFAULT_REPOSITORY_ROOT)).resolve()
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree, check=False, text=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=worktree, check=False, text=True, capture_output=True)
    if branch.returncode or head.returncode or branch.stdout.strip() == "HEAD" or len(head.stdout.strip()) != 40:
        raise HardFailure("coding location Git identity is invalid")
    return {"repository_root": str(repository), "worktree": str(worktree), "todo_id": todo_id, "branch": branch.stdout.strip(), "head": head.stdout.strip(), "worker_identity": worker_identity}


def _validate_before_claim(document: dict[str, Any], coding: dict[str, Any], local_host: str, worker_identity: str) -> dict[str, Any]:
    if local_host != coding.get("host") or worker_identity != coding.get("worker_identity") or document.get("host") != local_host or document.get("worker_identity") != worker_identity:
        raise HardFailure("local coding worker identity does not match configured envelope")
    todo_id = int(document.get("todo_id", 0))
    worktree = Path(coding.get("worktree_root", "")).resolve() / f"todo-{todo_id}"
    location = local_location_identity(todo_id, str(worktree), coding, worker_identity)
    return {"location": location, "envelope_hash": _hash(location)}


class CodingProvisionClient:
    """Small authenticated client for the canonical coding-provision API."""

    def __init__(self, endpoint: str, credential: str, worker_identity: str) -> None:
        if not endpoint.startswith(("http://", "https://")) or not credential or not worker_identity:
            raise HardFailure("coding worker endpoint, credential, and identity are required")
        self.endpoint = endpoint.rstrip("/")
        self.credential = credential
        self.worker_identity = worker_identity

    def _call(self, path: str, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps(body).encode() if body is not None else None
        request = Request(self.endpoint + path, data=payload, method=method)
        request.add_header("X-TGW-Worker-Authorization", f"Bearer {self.credential}")
        request.add_header("X-TGW-Worker-Identity", self.worker_identity)
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with urlopen(request, timeout=15) as response:  # nosec: configured operator endpoint
                value = json.loads(response.read().decode())
        except (HTTPError, URLError, json.JSONDecodeError) as exc:
            raise HardFailure(f"canonical coding service request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise HardFailure("canonical coding service returned a non-object response")
        return value

    def get(self, request_id: str) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}")

    def claim(self, request_id: str, host: str, envelope_hash: str, location: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/claim", "POST", {"host": host, "envelope_hash": envelope_hash, "location": location, "snapshot": snapshot})

    def start(self, request_id: str, lease_token: str) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/start", "POST", {"lease_token": lease_token})

    def complete(self, request_id: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/complete", "POST", {"lease_token": lease_token, "result": result})

    def fail(self, request_id: str, lease_token: str, error: str) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/fail", "POST", {"lease_token": lease_token, "error": error[:2000]})


def configured_client(config: dict[str, Any]) -> CodingProvisionClient:
    coding = _coding(config)
    validate_worker_execution_config(coding)
    endpoint = coding.get("worker_api_endpoint", coding.get("api_endpoint"))
    reference = coding.get("worker_credential_env")
    credential = os.environ.get(reference, "")
    return CodingProvisionClient(str(endpoint or ""), credential, str(coding.get("worker_identity") or ""))


def local_snapshot_claim(config: dict[str, Any], worktree: str) -> dict[str, Any]:
    """Create the portable observation which tgw-prod will evaluate."""
    snapshot = build_coding_snapshot(worktree, CODING_READY_FOR_IMPLEMENTATION, CODING_TREATMENTS)
    return serialize_snapshot(snapshot)


def claim_and_run(
    config: dict[str, Any], *, request_id: str, local_host: str, worker_identity: str, provision: Callable[[dict[str, Any]], dict[str, Any]] | None = None, client: CodingProvisionClient | None = None
) -> dict[str, Any]:
    """Retrieve, locally fence, and execute one request through the service API."""
    coding = _coding(config)
    service = client or configured_client(config)
    document = service.get(request_id)
    if document.get("state") != "queued":
        raise HardFailure("coding provision request is not claimable")
    envelope = _validate_before_claim(document, coding, local_host, worker_identity)
    snapshot = local_snapshot_claim(config, envelope["location"]["worktree"])
    claimed = service.claim(request_id, local_host, envelope["envelope_hash"], envelope["location"], snapshot)
    lease_token = claimed.get("lease_token")
    if not isinstance(lease_token, str) or not lease_token:
        raise HardFailure("canonical coding service returned no lease token")
    try:
        authorized = claimed.get("request")
        if not isinstance(authorized, dict):
            raise HardFailure("canonical coding service returned no execution envelope")
        execution = execution_envelope(authorized)
        service.start(request_id, lease_token)
        # Re-attest at the last possible point before any treatment launcher.
        # The service's lease is not evidence that this worktree stayed put.
        current = local_location_identity(
            execution["todo_id"],
            envelope["location"]["worktree"],
            coding,
            worker_identity,
        )
        if current != envelope["location"]:
            raise HardFailure("coding worktree identity changed after claim")
        current_snapshot = local_snapshot_claim(config, current["worktree"])
        if current_snapshot.get("object_id") != current["worktree"] or current_snapshot.get("generation") != execution["object_generation"]:
            raise HardFailure("coding worktree object generation changed after claim")
        if provision:
            result = provision(authorized)
        else:
            result = execute_authorized_treatment(
                config,
                {
                    **execution,
                    "worktree": envelope["location"]["worktree"],
                    "object_id": envelope["location"]["worktree"],
                },
            )
        if not isinstance(result, dict):
            raise HardFailure("coding provision returned no structured result")
        result["execution_identity"] = current
        completed = service.complete(request_id, lease_token, result)
        receipt = completed.get("receipt")
        if not isinstance(receipt, dict) or receipt.get("worker_identity") != worker_identity:
            raise HardFailure("canonical coding service returned an invalid durable receipt")
        if receipt.get("envelope_hash") != envelope["envelope_hash"]:
            raise HardFailure("canonical coding service receipt envelope does not match request")
        if receipt.get("location") != envelope["location"]:
            raise HardFailure("canonical coding service receipt location does not match local envelope")
        if receipt.get("execution") != execution:
            raise HardFailure("canonical coding service receipt execution does not match authorization")
        if receipt.get("receipt_source") != f"queue-job:{request_id}":
            raise HardFailure("canonical coding service receipt source is invalid")
        return completed
    except Exception as exc:
        try:
            service.fail(request_id, lease_token, str(exc))
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-coding-provision-worker")
    parser.add_argument("request_id")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--worker-identity", required=True)
    args = parser.parse_args()
    result = claim_and_run(load_coding_worker_config(Path(args.config)), request_id=args.request_id, local_host=args.host, worker_identity=args.worker_identity)
    print(json.dumps(result.get("receipt"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
