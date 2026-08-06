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
        "api_endpoint": "https://tgw.example", "repository_root": str(tmp_path / "repo"),
        "worktree_root": str(tmp_path / "worktrees"), "role": "coding-requester",
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


def test_authenticated_api_to_native_job_to_local_claim_to_structured_receipt(tmp_path, monkeypatch, native, envelope):
    cfg = _config(tmp_path)
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", "coding-test-key")
    client = TestClient(http_server.app)
    body = {"todo_id": 1738, "worktree": envelope["worktree"], "object_generation": "gen-a"}

    assert client.post("/api/coding/requests", json=body).status_code == 401
    response = client.post("/api/coding/requests", headers={"Authorization": "Bearer coding-test-key"}, json=body)
    assert response.status_code == 200
    request_id = response.json()["request_id"]
    assert native.enqueues[0]["payload"]["location"]["head"] == "a" * 40

    finished = coding_provision.claim_and_run(
        cfg, request_id=request_id, local_host="tgw-lib-local", worker_identity="tgw-coding-worker",
        provision=lambda _: {"foreman": {"dispatched": 1}},
    )
    assert finished["state"] == "succeeded"
    assert finished["receipt"]["worker_identity"] == "tgw-coding-worker"
    assert native.jobs[request_id]["lease_token"] == "lease-token"


def test_create_request_uses_real_worktree_git_identity_and_reaches_native_enqueue(tmp_path, native):
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
    request = coding_provision.create_request(cfg, todo_id=1738, worktree=str(worktree), object_generation="gen-a")
    expected_branch = subprocess.run(
        ["git", "-C", str(worktree), "branch", "--show-current"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    expected_head = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()

    assert expected_branch == "coding/1738"
    assert len(expected_head) == 40
    assert native.enqueues[0]["payload"]["location"] == {
        "repository_root": str(repository.resolve()), "worktree": str(worktree.resolve()), "todo_id": 1738,
        "branch": expected_branch, "head": expected_head, "worker_identity": "tgw-coding-worker",
    }
    assert request["request_id"] == "job-1"


@pytest.mark.parametrize("field,value", [("head", "b" * 40), ("branch", "HEAD"), ("worktree", "/outside")])
def test_envelope_mismatch_fails_before_native_claim(tmp_path, monkeypatch, native, envelope, field, value):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, worktree=envelope["worktree"], object_generation="gen-a")
    changed = dict(envelope)
    changed[field] = value
    monkeypatch.setattr(coding_provision, "location_identity", lambda *_args: changed)

    with pytest.raises(HardFailure, match="envelope"):
        coding_provision.claim_and_run(cfg, request_id=request["request_id"], local_host="tgw-lib-local", worker_identity="tgw-coding-worker")
    assert native.claims == 0


def test_worker_cli_loads_supported_coding_config_contract(tmp_path, monkeypatch, capsys):
    cfg_path = tmp_path / "tgw.json"
    cfg_path.write_text(json.dumps(_config(tmp_path)))
    monkeypatch.setattr(coding_provision_worker, "claim_and_run", lambda cfg, **kwargs: {"receipt": {"config_seen": cfg["coding"]["host"], **kwargs}})
    monkeypatch.setattr("sys.argv", ["worker", "job-1", "--config", str(cfg_path), "--host", "tgw-lib-local", "--worker-identity", "tgw-coding-worker"])

    assert coding_provision_worker.main() == 0
    assert json.loads(capsys.readouterr().out)["config_seen"] == "tgw-lib-local"


def test_access_status_and_stop_preserve_receipt_model(tmp_path, native, envelope):
    cfg = _config(tmp_path)
    request = coding_provision.create_request(cfg, todo_id=1738, worktree=envelope["worktree"], object_generation="gen-a")
    assert coding_provision.access_status(cfg, request["request_id"])["receipt_source"] == "unknown"
    stopped = coding_provision.stop_request(cfg, request["request_id"])
    assert stopped["state"] == "cancelled"
    assert stopped["receipt"]["outcome"] == "stopped"


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


def test_execution_boundary_accepts_only_local_allowed_argv_runner(tmp_path):
    worker = CodingWorker("claude-review", {"coding": {
        "commands": {"claude-review": ["local-runner", "review"]},
        "allowed_runners": ["local-runner"],
    }})
    assert worker._configured_command("claude-review") == ["local-runner", "review"]
    worker.config["coding"]["commands"]["claude-review"] = ["ssh", "host", "run"]
    with pytest.raises(HardFailure, match="local argv protocol"):
        worker._configured_command("claude-review")
