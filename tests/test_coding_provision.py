"""Native-queue coding-provision route and worker regression coverage."""

from __future__ import annotations

import argparse
import ast
import inspect
import io
import json
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from tgw import api, coding_cli, coding_execution, coding_provision, coding_provision_worker, http_server
from tgw.development.coding_snapshot import serialize_snapshot
from tgw.development.foreman import TickResult
from tgw.development.treatments import CODING_TREATMENTS
from tgw.errors import TreatmentFailure
from tgw.queue.worker_base import HardFailure
from tgw.workers import coding as coding_worker
from tgw.workers.coding import CodingWorker
from tgw.workflow_kernel.contracts import EvidenceAssertion, FingerprintResult, ObjectSnapshot


def test_coding_defaults_use_shared_development_repository() -> None:
    expected = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")

    assert coding_execution.DEFAULT_REPOSITORY_ROOT == expected
    assert coding_worker.DEFAULT_REPOSITORY_ROOT == expected
    assert "/opt/TGW/src/trader-grims-warehouse" not in {
        str(coding_execution.DEFAULT_REPOSITORY_ROOT),
        str(coding_worker.DEFAULT_REPOSITORY_ROOT),
    }


class NativeQueue:
    """In-memory contract double for the state-machine job/receipt API."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.enqueues: list[dict] = []
        self.claims = 0
        self.claim_lease_seconds: list[int | None] = []

    def enqueue_job(self, queue_name, payload, **kwargs):
        assert queue_name == coding_provision.QUEUE_NAME
        assert kwargs["dedupe_key"].startswith(("coding-provision:", "development-launch:"))
        if kwargs.get("idempotent"):
            for job_id, job in self.jobs.items():
                prior = next(item for item in self.enqueues if item["dedupe_key"] == kwargs["dedupe_key"]) if any(item["dedupe_key"] == kwargs["dedupe_key"] for item in self.enqueues) else None
                if prior is None or job["state"] not in {"queued", "retry_wait", "leased", "running"}:
                    continue
                expected = {"queue_name": queue_name, "payload": payload, **kwargs}
                if prior != expected:
                    raise ValueError("active idempotency key has a different request manifest")
                return job_id
        job_id = f"job-{len(self.jobs) + 1}"
        self.enqueues.append({"queue_name": queue_name, "payload": payload, **kwargs})
        self.jobs[job_id] = {"job_id": job_id, "state": "queued", "payload_json": payload}
        return job_id

    def get_job(self, job_id):
        return dict(self.jobs.get(job_id)) if job_id in self.jobs else None

    def next_queued_job(self, queue_name, *, worker_identity=None):
        for job in self.jobs.values():
            if (
                job["state"] == "queued"
                and job["payload_json"].get("kind") in {"coding-provision/v1", "coding-provision/v2"}
                and (worker_identity is None or job["payload_json"].get("worker_identity") == worker_identity)
            ):
                return dict(job)
        return None

    def claim_job(self, job_id, lease_owner, **_kwargs):
        self.claims += 1
        job = self.jobs[job_id]
        if job["state"] != "queued":
            return None
        job.update(state="leased", lease_owner=lease_owner, lease_token="lease-token")
        return dict(job)

    def claim_job_with_envelope(self, job_id, lease_owner, envelope, **kwargs):
        self.claim_lease_seconds.append(kwargs.get("lease_seconds"))
        job = self.claim_job(job_id, lease_owner, **kwargs)
        if job is None:
            return None
        return self.record_claim_envelope(job_id, lease_owner, job["lease_token"], envelope)

    def record_claim_envelope(self, job_id, lease_owner, lease_token, envelope):
        job = self.jobs[job_id]
        assert (job["lease_owner"], job["lease_token"]) == (lease_owner, lease_token)
        job["payload_json"] = {**job["payload_json"], **envelope}
        return dict(job)

    def start_claimed_job(self, job_id, lease_owner, lease_token):
        job = self.jobs[job_id]
        assert (job["lease_owner"], job["lease_token"]) == (lease_owner, lease_token)
        job["state"] = "running"
        return dict(job)

    def succeed_claimed_job(self, job_id, lease_owner, lease_token, result):
        job = self.jobs[job_id]
        assert (job["lease_owner"], job["lease_token"]) == (lease_owner, lease_token)
        job.update(state="succeeded", result=result)
        return dict(job)

    def fail_claimed_job(self, job_id, lease_owner, lease_token, error, result=None):
        job = self.jobs[job_id]
        assert (job["lease_owner"], job["lease_token"]) == (lease_owner, lease_token)
        job.update(state="failed", error_detail=error, result=result)
        return dict(job)

    def cancel_job(self, job_id, _message=None, result=None):
        job = self.jobs[job_id]
        job.update(state="cancelled", result=result)
        return dict(job)


def _config(tmp_path: Path) -> dict:
    return {
        "coding": {
            "host": "tgw-lib-local",
            "worker_identity": "tgw-coding-worker",
            "api_endpoint": "https://tgw.example",
            "worker_api_endpoint": "https://tgw.example",
            "repository_root": str(tmp_path / "repo"),
            "worktree_root": str(tmp_path / "worktrees"),
            "role": "coding-requester",
            "worker_credential_env": "TGW_TEST_CODING_WORKER_CREDENTIAL",
        },
        "api_key": "coding-test-key",
    }


@pytest.fixture
def native(monkeypatch):
    queue = NativeQueue()
    monkeypatch.setattr(coding_provision, "state_machine", queue)
    monkeypatch.setattr(
        coding_provision,
        "todo_lookup",
        lambda todo_id: {
            "id": todo_id,
            "done_at": None,
            "agent": "codex",
            "body": "coding todo",
        },
    )
    return queue


@pytest.fixture
def envelope(monkeypatch, tmp_path):
    # The local worker discovers the declared worktree before its identity
    # function is mocked. Keep the fixture aligned with that production fence.
    (tmp_path / "worktrees" / "todo-1738").mkdir(parents=True)
    identity = {
        "repository_root": str((tmp_path / "repo").resolve()),
        "worktree": str((tmp_path / "worktrees" / "todo-1738").resolve()),
        "todo_id": 1738,
        "branch": "codex/1738",
        "head": "a" * 40,
        "worker_identity": "tgw-coding-worker",
    }
    monkeypatch.setattr(coding_provision_worker, "local_location_identity", lambda *_args: dict(identity))
    monkeypatch.setattr(coding_provision_worker, "_prepare_request_worktree", lambda *_args: dict(identity))
    snapshot = serialize_snapshot(
        ObjectSnapshot(
            object_id=identity["worktree"],
            generation="gen-a",
            assertions=tuple(EvidenceAssertion(condition, FingerprintResult.TRUE) for condition in ("implemented", "tested", "linted")),
        )
    )
    monkeypatch.setattr(coding_provision_worker, "local_snapshot_claim", lambda *_args: snapshot)
    return identity


class WorkerServiceClient:
    """Test transport proving the local worker uses only service HTTP routes."""

    def __init__(self, client: TestClient, credential: str, identity: str) -> None:
        self.client, self.credential, self.identity = client, credential, identity

    def _headers(self):
        return {"X-TGW-Worker-Authorization": f"Bearer {self.credential}", "X-TGW-Worker-Identity": self.identity}

    def _call(self, method, path, body=None):
        response = self.client.request(method, path, headers=self._headers(), json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def get(self, request_id):
        return self._call("GET", f"/api/coding/worker/requests/{request_id}")

    def next(self):
        return self._call("GET", "/api/coding/worker/requests/next")

    def claim(self, request_id, host, envelope_hash, location, snapshot):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/claim", {"host": host, "envelope_hash": envelope_hash, "location": location, "snapshot": snapshot})

    def start(self, request_id, lease_token):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/start", {"lease_token": lease_token})

    def complete(self, request_id, lease_token, result):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/complete", {"lease_token": lease_token, "result": result})

    def fail(self, request_id, lease_token, error, result=None):
        body = {"lease_token": lease_token, "error": error}
        if result is not None:
            body["result"] = result
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/fail", body)


def _execution_envelope() -> dict:
    """A service-issued, bounded authorization for one registered treatment."""
    treatment = next(item for item in CODING_TREATMENTS if item.identity == "claude-review")
    task_spec = {
        "schema": "coding-task/v1",
        "todo_id": 1738,
        "agent": "codex",
        "body": "coding todo",
    }
    return {
        "todo_id": 1738,
        "treatment_id": treatment.identity,
        "treatment_version": treatment.version,
        "graph_id": "graph-1738",
        "object_generation": "gen-a",
        "evaluator_version": "foreman/v1",
        "evidence_set_hash": "evidence-1738",
        "treatment_registry_hash": "registry-1738",
        "task_spec": task_spec,
        "task_spec_hash": coding_provision._hash(task_spec),
    }


def _snapshot_claim(location: dict) -> dict:
    return serialize_snapshot(
        ObjectSnapshot(
            object_id=location["worktree"],
            generation="gen-a",
            assertions=tuple(EvidenceAssertion(condition, FingerprintResult.TRUE) for condition in ("implemented", "tested", "linted")),
        )
    )


def _satisfied_receipt(execution: dict | None = None, execution_identity: dict | None = None) -> dict:
    execution = execution or _execution_envelope()
    treatment = next(item for item in CODING_TREATMENTS if item.identity == execution["treatment_id"])
    receipt = {
        "treatment_id": execution["treatment_id"],
        "treatment_version": execution["treatment_version"],
        "graph_id": execution["graph_id"],
        "object_generation": execution["object_generation"],
        "outcome": "satisfied",
        "established_conditions": [treatment.may_establish[0]],
        "artifacts": [{"kind": "review", "path": "review.md"}],
        "receipt_schema_id": treatment.receipt_schema_id,
    }
    if execution_identity is not None:
        receipt["execution_identity"] = execution_identity
    return receipt


def _failed_receipt(execution: dict | None = None) -> dict:
    execution = execution or _execution_envelope()
    treatment = next(item for item in CODING_TREATMENTS if item.identity == execution["treatment_id"])
    return {
        "treatment_id": execution["treatment_id"],
        "treatment_version": execution["treatment_version"],
        "graph_id": execution["graph_id"],
        "object_generation": execution["object_generation"],
        "outcome": "failed",
        "established_conditions": [],
        "artifacts": [{"kind": "check", "name": "review", "status": "failed"}],
        "receipt_schema_id": treatment.receipt_schema_id,
    }


def test_structured_unsatisfied_treatment_result_becomes_canonical_failed_receipt(
    tmp_path,
    monkeypatch,
    native,
    envelope,
):
    """A launcher-declared failure keeps its evidence through /fail and into
    the service-authored terminal receipt, under the claimed lease."""
    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "worker-test-key")
    client = TestClient(http_server.app)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()

    def failed_treatment(document):
        raise TreatmentFailure("coding treatment reported failed", _failed_receipt(document["execution"]))

    with pytest.raises(TreatmentFailure, match="reported failed"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            provision=failed_treatment,
            client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
        )

    failed = coding_provision.get_request(cfg, request["request_id"])
    assert failed["state"] == "failed"
    assert failed["receipt"]["receipt_source"] == f"queue-job:{request['request_id']}"
    assert failed["receipt"]["outcome"] == "failed"
    assert failed["receipt"]["result"]["outcome"] == "failed"
    assert failed["receipt"]["result"]["execution_identity"] == envelope


def test_worker_cannot_turn_failed_canonical_work_into_succeeded(tmp_path, native, envelope):
    """A swallowed Todo/evaluator/dispatch failure must fail the lease, never
    produce the old fake ``foreman: dispatched=0, errors=0`` success receipt."""
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()

    class Service:
        def get(self, request_id):
            return coding_provision.get_request(cfg, request_id)

        def claim(self, request_id, host, envelope_hash, location, snapshot):
            return coding_provision.claim_request(
                cfg,
                request_id=request_id,
                local_host=host,
                worker_identity="tgw-coding-worker",
                envelope_hash=envelope_hash,
                location=location,
                snapshot=snapshot,
            )

        def start(self, request_id, lease_token):
            return coding_provision.start_request(
                cfg,
                request_id=request_id,
                worker_identity="tgw-coding-worker",
                lease_token=lease_token,
            )

        def complete(self, request_id, lease_token, result):
            return coding_provision.complete_request(
                cfg,
                request_id=request_id,
                worker_identity="tgw-coding-worker",
                lease_token=lease_token,
                result=result,
            )

        def fail(self, request_id, lease_token, error):
            return coding_provision.fail_request(
                cfg,
                request_id=request_id,
                worker_identity="tgw-coding-worker",
                lease_token=lease_token,
                error=error,
            )

    with pytest.raises(HardFailure, match="receipt"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            provision=lambda _document: {"foreman": {"dispatched": 0, "errors": 0}},
            client=Service(),
        )
    assert native.jobs[request["request_id"]]["state"] == "failed"
    assert "receipt" in coding_provision.get_request(cfg, request["request_id"])["error"]


def test_worker_cannot_execute_without_an_api_issued_envelope(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")

    class MissingEnvelopeService:
        def get(self, _request_id):
            return coding_provision.get_request(cfg, request["request_id"])

        def claim(self, _request_id, _host, _hash, _location, _snapshot):
            native.claim_job(request["request_id"], "tgw-coding-worker")
            return {"lease_token": "lease-token", "request": self.get(request["request_id"])}

        def fail(self, _request_id, lease_token, error):
            return coding_provision.fail_request(
                cfg,
                request_id=request["request_id"],
                worker_identity="tgw-coding-worker",
                lease_token=lease_token,
                error=error,
            )

    with pytest.raises(HardFailure, match="execution envelope"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            client=MissingEnvelopeService(),
        )
    assert coding_provision.get_request(cfg, request["request_id"])["state"] == "failed"


def _init_coding_repository(tmp_path):
    repository = tmp_path / "repo"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "coding-test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Coding Test"], check=True)
    (repository / "README").write_text("test\n")
    subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True, text=True)
    (tmp_path / "worktrees").mkdir()
    return repository


def _queued_document(request_id="request-1706"):
    return {"request_id": request_id, "host": "tgw-lib-local", "worker_identity": "tgw-coding-worker", "todo_id": 1706}


def test_worker_creates_fresh_request_bound_worktree_from_repository_head(tmp_path):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    document = _queued_document()
    result = coding_provision_worker._validate_before_claim(document, cfg["coding"], "tgw-lib-local", "tgw-coding-worker")
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()
    assert result["location"]["worktree"] == str(expected)
    assert result["location"]["branch"] == "coding/todo-1706-request-1706"
    assert result["location"]["head"] == head


def test_worker_creates_the_exact_v2_card_allocation(tmp_path):
    repository = _init_coding_repository(tmp_path)
    source = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    root = tmp_path / "development-worktrees"
    allocated = root / "development-request" / "attempt-001" / "worktree"
    document = {
        "kind": "coding-provision/v2",
        "development_request_hash": "sha256:" + "1" * 64,
        "source_commit": source,
        "host": "tgw-lib-local",
        "worker_identity": "tgw-coding-worker",
        "lifecycle": {
            "launch_cards": [
                {
                    "allocation": {
                        "attempt_id": "attempt-001",
                        "worktree": str(allocated),
                        "attempt_root": str(tmp_path / "attempts" / "attempt-001"),
                    }
                }
            ]
        },
    }
    coding = {
        **_config(tmp_path)["coding"],
        "development_worktree_root": str(root),
    }
    result = coding_provision_worker._validate_before_claim(
        document,
        coding,
        "tgw-lib-local",
        "tgw-coding-worker",
    )
    assert result["location"]["worktree"] == str(allocated)
    assert result["location"]["request_hash"] == document["development_request_hash"]
    assert result["location"]["head"] == source
    assert result["location"]["branch"].startswith("development/")


def test_worker_refuses_duplicate_or_unchecked_later_card_allocation(tmp_path):
    root = tmp_path / "development-worktrees"
    first = root / "development-request" / "attempt-001" / "worktree"
    card = {
        "allocation": {
            "attempt_id": "attempt-001",
            "worktree": str(first),
            "attempt_root": str(tmp_path / "attempts" / "attempt-001"),
        },
    }
    document = {
        "lifecycle": {"launch_cards": [card, dict(card)]},
        "development_request_hash": "sha256:" + "1" * 64,
    }
    coding = {"development_worktree_root": str(root)}
    with pytest.raises(HardFailure, match="not unique"):
        coding_provision_worker._development_allocations(document, coding)

    document["lifecycle"]["launch_cards"][1] = {
        "allocation": {
            "attempt_id": "attempt-002",
            "worktree": "/tmp/escaped/worktree",
            "attempt_root": str(tmp_path / "attempts" / "attempt-002"),
        },
    }
    with pytest.raises(HardFailure, match="outside"):
        coding_provision_worker._development_allocations(document, coding)


def test_worker_runs_v2_without_reinterpreting_it_as_a_todo(tmp_path, monkeypatch):
    location = {
        "repository_root": str(tmp_path / "repo"),
        "worktree": str(tmp_path / "worktree"),
        "request_hash": "sha256:" + "2" * 64,
        "branch": "development/request",
        "head": "a" * 40,
        "worker_identity": "tgw-coding-worker",
    }
    execution = {
        "schema": "tgw-development-execution/v1",
        "development_request_hash": location["request_hash"],
        "source_commit": "a" * 40,
        "provider_registry_hash": "sha256:" + "3" * 64,
        "card_idempotency_keys": [],
        "location": location,
    }
    queued = {
        "kind": "coding-provision/v2",
        "request_id": "v2-request",
        "state": "queued",
        "host": "tgw-lib-local",
        "worker_identity": "tgw-coding-worker",
    }
    authorized = {**queued, "state": "leased", "execution": execution}
    envelope = {
        "location": location,
        "envelope_hash": coding_provision_worker._hash(location),
        "attempt_created": False,
    }
    monkeypatch.setattr(coding_provision_worker, "_validate_before_claim", lambda *_args: envelope)
    monkeypatch.setattr(coding_provision_worker, "_prepare_development_worktree", lambda *_args: location)

    class Service:
        completed = None

        def get(self, _request_id):
            return queued

        def claim(self, *_args):
            return {"lease_token": "v2-lease", "request": authorized}

        def start(self, *_args):
            return authorized

        def complete(self, _request_id, _lease, result):
            self.completed = result
            return {
                "receipt": {
                    "worker_identity": "tgw-coding-worker",
                    "envelope_hash": envelope["envelope_hash"],
                    "location": location,
                    "execution": execution,
                    "receipt_source": "queue-job:v2-request",
                }
            }

        def fail(self, *_args):
            raise AssertionError("v2 execution unexpectedly failed")

    service = Service()
    result = {"schema": "test-v2-result", "outcome": "satisfied"}
    coding_provision_worker.claim_and_run(
        _config(tmp_path),
        request_id="v2-request",
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda document: result,
        client=service,
    )
    assert service.completed == result
    assert "execution_identity" not in service.completed


def test_worker_does_not_reuse_historical_todo_prefix_worktree(tmp_path):
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    historical = tmp_path / "worktrees" / "todo-1706-obsolete"
    historical.mkdir()
    result = coding_provision_worker._validate_before_claim(_queued_document(), cfg["coding"], "tgw-lib-local", "tgw-coding-worker")
    assert result["location"]["worktree"] != str(historical)
    assert historical.is_dir()


def test_worker_resumes_only_the_exact_request_bound_worktree(tmp_path, monkeypatch):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    subprocess.run(["git", "-C", str(repository), "worktree", "add", "-b", "coding/todo-1706-request-1706", str(expected), "HEAD"], check=True, capture_output=True, text=True)
    monkeypatch.setattr(coding_provision_worker, "local_snapshot_claim", lambda *_args: {"generation": "gen-a"})
    result = coding_provision_worker._validate_before_claim(
        {**_queued_document(), "object_generation": "gen-a"},
        cfg["coding"],
        "tgw-lib-local",
        "tgw-coding-worker",
    )
    assert result["location"]["worktree"] == str(expected)


def test_worker_resumes_clean_unbound_worktree_after_preclaim_crash(tmp_path):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    document = _queued_document()

    first = coding_provision_worker._validate_before_claim(
        document,
        cfg["coding"],
        "tgw-lib-local",
        "tgw-coding-worker",
    )
    second = coding_provision_worker._validate_before_claim(
        document,
        cfg["coding"],
        "tgw-lib-local",
        "tgw-coding-worker",
    )

    assert first["attempt_created"] is True
    assert second["attempt_created"] is False
    assert second["location"] == first["location"]
    assert (
        second["location"]["head"]
        == subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    )


def test_worker_resumes_clean_unbound_worktree_after_repository_head_advances(tmp_path):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    document = _queued_document()
    first = coding_provision_worker._validate_before_claim(
        document,
        cfg["coding"],
        "tgw-lib-local",
        "tgw-coding-worker",
    )
    (repository / "NEXT").write_text("new repository head\n")
    subprocess.run(["git", "add", "NEXT"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "advance canonical head"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    )

    second = coding_provision_worker._validate_before_claim(
        document,
        cfg["coding"],
        "tgw-lib-local",
        "tgw-coding-worker",
    )

    assert second["attempt_created"] is False
    assert second["location"] == first["location"]
    assert (
        second["location"]["head"]
        != subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    )


def test_existing_unbound_request_worktree_must_still_be_clean(tmp_path):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    subprocess.run(
        ["git", "-C", str(repository), "worktree", "add", "-b", "coding/todo-1706-request-1706", str(expected), "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    (expected / "untrusted").write_text("changed\n")

    with pytest.raises(HardFailure, match="not clean"):
        coding_provision_worker._validate_before_claim(
            _queued_document(),
            cfg["coding"],
            "tgw-lib-local",
            "tgw-coding-worker",
        )


def test_existing_request_worktree_without_matching_generation_cannot_claim(
    tmp_path,
    monkeypatch,
):
    repository = _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    subprocess.run(["git", "-C", str(repository), "worktree", "add", "-b", "coding/todo-1706-request-1706", str(expected), "HEAD"], check=True, capture_output=True, text=True)
    monkeypatch.setattr(coding_provision_worker, "local_snapshot_claim", lambda *_args: {"generation": "gen-b"})
    claimed = False

    class Service:
        def get(self, _request_id):
            document = {**_queued_document(), "state": "queued"}
            document["object_generation"] = "gen-a"
            return document

        def claim(self, *_args):
            nonlocal claimed
            claimed = True
            return {}

    with pytest.raises(HardFailure, match="generation does not match"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id="request-1706",
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            client=Service(),
        )
    assert not claimed


def test_new_worktree_is_removed_after_post_create_attestation_failure(tmp_path, monkeypatch):
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    monkeypatch.setattr(coding_provision_worker, "validated_coding_worktree", lambda *_args: (_ for _ in ()).throw(HardFailure("attestation failed")))

    with pytest.raises(HardFailure, match="attestation failed"):
        coding_provision_worker._prepare_request_worktree(_queued_document(), cfg["coding"], "tgw-coding-worker")
    assert not expected.exists()


def test_post_create_attestation_failure_never_removes_existing_worktree(tmp_path, monkeypatch):
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"
    expected.mkdir()
    marker = expected / "preserve-me"
    marker.write_text("existing\n")
    monkeypatch.setattr(coding_provision_worker, "validated_coding_worktree", lambda *_args: (_ for _ in ()).throw(HardFailure("attestation failed")))

    with pytest.raises(HardFailure, match="attestation failed"):
        coding_provision_worker._prepare_request_worktree(_queued_document(), cfg["coding"], "tgw-coding-worker")
    assert marker.read_text() == "existing\n"


def test_worker_does_not_claim_when_request_worktree_preparation_fails(tmp_path):
    cfg = _config(tmp_path)
    claimed = False

    class Service:
        def get(self, _request_id):
            return {**_queued_document(), "state": "queued"}

        def claim(self, *_args):
            nonlocal claimed
            claimed = True
            return {}

    with pytest.raises(HardFailure, match="root is unavailable"):
        coding_provision_worker.claim_and_run(cfg, request_id="request-1706", local_host="tgw-lib-local", worker_identity="tgw-coding-worker", client=Service())
    assert not claimed


def test_failed_canonical_claim_preserves_reusable_attempt_and_retry_reaches_claim(tmp_path):
    """A claim error is ambiguous, so retry reuses rather than deletes the attempt."""
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class RejectingService:
        def __init__(self):
            self.claims = 0

        def get(self, _request_id):
            return {**_queued_document(), "state": "queued"}

        def claim(self, *_args):
            self.claims += 1
            raise HardFailure("canonical evaluation rejected claim")

    service = RejectingService()
    for expected_claims in (1, 2):
        with pytest.raises(HardFailure, match="evaluation rejected"):
            coding_provision_worker.claim_and_run(
                cfg,
                request_id="request-1706",
                local_host="tgw-lib-local",
                worker_identity="tgw-coding-worker",
                client=service,
            )
        assert service.claims == expected_claims
        assert expected.is_dir()


def test_definitive_claim_rejection_removes_only_new_exact_attempt(tmp_path):
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class RejectedService:
        def get(self, _request_id):
            return {**_queued_document(), "state": "queued"}

        def claim(self, *_args):
            raise coding_provision_worker.DefinitiveClaimRejected("canonical service completed a 409 rejection")

    with pytest.raises(
        coding_provision_worker.DefinitiveClaimRejected,
        match="409 rejection",
    ):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id="request-1706",
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            client=RejectedService(),
        )
    assert not expected.exists()


def test_client_classifies_completed_claim_409_as_definitive(monkeypatch):
    error = HTTPError(
        "https://tgw.example/api/coding/worker/requests/request/claim",
        409,
        "Conflict",
        {},
        io.BytesIO(b'{"detail":"rejected"}'),
    )
    monkeypatch.setattr(
        coding_provision_worker,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )
    client = coding_provision_worker.CodingProvisionClient(
        "https://tgw.example",
        "secret",
        "worker",
    )

    with pytest.raises(coding_provision_worker.DefinitiveClaimRejected):
        client.claim("request", "host", "hash", {}, {})


def test_failed_claim_preserves_generation_bound_attempt_and_retry_reaches_claim(
    tmp_path,
    monkeypatch,
):
    """A request generation is not evidence that the service committed a claim."""
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class RejectingService:
        def __init__(self):
            self.claims = 0

        def get(self, _request_id):
            return {
                **_queued_document(),
                "state": "queued",
                "object_generation": "prebound-generation",
            }

        def claim(self, *_args):
            self.claims += 1
            raise HardFailure("canonical evaluation rejected claim")

    service = RejectingService()
    monkeypatch.setattr(
        coding_provision_worker,
        "local_snapshot_claim",
        lambda *_args: {"generation": "prebound-generation"},
    )
    for expected_claims in (1, 2):
        with pytest.raises(HardFailure, match="evaluation rejected"):
            coding_provision_worker.claim_and_run(
                cfg,
                request_id="request-1706",
                local_host="tgw-lib-local",
                worker_identity="tgw-coding-worker",
                client=service,
            )
        assert service.claims == expected_claims
        assert expected.is_dir()


@pytest.mark.parametrize("claim_response", [{}, None])
def test_unconfirmed_claim_response_preserves_reusable_attempt_and_retry_reaches_claim(tmp_path, claim_response):
    """A malformed response is ambiguous; retry re-attests the same clean attempt."""
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class MalformedService:
        def __init__(self):
            self.claims = 0

        def get(self, _request_id):
            return {**_queued_document(), "state": "queued"}

        def claim(self, *_args):
            self.claims += 1
            return claim_response

    service = MalformedService()
    for expected_claims in (1, 2):
        with pytest.raises(HardFailure, match="returned no lease token"):
            coding_provision_worker.claim_and_run(
                cfg,
                request_id="request-1706",
                local_host="tgw-lib-local",
                worker_identity="tgw-coding-worker",
                client=service,
            )
        assert service.claims == expected_claims
        assert expected.is_dir()


def test_unconfirmed_claim_response_preserves_attempt_when_durable_claim_committed(tmp_path):
    """A lost/malformed response cannot erase a durably bound attempt."""
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class CommittedService:
        def __init__(self):
            self.claim_called = False

        def get(self, _request_id):
            document = {**_queued_document(), "state": "queued"}
            if self.claim_called:
                document.update(state="leased", object_generation="generation", location={"worktree": str(expected)})
            return document

        def claim(self, *_args):
            self.claim_called = True
            return {}

    with pytest.raises(HardFailure, match="returned no lease token"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id="request-1706",
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            client=CommittedService(),
        )
    assert expected.is_dir()


def test_unconfirmed_claim_response_preserves_attempt_when_reconciliation_read_fails(tmp_path):
    """Unknown durable state is never grounds for deleting an attempt."""
    _init_coding_repository(tmp_path)
    cfg = _config(tmp_path)
    expected = tmp_path / "worktrees" / "todo-1706-request-1706"

    class UnreadableService:
        def __init__(self):
            self.claim_called = False

        def get(self, _request_id):
            if self.claim_called:
                raise HardFailure("canonical state unavailable")
            return {**_queued_document(), "state": "queued"}

        def claim(self, *_args):
            self.claim_called = True
            return None

    with pytest.raises(HardFailure, match="returned no lease token"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id="request-1706",
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            client=UnreadableService(),
        )
    assert expected.is_dir()


def test_tgw_lib_provision_worker_has_no_direct_canonical_state_dependencies():
    """The one-shot tgw-lib process is HTTP + local treatment execution only."""
    tree = ast.parse(inspect.getsource(coding_provision_worker))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names} | {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = ("psycopg", "tgw.queue.state_machine", "tgw.development.foreman")
    assert not any(name == blocked or name.startswith(f"{blocked}.") for name in imported for blocked in forbidden)


def test_default_worker_branch_is_transport_only_without_queue_or_foreman_imports(tmp_path):
    """A clean worker process can run its default path with no DB/queue setup.

    This deliberately uses a subprocess so imports performed earlier by this
    test module cannot hide a transitive runtime dependency.
    """
    source = Path(__file__).parents[1] / "src"
    script = """
