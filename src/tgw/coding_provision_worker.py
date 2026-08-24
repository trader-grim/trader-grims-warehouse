"""One-shot HTTP worker for a canonical coding provision request.

This process deliberately has neither a PostgreSQL dependency nor an import of
the queue state machine.  tgw-prod owns every durable transition.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from tgw.coding_execution import DEFAULT_REPOSITORY_ROOT, execute_authorized_treatment, execution_envelope, validated_coding_worktree
from tgw.config import (
    DEFAULT_CONFIG,
    load_coding_worker_config,
    validate_worker_endpoint,
    validate_worker_execution_config,
)
from tgw.errors import HardFailure, TreatmentFailure
from tgw.development.coding_snapshot import build_coding_snapshot, serialize_snapshot
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.development.treatments import CODING_TREATMENTS


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
    if branch.returncode or head.returncode or len(head.stdout.strip()) != 40:
        raise HardFailure("coding location Git identity is invalid")
    return {"repository_root": str(repository), "worktree": str(worktree), "todo_id": todo_id, "branch": branch.stdout.strip(), "head": head.stdout.strip(), "worker_identity": worker_identity}


def _request_worktree(todo_id: int, request_id: str, root: Path) -> tuple[Path, str]:
    """Return the only worktree and branch a request is permitted to use."""
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", request_id):
        raise HardFailure("coding provision request id is unsafe for a worktree")
    token = f"todo-{todo_id}-{request_id}"
    return root / token, f"coding/{token}"


def _git_head(repository: Path) -> str:
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--show-toplevel", "--verify", "HEAD"],
            cwd=repository,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise HardFailure("coding repository Git identity is invalid") from exc
    values = probe.stdout.splitlines()
    head = values[1] if len(values) == 2 else ""
    if probe.returncode or len(values) != 2 or Path(values[0]).resolve() != repository or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise HardFailure("coding repository Git HEAD is invalid")
    return head


def _verified_source_commit(repository: Path, requested: object) -> str:
    """Resolve only an exact commit already present in the registered repo."""
    if requested is None:
        return _git_head(repository)
    if not isinstance(requested, str) or re.fullmatch(r"[0-9a-f]{40}", requested) is None:
        raise HardFailure("coding request source_commit is invalid")
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{requested}^{{commit}}"],
        cwd=repository,
        check=False,
        text=True,
        capture_output=True,
    )
    if probe.returncode:
        raise HardFailure("coding request source_commit is absent from the registered repository")
    return requested


def _require_clean_worktree(worktree: Path) -> None:
    """Refuse to resume a request worktree with any uncommitted state."""
    probe = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=worktree,
        check=False,
        text=True,
        capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    if probe.returncode or probe.stdout:
        raise HardFailure("existing request-bound coding worktree is not clean")


def _remove_incomplete_worktree(repository: Path, worktree: Path, branch: str) -> None:
    """Remove only a just-created request-bound worktree and its exact branch."""
    if worktree.is_symlink() or worktree.parent != worktree.parent.resolve():
        return
    subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repository, check=False, text=True, capture_output=True)
    if worktree.exists() and worktree.is_dir() and not worktree.is_symlink():
        shutil.rmtree(worktree)
    if not worktree.exists():
        subprocess.run(["git", "branch", "--delete", "--force", branch], cwd=repository, check=False, text=True, capture_output=True)


def _prepare_request_worktree(document: dict[str, Any], coding: dict[str, Any], worker_identity: str) -> dict[str, Any]:
    """Create or attest the exact request-bound worktree before a claim."""
    todo_id = int(document.get("todo_id", 0))
    request_id = document.get("request_id")
    root_value = coding.get("worktree_root")
    if not isinstance(root_value, str) or not root_value.strip():
        raise HardFailure("coding worktree root is not configured")
    root = Path(root_value).resolve()
    if not root.is_dir():
        raise HardFailure(f"coding worktree root is unavailable: {root}")
    repository_value = coding.get("repository_root", DEFAULT_REPOSITORY_ROOT)
    if not isinstance(repository_value, str) or not repository_value.strip():
        raise HardFailure("coding repository root is not configured")
    repository = Path(repository_value).resolve()
    worktree, branch = _request_worktree(todo_id, request_id, root)
    requested_source = document.get("source_commit")
    source_head = None if (worktree.exists() and requested_source is None) else _verified_source_commit(repository, requested_source)
    created = False
    try:
        if worktree.exists() or worktree.is_symlink():
            if worktree.is_symlink():
                raise HardFailure("request-bound coding worktree must not be a symlink")
        else:
            added = subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(worktree), source_head],
                cwd=repository,
                check=False,
                text=True,
                capture_output=True,
            )
            if added.returncode:
                raise HardFailure(f"failed to create request-bound coding worktree: {added.stderr.strip()}")
            created = True
        # This validates direct-child placement, common Git dir, and both Git
        # top levels.  An existing target is resumed only after this attestation.
        validated_coding_worktree(str(worktree), str(worktree), coding)
        location = local_location_identity(todo_id, str(worktree), coding, worker_identity)
        if location["worktree"] != str(worktree) or location["branch"] != branch:
            raise HardFailure("request-bound coding worktree branch is invalid")
        if source_head is None:
            source_head = _verified_source_commit(repository, location["head"])
        if location["head"] != source_head:
            raise HardFailure("request-bound coding worktree HEAD is invalid")
        if not created:
            _require_clean_worktree(worktree)
            expected_generation = document.get("object_generation")
            if expected_generation is not None:
                if not isinstance(expected_generation, str) or not expected_generation:
                    raise HardFailure("existing request-bound coding worktree has invalid expected object generation")
                snapshot = local_snapshot_claim({"coding": coding}, str(worktree))
                if snapshot.get("generation") != expected_generation:
                    raise HardFailure("existing request-bound coding worktree generation does not match request")
        return location
    except Exception:
        if created:
            _remove_incomplete_worktree(repository, worktree, branch)
        raise


def _development_allocations(
    document: dict[str, Any],
    coding: dict[str, Any],
) -> list[tuple[dict[str, Any], Path]]:
    lifecycle = document.get("lifecycle")
    cards = lifecycle.get("launch_cards") if isinstance(lifecycle, dict) else None
    if not isinstance(cards, list) or not cards:
        raise HardFailure("development request has no exact card allocations")
    root = PurePosixPath(str(coding.get("development_worktree_root", "/opt/TGW/w/attempts")))
    observed: list[tuple[dict[str, Any], Path]] = []
    identities: set[tuple[str, str, str]] = set()
    for card in cards:
        allocation = card.get("allocation") if isinstance(card, dict) else None
        raw = allocation.get("worktree") if isinstance(allocation, dict) else None
        attempt_id = allocation.get("attempt_id") if isinstance(allocation, dict) else None
        attempt_root = allocation.get("attempt_root") if isinstance(allocation, dict) else None
        if not all(isinstance(value, str) and value for value in (raw, attempt_id, attempt_root)):
            raise HardFailure("development request has an incomplete card allocation")
        parsed = PurePosixPath(raw)
        try:
            relative = parsed.relative_to(root)
        except ValueError as exc:
            raise HardFailure("development worktree is outside the configured allocation root") from exc
        if (
            not parsed.is_absolute()
            or ".." in parsed.parts
            or len(relative.parts) != 3
            or relative.parts[-1] != "worktree"
            or relative.parts[-2] != attempt_id
            or not PurePosixPath(attempt_root).is_absolute()
            or PurePosixPath(attempt_root).name != attempt_id
        ):
            raise HardFailure("development worktree allocation is unsafe")
        identity = (attempt_id, raw, attempt_root)
        if identity in identities:
            raise HardFailure("development card allocations are not unique")
        identities.add(identity)
        observed.append((card, Path(raw)))
    return observed


def _development_worktree(document: dict[str, Any], coding: dict[str, Any]) -> tuple[Path, str]:
    allocations = _development_allocations(document, coding)
    raw = allocations[0][1]
    request_hash = document.get("development_request_hash")
    if not isinstance(request_hash, str) or not request_hash.startswith("sha256:"):
        raise HardFailure("development request hash is invalid")
    return raw, "development/" + request_hash.removeprefix("sha256:")[:20] + "-001"


def _prepare_development_worktree(
    document: dict[str, Any],
    coding: dict[str, Any],
    worker_identity: str,
) -> dict[str, Any]:
    repository = Path(str(coding.get("repository_root", DEFAULT_REPOSITORY_ROOT))).resolve()
    worktree, branch = _development_worktree(document, coding)
    source = _verified_source_commit(repository, document.get("source_commit"))
    created = False
    try:
        if worktree.exists() or worktree.is_symlink():
            if worktree.is_symlink():
                raise HardFailure("development worktree must not be a symlink")
            _require_clean_worktree(worktree)
        else:
            worktree.parent.mkdir(parents=True, exist_ok=True)
            added = subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(worktree), source],
                cwd=repository,
                check=False,
                text=True,
                capture_output=True,
            )
            if added.returncode:
                raise HardFailure(f"failed to create development worktree: {added.stderr.strip()}")
            created = True
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=worktree,
            check=False,
            text=True,
            capture_output=True,
        )
        current_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=worktree,
            check=False,
            text=True,
            capture_output=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=worktree,
            check=False,
            text=True,
            capture_output=True,
        )
        if top.returncode or current_branch.returncode or head.returncode or top.stdout.strip() != str(worktree) or current_branch.stdout.strip() != branch or head.stdout.strip() != source:
            raise HardFailure("development worktree Git identity is invalid")
        return {
            "repository_root": str(repository),
            "worktree": str(worktree),
            "request_hash": document["development_request_hash"],
            "branch": branch,
            "head": source,
            "worker_identity": worker_identity,
        }
    except Exception:
        if created:
            _remove_incomplete_worktree(repository, worktree, branch)
        raise


def _validate_before_claim(document: dict[str, Any], coding: dict[str, Any], local_host: str, worker_identity: str) -> dict[str, Any]:
    if local_host != coding.get("host") or worker_identity != coding.get("worker_identity") or document.get("host") != local_host or document.get("worker_identity") != worker_identity:
        raise HardFailure("local coding worker identity does not match configured envelope")
    if document.get("kind") == "coding-provision/v2":
        worktree, _branch = _development_worktree(document, coding)
        attempt_created = not worktree.exists() and not worktree.is_symlink()
        location = _prepare_development_worktree(document, coding, worker_identity)
        return {"location": location, "envelope_hash": _hash(location), "attempt_created": attempt_created}
    root = Path(str(coding.get("worktree_root", ""))).resolve()
    worktree, _branch = _request_worktree(int(document.get("todo_id", 0)), document.get("request_id"), root)
    attempt_created = not worktree.exists() and not worktree.is_symlink()
    location = _prepare_request_worktree(document, coding, worker_identity)
    return {"location": location, "envelope_hash": _hash(location), "attempt_created": attempt_created}


class CodingProvisionClient:
    """Small authenticated client for the canonical coding-provision API."""

    def __init__(self, endpoint: str, credential: str, worker_identity: str) -> None:
        try:
            validate_worker_endpoint(endpoint)
        except ValueError as exc:
            raise HardFailure(str(exc)) from exc
        if not credential or not worker_identity:
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
        except HTTPError as exc:
            if method == "POST" and path.endswith("/claim") and exc.code == 409:
                raise DefinitiveClaimRejected(f"canonical coding service rejected claim: {exc}") from exc
            raise HardFailure(f"canonical coding service request failed: {exc}") from exc
        except (URLError, json.JSONDecodeError) as exc:
            raise HardFailure(f"canonical coding service request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise HardFailure("canonical coding service returned a non-object response")
        return value

    def get(self, request_id: str) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}")

    def next(self) -> dict[str, Any]:
        return self._call("/api/coding/worker/requests/next")

    def claim(self, request_id: str, host: str, envelope_hash: str, location: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/claim", "POST", {"host": host, "envelope_hash": envelope_hash, "location": location, "snapshot": snapshot})

    def start(self, request_id: str, lease_token: str) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/start", "POST", {"lease_token": lease_token})

    def complete(self, request_id: str, lease_token: str, result: dict[str, Any]) -> dict[str, Any]:
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/complete", "POST", {"lease_token": lease_token, "result": result})

    def fail(
        self,
        request_id: str,
        lease_token: str,
        error: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"lease_token": lease_token, "error": error[:2000]}
        if result is not None:
            body["result"] = result
        return self._call(f"/api/coding/worker/requests/{quote(request_id, safe='')}/fail", "POST", body)


def configured_client(config: dict[str, Any]) -> CodingProvisionClient:
    coding = _coding(config)
    validate_worker_execution_config(coding)
    endpoint = coding.get("worker_api_endpoint", coding.get("api_endpoint"))
    reference = coding.get("worker_credential_env")
    credential = os.environ.get(reference, "")
    return CodingProvisionClient(str(endpoint or ""), credential, str(coding.get("worker_identity") or ""))


def local_snapshot_claim(
    config: dict[str, Any],
    worktree: str,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Create the portable observation which tgw-prod will evaluate."""
    snapshot = build_coding_snapshot(
        worktree,
        CODING_READY_FOR_IMPLEMENTATION,
        CODING_TREATMENTS,
        implementation_baseline_commit=source_commit,
    )
    return serialize_snapshot(snapshot)


