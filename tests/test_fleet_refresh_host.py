import json

import pytest

from tgw.fleet_refresh_host import FleetRefreshHostError, _FleetProvider, run_configured_fleet_refresh


def _request():
    return {
        "schema": "tgw-w18-fleet-refresh-request/v1", "transaction_id": "refresh-one",
        "idempotency_key": "refresh-one-key", "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": "sha256:" + "b" * 64,
        "revisions": {
            "plan": "f" * 40, "solution": "sha256:" + "1" * 64,
            "source": "e" * 40, "catalog": "sha256:" + "2" * 64,
            "bootstrap": "sha256:" + "3" * 64, "broker_policy": "sha256:" + "4" * 64,
            "admission": "sha256:" + "5" * 64,
        }, "actors": ["codex", "claude"],
    }


def _config(tmp_path):
    requests, receipts = tmp_path / "requests", tmp_path / "receipts"
    requests.mkdir(parents=True)
    receipts.mkdir()
    (requests / "refresh-one.json").write_text(json.dumps(_request()))
    return {
        "fleet_refresh": {
            "schema": "tgw-fleet-refresh-host/v1", "request_root": str(requests),
            "receipt_root": str(receipts), "lease_path": str(tmp_path / "fleet.lock"),
            "provider": {
                "schema": "tgw-fleet-refresh-provider-binding/v1", "provider_id": "tgw-fleet-refresh-provider@1",
                "endpoint": "http://127.0.0.1:7555", "credential_env": "TGW_TEST_FLEET_TOKEN", "timeout_seconds": 3,
            },
        },
    }


def test_configured_fleet_controller_runs_exact_request_and_actor_set(tmp_path, monkeypatch):
    events = []

    def call(self, step, arguments):
        events.append(step)
        statuses = {
            "checkpoint": "CHECKPOINTED", "quiesce": "QUIESCED", "rebuild": "REBUILT",
            "activate": "ACTIVATED", "restart": "RESTARTED", "health": "HEALTHY",
            "resume": "RESUMED", "rollback": "ROLLED_BACK",
        }
        if step == "checkpoint":
            return {"status": "CHECKPOINTED", "live_requests": [], "role_leases": [], "rendered_surfaces": [], "continuations": []}
        if step == "verify-actor":
            return {"status": "VERIFIED", "actor": arguments[0], "generation": arguments[1]["successor_generation"]}
        if step == "resume":
            return {"status": "RESUMED", "dispositions": {
                "live_requests": [], "role_leases": [], "rendered_surfaces": [], "continuations": [],
            }}
        return {"status": statuses[step]}

    monkeypatch.setattr(_FleetProvider, "call", call)
    receipt = run_configured_fleet_refresh(_config(tmp_path), "refresh-one")
    assert receipt["status"] == "VERIFIED_AND_RESUMED"
    assert events == ["checkpoint", "quiesce", "rebuild", "activate", "restart", "health", "verify-actor", "verify-actor", "resume"]


def test_fleet_controller_refuses_unbound_request_or_tmp_root(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(FleetRefreshHostError, match="unavailable"):
        run_configured_fleet_refresh(config, "unknown")
    config["fleet_refresh"]["receipt_root"] = "/tmp/fleet"
    with pytest.raises(FleetRefreshHostError, match="outside /tmp"):
        run_configured_fleet_refresh(config, "refresh-one")
