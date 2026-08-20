import hashlib
import json
import os
import pwd
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgw.actor_fleet_provider import ActorFleetProvider, create_actor_fleet_app


@pytest.fixture
def durable_path():
    path = Path("/opt/TGW/var/tmp") / f"actor-provider-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class _Materializer:
    def materialize_complete_actor_contracts(self, bundle, *, source_root, contracts, trusted_contract_public_key, apply=False, replace_existing=False, additional_source_roots=()):
        bindings = []
        for actor, specification in bundle["actors"].items():
            for raw in specification["bindings"]:
                source = (Path(source_root) / raw["source"]).resolve()
                destination = Path(raw["destination"])
                if apply:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if destination.is_symlink():
                        destination.unlink()
                    destination.symlink_to(source)
                bindings.append({"actor": actor, "source": str(source), "destination": str(destination), "kind": raw["kind"]})
        return {
            "schema": "tgw-w18-complete-actor-materialization/v1",
            "status": "COMPLETE_MATERIALIZED_NOT_SERVICE_ACTIVATED" if apply else "PREPARED",
            "bindings": bindings, "rollback_journal": [],
        }

    def rollback_complete_actor_contracts(self, receipt):
        for binding in receipt["bindings"]:
            path = Path(binding["destination"])
            if path.is_symlink():
                path.unlink()


def _fixture(tmp_path):
    state, releases, admissions, generations = (tmp_path / name for name in ("state", "releases", "admissions", "generations"))
    for path in (state, releases, admissions, generations):
        path.mkdir()
    actor = pwd.getpwuid(os.getuid()).pw_name
    source_commit, plan_commit = "e" * 40, "f" * 40
    solution, catalog = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    release = releases / "candidate"
    release.mkdir()
    (release / ".release-manifest.json").write_text(json.dumps({"commit": source_commit}))
    (release / "source").write_text("actor source\n")
    destination = tmp_path / "actor-home" / "binding"
    generation_hash = "sha256:" + "b" * 64
    generation = generations / generation_hash.removeprefix("sha256:")
    (generation / "contracts").mkdir(parents=True)
    contract = {
        "actor": actor, "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {"commit": source_commit},
    }
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1", "generation": generation_hash,
        "actors": {actor: {"home": str(tmp_path / "actor-home"), "project": str(tmp_path / "project"), "bindings": [
            {"kind": "launcher", "name": "launcher", "source": "source", "destination": str(destination), "sha256": _hash("source")},
        ]}},
    }
    (generation / "bundle.json").write_text(json.dumps(bundle))
    (generation / "contracts" / f"{actor}.json").write_text(json.dumps(contract))
    receipt_unsigned = {
        "schema": "tgw-w18-actor-generation-receipt/v1", "status": "PREPARED",
        "generation": generation_hash, "actors": [actor], "bundle_hash": _hash(bundle),
        "signer_public_key": "fixture-key",
        "generation_identity": {
            "catalog_hash": catalog, "plan_commit": plan_commit,
            "solution_hash": solution, "source_commit": source_commit,
        },
    }
    (generation / "generation-receipt.json").write_text(json.dumps({**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}))
    admission_unsigned = {
        "schema": "tgw-w16-release-admission-receipt/v1", "status": "ADMITTED",
        "candidate": {"commit": source_commit}, "plan": {"commit": plan_commit, "solution_hash": solution},
    }
    admission = {**admission_unsigned, "receipt_hash": _hash(admission_unsigned)}
    (admissions / (admission["receipt_hash"].removeprefix("sha256:") + ".json")).write_text(json.dumps(admission))
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixture\n")
    config = {
        "schema": "tgw-actor-fleet-provider/v1", "token_sha256": "sha256:" + hashlib.sha256(b"secret").hexdigest(),
        "state_root": str(state), "release_root": str(releases), "admission_root": str(admissions),
        "actor_generation_root": str(generations), "contract_public_key": "fixture-key",
        "systemctl_path": str(systemctl), "managed_services": ["tgw-coding-provision-pull.timer"],
        "quiescence_units": ["tgw-coding-provision-pull.service"],
    }
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1", "transaction_id": "refresh-one", "idempotency_key": "refresh-one",
        "predecessor_generation": "sha256:" + "a" * 64, "successor_generation": generation_hash,
        "revisions": {
            "plan": plan_commit, "solution": solution, "source": source_commit, "catalog": catalog,
            "bootstrap": "sha256:" + "3" * 64, "broker_policy": "sha256:" + "4" * 64,
            "admission": admission["receipt_hash"],
        }, "actors": [actor],
    }
    return config, request, destination


def test_actor_provider_materializes_verifies_repairs_and_rolls_back(durable_path):
    config, request, destination = _fixture(durable_path)
    service_state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            service_state["value"] = "inactive"
        if arguments[0] == "restart":
            service_state["value"] = "active"
        return subprocess.CompletedProcess(arguments, 0, service_state["value"] + "\n", "")

    provider = ActorFleetProvider(config, service_runner=service, materializer_loader=lambda _: _Materializer())
    assert provider.quiesce(request)["status"] == "QUIESCED"
    rebuilt = provider.rebuild(request)
    activated = provider.activate(request, rebuilt)
    restarted = provider.restart(activated)
    assert provider.health(restarted)["status"] == "HEALTHY"
    assert provider.verify_actor(request["actors"][0], request)["status"] == "VERIFIED"
    assert provider.repair(request)["status"] == "REPAIRED"
    assert destination.is_symlink()
    assert provider.rollback(request)["status"] == "ROLLED_BACK"
    assert not destination.exists()


def test_actor_provider_http_rejects_auth_and_unbound_invocation(durable_path):
    config, request, _destination = _fixture(durable_path)
    app = create_actor_fleet_app(
        {"actor_fleet_provider": config}, materializer_loader=lambda _: _Materializer(),
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "inactive\n", ""),
    )
    client = TestClient(app)
    invocation = {"schema": "tgw-actor-fleet-provider-invocation/v1", "step": "quiesce", "arguments": [request]}
    body = {**invocation, "invocation_hash": _hash(invocation)}
    assert client.post("/v1/actor-fleet/quiesce", json=body).status_code == 401
    assert client.post(
        "/v1/actor-fleet/quiesce", json={**body, "invocation_hash": "sha256:" + "0" * 64},
        headers={"Authorization": "Bearer secret"},
    ).status_code == 409
