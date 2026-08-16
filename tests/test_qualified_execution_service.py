"""Qualified execution is a service-side proof, never a client-side receipt."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.qualified_execution_service import (
    SERVICE_CATALOG_SCHEMA,
    SERVICE_DESCRIPTOR_SCHEMA,
    Client,
    QualifiedExecutionClient,
    QualifiedExecutionConfig,
    QualifiedExecutionError,
    QualifiedExecutionService,
    _run_bounded,
    create_qualified_execution_server,
    execution_public_key,
    execution_service_descriptor_hash,
    validate_execution_proof,
)


def _hash_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _write(path: Path, value: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    if executable:
        path.chmod(0o700)


def _wrapper(path: Path) -> Path:
    _write(path, '#!/bin/sh\nexec /opt/TGW/.venvs/controller/bin/python "$@"\n', executable=True)
    return path


def _repository(tmp_path: Path) -> tuple[Path, str, str, str, str, dict[str, object]]:
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "execution@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Qualified execution"], cwd=repo, check=True)
    _write(repo / "tests" / "test_candidate.py", "def test_candidate():\n    assert 2 + 2 == 4\n")
    _write(repo / "scripts" / "candidate-runner.py", "# hash-bound candidate runner\n")
    runner_hash = _hash_bytes((repo / "scripts" / "candidate-runner.py").read_bytes())
    plan = {
        "schema": "tgw-candidate-test-plan/v1",
        "plan_id": "qualified-execution-test",
        "version": 1,
        "runner": {"path": "scripts/candidate-runner.py", "sha256": runner_hash, "argv_prefix": ["-m", "pytest"]},
        "scopes": {"focused": {"argv": ["-q", "tests/test_candidate.py"]}, "full": {"argv": ["-q"]}},
    }
    _write(repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json", json.dumps(plan, sort_keys=True))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base, base_tree = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")
    _write(repo / "migrations" / "001.sql", "CREATE TABLE qualified_execution(id integer);\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    candidate, tree = _git(repo, "rev-parse", "HEAD"), _git(repo, "rev-parse", "HEAD^{tree}")
    return repo, candidate, tree, base, base_tree, plan


def _migration_runner(path: Path, python: Path) -> Path:
    # This fixture runner simulates an external isolated PostgreSQL runner.  It
    # independently writes a complete migration receipt; the service validates
    # that receipt against the Git objects it resolved itself.
    source = f"""#!{python}
