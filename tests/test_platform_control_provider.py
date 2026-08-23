import hashlib
import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgw.platform_control_provider import (
    PlatformControlError,
    PlatformControlProvider,
    create_platform_control_app,
)


@pytest.fixture
def durable_path():
    path = Path("/opt/TGW/var/tmp") / f"platform-provider-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _ActorClient:
    statuses = {
        "quiesce": "QUIESCED", "rebuild": "REBUILT", "activate": "ACTIVATED",
        "restart": "RESTARTED", "health": "HEALTHY", "verify-actor": "VERIFIED",
        "rollback": "ROLLED_BACK", "repair": "REPAIRED",
    }

    def __init__(self):
        self.calls = []

    def call(self, step, arguments):
        self.calls.append((step, arguments))
        request = arguments[-1] if step == "verify-actor" else arguments[0]
        result = {"status": self.statuses[step], "transaction_id": request.get("transaction_id")}
        if step == "verify-actor":
            result.update({"actor": arguments[0], "generation": request["successor_generation"]})
        return result


class _SnapshotSource:
    def __init__(self, snapshot):
        self.value = snapshot

    def snapshot(self, generation):
        assert generation == self.value["generation"]
        return dict(self.value)


def _fixture(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    snapshot_unsigned = {
        "schema": "tgw-w18-lifecycle-snapshot/v1", "generation": "sha256:" + "a" * 64,
        "collections": {
            "live_requests": [{"request_id": "request-one"}], "role_leases": [],
            "rendered_surfaces": [{"surface_id": "surface-one"}], "continuations": [],
        },
    }
    snapshot = {**snapshot_unsigned, "snapshot_hash": _hash(snapshot_unsigned)}
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixed fixture\n")
    config = {
        "schema": "tgw-platform-control-provider/v1",
        "token_sha256": "sha256:" + hashlib.sha256(b"secret").hexdigest(),
        "state_root": str(state),
        "lifecycle_snapshot_source": {
            "schema": "tgw-lifecycle-snapshot-source/v1", "dsn_env": "TGW_POSTGRES_DSN",
            "surface_root": str(tmp_path), "max_records": 100,
        },
        "actor_provider": {
            "schema": "tgw-actor-fleet-provider-binding/v1", "provider_id": "tgw-actor-fleet-provider@1",
            "endpoint": "http://127.0.0.1:7556", "transport": "loopback-http",
            "expected_host": "127.0.0.1", "credential_env": "TGW_ACTOR_FLEET_KEY", "timeout_seconds": 10,
        },
        "systemctl_path": str(systemctl), "managed_services": ["tgw-context.service"],
    }
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1", "transaction_id": "refresh-one",
        "idempotency_key": "refresh-one", "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": "sha256:" + "b" * 64,
        "revisions": {
            "plan": "f" * 40, "solution": "sha256:" + "1" * 64,
            "evidence_plan": "d" * 40, "evidence_tree": "c" * 40,
            "source": "e" * 40, "source_tree": "b" * 40,
            "current_plan_sources": {
                "plan/execution/AMENDMENT-20260823-MCP-LIVE-CLIENT-CONVERGENCE.yaml": "sha256:" + "6" * 64,
                "pp/PP-ACTOR-MCP-BOUNDARY-001.md": "sha256:" + "7" * 64,
                "plan/execution/targets/W19-W21-MCP-ONLY-ACTOR-HARDENING-v1.yaml": "sha256:" + "8" * 64,
                "plan/execution/ACTIVE-PLAN-AMENDMENT-PROCESS-v1.yaml": "sha256:" + "9" * 64,
            },
            "catalog": "sha256:" + "2" * 64,
            "bootstrap": "sha256:" + "3" * 64, "broker_policy": "sha256:" + "4" * 64,
            "review": "sha256:" + "a" * 64, "admission": "sha256:" + "5" * 64,
        },
        "actors": ["codex"],
    }
    return config, request, snapshot


def test_platform_provider_coordinates_closed_full_fleet_sequence(durable_path):
    config, request, snapshot = _fixture(durable_path)
    service_state = {"value": "active"}
    actor = _ActorClient()

    def service(arguments):
        if arguments[0] == "stop":
            service_state["value"] = "inactive"
        if arguments[0] == "restart":
            service_state["value"] = "active"
        return subprocess.CompletedProcess(arguments, 0, service_state["value"] + "\n", "")

    provider = PlatformControlProvider(
        config, service_runner=service, actor_client=actor, snapshot_source=_SnapshotSource(snapshot),
    )
    checkpoint = provider.checkpoint(request)
    assert provider.quiesce(checkpoint)["status"] == "QUIESCED"
    rebuilt = provider.rebuild(request)
    activated = provider.activate(request, rebuilt)
    restarted = provider.restart(activated)
    assert provider.health(restarted)["status"] == "HEALTHY"
    assert provider.verify_actor("codex", request)["status"] == "VERIFIED"
    resumed = provider.resume(checkpoint, request)
    assert resumed["status"] == "RESUMED"
    assert resumed["dispositions"]["live_requests"][0]["disposition"] == "reconcile"
    assert json.loads((Path(config["state_root"]) / "fleet-transition-gate.json").read_text())["status"] == "ACTIVE"
    assert [step for step, _ in actor.calls] == ["quiesce", "rebuild", "activate", "restart", "health", "verify-actor"]

    invocation = {
        "recovery": {"candidate_commit": request["revisions"]["source"]},
    }
    with pytest.raises(
        PlatformControlError,
        match="not recoverable from RESUMED",
    ):
        provider.recover("rollback-platform", invocation)
    assert [step for step, _ in actor.calls].count("rollback") == 0


def test_platform_rollback_requires_failed_controller_and_effectful_provider_state(
    durable_path,
):
    config, request, snapshot = _fixture(durable_path)
    provider = PlatformControlProvider(
        config,
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "active\n", ""),
        actor_client=_ActorClient(),
        snapshot_source=_SnapshotSource(snapshot),
    )
    provider.checkpoint(request)
    controller = {
        "schema": "tgw-w18-fleet-refresh-journal/v1",
        "request_hash": _hash(request),
        "request": request,
        "status": "ROLLBACK_REQUIRED",
        "steps": [],
    }
    with pytest.raises(PlatformControlError, match="not legal from CHECKPOINTED"):
        provider.rollback(request, controller)


def test_platform_provider_http_rejects_missing_auth_and_invocation_drift(durable_path):
    config, request, snapshot = _fixture(durable_path)
    app = create_platform_control_app(
        {"platform_control_provider": config}, actor_client=_ActorClient(), snapshot_source=_SnapshotSource(snapshot),
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "inactive\n", ""),
    )
    client = TestClient(app)
    invocation = {"schema": "tgw-fleet-refresh-provider-invocation/v1", "step": "checkpoint", "arguments": [request]}
    body = {**invocation, "invocation_hash": _hash(invocation)}
    assert client.post("/v1/fleet-refresh/checkpoint", json=body).status_code == 401
    held = client.post(
        "/v1/fleet-refresh/checkpoint", json={**body, "invocation_hash": "sha256:" + "0" * 64},
        headers={"Authorization": "Bearer secret"},
    )
    assert held.status_code == 409
