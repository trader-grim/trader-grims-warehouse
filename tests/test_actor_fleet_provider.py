import grp
import hashlib
import json
import os
import pwd
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from tgw.actor_fleet_provider import ActorFleetProvider, create_actor_fleet_app
from tgw.admission_recovery import compile_release_admission


@pytest.fixture
def durable_path():
    path = Path("/opt/TGW/var/tmp") / f"actor-provider-test-{uuid.uuid4().hex}"
    path.mkdir(mode=0o700)
    try:
        yield path
    finally:
        for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if not child.is_symlink():
                child.chmod(0o700 if child.is_dir() else 0o600)
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
            "bindings": bindings,
            "rollback_journal": [],
        }

    def rollback_complete_actor_contracts(self, receipt):
        for binding in receipt["bindings"]:
            path = Path(binding["destination"])
            if path.is_symlink():
                path.unlink()


def _fixture(tmp_path, *, admission_expires_at="2026-08-22T00:00:00Z"):
    state, releases, admissions, generations, workspaces, caches, startup_bindings = (
        tmp_path / name for name in ("state", "releases", "admissions", "generations", "workspaces", "caches", "startup-bindings")
    )
    for path in (state, releases, admissions, generations, workspaces, caches, startup_bindings):
        path.mkdir()
    startup_bindings.chmod(0o755)
    workspaces.chmod(0o2770)
    caches.chmod(0o2770)
    actor_group = grp.getgrgid(workspaces.stat().st_gid).gr_name
    actor = pwd.getpwuid(os.getuid()).pw_name
    source_commit, plan_commit = "e" * 40, "f" * 40
    solution = "sha256:" + "1" * 64
    release = releases / "candidate"
    release.mkdir()
    source_tree = "d" * 40
    for name, content in {
        "launcher": "actor launcher\n",
        "bootstrap.json": '{"status":"PASS"}\n',
        "mcp.json": '{"endpoint":"tgw-context"}\n',
        "skill/SKILL.md": "bounded plan\n",
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
        "bootstrap_revision": {"content_sha256": "sha256:" + "3" * 64},
        "broker_policy_revision": {"content_sha256": "sha256:" + "4" * 64},
        "flake_lock": {"path": "flake.lock", "sha256": "1" * 64},
        "actors": {
            actor: {
                "enabled": True,
                "permitted_profiles": ["development"],
                "required_skills": ["tgw-plan"],
                "required_hooks": [],
                "required_mcp_endpoints": ["tgw-context"],
            }
        },
        "profiles": {
            "development": {
                "state": "ready-for-preflight",
                "broker_capabilities": ["plan-read", "source-read"],
            }
        },
    }
    environment_path = generation / "environment-catalog.json"
    environment_path.write_text(json.dumps(environment))
    catalog = _hash(environment)
    launcher_hash = _file_hash(release / "launcher")
    skill_hash = _tree_hash(release / "skill")
    mcp_hash = _file_hash(release / "mcp.json")
    mcp_destination = home / ".mcp/tgw-context.json"
    launcher_local = {"path": str(destination), "sha256": launcher_hash}
    mcp_local = {
        "endpoints": ["tgw-context"],
        "binding_hash": _hash(
            [
                {
                    "endpoint": "tgw-context",
                    "source_sha256": mcp_hash,
                    "destination": str(mcp_destination),
                }
            ]
        ),
    }
    bootstrap_body = {
        "schema": "tgw-actor-bootstrap-receipt/v1",
        "status": "READY",
        "actor": actor,
        "profile": "development",
        "generation": generation_hash,
        "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {"commit": source_commit, "tree": source_tree, "freshness_hash": "sha256:" + "3" * 64},
        "declared_policy_hash": "sha256:" + "4" * 64,
        "launcher": launcher_local,
        "skills": {"tgw-plan": skill_hash},
        "hooks": {},
        "mcp": mcp_local,
    }
    (release / "bootstrap.json").write_text(json.dumps({**bootstrap_body, "receipt_hash": _hash(bootstrap_body)}))
    files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(release.rglob("*"))
        if path.is_file()
    }
    content_hash = hashlib.sha256(
        (json.dumps(dict(sorted(files.items())), sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    (release / ".release-manifest.json").write_text(
        json.dumps(
            {
                "schema": "tgw-release-manifest-v1",
                "generation": "candidate",
                "commit": source_commit,
                "tree": f"exact-git-archive:{source_commit}",
                "git_tree": source_tree,
                "src_root": "src",
                "archive_sha256": "a" * 64,
                "content_manifest_sha256": content_hash,
                "file_count": len(files),
                "files": files,
            }
        )
    )
    for path in sorted(release.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
    release.chmod(0o555)
    bootstrap_hash = _file_hash(release / "bootstrap.json")
    contract = {
        "actor": actor,
        "catalog_hash": catalog,
        "plan": {"commit": plan_commit, "solution_hash": solution},
        "code_graph": {"commit": source_commit},
        "profile": "development",
        "local": {
            "bootstrap_receipt_hash": bootstrap_hash,
            "launcher": launcher_local,
            "skills": {"tgw-plan": skill_hash},
            "hooks": {},
            "mcp": mcp_local,
        },
    }
    bindings = [
        {"kind": "skill", "name": "tgw-plan", "source": "skill", "destination": str(home / ".skills/tgw-plan"), "sha256": skill_hash},
        {"kind": "mcp", "name": "tgw-context", "source": "mcp.json", "destination": str(mcp_destination), "sha256": mcp_hash},
        {"kind": "launcher", "name": "launcher", "source": "launcher", "destination": str(destination), "sha256": launcher_hash},
        {"kind": "bootstrap", "name": "bootstrap-receipt", "source": "bootstrap.json", "destination": str(home / ".tgw/bootstrap.json"), "sha256": bootstrap_hash},
        {
            "kind": "environment",
            "name": "environment-catalog",
            "source": str(environment_path),
            "destination": str(home / ".tgw/execution-environment-catalog.json"),
            "sha256": _file_hash(environment_path),
        },
    ]
    bundle = {
        "schema": "tgw-complete-actor-contract-bundle/v1",
        "generation": generation_hash,
        "actors": {actor: {"home": str(home), "project": str(project), "bindings": bindings}},
    }
    (generation / "bundle.json").write_text(json.dumps(bundle))
    (generation / "contracts" / f"{actor}.json").write_text(json.dumps(contract))
    receipt_unsigned = {
        "schema": "tgw-w18-actor-generation-receipt/v1",
        "status": "PREPARED",
        "generation": generation_hash,
        "actors": [actor],
        "bundle_hash": _hash(bundle),
        "signer_public_key": "fixture-key",
        "generation_identity": {
            "catalog_hash": catalog,
            "plan_commit": plan_commit,
            "solution_hash": solution,
            "source_commit": source_commit,
        },
    }
    (generation / "generation-receipt.json").write_text(json.dumps({**receipt_unsigned, "receipt_hash": _hash(receipt_unsigned)}))
    admission_key = Ed25519PrivateKey.generate()
    admission = compile_release_admission(
        request={
            "schema": "tgw-w16-release-admission-request/v1",
            "request_id": "actor-fleet-fixture",
            "candidate": {"commit": source_commit, "tree": source_tree},
            "plan": {"commit": plan_commit, "solution_hash": solution},
            "environment": {"catalog_hash": catalog, "receipt_hash": "sha256:" + "5" * 64},
            "review": {
                "status": "PASS",
                "candidate_commit": source_commit,
                "solution_hash": solution,
                "receipt_hash": "sha256:" + "6" * 64,
            },
            "admission": {
                "status": "PASS",
                "candidate_commit": source_commit,
                "solution_hash": solution,
                "receipt_hash": "sha256:" + "7" * 64,
            },
        },
        signing_private_key=admission_key,
        signer_key_id="actor-fixture",
        issued_at="2026-08-21T00:00:00Z",
        expires_at=admission_expires_at,
    )
    (admissions / (admission["receipt_hash"].removeprefix("sha256:") + ".json")).write_text(json.dumps(admission))
    admission_public_key = tmp_path / "release-admission.pub"
    admission_public_key.write_bytes(admission_key.public_key().public_bytes_raw())
    admission_public_key.chmod(0o444)
    systemctl = tmp_path / "systemctl"
    systemctl.write_text("fixture\n")
    config = {
        "schema": "tgw-actor-fleet-provider/v1",
        "token_sha256": "sha256:" + hashlib.sha256(b"secret").hexdigest(),
        "state_root": str(state),
        "release_root": str(releases),
        "admission_root": str(admissions),
        "actor_generation_root": str(generations),
        "admission_public_key": str(admission_public_key),
        "contract_public_key": "fixture-key",
        "startup_binding_root": str(startup_bindings),
        "actor_group": actor_group,
        "attempt_workspace_root": str(workspaces),
        "attempt_cache_root": str(caches),
        "systemctl_path": str(systemctl),
        "managed_services": ["tgw-coding-provision-pull.timer"],
        "quiescence_units": ["tgw-coding-provision-pull.service"],
    }
    request = {
        "schema": "tgw-w18-fleet-refresh-request/v1",
        "transaction_id": "refresh-one",
        "idempotency_key": "refresh-one",
        "predecessor_generation": "sha256:" + "a" * 64,
        "successor_generation": generation_hash,
        "revisions": {
            "plan": plan_commit,
            "solution": solution,
            "source": source_commit,
            "catalog": catalog,
            "bootstrap": "sha256:" + "3" * 64,
            "broker_policy": "sha256:" + "4" * 64,
            "admission": admission["receipt_hash"],
        },
        "actors": [actor],
    }
    return config, request, destination


def test_actor_provider_holds_on_drifted_attempt_root(durable_path):
    config, _request, _destination = _fixture(durable_path)
    Path(config["attempt_cache_root"]).chmod(0o750)
    with pytest.raises(ValueError, match="mode 2770"):
        ActorFleetProvider(config)


def test_actor_provider_materializes_verifies_repairs_and_rolls_back(durable_path):
    config, request, destination = _fixture(durable_path)
    service_state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            service_state["value"] = "inactive"
        if arguments[0] == "restart":
            service_state["value"] = "active"
        return subprocess.CompletedProcess(arguments, 0, service_state["value"] + "\n", "")

    provider = ActorFleetProvider(
        config,
        service_runner=service,
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    assert provider.quiesce(request)["status"] == "QUIESCED"
    rebuilt = provider.rebuild(request)
    activated = provider.activate(request, rebuilt)
    restarted = provider.restart(activated)
    assert provider.health(restarted)["status"] == "HEALTHY"
    assert provider.verify_actor(request["actors"][0], request)["status"] == "VERIFIED"
    assert provider.repair(request)["status"] == "REPAIRED"
    assert destination.is_symlink()
    startup = Path(config["startup_binding_root"]) / f"{request['actors'][0]}-startup.json"
    assert json.loads(startup.read_text())["expected_generation"] == request["successor_generation"]
    assert startup.stat().st_mode & 0o022 == 0
    assert provider.rollback(request)["status"] == "ROLLED_BACK"
    assert not destination.exists()
    assert not startup.exists()


def test_actor_provider_rejects_forged_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path)
    admission_path = Path(config["admission_root"]) / (
        request["revisions"]["admission"].removeprefix("sha256:") + ".json"
    )
    admission = json.loads(admission_path.read_text())
    admission["signature"] = "A" + admission["signature"][1:]
    admission_path.write_text(json.dumps(admission))
    state = {"value": "active"}

    def service(arguments):
        if arguments[0] == "stop":
            state["value"] = "inactive"
        return subprocess.CompletedProcess(arguments, 0, state["value"] + "\n", "")

    provider = ActorFleetProvider(
        config,
        service_runner=service,
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    provider.quiesce(request)
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        provider.rebuild(request)


def test_actor_provider_rejects_expired_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path, admission_expires_at="2026-08-21T11:00:00Z")
    provider = ActorFleetProvider(
        config,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    provider.quiesce(request)
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        provider.rebuild(request)


def test_actor_provider_rejects_cross_tree_signed_admission(durable_path):
    config, request, _destination = _fixture(durable_path)
    manifest_path = Path(config["release_root"]) / "candidate/.release-manifest.json"
    manifest_path.chmod(0o644)
    manifest = json.loads(manifest_path.read_text())
    manifest["git_tree"] = "c" * 40
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o444)
    provider = ActorFleetProvider(
        config,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    provider.quiesce(request)
    with pytest.raises(ValueError, match="admission receipt is not exact"):
        provider.rebuild(request)


def test_actor_provider_rejects_manifest_content_drift(durable_path):
    config, request, _destination = _fixture(durable_path)
    launcher = Path(config["release_root"]) / "candidate/launcher"
    launcher.chmod(0o644)
    launcher.write_text("drifted actor launcher\n")
    launcher.chmod(0o444)
    provider = ActorFleetProvider(
        config,
        service_runner=lambda arguments: subprocess.CompletedProcess(arguments, 0, "inactive\n", ""),
        materializer_loader=lambda _: _Materializer(),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    provider.quiesce(request)
    with pytest.raises(ValueError, match="content does not match its manifest"):
        provider.rebuild(request)


def test_actor_provider_http_rejects_auth_and_unbound_invocation(durable_path):
    config, request, _destination = _fixture(durable_path)
    app = create_actor_fleet_app(
        {"actor_fleet_provider": config},
        materializer_loader=lambda _: _Materializer(),
        service_runner=lambda args: subprocess.CompletedProcess(args, 0, "inactive\n", ""),
        current_time=lambda: datetime(2026, 8, 21, 12, tzinfo=timezone.utc),
    )
    client = TestClient(app)
    invocation = {"schema": "tgw-actor-fleet-provider-invocation/v1", "step": "quiesce", "arguments": [request]}
    body = {**invocation, "invocation_hash": _hash(invocation)}
    assert client.post("/v1/actor-fleet/quiesce", json=body).status_code == 401
    assert (
        client.post(
            "/v1/actor-fleet/quiesce",
            json={**body, "invocation_hash": "sha256:" + "0" * 64},
            headers={"Authorization": "Bearer secret"},
        ).status_code
        == 409
    )