import sys
from tgw import coding_provision_worker as worker
location = {
    "repository_root": "/repo", "worktree": "/worktrees/todo-1738", "todo_id": 1738,
    "branch": "coding/1738", "head": "a" * 40, "worker_identity": "worker",
}
execution = {
    "todo_id": 1738, "treatment_id": "claude-review", "treatment_version": "1",
    "graph_id": "graph", "object_generation": "generation", "evaluator_version": "foreman/v1",
    "evidence_set_hash": "evidence", "treatment_registry_hash": "registry",
    "task_spec": {"schema": "coding-task/v1", "todo_id": 1738, "agent": "codex", "body": "coding todo"},
    "task_spec_hash": "task-hash",
}
document = {"state": "queued", "todo_id": 1738, "object_generation": "generation", "host": "host", "worker_identity": "worker"}
worker._validate_before_claim = lambda *_: {"location": location, "envelope_hash": "hash"}
worker.local_snapshot_claim = lambda *_: {"object_id": location["worktree"], "generation": "generation"}
worker.execution_envelope = lambda _: execution
worker.local_location_identity = lambda *_: location
worker.execute_authorized_treatment = lambda *_: {
    "treatment_id": "claude-review", "treatment_version": "1", "graph_id": "graph",
    "object_generation": "generation", "outcome": "satisfied",
    "established_conditions": ["reviewed"], "artifacts": [{"kind": "review"}],
    "receipt_schema_id": "receipt/tgw-development/v1",
}
class Service:
    def get(self, _): return document
    def claim(self, *_): return {"lease_token": "lease", "request": {**document, "execution": execution}}
    def start(self, *_): return {}
    def complete(self, *_): return {"receipt": {"worker_identity": "worker", "envelope_hash": "hash", "location": location, "execution": execution, "receipt_source": "queue-job:request"}}
    def fail(self, *_): raise AssertionError("default path failed")
