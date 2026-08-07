"""Native-queue coding-provision route and worker regression coverage."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgw import coding_cli, coding_provision, coding_provision_worker, http_server
from tgw.queue.worker_base import HardFailure
from tgw.workers.coding import CodingWorker


class NativeQueue:
    """In-memory contract double for the state-machine job/receipt API."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.enqueues: list[dict] = []
        self.claims = 0

    def enqueue_job(self, queue_name, payload, **kwargs):
        assert queue_name == coding_provision.QUEUE_NAME
        assert kwargs["dedupe_key"].startswith("coding-provision:")
        job_id = f"job-{len(self.jobs) + 1}"
        self.enqueues.append({"queue_name": queue_name, "payload": payload, **kwargs})
        self.jobs[job_id] = {"job_id": job_id, "state": "queued", "payload_json": payload}
        return job_id

    def get_job(self, job_id):
        return dict(self.jobs.get(job_id)) if job_id in self.jobs else None

    def claim_job(self, job_id, lease_owner, **_kwargs):
        self.claims += 1
        job = self.jobs[job_id]
        if job["state"] != "queued":
            return None
        job.update(state="leased", lease_owner=lease_owner, lease_token="lease-token")
        return dict(job)

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

    def fail_claimed_job(self, job_id, lease_owner, lease_token, error):
        job = self.jobs[job_id]
        assert (job["lease_owner"], job["lease_token"]) == (lease_owner, lease_token)
        job.update(state="failed", error_detail=error)
        return dict(job)

    def cancel_job(self, job_id, _message=None, result=None):
        job = self.jobs[job_id]
        job.update(state="cancelled", result=result)
        return dict(job)


def _config(tmp_path: Path) -> dict:
    return {"coding": {
        "host": "tgw-lib-local", "worker_identity": "tgw-coding-worker",
        "api_endpoint": "https://tgw.example", "worker_api_endpoint": "https://tgw.example",
        "repository_root": str(tmp_path / "repo"),
        "worktree_root": str(tmp_path / "worktrees"), "role": "coding-requester",
        "worker_credential_env": "TGW_TEST_CODING_WORKER_CREDENTIAL",
    }, "api_key": "coding-test-key"}


@pytest.fixture
def native(monkeypatch):
    queue = NativeQueue()
    monkeypatch.setattr(coding_provision, "state_machine", queue)
    return queue


@pytest.fixture
def envelope(monkeypatch, tmp_path):
    identity = {
        "repository_root": str((tmp_path / "repo").resolve()),
        "worktree": str((tmp_path / "worktrees" / "todo-1738").resolve()),
        "todo_id": 1738, "branch": "codex/1738", "head": "a" * 40,
        "worker_identity": "tgw-coding-worker",
    }
    monkeypatch.setattr(coding_provision, "location_identity", lambda *_args: dict(identity))
    return identity


