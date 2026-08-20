import hashlib
import json
import os
import pwd
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from tgw.platform_control_provider import PlatformControlProvider, create_platform_control_app


def _hash(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write_hashed(path, value, field):
    value = {**value, field: _hash(value)}
    path.write_text(json.dumps(value))
    return value


def _fixture(tmp_path):
    state, releases, admissions, generations = tmp_path / "state", tmp_path / "releases", tmp_path / "admissions", tmp_path / "generations"
    state.mkdir(); releases.mkdir(); admissions.mkdir(); generations.mkdir()
    source_commit, plan_commit = "e" * 40, "f" * 40
    solution, catalog = "sha256:" + "1" * 64, "sha256:" + "2" * 64
    actor = pwd.getpwuid(os.getuid()).pw_name
    release = releases / "candidate"
    release.mkdir()
    generation = generations / ("b" * 64)
    (generation / "contracts").mkdir(parents=True)
    (release / ".release-manifest.json").write_text(json.dumps({"commit": source_commit}))
    source = release / "source"
    source.write_text("actor source\n")
    destination = tmp_path / "actor-home" / "binding"
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1", "generation": "sha256:" + "b" * 64,
        "actors": {actor: {"home": str(tmp_path / "actor-home"), "project": str(tmp_path / "project"), "bindings": [
            {"kind": "launcher", "name": "launcher", "source": "source", "destination": str(destination), "sha256": _hash("source")},
        ]}},
    }
    (generation / "bundle.json").write_text(json.dumps(bundle))
    (generation / "contracts" / f"{actor}.json").write_text(json.dumps({"actor": actor, "catalog_hash": catalog}))
    admission_unsigned = {
        "schema": "tgw-w16-release-admission-receipt/v1", "status": "ADMITTED",
        "candidate": {"commit": source_commit},
        "plan": {"commit": plan_commit, "solution_hash": solution},
    }
    admission = {**admission_unsigned, "receipt_hash": _hash(admission_unsigned)}
    (admissions / (admission["receipt_hash"].removeprefix("sha256:") + ".json")).write_text(json.dumps(admission))
    snapshot_unsigned = {
        "schema": "tgw-w18-lifecycle-snapshot/v1", "generation": "sha256:" + "a" * 64,
        "collections": {
            "live_requests": [{"request_id": "request-one"}], "role_leases": [],
            "rendered_surfaces": [{"surface_id": "surface-one"}], "continuations": [],
        },
    }
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps({**snapshot_unsigned, "snapshot_hash": _hash(snapshot_unsigned)}))
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixed fixture\n")
    config = {
        "schema": "tgw-platform-control-provider/v1",
        "token_sha256": "sha256:" + hashlib.sha256(b"secret").hexdigest(),
        "state_root": str(state), "lifecycle_snapshot_path": str(snapshot),
        "release_root": str(releases), "admission_root": str(admissions),
        "actor_generation_root": str(generations),
        "contract_public_key": "fixture-key", "systemctl_path": str(systemctl),
        "managed_services": ["tgw-context.service"],
    }
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1", "transaction_id": "refresh-one",
        "idempotency_key": "refresh-one", "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": "sha256:" + "b" * 64,
        "revisions": {
            "plan": plan_commit, "solution": solution, "source": source_commit, "catalog": catalog,
            "bootstrap": "sha256:" + "3" * 64, "broker_policy": "sha256:" + "4" * 64,
            "admission": admission["receipt_hash"],
        },
        "actors": [actor],
    }
    return config, request, release, destination


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


def test_platform_provider_runs_closed_full_fleet_sequence_and_reconciles_every_checkpoint(tmp_path):
    config, request, _release, destination = _fixture(tmp_path)
    service_state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop": service_state["value"] = "inactive"
        if arguments[0] == "restart": service_state["value"] = "active"
        return subprocess.CompletedProcess(arguments, 0, service_state["value"] + "\n", "")

    provider = PlatformControlProvider(config, service_runner=service, materializer_loader=lambda _: _Materializer())
    checkpoint = provider.checkpoint(request)
    assert provider.quiesce(checkpoint)["status"] == "QUIESCED"
    rebuilt = provider.rebuild(request)
    activated = provider.activate(request, rebuilt)
    restarted = provider.restart(activated)
    assert provider.health(restarted)["status"] == "HEALTHY"
    assert provider.verify_actor(request["actors"][0], request)["status"] == "VERIFIED"
    resumed = provider.resume(checkpoint, request)
    assert resumed["status"] == "RESUMED"
    assert resumed["dispositions"]["live_requests"][0]["disposition"] == "reconcile"
    assert resumed["dispositions"]["rendered_surfaces"][0]["disposition"] == "reconcile"
    assert destination.is_symlink()
    assert json.loads((Path(config["state_root"]) / "fleet-transition-gate.json").read_text())["status"] == "ACTIVE"


def test_platform_provider_http_rejects_missing_auth_and_invocation_drift(tmp_path):
    config, request, _release, _destination = _fixture(tmp_path)
    app = create_platform_control_app(
        {"platform_control_provider": config},
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
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