worker.claim_and_run({"coding": {"host": "host", "worker_identity": "worker"}}, request_id="request", local_host="host", worker_identity="worker", client=Service())
for forbidden in ("tgw.queue.worker_base", "tgw.queue.state_machine", "tgw.development.foreman", "tgw.workers.coding", "psycopg"):
    assert not any(name == forbidden or name.startswith(forbidden + ".") for name in sys.modules), forbidden
"""
    env = {**__import__("os").environ, "PYTHONPATH": str(source)}
    completed = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, env=env)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("changed", [{"head": "b" * 40}, {"branch": "other/1738"}])
def test_worker_revalidates_worktree_identity_immediately_before_execution(tmp_path, native, envelope, monkeypatch, changed):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()
    observations = [dict(envelope, **changed)]
    monkeypatch.setattr(coding_provision_worker, "local_location_identity", lambda *_: observations.pop(0))
    launched = []

    class Service:
        def get(self, request_id):
            return coding_provision.get_request(cfg, request_id)

        def claim(self, request_id, host, envelope_hash, location, snapshot):
            return coding_provision.claim_request(cfg, request_id=request_id, local_host=host, worker_identity="tgw-coding-worker", envelope_hash=envelope_hash, location=location, snapshot=snapshot)

        def start(self, request_id, lease_token):
            return coding_provision.start_request(cfg, request_id=request_id, worker_identity="tgw-coding-worker", lease_token=lease_token)

        def fail(self, request_id, lease_token, error):
            return coding_provision.fail_request(cfg, request_id=request_id, worker_identity="tgw-coding-worker", lease_token=lease_token, error=error)

    with pytest.raises(HardFailure, match="identity changed"):
        coding_provision_worker.claim_and_run(
            cfg, request_id=request["request_id"], local_host="tgw-lib-local", worker_identity="tgw-coding-worker", provision=lambda _: launched.append(True), client=Service()
        )
    assert not launched
    assert coding_provision.get_request(cfg, request["request_id"])["state"] == "failed"


def test_complete_requires_contract_bound_execution_and_receipt_evidence(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()
    claimed = coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )
    coding_provision.start_request(
        cfg,
        request_id=request["request_id"],
        worker_identity="tgw-coding-worker",
        lease_token=claimed["lease_token"],
    )
    with pytest.raises(HardFailure, match="receipt"):
        coding_provision.complete_request(
            cfg,
            request_id=request["request_id"],
            worker_identity="tgw-coding-worker",
            lease_token=claimed["lease_token"],
            result={"foreman": {"dispatched": 1}},
        )
    completed = coding_provision.complete_request(
        cfg,
        request_id=request["request_id"],
        worker_identity="tgw-coding-worker",
        lease_token=claimed["lease_token"],
        result=_satisfied_receipt(claimed["request"]["execution"], envelope),
    )
    assert completed["state"] == "succeeded"


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda result, _location: result.update(treatment_id="foreign-treatment"), "treatment_id does not match"),
        (lambda result, _location: result.update(outcome="satisfied", established_conditions=["reviewed"]), "not a non-satisfied outcome"),
        (lambda result, location: result.update(execution_identity={**location, "head": "b" * 40}), "execution identity does not match"),
        (lambda result, _location: result.update(established_conditions=["reviewed"]), "invalid non-satisfied evidence"),
    ],
    ids=("mismatched-envelope", "satisfied-outcome", "foreign-execution-identity", "invalid-failed-evidence"),
)
def test_failed_result_rejections_leave_claim_running_without_receipt(tmp_path, native, envelope, mutate, match):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()
    claimed = coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )
    coding_provision.start_request(
        cfg,
        request_id=request["request_id"],
        worker_identity="tgw-coding-worker",
        lease_token=claimed["lease_token"],
    )
    result = _failed_receipt(claimed["request"]["execution"])
    result["execution_identity"] = envelope
    mutate(result, envelope)

    with pytest.raises(HardFailure, match=match):
        coding_provision.fail_request(
            cfg,
            request_id=request["request_id"],
            worker_identity="tgw-coding-worker",
            lease_token=claimed["lease_token"],
            error="failed treatment",
            result=result,
        )

    rejected = coding_provision.get_request(cfg, request["request_id"])
    assert rejected["state"] == "running"
    assert rejected["receipt"] is None


@pytest.mark.parametrize(
    ("launcher", "match"),
    [
        (
            lambda command: subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "outcome": "failed",
                        "established_conditions": [],
                        "artifacts": [{"kind": "check", "status": "failed"}],
                    }
                ),
                stderr="",
            ),
            "reported failed",
        ),
        (lambda command: subprocess.CompletedProcess(command, 1, stdout="", stderr="boom"), "mechanical failure"),
    ],
    ids=("non-satisfied", "mechanical-failure"),
)
def test_execute_authorized_treatment_raises_treatment_failure_with_result(tmp_path, monkeypatch, launcher, match):
    execution = _execution_envelope()
    monkeypatch.setattr(coding_execution, "_git_identity", lambda path: (path.resolve(), (path / ".git").resolve()))
    monkeypatch.setattr(coding_execution.subprocess, "run", lambda command, **_kwargs: launcher(command))
    payload = {**execution, "worktree": str(tmp_path), "object_id": str(tmp_path)}
    config = {
        "coding": {
            "worktree_root": str(tmp_path.parent),
            "repository_root": str(tmp_path),
            "commands": {execution["treatment_id"]: ["local-runner"]},
        }
    }

    with pytest.raises(TreatmentFailure, match=match) as raised:
        coding_execution.execute_authorized_treatment(config, payload)

    assert raised.value.result["outcome"] == "failed"
    assert json.loads(coding_execution.receipt_path_for_treatment(tmp_path, execution["treatment_id"]).read_text()) == raised.value.result


def test_authorized_treatment_runner_keeps_trusted_imports_and_names_claimed_source(tmp_path, monkeypatch):
    execution = _execution_envelope()
    monkeypatch.setattr(coding_execution, "_git_identity", lambda path: (path.resolve(), (path / ".git").resolve()))
    monkeypatch.setenv("PYTHONPATH", "/immutable/worker/release/src")
    observed = {}

    def launch(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "outcome": "satisfied",
                    "established_conditions": ["reviewed"],
                    "artifacts": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(coding_execution.subprocess, "run", launch)
    payload = {**execution, "worktree": str(tmp_path), "object_id": str(tmp_path)}
    config = {
        "coding": {
            "worktree_root": str(tmp_path.parent),
            "repository_root": str(tmp_path),
            "commands": {execution["treatment_id"]: ["local-runner"]},
        }
    }

    coding_execution.execute_authorized_treatment(config, payload)

    assert observed["env"]["PYTHONPATH"] == "/immutable/worker/release/src"
    assert observed["env"]["TGW_CODING_WORKTREE_SRC"] == str(tmp_path / "src")
    assert observed["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_canonical_claim_looks_up_todo_and_derives_contract_bound_envelope(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")

    claimed = coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )

    execution = claimed["request"]["execution"]
    assert execution["todo_id"] == 1738
    assert execution["object_generation"] == "gen-a"
    assert execution["treatment_id"] == "claude-review"
    assert execution["treatment_version"] == "1"
    assert execution["graph_id"]
    assert execution["evaluator_version"] == "foreman/v1"
    assert execution["evidence_set_hash"]
    assert execution["treatment_registry_hash"]
    assert execution["task_spec"] == {
        "schema": "coding-task/v1",
        "todo_id": 1738,
        "agent": "codex",
        "body": "coding todo",
    }
    assert execution["task_spec_hash"] == coding_provision._hash(execution["task_spec"])
    assert native.jobs[request["request_id"]]["payload_json"]["snapshot"] == _snapshot_claim(envelope)
    assert native.claim_lease_seconds == [2100]


def test_canonical_claim_lease_exceeds_configured_execution_timeout(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    cfg["coding"]["timeout_s"] = 2400
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")

    coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )

    assert native.claim_lease_seconds == [2700]


@pytest.mark.parametrize("timeout", [True, 0, "invalid"])
def test_canonical_claim_rejects_invalid_execution_timeout(tmp_path, native, envelope, timeout):
    cfg = _config(tmp_path)
    cfg["coding"]["timeout_s"] = timeout
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")

    with pytest.raises(HardFailure, match="timeout_s"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(envelope),
            location=envelope,
            snapshot=_snapshot_claim(envelope),
        )

    assert native.claims == 0


def test_canonical_claim_evaluation_failure_leaves_request_unclaimed(tmp_path, native, envelope, monkeypatch):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    monkeypatch.setattr(coding_provision, "todo_lookup", lambda _todo_id: None)

    with pytest.raises(HardFailure, match="Todo"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(envelope),
            location=envelope,
            snapshot=_snapshot_claim(envelope),
        )

    pending = coding_provision.get_request(cfg, request["request_id"])
    assert pending["state"] == "queued"
    assert pending["receipt"] is None


def test_canonical_dispatch_binding_failure_is_durable_not_success(tmp_path, native, envelope, monkeypatch):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    monkeypatch.setattr(native, "claim_job_with_envelope", lambda *_args, **_kwargs: None)

    with pytest.raises(HardFailure, match="not claimable"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(envelope),
            location=envelope,
            snapshot=_snapshot_claim(envelope),
        )

    assert coding_provision.get_request(cfg, request["request_id"])["state"] == "queued"


def test_authenticated_api_to_native_job_to_local_claim_to_structured_receipt(tmp_path, monkeypatch, native, envelope):
    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "worker-test-key")
    client = TestClient(http_server.app)
    body = {"todo_id": 1738, "object_generation": "gen-a"}

    assert client.post("/api/coding/requests", json=body).status_code == 401
    response = client.post("/api/coding/requests", headers={"Authorization": "Bearer coding-test-key"}, json=body)
    assert response.status_code == 200
    request_id = response.json()["request_id"]
    assert set(native.enqueues[0]["payload"]) == {"kind", "todo_id", "object_generation", "host", "worker_identity"}
    native.jobs[request_id]["payload_json"]["execution"] = _execution_envelope()
    assert client.get(f"/api/coding/worker/requests/{request_id}", headers={"Authorization": "Bearer coding-test-key"}).status_code == 401

    finished = coding_provision_worker.claim_and_run(
        cfg,
        request_id=request_id,
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda document: _satisfied_receipt(document["execution"]),
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    assert finished["receipt"]["worker_identity"] == "tgw-coding-worker"
    assert native.jobs[request_id]["lease_token"] == "lease-token"
    visible = client.get(f"/api/coding/requests/{request_id}", headers={"Authorization": "Bearer coding-test-key"})
    assert visible.status_code == 200
    assert visible.json()["receipt"]["receipt_id"] == finished["receipt"]["receipt_id"]


def test_create_request_performs_no_git_or_worktree_probing(tmp_path, native):
    """The canonical service must accept a request without ever probing Git or
    a tgw-lib worktree: the enqueued payload is request-safe data only, and
    missing worktree/repository config must not block enqueueing."""

    cfg = _config(tmp_path)
    for field in ("repository_root", "worktree_root", "worker_api_endpoint", "worker_credential_env"):
        cfg["coding"].pop(field)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert request["state"] == "queued"
    assert native.enqueues[0]["payload"] == {
        "kind": "coding-provision/v1",
        "todo_id": 1738,
        "object_generation": "gen-a",
        "host": "tgw-lib-local",
        "worker_identity": "tgw-coding-worker",
    }


def test_local_claimed_worker_produces_receipt_with_local_envelope(tmp_path, monkeypatch, native, envelope):
    """A tgw-lib worker that claims the request validates its local worktree
    envelope and the durable receipt returned by the service records that same
    immutable envelope."""
    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "worker-test-key")
    client = TestClient(http_server.app)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()

    finished = coding_provision_worker.claim_and_run(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda document: _satisfied_receipt(document["execution"]),
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    receipt = finished["receipt"]
    assert receipt["worker_identity"] == "tgw-coding-worker"
    assert receipt["location"] == envelope
    assert receipt["envelope_hash"] == coding_provision._hash(envelope)


def test_local_worker_claims_real_git_worktree_envelope_in_durable_receipt(tmp_path, monkeypatch, native):
    """End-to-end: the worker creates and attests its request-bound worktree."""
    repository = tmp_path / "repo"
    worktree_root = tmp_path / "worktrees"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "coding-test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Coding Test"], check=True)
    (repository / "README").write_text("test\n")
    subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True, text=True)
    worktree_root.mkdir()

    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "worker-test-key")
    client = TestClient(http_server.app)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation=None)
    assert "location" not in native.enqueues[0]["payload"]
    native.jobs[request["request_id"]]["payload_json"]["execution"] = _execution_envelope()
    finished = coding_provision_worker.claim_and_run(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda document: _satisfied_receipt(document["execution"]),
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    receipt = finished["receipt"]
    location = receipt["location"]
    assert location["worktree"] == str((worktree_root / "todo-1738-job-1").resolve())
    assert location["branch"] == "coding/todo-1738-job-1"
    assert len(location["head"]) == 40
    assert location["todo_id"] == 1738
    assert location["worker_identity"] == "tgw-coding-worker"
    assert receipt["envelope_hash"] == coding_provision._hash(location)
    assert native.jobs[request["request_id"]]["payload_json"]["location"] == location


def test_worker_identity_fence_fails_before_native_claim(tmp_path, native):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")

    class UnusedService:
        def get(self, _request_id):
            return coding_provision.get_request(cfg, request["request_id"])

    with pytest.raises(HardFailure, match="identity"):
        coding_provision_worker.claim_and_run(
            cfg,
            request_id=request["request_id"],
            local_host="wrong-host",
            worker_identity="tgw-coding-worker",
            client=UnusedService(),
        )
    assert native.claims == 0


def test_duplicate_claim_is_rejected_without_replacing_the_existing_lease(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    first = coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )
    with pytest.raises(HardFailure, match="not claimable"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(envelope),
            location=envelope,
            snapshot=_snapshot_claim(envelope),
        )
    assert native.jobs[request["request_id"]]["lease_token"] == first["lease_token"]


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda env: dict(env, todo_id=9999), "todo_id"),
        (lambda env: dict(env, worker_identity="some-other-worker"), "worker identity"),
        (lambda env: dict(env, head="x" * 40), "head"),
    ],
)
def test_claim_rejects_envelope_not_bound_to_request(tmp_path, native, envelope, mutate, fragment):
    """The service rejects a claimed envelope that is not self-consistent and
    bound to the exact request, todo, and worker before it grants a lease."""
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    shifted = mutate(dict(envelope))
    with pytest.raises(HardFailure, match=fragment):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(shifted),
            location=shifted,
            snapshot=_snapshot_claim(envelope),
        )
    assert native.claims == 0


def test_claim_rejects_envelope_hash_mismatch(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    with pytest.raises(HardFailure, match="hash"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash="0" * 64,
            location=dict(envelope),
            snapshot=_snapshot_claim(envelope),
        )
    assert native.claims == 0


def test_worker_cli_loads_supported_coding_config_contract(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "tgw.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)))
    monkeypatch.setattr(coding_provision_worker, "claim_and_run", lambda cfg, **kwargs: {"receipt": {"config_seen": cfg["coding"]["host"], **kwargs}})
    monkeypatch.setattr("sys.argv", ["worker", "job-1", "--config", str(cfg_path), "--host", "tgw-lib-local", "--worker-identity", "tgw-coding-worker"])

    assert coding_provision_worker.main() == 0
    assert json.loads(capsys.readouterr().out)["config_seen"] == "tgw-lib-local"


def test_worker_cli_discovers_and_runs_oldest_request_without_foreman(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "tgw.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)))

    class PullClient:
        def next(self):
            return {"request_id": "job-pulled"}

    service = PullClient()
    seen = {}
    monkeypatch.setattr(coding_provision_worker, "configured_client", lambda _cfg: service)
    monkeypatch.setattr(
        coding_provision_worker,
        "claim_and_run",
        lambda _cfg, **kwargs: seen.update(kwargs) or {"receipt": {"request_id": kwargs["request_id"]}},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["worker", "--config", str(cfg_path), "--host", "tgw-lib-local", "--worker-identity", "tgw-coding-worker"],
    )

    assert coding_provision_worker.main() == 0
    assert seen == {
        "request_id": "job-pulled",
        "local_host": "tgw-lib-local",
        "worker_identity": "tgw-coding-worker",
        "client": service,
    }
    assert json.loads(capsys.readouterr().out) == {"request_id": "job-pulled"}


def test_worker_cli_idle_poll_is_successful_noop(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "tgw.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)))
    monkeypatch.setattr(coding_provision_worker, "configured_client", lambda _cfg: type("Idle", (), {"next": lambda self: {}})())
    monkeypatch.setattr("sys.argv", ["worker", "--config", str(cfg_path), "--worker-identity", "tgw-coding-worker"])

    assert coding_provision_worker.main() == 0
    assert json.loads(capsys.readouterr().out) is None


def test_worker_can_observe_next_request_without_claim(tmp_path, native):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738)

    pending = coding_provision.next_request(cfg, "tgw-coding-worker")

    assert pending["request_id"] == request["request_id"]
    assert pending["state"] == "queued"
    assert native.claims == 0


def test_next_request_skips_older_request_for_different_worker(tmp_path, native):
    cfg = _config(tmp_path)
    stale = coding_provision.create_request(cfg, todo_id=1737)
    native.jobs[stale["request_id"]]["payload_json"]["worker_identity"] = "retired-worker"
    current = coding_provision.create_request(cfg, todo_id=1738)

    pending = coding_provision.next_request(cfg, "tgw-coding-worker")

    assert pending["request_id"] == current["request_id"]
    assert native.claims == 0


def test_worker_next_route_is_static_and_worker_authenticated():
    routes = list(http_server.app.routes)
    static_index = next(i for i, route in enumerate(routes) if getattr(route, "path", None) == "/api/coding/worker/requests/next")
    dynamic_index = next(i for i, route in enumerate(routes) if getattr(route, "path", None) == "/api/coding/worker/requests/{request_id}")
    route = routes[static_index]

    assert static_index < dynamic_index
    assert any(dependency.call is http_server._require_coding_worker for dependency in route.dependant.dependencies)


def test_access_status_and_stop_preserve_receipt_model(tmp_path, native):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert coding_provision.access_status(cfg, request["request_id"])["receipt_source"] == "unknown"
    stopped = coding_provision.stop_request(cfg, request["request_id"])
    assert stopped["state"] == "cancelled"
    assert stopped["receipt"]["outcome"] == "stopped"


@pytest.mark.parametrize("terminal_state", ["failed", "dead_letter"])
def test_stop_preserves_failed_and_dead_letter_state_without_fabricating_receipt(
    tmp_path,
    native,
    terminal_state,
):
    """A stop on an already failed/dead-lettered request must not fabricate a
    synthetic ``stopped`` receipt that masks its terminal state, nor change the
    state — a stop may only fabricate a stopped receipt for a job it actually
    cancelled or that was already cancelled."""
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    native.jobs[request["request_id"]]["state"] = terminal_state
    stopped = coding_provision.stop_request(cfg, request["request_id"])
    assert stopped["state"] == terminal_state
    assert stopped["receipt"] is None


@pytest.mark.parametrize(
    ("request_id", "expected_todo"),
    [
        (None, None),
        ("1732", 1732),
    ],
)
def test_coding_cli_access_status_reads_only_the_local_workflow(
    monkeypatch,
    capsys,
    request_id,
    expected_todo,
):
    observed: list[tuple[int | None, object]] = []
    monkeypatch.setattr(
        coding_cli,
        "status",
        lambda todo_id, *, config_path: observed.append((todo_id, config_path))
        or {"ok": True, "dependencies": {"tgw_prod": False}},
    )

    assert coding_cli.run(argparse.Namespace(coding_op="access-status", request_id=request_id, config=None)) == 0
    assert observed == [(expected_todo, coding_cli.DEFAULT_CONFIG)]
    assert json.loads(capsys.readouterr().out)["dependencies"]["tgw_prod"] is False


@pytest.mark.parametrize(
    "field",
    [
        "api_endpoint",
        "host",
        "worker_identity",
    ],
)
def test_create_request_fails_clearly_on_incomplete_coding_config(tmp_path, native, field):
    cfg = _config(tmp_path)
    del cfg["coding"][field]
    with pytest.raises(ValueError, match=f"coding\\.{field}"):
        coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")


@pytest.mark.parametrize(
    "field",
    [
        "worker_api_endpoint",
        "host",
        "worker_identity",
        "repository_root",
        "worktree_root",
        "worker_credential_env",
    ],
)
def test_configured_client_fails_clearly_on_incomplete_coding_config(tmp_path, field):
    cfg = _config(tmp_path)
    del cfg["coding"][field]
    with pytest.raises(ValueError, match=f"coding\\.{field}"):
        coding_provision_worker.configured_client(cfg)


@pytest.mark.parametrize(
    "endpoint",
    ["http://tgw-prod:7373", "http://100.107.99.66:7373", "ftp://127.0.0.1"],
)
def test_worker_rejects_insecure_credential_endpoint(tmp_path, monkeypatch, endpoint):
    cfg = _config(tmp_path)
    cfg["coding"]["worker_api_endpoint"] = endpoint
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "secret")

    with pytest.raises(ValueError, match="HTTPS|secure endpoint"):
        coding_provision_worker.configured_client(cfg)


@pytest.mark.parametrize(
    "endpoint",
    ["http://127.0.0.1:7373", "http://localhost:7373", "http://[::1]:7373"],
)
def test_worker_allows_explicit_loopback_http_endpoint(tmp_path, monkeypatch, endpoint):
    cfg = _config(tmp_path)
    cfg["coding"]["worker_api_endpoint"] = endpoint
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "secret")

    client = coding_provision_worker.configured_client(cfg)

    assert client.endpoint == endpoint


def test_client_constructor_rejects_insecure_nonloopback_http():
    with pytest.raises(HardFailure, match="HTTPS"):
        coding_provision_worker.CodingProvisionClient(
            "http://tgw-prod:7373",
            "secret",
            "worker",
        )


def test_create_request_accepts_config_without_tgw_lib_local_paths(tmp_path, native):
    """The canonical service must accept a request when the tgw-lib-local
    filesystem roots (and worker-only fields) are absent from the coding
    section; those are validated only at the local worker boundary."""
    cfg = _config(tmp_path)
    for field in ("repository_root", "worktree_root", "worker_api_endpoint", "worker_credential_env"):
        cfg["coding"].pop(field)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert request["state"] == "queued"


def test_create_request_retry_returns_exact_active_request(tmp_path, native):
    cfg = _config(tmp_path)
    first = coding_provision.create_request(
        cfg,
        todo_id=1738,
        object_generation="gen-a",
        source_commit="a" * 40,
    )
    second = coding_provision.create_request(
        cfg,
        todo_id=1738,
        object_generation="gen-a",
        source_commit="a" * 40,
    )

    assert second == first
    assert len(native.enqueues) == 1


def test_create_request_retry_rejects_same_key_with_changed_manifest(tmp_path, native):
    cfg = _config(tmp_path)
    coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    cfg["coding"]["worker_identity"] = "replacement-worker"

    with pytest.raises(ValueError, match="different request manifest"):
        coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")


def test_request_binds_exact_source_commit_through_native_payload(tmp_path, native):
    cfg = _config(tmp_path)
    commit = "a" * 40
    coding_provision.create_request(
        cfg,
        todo_id=1738,
        object_generation=None,
        source_commit=commit,
    )
    assert native.enqueues[0]["payload"]["source_commit"] == commit
    assert commit in native.enqueues[0]["dedupe_key"]


@pytest.mark.parametrize("value", ["", "A" * 40, "a" * 39, "../main", True])
def test_request_rejects_noncanonical_source_commit(tmp_path, native, value):
    with pytest.raises(HardFailure, match="source_commit"):
        coding_provision.create_request(
            _config(tmp_path),
            todo_id=1738,
            source_commit=value,
        )


def test_worker_resolves_only_commit_present_in_registered_repository(tmp_path):
    repository = _init_coding_repository(tmp_path)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert coding_provision_worker._verified_source_commit(repository, commit) == commit
    with pytest.raises(HardFailure, match="absent"):
        coding_provision_worker._verified_source_commit(repository, "f" * 40)


def test_service_rejects_worker_head_not_matching_requested_source(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(
        cfg,
        todo_id=1738,
        source_commit="b" * 40,
    )
    with pytest.raises(HardFailure, match="requested source commit"):
        coding_provision.claim_request(
            cfg,
            request_id=request["request_id"],
            local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(envelope),
            location=envelope,
            snapshot=_snapshot_claim(envelope),
        )


def test_unbound_request_binds_attested_generation_at_local_claim(tmp_path, native, envelope):
    """A normal client may request a Todo without knowing tgw-lib's local source hash."""
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation=None)

    assert "object_generation" not in native.enqueues[0]["payload"]
    claimed = coding_provision.claim_request(
        cfg,
        request_id=request["request_id"],
        local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        envelope_hash=coding_provision._hash(envelope),
        location=envelope,
        snapshot=_snapshot_claim(envelope),
    )
    assert claimed["request"]["execution"]["object_generation"] == "gen-a"
    assert claimed["request"]["object_generation"] == "gen-a"


