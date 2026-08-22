from __future__ import annotations

import hashlib
import json

import pytest

import tgw.fleet_refresh_trigger as trigger_module
from tgw.fleet_refresh_trigger import (
    FleetRefreshTriggerError,
    _require_root_owned,
    trigger_configured_fleet_refresh,
)


@pytest.fixture(autouse=True)
def _root_owned_watched_input(monkeypatch):
    monkeypatch.setattr(trigger_module, "_require_root_owned", lambda _path, _label: None)


def _config(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    watched = tmp_path / "watched.json"
    request_root = tmp_path / "requests"
    state_root = tmp_path / "state"
    generation_root = tmp_path / "generations"
    request_root.mkdir()
    state_root.mkdir()
    generation_root.mkdir()
    actors = ["codex", "hermes"]
    successor = "sha256:" + "6" * 64
    revisions = {
        "plan": "b" * 40,
        "solution": "sha256:" + "1" * 64,
        "source": "c" * 40,
        "catalog": "sha256:" + "2" * 64,
        "bootstrap": "sha256:" + "3" * 64,
        "broker_policy": "sha256:" + "4" * 64,
        "admission": "sha256:" + "5" * 64,
    }
    watched.write_text(json.dumps({
        "schema": "tgw-w18-fleet-watched-input/v1",
        "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": successor,
        "revisions": revisions,
    }, sort_keys=True))
    _write_generation(generation_root, successor, revisions, actors)
    return {
        "fleet_refresh_trigger": {
            "schema": "tgw-fleet-refresh-trigger/v1",
            "input_path": str(watched),
            "request_root": str(request_root),
            "state_root": str(state_root),
            "actor_generation_root": str(generation_root),
            "actors": actors,
        },
    }


def _write_generation(generation_root, generation, revisions, actors):
    receipt = {
        "schema": "tgw-actor-generation-receipt/v1",
        "status": "PREPARED",
        "generation": generation,
        "generation_identity": {
            "catalog_hash": revisions["catalog"],
            "plan_commit": revisions["plan"],
            "solution_hash": revisions["solution"],
            "source_commit": revisions["source"],
        },
        "actors": actors,
    }
    receipt["receipt_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    root = generation_root / generation.removeprefix("sha256:")
    root.mkdir()
    (root / "generation-receipt.json").write_text(json.dumps(receipt))


def test_watched_revision_creates_one_durable_coalesced_request(durable_path):
    config = _config(durable_path)
    first = trigger_configured_fleet_refresh(config)
    second = trigger_configured_fleet_refresh(config)

    assert first["status"] == "TRIGGERED"
    assert second["status"] == "COALESCED"
    assert second["request_hash"] == first["request_hash"]
    request_root = durable_path / "requests"
    requests = list(request_root.glob("*.json"))
    assert len(requests) == 1
    request = json.loads(requests[0].read_text())
    assert request["transaction_id"] == first["request_id"]
    assert request["successor_generation"] == first["successor_generation"]
    assert json.loads((durable_path / "state/pending-refresh.json").read_text())[
        "request_hash"
    ] == first["request_hash"]


def test_changed_watched_revision_creates_a_new_exact_request(durable_path):
    config = _config(durable_path)
    first = trigger_configured_fleet_refresh(config)
    watched = durable_path / "watched.json"
    value = json.loads(watched.read_text())
    value["revisions"]["catalog"] = "sha256:" + "9" * 64
    value["successor_generation"] = "sha256:" + "8" * 64
    watched.write_text(json.dumps(value, sort_keys=True))
    _write_generation(
        durable_path / "generations",
        value["successor_generation"],
        value["revisions"],
        config["fleet_refresh_trigger"]["actors"],
    )

    second = trigger_configured_fleet_refresh(config)

    assert second["status"] == "TRIGGERED"
    assert second["request_id"] != first["request_id"]
    assert len(list((durable_path / "requests").glob("*.json"))) == 2


def test_trigger_refuses_missing_or_cross_revision_generation(durable_path):
    config = _config(durable_path)
    watched = durable_path / "watched.json"
    value = json.loads(watched.read_text())
    value["successor_generation"] = "sha256:" + "7" * 64
    watched.write_text(json.dumps(value, sort_keys=True))
    with pytest.raises(FleetRefreshTriggerError, match="generation is unavailable"):
        trigger_configured_fleet_refresh(config)

    _write_generation(
        durable_path / "generations",
        value["successor_generation"],
        value["revisions"],
        config["fleet_refresh_trigger"]["actors"],
    )
    value["revisions"]["catalog"] = "sha256:" + "9" * 64
    watched.write_text(json.dumps(value, sort_keys=True))
    with pytest.raises(FleetRefreshTriggerError, match="does not match watched revisions"):
        trigger_configured_fleet_refresh(config)


def test_trigger_refuses_tmp_or_malformed_watched_input(durable_path):
    config = _config(durable_path)
    config["fleet_refresh_trigger"]["state_root"] = "/tmp/tgw-trigger"
    with pytest.raises(FleetRefreshTriggerError, match="outside /tmp"):
        trigger_configured_fleet_refresh(config)

    config = _config(durable_path / "second")
    watched = durable_path / "second/watched.json"
    watched.write_text("{}")
    with pytest.raises(FleetRefreshTriggerError, match="fields are not exact"):
        trigger_configured_fleet_refresh(config)


def test_watched_input_requires_root_ownership(tmp_path):
    watched = tmp_path / "watched.json"
    watched.write_text("{}")
    with pytest.raises(FleetRefreshTriggerError, match="root-owned"):
        _require_root_owned(watched, "fleet watched input")
