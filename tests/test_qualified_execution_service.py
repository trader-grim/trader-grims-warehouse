"""The signer accepts only a separately authenticated confined runner."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.candidate_manifest import create_test_output_artifact, create_test_receipt, load_candidate_test_plan
from tgw.qualified_execution_service import (
    POLICY_SCHEMA,
    REQUEST_SCHEMA,
    RUNNER_DESCRIPTOR_SCHEMA,
    RUNNER_IDENTITY_REQUEST_SCHEMA,
    RUNNER_RESPONSE_SCHEMA,
    SERVICE_CATALOG_SCHEMA,
    SERVICE_CONFIG_SCHEMA,
    SERVICE_DESCRIPTOR_SCHEMA,
    TRANSCRIPT_SCHEMA,
    QualifiedExecutionClient,
    QualifiedExecutionConfig,
    QualifiedExecutionConfigurationError,
    QualifiedExecutionError,
    QualifiedExecutionService,
    create_qualified_execution_server,
    execution_public_key,
    execution_service_descriptor_hash,
    issue_runner_identity,
    issue_runner_transcript,
    qualified_runner_descriptor_hash,
    validate_execution_proof,
)


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def candidate_repo(tmp_path: Path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    for command in (("git", "init", "-q"), ("git", "config", "user.email", "runner@example.invalid"), ("git", "config", "user.name", "Confined runner")):
        subprocess.run(command, cwd=repo, check=True)
    write(repo / "tests" / "test_candidate.py", "def test_candidate():\n    assert True\n")
    write(repo / "scripts" / "candidate-test-runner.py", "# candidate runner source\n")
    runner_hash = digest((repo / "scripts" / "candidate-test-runner.py").read_bytes())
    test_plan = {
        "schema": "tgw-candidate-test-plan/v1",
        "plan_id": "runner-signer-fixture",
        "version": 1,
        "runner": {"path": "scripts/candidate-test-runner.py", "sha256": runner_hash, "argv_prefix": ["-m", "pytest"]},
        "scopes": {"focused": {"argv": ["-q", "tests/test_candidate.py"]}, "full": {"argv": ["-q"]}},
    }
    write(repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json", json.dumps(test_plan, sort_keys=True))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=repo, check=True)
    base, base_tree = git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")
    write(repo / "candidate.py", "VALUE = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}"), base, base_tree, test_plan


def runner_runtime():
    environment = {"LANG": "C.UTF-8"}
    return {
        "runner_path": "/runner/bin/qualified-runner",
        "runner_sha256": "sha256:" + "1" * 64,
        "interpreter_path": "/runner/bin/python",
        "interpreter_sha256": "sha256:" + "2" * 64,
        "interpreter_version_sha256": "sha256:" + "3" * 64,
        "dependency_manifest_path": "/runner/dependencies.lock",
        "dependency_manifest_sha256": "sha256:" + "4" * 64,
        "environment": environment,
        "environment_hash": digest(canonical(environment)),
    }


class MockConfinedRunner:
    """Separately keyed endpoint representing an externally confined runner."""

    def __init__(
        self,
        *,
        repo: Path,
        candidate: str,
        tree: str,
        base: str,
        base_tree: str,
        plan_commit: str,
        policy_path: str,
        policy_hash: str,
        policy: dict,
        runner_key: Ed25519PrivateKey,
        timeout_ok: bool = True,
        output_limit_ok: bool = True,
        runtime_rehashed: bool = True,
        drift_after_identity: bool = False,
        result_signing_key: Ed25519PrivateKey | None = None,
    ) -> None:
        self.repo, self.candidate, self.tree, self.base, self.base_tree = repo, candidate, tree, base, base_tree
        self.policy, self.key, self.timeout_ok = policy, runner_key, timeout_ok
        self.output_limit_ok, self.runtime_rehashed = output_limit_ok, runtime_rehashed
        self.drift_after_identity = drift_after_identity
        self.result_key = result_signing_key or runner_key
        self.token = "runner-to-signer-secret"
        self.descriptor = {
            "schema": RUNNER_DESCRIPTOR_SCHEMA,
            "id": "confined-fixture-runner",
            "runner_identity": "confined-runner-identity",
            "namespace_id": "confined-runner-namespace",
            "endpoint": "http://127.0.0.1:0",
            "credential_env": "RUNNER_TOKEN",
            "timeout_seconds": 10,
            "attestation_key_id": "runner-fixture-key",
            "attestation_public_key": execution_public_key(runner_key),
            "isolation_profile_hash": "sha256:" + "9" * 64,
            "plan_commit": plan_commit,
            "policy_path": policy_path,
            "policy_artifact_hash": policy_hash,
            "profiles": [item["id"] for item in policy["profiles"]],
        }
        self.server = self._server()
        self.descriptor = {**self.descriptor, "endpoint": f"http://127.0.0.1:{self.server.server_port}"}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def runtime(self):
        return runner_runtime()

    @property
    def result_runtime(self):
        if not self.drift_after_identity:
            return self.runtime
        return {**self.runtime, "runner_sha256": "sha256:" + "d" * 64}

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _package(self, request: dict, profile_id: str):
        profile = next(item for item in self.policy["profiles"] if item["id"] == profile_id)
        plan = load_candidate_test_plan(self.repo, source_commit=self.candidate)
        output = create_test_output_artifact(
            scope=profile["scope"],
            command=profile["command"],
            source_commit=self.candidate,
            source_tree=self.tree,
            stdout=b"confined runner PASS\n",
            stderr=b"",
        )
        receipt = create_test_receipt(
            scope=profile["scope"],
            command=profile["command"],
            source_commit=self.candidate,
            source_tree=self.tree,
            returncode=0,
            test_plan=plan,
            output_artifact=output,
        )
        inputs = {
            "scope": profile["scope"],
            "test_plan_path": plan["path"],
            "test_plan_sha256": plan["sha256"],
            "test_runner_path": plan["runner_path"],
            "test_runner_sha256": plan["runner_sha256"],
            "test_receipt_hash": receipt["receipt_hash"],
            "test_output_artifact_hash": output["artifact_hash"],
        }
        stdout, stderr = b"confined runner PASS\n", b""
        transcript = issue_runner_transcript(
            {
                "schema": TRANSCRIPT_SCHEMA,
                "runner_id": self.descriptor["id"],
                "runner_identity": self.descriptor["runner_identity"],
                "namespace_id": self.descriptor["namespace_id"],
                "runner_identity_hash": request["runner_identity_hash"],
                "service_id": request["service_id"],
                "client_id": request["client_id"],
                "run_id": "runner-result-focused",
                "profile_id": profile_id,
                "kind": "test",
                "candidate_commit": self.candidate,
                "candidate_tree": self.tree,
                "base_commit": self.base,
                "base_tree": self.base_tree,
                "plan_commit": request["plan_commit"],
                "policy_path": request["policy_path"],
                "policy_artifact_hash": request["policy_artifact_hash"],
                "inputs": inputs,
                "runtime": self.result_runtime,
                "command": ["/runner/bin/python", *profile["command"]],
                "stdout_base64": base64.b64encode(stdout).decode(),
                "stderr_base64": "",
                "stdout_sha256": digest(stdout),
                "stderr_sha256": digest(stderr),
                "output_hash": digest(stdout + b"\0" + stderr),
                "output_complete": True,
                "returncode": 0,
                "timed_out": False,
                "timeout_enforced": self.timeout_ok,
                "output_limit_enforced": self.output_limit_ok,
                "runtime_rehashed_before_dispatch": self.runtime_rehashed,
                "isolated": True,
                "isolation_profile_hash": self.descriptor["isolation_profile_hash"],
                "status": "PASS",
                "attestation_key_id": self.descriptor["attestation_key_id"],
            },
            signing_private_key=self.result_key,
        )
        return {"transcript": transcript, "test_receipt": receipt, "test_output": output}

    def _server(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_POST(self):  # noqa: N802
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_error(404)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = json.loads(raw)
                if self.path == "/v1/identity":
                    assert request["schema"] == RUNNER_IDENTITY_REQUEST_SCHEMA
                    response = issue_runner_identity(
                        {
                            "schema": "tgw-qualified-runner-identity/v1",
                            "runner_id": outer.descriptor["id"],
                            "runner_identity": outer.descriptor["runner_identity"],
                            "namespace_id": outer.descriptor["namespace_id"],
                            "nonce": request["nonce"],
                            "plan_commit": request["plan_commit"],
                            "policy_path": request["policy_path"],
                            "policy_artifact_hash": request["policy_artifact_hash"],
                            "isolation_profile_hash": outer.descriptor["isolation_profile_hash"],
                            "runtime": outer.runtime,
                            "attestation_key_id": outer.descriptor["attestation_key_id"],
                        },
                        signing_private_key=outer.key,
                    )
                elif self.path == "/v1/execute":
                    response = {"schema": RUNNER_RESPONSE_SCHEMA, "runner_id": outer.descriptor["id"], "results": [outer._package(request, profile) for profile in request["profiles"]]}
                else:
                    self.send_error(404)
                    return
                body = canonical(response)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def configured(
    tmp_path: Path,
    *,
    timeout_ok: bool = True,
    output_limit_ok: bool = True,
    runtime_rehashed: bool = True,
    drift_after_identity: bool = False,
    result_signing_key: Ed25519PrivateKey | None = None,
):
    repo, candidate, tree, base, base_tree, test_plan = candidate_repo(tmp_path)
    plan_repo = tmp_path / "plan"
    plan_repo.mkdir()
    for command in (("git", "init", "-q"), ("git", "config", "user.email", "plan@example.invalid"), ("git", "config", "user.name", "Plan")):
        subprocess.run(command, cwd=plan_repo, check=True)
    runner_key = Ed25519PrivateKey.generate()
    policy_runtime = runner_runtime()
    policy = {
        "schema": POLICY_SCHEMA,
        "policy_id": "fixture-policy",
        "runtime": policy_runtime,
        "profiles": [
            {
                "id": "focused",
                "kind": "test",
                "timeout_seconds": 30,
                "max_output_bytes": 65536,
                "scope": "focused",
                "test_plan_sha256": digest((repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json").read_bytes()),
                "test_runner_path": test_plan["runner"]["path"],
                "test_runner_sha256": test_plan["runner"]["sha256"],
                "command": ["-m", "pytest", "-q", "tests/test_candidate.py"],
            }
        ],
    }
    policy_path = "policies/qualified-execution.json"
    policy_source = canonical(policy)
    write(plan_repo / policy_path, policy_source.decode())
    subprocess.run(["git", "add", "."], cwd=plan_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "policy"], cwd=plan_repo, check=True)
    plan_commit = git(plan_repo, "rev-parse", "HEAD")
    runner = MockConfinedRunner(
        repo=repo,
        candidate=candidate,
        tree=tree,
        base=base,
        base_tree=base_tree,
        plan_commit=plan_commit,
        policy_path=policy_path,
        policy_hash=digest(policy_source),
        policy=policy,
        runner_key=runner_key,
        timeout_ok=timeout_ok,
        output_limit_ok=output_limit_ok,
        runtime_rehashed=runtime_rehashed,
        drift_after_identity=drift_after_identity,
        result_signing_key=result_signing_key,
    )
    signer_key = Ed25519PrivateKey.generate()
    descriptor = {
        "schema": SERVICE_DESCRIPTOR_SCHEMA,
        "id": "fixture-signer",
        "client_id": "fixture-client",
        "endpoint": "http://127.0.0.1:1",
        "credential_env": "SIGNER_TOKEN",
        "timeout_seconds": 10,
    }
    config = {
        "schema": SERVICE_CONFIG_SCHEMA,
        "service_id": "fixture-signer",
        "signer_identity": "fixture-signer-identity",
        "signer_namespace_id": "fixture-signer-namespace",
        "repository": str(repo),
        "plan_repository": str(plan_repo),
        "plan_commit": plan_commit,
        "policy_path": policy_path,
        "policy_artifact_hash": digest(policy_source),
        "runner": runner.descriptor,
        "clients": [
            {
                "id": "fixture-client",
                "credential_env": "SIGNER_TOKEN",
                "descriptor_hash": execution_service_descriptor_hash(descriptor),
                "profiles": ["focused"],
            }
        ],
        "max_active_requests": 1,
        "max_retained_proofs_per_client": 2,
        "attestation_key_id": "signer-fixture-key",
        "attestation_private_key_env": "SIGNER_KEY",
    }
    parsed = QualifiedExecutionConfig.parse(config)
    catalog = {
        "schema": SERVICE_CATALOG_SCHEMA,
        "catalog_ref": "catalog:fixture@2",
        "plan_commit": plan_commit,
        "policy_artifact_hash": parsed.policy.artifact_hash,
        "services": [
            {
                "id": parsed.service_id,
                "client_id": "fixture-client",
                "signer_identity": parsed.signer_identity,
                "signer_namespace_id": parsed.signer_namespace_id,
                "descriptor_hash": execution_service_descriptor_hash(descriptor),
                "runner_descriptor_hash": qualified_runner_descriptor_hash(runner.descriptor),
                "policy_artifact_hash": parsed.policy.artifact_hash,
                "capabilities": ["candidate-test-execution"],
                "attestation_key_id": "signer-fixture-key",
                "attestation_public_key": execution_public_key(signer_key),
            }
        ],
    }
    return parsed, runner, signer_key, descriptor, catalog, candidate, tree, base, base_tree


def test_signer_only_accepts_fresh_separately_signed_confined_runner_result(tmp_path):
    config, runner, signer_key, descriptor, catalog, candidate, tree, base, base_tree = configured(tmp_path)
    runner.start()
    signer = create_qualified_execution_server(config, {"fixture-client": "signer-secret"}, signing_private_key=signer_key, environment={"RUNNER_TOKEN": runner.token})
    thread = threading.Thread(target=signer.serve_forever, daemon=True)
    thread.start()
    try:
        client = QualifiedExecutionClient({**descriptor, "endpoint": f"http://127.0.0.1:{signer.server_port}"}, environment={"SIGNER_TOKEN": "signer-secret"})
        response = client.execute(candidate_commit=candidate, candidate_tree=tree, base_commit=base, base_tree=base_tree, plan_commit=config.plan_commit, profiles=["focused"])
    finally:
        signer.shutdown()
        signer.server_close()
        thread.join(timeout=5)
        runner.stop()
    proof = validate_execution_proof(response["results"][0]["proof"], response["results"][0]["transcript"], catalog=catalog, runner_descriptor=runner.descriptor)
    assert proof["status"] == "PASS"
    assert proof["policy_artifact_hash"] == config.policy.artifact_hash
    assert "subprocess.Popen" not in Path("src/tgw/qualified_execution_service.py").read_text()


def test_runner_identity_namespace_and_deadline_attestation_fail_closed(tmp_path):
    config, runner, signer_key, _descriptor, _catalog, candidate, tree, base, base_tree = configured(tmp_path, timeout_ok=False)
    runner.start()
    try:
        service = create_qualified_execution_server(config, {"fixture-client": "signer-secret"}, signing_private_key=signer_key, environment={"RUNNER_TOKEN": runner.token})
        client = QualifiedExecutionClient(
            {
                "schema": SERVICE_DESCRIPTOR_SCHEMA,
                "id": config.service_id,
                "client_id": "fixture-client",
                "endpoint": f"http://127.0.0.1:{service.server_port}",
                "credential_env": "SIGNER_TOKEN",
                "timeout_seconds": 10,
            },
            environment={"SIGNER_TOKEN": "signer-secret"},
        )
        thread = threading.Thread(target=service.serve_forever, daemon=True)
        thread.start()
        with pytest.raises(QualifiedExecutionError):
            client.execute(candidate_commit=candidate, candidate_tree=tree, base_commit=base, base_tree=base_tree, plan_commit=config.plan_commit, profiles=["focused"])
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)
    finally:
        runner.stop()
    bad = {
        "schema": SERVICE_CONFIG_SCHEMA,
        "service_id": "signer",
        "signer_identity": "same",
        "signer_namespace_id": "signer-namespace",
        "repository": str(config.repository),
        "plan_repository": str(config.plan_repository),
        "plan_commit": config.plan_commit,
        "policy_path": config.policy.path,
        "policy_artifact_hash": config.policy.artifact_hash,
        "runner": {**runner.descriptor, "runner_identity": "same"},
        "clients": [
            {
                "id": "fixture-client",
                "credential_env": "SIGNER_TOKEN",
                "descriptor_hash": config.clients["fixture-client"].descriptor_hash,
                "profiles": ["focused"],
            }
        ],
        "max_active_requests": 1,
        "max_retained_proofs_per_client": 2,
        "attestation_key_id": "signer-key",
        "attestation_private_key_env": "SIGNER_KEY",
    }
    with pytest.raises(QualifiedExecutionConfigurationError, match="must not share"):
        QualifiedExecutionConfig.parse(bad)


@pytest.mark.parametrize(
    "variant",
    ["output-limit", "runtime-rehash", "runtime-drift", "wrong-runner-key"],
)
def test_signer_rejects_unconfined_or_replaced_runner_results(tmp_path, variant):
    options = {
        "output_limit_ok": variant != "output-limit",
        "runtime_rehashed": variant != "runtime-rehash",
        "drift_after_identity": variant == "runtime-drift",
        "result_signing_key": Ed25519PrivateKey.generate() if variant == "wrong-runner-key" else None,
    }
    config, runner, signer_key, _descriptor, _catalog, candidate, tree, base, base_tree = configured(tmp_path, **options)
    runner.start()
    service = create_qualified_execution_server(config, {"fixture-client": "signer-secret"}, signing_private_key=signer_key, environment={"RUNNER_TOKEN": runner.token})
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        client = QualifiedExecutionClient(
            {
                "schema": SERVICE_DESCRIPTOR_SCHEMA,
                "id": config.service_id,
                "client_id": "fixture-client",
                "endpoint": f"http://127.0.0.1:{service.server_port}",
                "credential_env": "SIGNER_TOKEN",
                "timeout_seconds": 10,
            },
            environment={"SIGNER_TOKEN": "signer-secret"},
        )
        with pytest.raises(QualifiedExecutionError):
            client.execute(
                candidate_commit=candidate,
                candidate_tree=tree,
                base_commit=base,
                base_tree=base_tree,
                plan_commit=config.plan_commit,
                profiles=["focused"],
            )
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=5)
        runner.stop()


def test_config_rejects_runner_policy_pin_drift(tmp_path):
    config, runner, _signer_key, _descriptor, _catalog, _candidate, _tree, _base, _base_tree = configured(tmp_path)
    bad = {
        "schema": SERVICE_CONFIG_SCHEMA,
        "service_id": config.service_id,
        "signer_identity": config.signer_identity,
        "signer_namespace_id": config.signer_namespace_id,
        "repository": str(config.repository),
        "plan_repository": str(config.plan_repository),
        "plan_commit": config.plan_commit,
        "policy_path": config.policy.path,
        "policy_artifact_hash": config.policy.artifact_hash,
        "runner": {**runner.descriptor, "policy_artifact_hash": "sha256:" + "0" * 64},
        "clients": [
            {
                "id": "fixture-client",
                "credential_env": "SIGNER_TOKEN",
                "descriptor_hash": config.clients["fixture-client"].descriptor_hash,
                "profiles": ["focused"],
            }
        ],
        "max_active_requests": 1,
        "max_retained_proofs_per_client": 2,
        "attestation_key_id": "signer-fixture-key",
        "attestation_private_key_env": "SIGNER_KEY",
    }
    with pytest.raises(QualifiedExecutionConfigurationError, match="Plan policy"):
        QualifiedExecutionConfig.parse(bad)


def test_signer_retains_bounded_client_bound_proof_transcripts(tmp_path):
    config, runner, signer_key, _descriptor, _catalog, candidate, tree, base, base_tree = configured(tmp_path)
    runner.start()
    try:
        service = QualifiedExecutionService(
            config,
            {"fixture-client": "signer-secret"},
            signing_private_key=signer_key,
            environment={"RUNNER_TOKEN": runner.token},
        )
        client = service.client_for_authorization("Bearer signer-secret")
        assert client is not None
        request = {
            "schema": REQUEST_SCHEMA,
            "service_id": config.service_id,
            "client_id": client.client_id,
            "candidate_commit": candidate,
            "candidate_tree": tree,
            "base_commit": base,
            "base_tree": base_tree,
            "plan_commit": config.plan_commit,
            "profiles": ["focused"],
            "review_packet": None,
        }
        response = service.execute(request, client)
        proof = response["results"][0]["proof"]
        retained = service.retained_proof(client, proof["run_id"], proof["profile_id"])
        assert retained == {"proof": proof, "transcript": response["results"][0]["transcript"]}
    finally:
        runner.stop()