import argparse, hashlib, json, subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--repo", required=True)
parser.add_argument("--commit", required=True)
parser.add_argument("--base-commit", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
def git(*items):
    return subprocess.check_output(["/usr/bin/git", *items], cwd=args.repo).decode().strip()
def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()
candidate_tree = git("rev-parse", args.commit + "^{{tree}}")
base_tree = git("rev-parse", args.base_commit + "^{{tree}}")
migration_path = "migrations/001.sql"
migration = subprocess.check_output(["/usr/bin/git", "show", args.commit + ":" + migration_path], cwd=args.repo)
stable = b"qualified fixture postgres state"
receipt = {{
    "schema": "tgw-database-migration-receipt/v2", "candidate_commit": args.commit,
    "candidate_tree": candidate_tree, "base_commit": args.base_commit, "base_tree": base_tree,
    "migration_path": migration_path, "migration_sha256": digest(migration),
    "schema_snapshot_path": None, "schema_snapshot_sha256": None,
    "postgres_version": "PostgreSQL 17.10", "backup_sha256": digest(stable),
    "source_schema_sha256": digest(stable), "restored_schema_sha256": digest(stable),
    "source_data_sha256": digest(stable), "restored_data_sha256": digest(stable),
    "migrated_schema_sha256": digest(stable), "migrated_data_sha256": digest(stable), "verified": True,
}}
receipt["receipt_hash"] = digest(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
open(args.output, "w", encoding="utf-8").write(json.dumps(receipt, sort_keys=True))
print("qualified migration PASS")
"""
    _write(path, source, executable=True)
    return path


def _review_runner(path: Path, python: Path) -> Path:
    source = f"""#!{python}
import argparse, hashlib, json
parser = argparse.ArgumentParser()
parser.add_argument("--review-packet", required=True)
args = parser.parse_args()
packet = json.load(open(args.review_packet, encoding="utf-8"))
result = {{"packet_hash": packet["packet_hash"], "result_hash": "sha256:" + hashlib.sha256(b"service-review-result").hexdigest()}}
print(json.dumps(result, sort_keys=True))
"""
    _write(path, source, executable=True)
    return path


def _configured_service(tmp_path: Path):
    repo, candidate, tree, base, base_tree, plan = _repository(tmp_path)
    plan_repo = tmp_path / "approved-plan"
    plan_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=plan_repo, check=True)
    subprocess.run(["git", "config", "user.email", "plan@example.invalid"], cwd=plan_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Qualified Plan authority"], cwd=plan_repo, check=True)
    _write(plan_repo / "approved-plan.txt", "qualified execution external Plan\n")
    subprocess.run(["git", "add", "."], cwd=plan_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "approved Plan"], cwd=plan_repo, check=True)
    plan_commit = _git(plan_repo, "rev-parse", "HEAD")
    policy = tmp_path / "policy"
    python = _wrapper(policy / "python")
    dependencies = policy / "dependencies.lock"
    _write(dependencies, "pytest==9.1.1\n")
    migration = _migration_runner(policy / "migration-runner", python)
    review = _review_runner(policy / "review-runner", python)
    plan_source = (repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json").read_bytes()
    config = {
        "schema": "tgw-qualified-execution-service-config/v1",
        "service_id": "qualified-test-service",
        "repository": str(repo),
        "plan_repository": str(plan_repo),
        "plan_commit": plan_commit,
        "max_active_requests": 2,
        "clients": [
            {
                "id": "fixture-client",
                "credential_env": "QEXEC_TOKEN",
                "profiles": ["focused-tests", "migration", "review"],
            }
        ],
        "attestation_key_id": "fixture-key",
        "attestation_private_key_env": "QEXEC_SIGNING_KEY",
        "runtime": {
            "interpreter_path": str(python),
            "interpreter_sha256": _hash_bytes(python.read_bytes()),
            "dependency_manifest_path": str(dependencies),
            "dependency_manifest_sha256": _hash_bytes(dependencies.read_bytes()),
            "environment": {"LANG": "C.UTF-8"},
        },
        "profiles": [
            {
                "id": "focused-tests",
                "kind": "test",
                "timeout_seconds": 30,
                "max_output_bytes": 256 * 1024,
                "scope": "focused",
                "test_plan_sha256": _hash_bytes(plan_source),
                "test_runner_path": plan["runner"]["path"],
                "test_runner_sha256": plan["runner"]["sha256"],
                "command": ["-m", "pytest", "-q", "tests/test_candidate.py"],
            },
            {
                "id": "migration",
                "kind": "migration",
                "timeout_seconds": 30,
                "max_output_bytes": 256 * 1024,
                "migration_path": "migrations/001.sql",
                "schema_snapshot_path": None,
                "runner_path": str(migration),
                "runner_sha256": _hash_bytes(migration.read_bytes()),
            },
            {
                "id": "review",
                "kind": "review",
                "timeout_seconds": 30,
                "max_output_bytes": 256 * 1024,
                "runner_path": str(review),
                "runner_sha256": _hash_bytes(review.read_bytes()),
            },
        ],
    }
    private_key = Ed25519PrivateKey.generate()
    parsed = QualifiedExecutionConfig.parse(config)
    descriptor = {
        "schema": SERVICE_DESCRIPTOR_SCHEMA,
        "id": parsed.service_id,
        "client_id": "fixture-client",
        "endpoint": "http://127.0.0.1:1",
        "credential_env": "QEXEC_TOKEN",
        "timeout_seconds": 30,
    }
    catalog = {
        "schema": SERVICE_CATALOG_SCHEMA,
        "catalog_ref": "catalog:fixture-qualified-execution@1",
        "plan_commit": parsed.plan_commit,
        "services": [
            {
                "id": parsed.service_id,
                "client_id": "fixture-client",
                "descriptor_hash": execution_service_descriptor_hash(descriptor),
                "capabilities": ["candidate-review-execution", "candidate-test-execution", "postgresql-migration-execution"],
                "attestation_key_id": "fixture-key",
                "attestation_public_key": execution_public_key(private_key),
            }
        ],
    }
    return parsed, candidate, tree, base, base_tree, descriptor, catalog, private_key


def _request_values(candidate: str, tree: str, base: str, base_tree: str, plan_commit: str) -> dict[str, object]:
    return {
        "candidate_commit": candidate,
        "candidate_tree": tree,
        "base_commit": base,
        "base_tree": base_tree,
        "plan_commit": plan_commit,
        "profiles": ["focused-tests", "migration", "review"],
        "review_packet": {"packet_hash": "sha256:" + "1" * 64, "candidate": candidate},
    }


def test_service_executes_only_policy_bound_profiles_and_signs_complete_proofs(tmp_path):
    config, candidate, tree, base, base_tree, descriptor, catalog, private_key = _configured_service(tmp_path)
    server = create_qualified_execution_server(config, {"fixture-client": "client-secret"}, signing_private_key=private_key)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        descriptor = {**descriptor, "endpoint": f"http://127.0.0.1:{server.server_port}"}
        client = QualifiedExecutionClient(descriptor, environment={"QEXEC_TOKEN": "client-secret"})
        response = client.execute(**_request_values(candidate, tree, base, base_tree, config.plan_commit))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert [item["proof"]["kind"] for item in response["results"]] == ["test", "migration", "review"]
    for item in response["results"]:
        proof = validate_execution_proof(item["proof"], item["transcript"], catalog=catalog)
        assert proof["status"] == "PASS"
        assert item["transcript"]["output_complete"] is True
    assert response["results"][1]["migration_receipt"]["verified"] is True
    review = response["results"][2]
    assert review["proof"]["inputs"]["review_packet_content_sha256"] == _hash_bytes(
        json.dumps(_request_values(candidate, tree, base, base_tree, config.plan_commit)["review_packet"], sort_keys=True, separators=(",", ":")).encode()
    )


def test_service_and_validator_fail_closed_for_forged_identity_inputs_and_key(tmp_path):
    config, candidate, tree, base, base_tree, _descriptor, catalog, private_key = _configured_service(tmp_path)
    service = QualifiedExecutionService(config, {"fixture-client": "client-secret"}, signing_private_key=private_key)
    client = service.client_for_authorization("Bearer client-secret")
    assert client is not None
    values = {
        "schema": "tgw-qualified-execution-request/v1",
        "service_id": config.service_id,
        "client_id": client.client_id,
        **_request_values(candidate, tree, base, base_tree, config.plan_commit),
    }
    with pytest.raises(ValueError, match="tree binding"):
        service.execute({**values, "candidate_tree": "0" * 40}, client)
    with pytest.raises(ValueError, match="Plan"):
        service.execute({**values, "plan_commit": "b" * 40}, client)
    with pytest.raises(ValueError, match="client identity"):
        service.execute({**values, "client_id": "forged-client"}, client)
    assert service._slots.acquire(blocking=False)
    try:
        assert service._slots.acquire(blocking=False)
        try:
            with pytest.raises(ValueError, match="capacity"):
                service.execute(values, client)
        finally:
            service._slots.release()
    finally:
        service._slots.release()
    restricted = replace(
        config,
        clients={"fixture-client": Client("fixture-client", "QEXEC_TOKEN", ("focused-tests",))},
    )
    restricted_service = QualifiedExecutionService(restricted, {"fixture-client": "client-secret"}, signing_private_key=private_key)
    restricted_client = restricted_service.client_for_authorization("Bearer client-secret")
    assert restricted_client is not None
    with pytest.raises(ValueError, match="not granted"):
        restricted_service.execute(values, restricted_client)
    result = service.execute(values, client)["results"][0]
    forged_inputs = {**result["proof"], "inputs": {"scope": "forged"}}
    with pytest.raises(QualifiedExecutionError, match="proof hash"):
        validate_execution_proof(forged_inputs, result["transcript"], catalog=catalog)
    forged_runtime = {
        **result["proof"],
        "runtime": {**result["proof"]["runtime"], "environment": {"LANG": "forged"}},
    }
    with pytest.raises(QualifiedExecutionError, match="environment hash"):
        validate_execution_proof(forged_runtime, result["transcript"], catalog=catalog)
    wrong_key_catalog = json.loads(json.dumps(catalog))
    wrong_key_catalog["services"][0]["attestation_public_key"] = execution_public_key(Ed25519PrivateKey.generate())
    with pytest.raises(QualifiedExecutionError, match="signature"):
        validate_execution_proof(result["proof"], result["transcript"], catalog=wrong_key_catalog)
    with pytest.raises(QualifiedExecutionError, match="expected"):
        validate_execution_proof(result["proof"], result["transcript"], catalog=catalog, expected={"command": ["forged"]})


def test_bounded_runner_marks_truncation_and_timeout_as_non_passing(tmp_path):
    python = _wrapper(tmp_path / "policy" / "python")
    overflow = _run_bounded([str(python), "-c", "print('x' * 10000)"], cwd=tmp_path, environment={}, timeout_seconds=5, max_output_bytes=64)
    assert overflow.output_complete is False
    timeout = _run_bounded([str(python), "-c", "import time; time.sleep(10)"], cwd=tmp_path, environment={}, timeout_seconds=1, max_output_bytes=1024)
    assert timeout.timed_out is True
