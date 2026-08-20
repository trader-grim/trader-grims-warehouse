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


def _file_hash(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tree_hash(path):
    root = Path(path)
    digest = hashlib.sha256()
    for item in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda value: value.relative_to(root).as_posix()):
        digest.update(item.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


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
                bindings[-1].update({"name": raw["name"], "sha256": raw["sha256"]})
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
    solution = "sha256:" + "1" * 64
    release = releases / "candidate"
    release.mkdir()
    (release / ".release-manifest.json").write_text(json.dumps({"commit": source_commit}))
    for name, content in {
        "launcher": "actor launcher\n", "bootstrap.json": '{"status":"PASS"}\n',
        "mcp.json": '{"endpoint":"tgw-context"}\n', "skill/SKILL.md": "bounded plan\n",
    }.items():
        path = release / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    home, project = tmp_path / "actor-home", tmp_path / "project"
    destination = home / "bin/tgw-actor"
    generation_hash = "sha256:" + "b" * 64
    generation = generations / generation_hash.removeprefix("sha256:")
    (generation / "contracts").mkdir(parents=True)
    environment = {
        "schema": "tgw-execution-environment-catalog/v3",
        "flake_lock": {"path": "flake.lock", "sha256": "1" * 64},
        "actors": {actor: {
            "enabled": True, "permitted_profiles": ["development"],
            "required_skills": ["tgw-plan"], "required_hooks": [],
            "required_mcp_endpoints": ["tgw-context"],
        }},
        "profiles": {"development": {
            "state": "ready-for-preflight", "broker_capabilities": ["plan-read", "source-read"],
        }},
    }
    environment_path = generation / "environment-catalog.json"
    environment_path.write_text(json.dumps(environment))
    catalog = _hash(environment)
    launcher_hash = _file_hash(release / "launcher")
    bootstrap_hash = _file_hash(release / "bootstrap.json")
    skill_hash = _tree_hash(release / "skill")
    mcp_hash = _file_hash(release / "mcp.json")
    mcp_destination = home / ".mcp/tgw-context.json"
    contract = {
        "actor": actor, "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {"commit": source_commit}, "profile": "development",
        "local": {
            "bootstrap_receipt_hash": bootstrap_hash,
            "launcher": {"path": str(destination), "sha256": launcher_hash},
            "skills": {"tgw-plan": skill_hash}, "hooks": {},
            "mcp": {
                "endpoints": ["tgw-context"],
                "binding_hash": _hash([{
                    "endpoint": "tgw-context", "source_sha256": mcp_hash,
                    "destination": str(mcp_destination),
                }]),
            },
        },
    }
    bindings = [
        {"kind": "skill", "name": "tgw-plan", "source": "skill", "destination": str(home / ".skills/tgw-plan"), "sha256": skill_hash},
        {"kind": "mcp", "name": "tgw-context", "source": "mcp.json", "destination": str(mcp_destination), "sha256": mcp_hash},
        {"kind": "launcher", "name": "launcher", "source": "launcher", "destination": str(destination), "sha256": launcher_hash},
        {"kind": "bootstrap", "name": "bootstrap-receipt", "source": "bootstrap.json", "destination": str(home / ".tgw/bootstrap.json"), "sha256": bootstrap_hash},
        {"kind": "environment", "name": "environment-catalog", "source": str(environment_path), "destination": str(home / ".tgw/execution-environment-catalog.json"), "sha256": _file_hash(environment_path)},
    ]
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1", "generation": generation_hash,
        "actors": {actor: {"home": str(home), "project": str(project), "bindings": bindings}},
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