class WorkerServiceClient:
    """Test transport proving the local worker uses only service HTTP routes."""

    def __init__(self, client: TestClient, credential: str, identity: str) -> None:
        self.client, self.credential, self.identity = client, credential, identity

    def _headers(self):
        return {"X-TGW-Worker-Authorization": f"Bearer {self.credential}",
                "X-TGW-Worker-Identity": self.identity}

    def _call(self, method, path, body=None):
        response = self.client.request(method, path, headers=self._headers(), json=body)
        assert response.status_code == 200, response.text
        return response.json()

    def get(self, request_id):
        return self._call("GET", f"/api/coding/worker/requests/{request_id}")

    def claim(self, request_id, host, envelope_hash, location):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/claim",
                          {"host": host, "envelope_hash": envelope_hash, "location": location})

    def start(self, request_id, lease_token):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/start", {"lease_token": lease_token})

    def complete(self, request_id, lease_token, result):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/complete",
                          {"lease_token": lease_token, "result": result})

    def fail(self, request_id, lease_token, error):
        return self._call("POST", f"/api/coding/worker/requests/{request_id}/fail",
                          {"lease_token": lease_token, "error": error})


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
    assert client.get(f"/api/coding/worker/requests/{request_id}", headers={"Authorization": "Bearer coding-test-key"}).status_code == 401

    finished = coding_provision_worker.claim_and_run(
        cfg, request_id=request_id, local_host="tgw-lib-local", worker_identity="tgw-coding-worker",
        provision=lambda _: {"foreman": {"dispatched": 1}},
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    assert finished["receipt"]["worker_identity"] == "tgw-coding-worker"
    assert native.jobs[request_id]["lease_token"] == "lease-token"
    visible = client.get(f"/api/coding/requests/{request_id}", headers={"Authorization": "Bearer coding-test-key"})
    assert visible.status_code == 200
    assert visible.json()["receipt"]["receipt_id"] == finished["receipt"]["receipt_id"]


def test_create_request_performs_no_git_or_worktree_probing(tmp_path, native, monkeypatch):
    """The canonical service must accept a request without ever probing Git or
    a tgw-lib worktree: any location_identity call or subprocess spawn is a
    forbidden regression, and the enqueued payload is request-safe data only."""
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("create_request probed a Git worktree or repository")

    monkeypatch.setattr(coding_provision, "location_identity", _forbidden)
    monkeypatch.setattr(coding_provision.subprocess, "run", _forbidden)
    cfg = _config(tmp_path)
    for field in ("repository_root", "worktree_root", "worker_api_endpoint", "worker_credential_env"):
        cfg["coding"].pop(field)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert request["state"] == "queued"
    assert native.enqueues[0]["payload"] == {
        "kind": "coding-provision/v1", "todo_id": 1738, "object_generation": "gen-a",
        "host": "tgw-lib-local", "worker_identity": "tgw-coding-worker",
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

    finished = coding_provision_worker.claim_and_run(
        cfg, request_id=request["request_id"], local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda _: {"foreman": {"dispatched": 1}},
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    receipt = finished["receipt"]
    assert receipt["worker_identity"] == "tgw-coding-worker"
    assert receipt["location"] == envelope
    assert receipt["envelope_hash"] == coding_provision._hash(envelope)


def test_local_worker_claims_real_git_worktree_envelope_in_durable_receipt(tmp_path, monkeypatch, native):
    """End-to-end: the worker resolves worktree_root/todo-<id>, validates it as
    a real Git worktree, and the durable receipt carries that local envelope."""
    repository = tmp_path / "repo"
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "todo-1738"
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "coding-test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Coding Test"], check=True)
    (repository / "README").write_text("test\n")
    subprocess.run(["git", "-C", str(repository), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "initial"], check=True, capture_output=True, text=True)
    worktree_root.mkdir()
    subprocess.run(["git", "-C", str(repository), "worktree", "add", "-b", "coding/1738", str(worktree)], check=True, capture_output=True, text=True)

    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    monkeypatch.setenv("TGW_TEST_CODING_WORKER_CREDENTIAL", "worker-test-key")
    client = TestClient(http_server.app)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert "location" not in native.enqueues[0]["payload"]
    finished = coding_provision_worker.claim_and_run(
        cfg, request_id=request["request_id"], local_host="tgw-lib-local",
        worker_identity="tgw-coding-worker",
        provision=lambda _: {"foreman": {"dispatched": 1}},
        client=WorkerServiceClient(client, "worker-test-key", "tgw-coding-worker"),
    )
    assert finished["state"] == "succeeded"
    receipt = finished["receipt"]
    location = receipt["location"]
    assert location["worktree"] == str(worktree.resolve())
    assert location["branch"] == "coding/1738"
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
            cfg, request_id=request["request_id"], local_host="wrong-host",
            worker_identity="tgw-coding-worker", client=UnusedService(),
        )
    assert native.claims == 0


@pytest.mark.parametrize("mutate,fragment", [
    (lambda env: dict(env, todo_id=9999), "todo_id"),
    (lambda env: dict(env, worker_identity="some-other-worker"), "worker identity"),
    (lambda env: dict(env, head="x" * 40), "head"),
])
def test_claim_rejects_envelope_not_bound_to_request(tmp_path, native, envelope, mutate, fragment):
    """The service rejects a claimed envelope that is not self-consistent and
    bound to the exact request, todo, and worker before it grants a lease."""
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    shifted = mutate(dict(envelope))
    with pytest.raises(HardFailure, match=fragment):
        coding_provision.claim_request(
            cfg, request_id=request["request_id"], local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash=coding_provision._hash(shifted), location=shifted,
        )
    assert native.claims == 0