def test_authenticated_api_accepts_unbound_coding_request(tmp_path, monkeypatch, native):
    """The ordinary client may enqueue by Todo without a tgw-lib-local hash."""
    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    client = TestClient(http_server.app)

    response = client.post(
        "/api/coding/requests",
        headers={"Authorization": "Bearer coding-test-key"},
        json={"todo_id": 1738},
    )
    assert response.status_code == 200
    assert "object_generation" not in native.enqueues[0]["payload"]


def test_coding_cli_entrypoint_accepts_positional_todo_id(monkeypatch):
    """The ordinary shell surface is exactly ``tgw coding start TODO_ID``."""
    seen = {}
    monkeypatch.setattr("sys.argv", ["tgw", "coding", "start", "1738"])
    monkeypatch.setattr(api, "load_config", lambda _path: {})
    monkeypatch.setattr(coding_cli, "run", lambda args: seen.update(vars(args)) or 0)

    assert api.main() == 0
    assert seen["request_id"] == "1738"
    assert seen["object_generation"] is None


def test_local_coding_start_binds_and_ticks_only_the_selected_todo(monkeypatch):
    observed = {}
    monkeypatch.setattr(coding_cli, "_initialize", lambda _path: {"coding": {}})
    monkeypatch.setattr(coding_cli.todo, "todo_get", lambda todo_id: {
        "id": todo_id,
        "body": "build the operator CLI",
        "priority": 3,
        "done_at": None,
    })
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "codex")
    monkeypatch.setattr(
        coding_cli,
        "bind_command",
        lambda args: {
            "binding": {
                "worktree": "/opt/TGW/var/worktrees/todo-1732-plan-test",
                "worktree_identity": {"branch": "coding/codex/todo-1732-plan-test"},
                "source_commit": "a" * 40,
                "plan_commit": "b" * 40,
                "solution_hash": "sha256:" + "c" * 64,
            }
        },
    )

    def local_tick(_config, *, todo_ids):
        observed["todo_ids"] = todo_ids
        return TickResult(dispatched=1)

    monkeypatch.setattr(coding_cli, "tick", local_tick)
    monkeypatch.setattr(coding_cli, "_jobs", lambda todo_id, *, limit: [{"todo_id": todo_id}])

    result = coding_cli.start(1732, config_path=Path("/tmp/coding.json"))

    assert result["ok"] is True
    assert result["worktree"] == "/opt/TGW/var/worktrees/todo-1732-plan-test"
    assert result["session"]["codex"] == ["codex", "-C", result["worktree"]]
    assert result["dependencies"] == {
        "tgw_prod": False,
        "ssh": False,
        "sudo": False,
        "remote_provision_api": False,
        "approval_card": False,
    }
    assert observed["todo_ids"] == {1732}


def test_execution_boundary_accepts_only_local_allowed_argv_runner(tmp_path):
    worker = CodingWorker(
        "claude-review",
        {
            "coding": {
                "commands": {"claude-review": ["local-runner", "review"]},
                "allowed_runners": ["local-runner"],
            }
        },
    )
    assert worker._configured_command("claude-review") == ["local-runner", "review"]
    worker.config["coding"]["commands"]["claude-review"] = ["ssh", "host", "run"]
    with pytest.raises(HardFailure, match="local argv protocol"):
        worker._configured_command("claude-review")