class DefinitiveClaimRejected(HardFailure):
    """The canonical service completed a claim request without leasing it."""


def _discard_definitively_rejected_attempt(
    document: dict[str, Any],
    envelope: dict[str, Any],
    coding: dict[str, Any],
) -> None:
    """Remove only this invocation's exact attempt after a completed 409."""
    if not envelope.get("attempt_created"):
        return
    repository = Path(str(coding.get("repository_root", DEFAULT_REPOSITORY_ROOT))).resolve()
    if document.get("kind") == "coding-provision/v2":
        expected, branch = _development_worktree(document, coding)
        actual = Path(str(envelope.get("location", {}).get("worktree", "")))
        if actual == expected:
            _remove_incomplete_worktree(repository, actual, branch)
        return
    root = Path(str(coding.get("worktree_root", ""))).resolve()
    expected, branch = _request_worktree(
        int(document.get("todo_id", 0)),
        document.get("request_id"),
        root,
    )
    actual = Path(str(envelope.get("location", {}).get("worktree", ""))).resolve()
    if actual == expected:
        _remove_incomplete_worktree(repository, actual, branch)


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
    development = document.get("kind") == "coding-provision/v2"
    snapshot = (
        None
        if development
        else local_snapshot_claim(
            config,
            envelope["location"]["worktree"],
            document.get("source_commit"),
        )
    )
    try:
        claimed = service.claim(request_id, local_host, envelope["envelope_hash"], envelope["location"], snapshot)
        lease_token = claimed.get("lease_token") if isinstance(claimed, dict) else None
        if not isinstance(lease_token, str) or not lease_token:
            raise HardFailure("canonical coding service returned no lease token")
    except DefinitiveClaimRejected:
        _discard_definitively_rejected_attempt(document, envelope, coding)
        raise
    except Exception:
        # A transport error cannot prove the service did not commit the claim.
        # Preserve the exact clean request-bound worktree; a queued retry can
        # safely re-attest and reuse it, while a committed lease still owns it.
        raise
    try:
        authorized = claimed.get("request")
        if not isinstance(authorized, dict):
            raise HardFailure("canonical coding service returned no execution envelope")
        execution = authorized.get("execution") if development else execution_envelope(authorized)
        if development and (not isinstance(execution, dict) or execution.get("schema") != "tgw-development-execution/v1"):
            raise HardFailure("canonical coding service returned no development execution envelope")
        service.start(request_id, lease_token)
        # Re-attest at the last possible point before any treatment launcher.
        # The service's lease is not evidence that this worktree stayed put.
        current = (
            _prepare_development_worktree(authorized, coding, worker_identity)
            if development
            else local_location_identity(
                execution["todo_id"],
                envelope["location"]["worktree"],
                coding,
                worker_identity,
            )
        )
        if current != envelope["location"]:
            raise HardFailure("coding worktree identity changed after claim")
        if not development:
            current_snapshot = local_snapshot_claim(config, current["worktree"])
            if current_snapshot.get("object_id") != current["worktree"] or current_snapshot.get("generation") != execution["object_generation"]:
                raise HardFailure("coding worktree object generation changed after claim")
        if provision:
            result = provision(authorized)
        elif development:
            from tgw.development_execution import execute_development_lifecycle

            result = execute_development_lifecycle(config, authorized)
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
        if not development:
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
            result = None
            if isinstance(exc, TreatmentFailure):
                current = locals().get("current")
                if isinstance(current, dict):
                    result = {**exc.result, "execution_identity": current}
            if result is None:
                service.fail(request_id, lease_token, str(exc))
            else:
                service.fail(request_id, lease_token, str(exc), result)
        except Exception:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(prog="tgw-coding-provision-worker")
    parser.add_argument("request_id", nargs="?")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--host", default=socket.gethostname())
    parser.add_argument("--worker-identity", required=True)
    args = parser.parse_args()
    config = load_coding_worker_config(Path(args.config))
    request_id = args.request_id
    if request_id is None:
        service = configured_client(config)
        pending = service.next()
        request_id = pending.get("request_id")
        if request_id is None:
            print("null")
            return 0
        if not isinstance(request_id, str) or not request_id:
            raise HardFailure("canonical coding service returned an invalid request id")
        result = claim_and_run(
            config,
            request_id=request_id,
            local_host=args.host,
            worker_identity=args.worker_identity,
            client=service,
        )
    else:
        result = claim_and_run(config, request_id=request_id, local_host=args.host, worker_identity=args.worker_identity)
    print(json.dumps(result.get("receipt"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