def test_claim_rejects_envelope_hash_mismatch(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    with pytest.raises(HardFailure, match="hash"):
        coding_provision.claim_request(
            cfg, request_id=request["request_id"], local_host="tgw-lib-local",
            worker_identity="tgw-coding-worker",
            envelope_hash="0" * 64, location=dict(envelope),
        )
    assert native.claims == 0


def test_worker_cli_loads_supported_coding_config_contract(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "tgw.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)))
    monkeypatch.setattr(coding_provision_worker, "claim_and_run", lambda cfg, **kwargs: {"receipt": {"config_seen": cfg["coding"]["host"], **kwargs}})
    monkeypatch.setattr("sys.argv", ["worker", "job-1", "--config", str(cfg_path), "--host", "tgw-lib-local", "--worker-identity", "tgw-coding-worker"])

    assert coding_provision_worker.main() == 0
    assert json.loads(capsys.readouterr().out)["config_seen"] == "tgw-lib-local"


def test_access_status_and_stop_preserve_receipt_model(tmp_path, native):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert coding_provision.access_status(cfg, request["request_id"])["receipt_source"] == "unknown"
    stopped = coding_provision.stop_request(cfg, request["request_id"])
    assert stopped["state"] == "cancelled"
    assert stopped["receipt"]["outcome"] == "stopped"


@pytest.mark.parametrize("terminal_state", ["failed", "dead_letter"])
def test_stop_preserves_failed_and_dead_letter_state_without_fabricating_receipt(
    tmp_path, native, terminal_state,
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


@pytest.mark.parametrize(("request_id", "expected_path"), [
    (None, "/api/coding/access-status"),
    ("request id/&?", "/api/coding/access-status?request_id=request+id%2F%26%3F"),
])
def test_coding_cli_access_status_optionally_passes_url_encoded_request_id(
    monkeypatch, capsys, request_id, expected_path,
):
    paths: list[str] = []
    monkeypatch.setattr(coding_cli, "_configured_credentials", lambda _args: ("https://tgw.example", "test-key"))
    monkeypatch.setattr(coding_cli, "_call", lambda _endpoint, _api_key, path: paths.append(path) or {})

    assert coding_cli.run(argparse.Namespace(coding_op="access-status", request_id=request_id)) == 0
    assert paths == [expected_path]
    assert json.loads(capsys.readouterr().out) == {}


@pytest.mark.parametrize("field", [
    "api_endpoint", "host", "worker_identity",
])
def test_create_request_fails_clearly_on_incomplete_coding_config(tmp_path, native, field):
    cfg = _config(tmp_path)
    del cfg["coding"][field]
    with pytest.raises(ValueError, match=f"coding\\.{field}"):
        coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")


@pytest.mark.parametrize("field", [
    "worker_api_endpoint", "host", "worker_identity",
    "repository_root", "worktree_root", "worker_credential_env",
])
def test_configured_client_fails_clearly_on_incomplete_coding_config(tmp_path, field):
    cfg = _config(tmp_path)
    del cfg["coding"][field]
    with pytest.raises(ValueError, match=f"coding\\.{field}"):
        coding_provision_worker.configured_client(cfg)


def test_create_request_accepts_config_without_tgw_lib_local_paths(tmp_path, native):
    """The canonical service must accept a request when the tgw-lib-local
    filesystem roots (and worker-only fields) are absent from the coding
    section; those are validated only at the local worker boundary."""
    cfg = _config(tmp_path)
    for field in ("repository_root", "worktree_root", "worker_api_endpoint", "worker_credential_env"):
        cfg["coding"].pop(field)
    request = coding_provision.create_request(cfg, todo_id=1738, object_generation="gen-a")
    assert request["state"] == "queued"


def test_execution_boundary_accepts_only_local_allowed_argv_runner(tmp_path):
    worker = CodingWorker("claude-review", {"coding": {
        "commands": {"claude-review": ["local-runner", "review"]},
        "allowed_runners": ["local-runner"],
    }})
    assert worker._configured_command("claude-review") == ["local-runner", "review"]
    worker.config["coding"]["commands"]["claude-review"] = ["ssh", "host", "run"]
    with pytest.raises(HardFailure, match="local argv protocol"):
        worker._configured_command("claude-review")
