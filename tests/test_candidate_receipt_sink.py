import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.bootstrap_deployment_contract import (
    BootstrapDeploymentContractError,
    PinnedBootstrapDeploymentContractResolver,
    bootstrap_deployment_contract_ref,
    derive_bootstrap_deployment_contract,
)
from tgw.candidate_manifest import (
    build_candidate_manifest,
    create_luet_conformance_receipt,
    create_migration_safety_receipt,
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
)
from tgw.candidate_receipt_sink import (
    CANDIDATE_EVIDENCE_BUNDLE_SCHEMA,
    CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
    GOVERNED_EXECUTION_BUNDLE_SCHEMA,
    INDEPENDENT_REVIEW_EVIDENCE_BUNDLE_SCHEMA,
    PINNED_CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
    RECEIPT_SINK_MANIFEST_SCHEMA,
    RECEIPT_SINK_SCHEMA,
    ROLLBACK_MANIFEST_SCHEMA,
    W06_PLAN_MATERIALIZATION_SCHEMA,
    CandidateReceiptSinkError,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    candidate_evidence_bundle_ref,
    governed_execution_bundle_ref,
    independent_review_evidence_bundle_ref,
    load_pinned_candidate_evidence_descriptor,
    load_receipt_sink_descriptor,
)
from tgw.candidate_review import PACKET_SCHEMA, RESULT_SCHEMA
from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    ed25519_public_key,
    issue_harness_retrieval_attestation,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
)
from tgw.governed_execution_receipt import create_candidate_governed_execution_receipt
from tgw.plan_luet import PINNED_LUET_BINARY_SHA256, PROVIDER_ID
from tgw.plan_solver import solve
from tgw.qualified_execution_service import (
    PROOF_SCHEMA,
    RUNNER_DESCRIPTOR_SCHEMA,
    SERVICE_CATALOG_SCHEMA,
    SERVICE_DESCRIPTOR_SCHEMA,
    TRANSCRIPT_SCHEMA,
    execution_public_key,
    execution_service_descriptor_hash,
    issue_execution_proof,
    issue_runner_transcript,
    qualified_runner_descriptor_hash,
)

ROOT = Path(__file__).resolve().parents[1]
PLAN_APPROVED_REF = "refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM"
TEST_ATTESTATION_KEY_ID = "candidate-sink-attestation-key-1"
TEST_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_EXECUTION_ATTESTATION_KEY_ID = "candidate-qualified-execution-key-1"
TEST_EXECUTION_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()
TEST_RUNNER_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def object_hash(value):
    return digest(canonical(value))


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def candidate_repository(tmp_path):
    repo = tmp_path / "candidate"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("answer = 0\n")
    _install_test_contract(repo)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    (repo / "app.py").write_text("answer = 42\n")
    (repo / "src" / "migrations").mkdir(parents=True)
    (repo / "src" / "migrations" / "001.sql").write_text("CREATE TABLE first_proof(id integer);\n")
    (repo / "src" / "migrations" / "002.sql").write_text("CREATE TABLE second_proof(id integer);\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")


def _install_test_contract(repo: Path):
    runner = repo / "scripts" / "candidate-test-runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("# fixture runner\n")
    plan = repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(
        json.dumps(
            {
                "schema": "tgw-candidate-test-plan/v1",
                "plan_id": "candidate-sink-fixture",
                "version": 1,
                "runner": {
                    "path": "scripts/candidate-test-runner.py",
                    "sha256": "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest(),
                    "argv_prefix": ["-m", "pytest"],
                },
                "scopes": {
                    "focused": {"argv": ["-q", "tests/test_governed_resource_service.py"]},
                    "full": {"argv": ["-q"]},
                },
            },
            sort_keys=True,
        )
    )


def approved_plan_repository(tmp_path):
    repo = tmp_path / "plans"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "plan@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Plan authority"], cwd=repo, check=True)
    (repo / "plan.txt").write_text("approved governed execution Plan\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "approved Plan"], cwd=repo, check=True)
    commit = git(repo, "rev-parse", "HEAD")
    subprocess.run(["git", "update-ref", PLAN_APPROVED_REF, commit], cwd=repo, check=True)
    return repo, commit


def w06_plan_materialization(tmp_path, *, plan_commit, corrupt=None):
    """Retain a concrete W06 solve outside the mutable candidate and S/D/X."""

    repo = tmp_path / "w06-plan-materialization"
    _new_sink(repo, email="w06@example.invalid", name="W06 Plan evidence")
    graph = {
        "schema": "tgw-plan/v2",
        "plan_commit": plan_commit,
        "capabilities": ["candidate.admission@1"],
        "providers": [{"id": "candidate-admission", "provides": ["candidate.admission@1"]}],
        "observations": [],
        "target": {
            "id": "fixture-approved-plan",
            "profile": "production",
            "minimum_state": "operationally_verified",
            "required_capabilities": ["candidate.admission@1"],
        },
    }
    native = solve(graph, expected_plan_commit=plan_commit)
    solution = solve(
        graph,
        expected_plan_commit=plan_commit,
        conformance_result={
            "provider_id": PROVIDER_ID,
            "available": True,
            "closure_hash": native["closure_hash"],
        },
    )
    materialization = {
        "schema": W06_PLAN_MATERIALIZATION_SCHEMA,
        "plan_commit": plan_commit,
        "graph": graph,
        "graph_hash": object_hash(graph),
        "solution": solution,
        "solution_hash": solution["solution_hash"],
        "closure_hash": solution["closure_hash"],
        "provider_id": PROVIDER_ID,
        "binary_sha256": PINNED_LUET_BINARY_SHA256,
    }
    if corrupt == "solution":
        materialization["solution"] = {**solution, "dispatchable": False}
    elif corrupt == "graph":
        materialization["graph"] = {**graph, "plan_commit": "0" * 40}
    raw = write_json(repo / "materialization.json", materialization)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "retain W06 Plan materialization"], cwd=repo, check=True)
    pin = {
        "schema": "tgw-pinned-git-w06-plan-materialization/v1",
        "repository": str(repo.resolve()),
        "commit": git(repo, "rev-parse", "HEAD"),
        "tree": git(repo, "rev-parse", "HEAD^{tree}"),
        "path": "materialization.json",
        "content_sha256": digest(raw),
    }
    if corrupt == "missing":
        pin["path"] = "absent-materialization.json"
    return repo, pin, materialization


def service_catalog(plan_commit):
    service = {
        "schema": "tgw-registered-resource-service/v2",
        "id": "candidate-receipt-service",
        "client_id": "candidate-receipt-client",
        "endpoint": "https://receipts.invalid",
        "credential_env": None,
        "timeout_seconds": 5,
    }
    catalog = {
        "schema": "tgw-registered-resource-service-catalog/v3",
        "catalog_ref": "catalog:candidate-receipt-service@1",
        "plan_commit": plan_commit,
        "services": [
            {
                "id": service["id"],
                "client_id": service["client_id"],
                "descriptor_hash": resource_service_descriptor_hash(service),
                "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
                "attestation_key_id": TEST_ATTESTATION_KEY_ID,
                "attestation_public_key": ed25519_public_key(TEST_ATTESTATION_PRIVATE_KEY),
            }
        ],
    }
    return service, catalog


def qualified_runner_descriptor(plan_commit):
    return {
        "schema": RUNNER_DESCRIPTOR_SCHEMA,
        "id": "candidate-confined-runner",
        "runner_identity": "candidate-confined-runner-identity",
        "namespace_id": "candidate-runner-namespace",
        "endpoint": "https://qualified-runner.invalid",
        "credential_env": "CANDIDATE_RUNNER_TOKEN",
        "timeout_seconds": 5,
        "attestation_key_id": "candidate-runner-key-1",
        "attestation_public_key": execution_public_key(TEST_RUNNER_ATTESTATION_PRIVATE_KEY),
        "isolation_profile_hash": "sha256:" + "2" * 64,
        "plan_commit": plan_commit,
        "policy_path": "policies/qualified-execution.json",
        "policy_artifact_hash": "sha256:" + "8" * 64,
        "profiles": ["focused-tests", "full-tests", "migration-1", "migration-2", "independent-review"],
    }


def qualified_execution_catalog(plan_commit, runner_descriptor):
    service = {
        "schema": SERVICE_DESCRIPTOR_SCHEMA,
        "id": "candidate-qualified-execution",
        "client_id": "candidate-qualified-client",
        "endpoint": "https://qualified-execution.invalid",
        "credential_env": None,
        "timeout_seconds": 5,
    }
    return {
        "schema": SERVICE_CATALOG_SCHEMA,
        "catalog_ref": "catalog:candidate-qualified-execution@1",
        "plan_commit": plan_commit,
        "policy_artifact_hash": "sha256:" + "8" * 64,
        "services": [
            {
                "id": service["id"],
                "client_id": service["client_id"],
                "signer_identity": "candidate-qualified-signer",
                "signer_namespace_id": "candidate-qualified-signer-namespace",
                "descriptor_hash": execution_service_descriptor_hash(service),
                "runner_descriptor_hash": qualified_runner_descriptor_hash(runner_descriptor),
                "policy_artifact_hash": "sha256:" + "8" * 64,
                "capabilities": [
                    "candidate-review-execution",
                    "candidate-test-execution",
                    "postgresql-migration-execution",
                ],
                "attestation_key_id": TEST_EXECUTION_ATTESTATION_KEY_ID,
                "attestation_public_key": execution_public_key(TEST_EXECUTION_ATTESTATION_PRIVATE_KEY),
                "runner_attestation_key_id": runner_descriptor["attestation_key_id"],
                "runner_attestation_public_key": runner_descriptor["attestation_public_key"],
            }
        ],
    }


def qualified_execution_artifact(
    *,
    kind,
    profile_id,
    source_commit,
    source_tree,
    base_commit,
    base_tree,
    plan_commit,
    inputs,
    runner_descriptor,
):
    command = ["/qualified/execution-runner", kind]
    stdout, stderr = b"qualified execution PASS\n", b""
    runtime_environment = {"LANG": "C.UTF-8"}
    runtime = {
        "runner_path": "/qualified/runner",
        "runner_sha256": "sha256:" + "2" * 64,
        "interpreter_path": "/qualified/python",
        "interpreter_sha256": "sha256:" + "3" * 64,
        "interpreter_version_sha256": "sha256:" + "4" * 64,
        "dependency_manifest_path": "/qualified/dependencies.lock",
        "dependency_manifest_sha256": "sha256:" + "5" * 64,
        "environment": runtime_environment,
        "environment_hash": object_hash(runtime_environment),
    }
    transcript_unsigned = {
        "schema": TRANSCRIPT_SCHEMA,
        "runner_id": runner_descriptor["id"],
        "runner_identity": runner_descriptor["runner_identity"],
        "namespace_id": runner_descriptor["namespace_id"],
        "runner_identity_hash": "sha256:" + "7" * 64,
        "service_id": "candidate-qualified-execution",
        "client_id": "candidate-qualified-client",
        "run_id": f"run-{kind}-{profile_id}",
        "profile_id": profile_id,
        "kind": kind,
        "candidate_commit": source_commit,
        "candidate_tree": source_tree,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "plan_commit": plan_commit,
        "policy_path": "policies/qualified-execution.json",
        "policy_artifact_hash": "sha256:" + "8" * 64,
        "inputs": inputs,
        "runtime": runtime,
        "command": command,
        "stdout_base64": base64.b64encode(stdout).decode(),
        "stderr_base64": base64.b64encode(stderr).decode(),
        "stdout_sha256": digest(stdout),
        "stderr_sha256": digest(stderr),
        "output_hash": digest(stdout + b"\0" + stderr),
        "output_complete": True,
        "returncode": 0,
        "timed_out": False,
        "timeout_enforced": True,
        "output_limit_enforced": True,
        "runtime_rehashed_before_dispatch": True,
        "isolated": True,
        "isolation_profile_hash": runner_descriptor["isolation_profile_hash"],
        "status": "PASS",
        "attestation_key_id": runner_descriptor["attestation_key_id"],
    }
    transcript = issue_runner_transcript(transcript_unsigned, signing_private_key=TEST_RUNNER_ATTESTATION_PRIVATE_KEY)
    proof_unsigned = {
        "schema": PROOF_SCHEMA,
        "service_id": transcript["service_id"],
        "signer_identity": "candidate-qualified-signer",
        "signer_namespace_id": "candidate-qualified-signer-namespace",
        "signer_descriptor_hash": execution_service_descriptor_hash(
            {
                "schema": SERVICE_DESCRIPTOR_SCHEMA,
                "id": "candidate-qualified-execution",
                "client_id": "candidate-qualified-client",
                "endpoint": "https://qualified-execution.invalid",
                "credential_env": None,
                "timeout_seconds": 5,
            }
        ),
        "client_id": transcript["client_id"],
        "run_id": transcript["run_id"],
        "profile_id": profile_id,
        "kind": kind,
        "candidate_commit": source_commit,
        "candidate_tree": source_tree,
        "base_commit": base_commit,
        "base_tree": base_tree,
        "plan_commit": plan_commit,
        "policy_path": "policies/qualified-execution.json",
        "policy_artifact_hash": "sha256:" + "8" * 64,
        "runner_id": transcript["runner_id"],
        "runner_identity": transcript["runner_identity"],
        "namespace_id": transcript["namespace_id"],
        "runner_descriptor_hash": qualified_runner_descriptor_hash(runner_descriptor),
        "runner_identity_hash": transcript["runner_identity_hash"],
        "runner_result_hash": transcript["runner_result_hash"],
        "inputs": inputs,
        "runtime": runtime,
        "command": command,
        "output_hash": transcript["output_hash"],
        "output_complete": True,
        "returncode": 0,
        "timed_out": False,
        "status": "PASS",
        "attestation_key_id": TEST_EXECUTION_ATTESTATION_KEY_ID,
    }
    return {
        "proof": issue_execution_proof(proof_unsigned, signing_private_key=TEST_EXECUTION_ATTESTATION_PRIVATE_KEY),
        "transcript": transcript,
    }


def role_evidence(*, role, source_commit, source_tree, plan_commit, receipt_sink_binding):
    service, catalog = service_catalog(plan_commit)

    def binding(ref, value):
        return {"ref": ref, "hash": digest(value.encode())}

    bindings = {
        "plan_input": binding("plan:input", "Plan input"),
        "plan_commit": binding("plan:commit", plan_commit),
        "plan_graph": binding("plan:graph", "Plan graph"),
        "codegraph_snapshot": binding("codegraph:snapshot", "CodeGraph"),
        "source_tree": binding(f"git:tree:{source_tree}", "source tree archive"),
        "execution_environment": binding("environment:manifest", "environment"),
        "authority_conditions": binding("authority:conditions", "authority"),
        "receipt_sink": receipt_sink_binding,
    }
    card_unsigned = {
        "schema": "tgw-execution-card/v1",
        "card_id": f"candidate-card-{role}",
        "solution_id": "sha256:" + "1" * 64,
        "role": role,
        "selected_provider": f"candidate-{role}-runner",
        "plan_commit": plan_commit,
        "resource_service": {
            "id": service["id"],
            "client_id": service["client_id"],
            "descriptor_hash": resource_service_descriptor_hash(service),
            "catalog_ref": catalog["catalog_ref"],
            "catalog_hash": resource_service_catalog_hash(catalog),
        },
        "bindings": bindings,
        "authority": ["source integration"],
        "exclusions": ["no deployment"],
        "acceptance": ["role receipt passes"],
        "receiver_profile": {"id": "codex", "version": 1},
        "lease": {"id": f"lease:{role}", "expires_at": "2027-08-11T23:00:00Z", "stop_policy": "hold"},
    }
    card = {**card_unsigned, "card_hash": object_hash(card_unsigned)}
    resources_unsigned = {
        "schema": "tgw-execution-resource-receipt/v1",
        "card_hash": card["card_hash"],
        "plan_commit": plan_commit,
        "resources": {name: bindings[name] for name in sorted(bindings)},
    }
    resources = {**resources_unsigned, "receipt_hash": object_hash(resources_unsigned)}
    execution_identity = f"isolated-context:{role}"
    handoff_hash = "sha256:" + hashlib.sha256(f"handoff:{role}".encode()).hexdigest()
    attestation_payload = {
        "schema": "tgw-registered-resource-retrieval-attestation/v3",
        "service_id": service["id"],
        "client_id": service["client_id"],
        "run_id": f"run-{role}",
        "card_hash": card["card_hash"],
        "role": role,
        "execution_identity": execution_identity,
        "handoff_hash": handoff_hash,
        "resource_receipt_hash": resources["receipt_hash"],
        "resources": {name: bindings[name] for name in sorted(bindings)},
        "attestation_key_id": TEST_ATTESTATION_KEY_ID,
    }
    attestation = issue_harness_retrieval_attestation(
        attestation_payload,
        signing_private_key=TEST_ATTESTATION_PRIVATE_KEY,
    )
    conditions = {
        "implementation": ["implemented"],
        "independent-review": ["reviewed"],
        "controller-verification": ["controller_verified"],
    }[role]
    role_unsigned = {
        "schema": "tgw-governed-coding-receipt/v1",
        "status": "PASS",
        "role": role,
        "selected_provider": card["selected_provider"],
        "execution_identity": execution_identity,
        "card_hash": card["card_hash"],
        "promptcraft_receipt_hash": "sha256:" + "2" * 64,
        "handoff_hash": handoff_hash,
        "resource_receipt_hash": resources["receipt_hash"],
        "harness_resource_receipt_hash": resources["receipt_hash"],
        "harness_retrieval_attestation_hash": attestation["attestation_hash"],
        "harness_retrieval_attestation": attestation,
        "resource_service_descriptor_hash": card["resource_service"]["descriptor_hash"],
        "resource_service_client_id": service["client_id"],
        "resource_service_catalog_ref": catalog["catalog_ref"],
        "resource_service_catalog_hash": card["resource_service"]["catalog_hash"],
        "outcome": "satisfied",
        "established_conditions": conditions,
        "artifacts": [],
    }
    role_receipt = {**role_unsigned, "receipt_hash": object_hash(role_unsigned)}
    candidate_receipt = create_candidate_governed_execution_receipt(
        card=card,
        resource_receipt=resources,
        role_receipt=role_receipt,
        resource_service_catalog=catalog,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
    )
    return {
        "candidate_receipt": candidate_receipt,
        "card": card,
        "resource_receipt": resources,
        "role_receipt": role_receipt,
        "resource_service_catalog": catalog,
    }


def write_json(path, value):
    raw = canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def release_manifest(repo, commit, generation):
    tree = git(repo, "rev-parse", f"{commit}^{{tree}}")
    files = {path: hashlib.sha256(subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo)).hexdigest() for path in git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()}
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=repo)
    content_manifest = hashlib.sha256((json.dumps(dict(sorted(files.items())), sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
    return {
        "schema": "tgw-release-manifest-v1",
        "generation": generation,
        "commit": commit,
        "tree": f"exact-git-archive:{commit}",
        "git_tree": tree,
        "src_root": "src",
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "content_manifest_sha256": content_manifest,
        "file_count": len(files),
        "files": files,
    }


def migration_receipt(repo, *, path, source_commit, source_tree, base_commit, base_tree):
    return create_migration_safety_receipt(
        candidate_commit=source_commit,
        candidate_tree=source_tree,
        base_commit=base_commit,
        base_tree=base_tree,
        migration_path=path,
        migration_source=subprocess.check_output(["git", "show", f"{source_commit}:{path}"], cwd=repo),
        postgres_version="PostgreSQL 17.10",
        backup=f"backup:{path}".encode(),
        source_schema=f"source-schema:{path}".encode(),
        restored_schema=f"source-schema:{path}".encode(),
        source_data=f"source-data:{path}".encode(),
        restored_data=f"source-data:{path}".encode(),
        migrated_schema=f"migrated-schema:{path}".encode(),
        migrated_data=f"migrated-data:{path}".encode(),
        verified=True,
    ).__dict__


def candidate_evidence(candidate_repo, *, source_commit, source_tree, plan_commit, w06_materialization):
    """Build S-only evidence before the external descriptor and any card exist."""

    base_commit = git(candidate_repo, "rev-parse", f"{source_commit}^")
    base_tree = git(candidate_repo, "rev-parse", f"{base_commit}^{{tree}}")
    predecessor = release_manifest(candidate_repo, base_commit, "previous")
    test_plan = load_candidate_test_plan(candidate_repo, source_commit=source_commit)
    focused_command = test_plan["commands"]["focused"]
    full_command = test_plan["commands"]["full"]
    focused_output = create_test_output_artifact(
        scope="focused",
        command=focused_command,
        source_commit=source_commit,
        source_tree=source_tree,
        stdout=b"focused passed\n",
        stderr=b"",
    )
    full_output = create_test_output_artifact(
        scope="full",
        command=full_command,
        source_commit=source_commit,
        source_tree=source_tree,
        stdout=b"full suite passed\n",
        stderr=b"",
    )
    focused = create_test_receipt(
        scope="focused",
        command=focused_command,
        source_commit=source_commit,
        source_tree=source_tree,
        returncode=0,
        test_plan=test_plan,
        output_artifact=focused_output,
    )
    full = create_test_receipt(
        scope="full",
        command=full_command,
        source_commit=source_commit,
        source_tree=source_tree,
        returncode=0,
        test_plan=test_plan,
        output_artifact=full_output,
    )
    migrations = [
        migration_receipt(
            candidate_repo,
            path=path,
            source_commit=source_commit,
            source_tree=source_tree,
            base_commit=base_commit,
            base_tree=base_tree,
        )
        for path in ("src/migrations/001.sql", "src/migrations/002.sql")
    ]
    luet_receipt = create_luet_conformance_receipt(
        {
            "provider_id": PROVIDER_ID,
            "available": True,
            "status": "AGREEMENT",
            "closure_hash": w06_materialization["closure_hash"],
            "selected_providers": w06_materialization["solution"]["selected_providers"],
        },
        graph=w06_materialization["graph"],
        plan_commit=plan_commit,
        source_commit=source_commit,
        source_tree=source_tree,
        binary_sha256=PINNED_LUET_BINARY_SHA256,
    )
    candidate_manifest = build_candidate_manifest(
        candidate_repo,
        commit=source_commit,
        base_commit=base_commit,
        predecessor_release=predecessor,
        plan_commit=plan_commit,
        solution_hash=w06_materialization["solution_hash"],
        closure_hash=w06_materialization["closure_hash"],
        focused_receipt=focused,
        full_suite_receipt=full,
        focused_output_artifact=focused_output,
        full_suite_output_artifact=full_output,
        graph=w06_materialization["graph"],
        conformance_receipt=luet_receipt,
        migration_receipts=migrations,
    )
    release = release_manifest(candidate_repo, source_commit, "candidate")
    rollback_unsigned = {
        "schema": ROLLBACK_MANIFEST_SCHEMA,
        "candidate_commit": source_commit,
        "candidate_tree": source_tree,
        "candidate_manifest_hash": candidate_manifest["manifest_hash"],
        "release_manifest_hash": object_hash(release),
        "rollback_release_manifest": predecessor,
    }
    rollback = {**rollback_unsigned, "manifest_hash": object_hash(rollback_unsigned)}
    runner_descriptor = qualified_runner_descriptor(plan_commit)
    execution_catalog = qualified_execution_catalog(plan_commit, runner_descriptor)
    execution_proofs = [
        qualified_execution_artifact(
            kind="test",
            profile_id="focused-tests",
            source_commit=source_commit,
            source_tree=source_tree,
            base_commit=base_commit,
            base_tree=base_tree,
            plan_commit=plan_commit,
            inputs={
                "scope": "focused",
                "test_plan_path": test_plan["path"],
                "test_plan_sha256": test_plan["sha256"],
                "test_runner_path": test_plan["runner_path"],
                "test_runner_sha256": test_plan["runner_sha256"],
                "test_receipt_hash": focused["receipt_hash"],
                "test_output_artifact_hash": focused_output["artifact_hash"],
            },
            runner_descriptor=runner_descriptor,
        ),
        qualified_execution_artifact(
            kind="test",
            profile_id="full-tests",
            source_commit=source_commit,
            source_tree=source_tree,
            base_commit=base_commit,
            base_tree=base_tree,
            plan_commit=plan_commit,
            inputs={
                "scope": "full",
                "test_plan_path": test_plan["path"],
                "test_plan_sha256": test_plan["sha256"],
                "test_runner_path": test_plan["runner_path"],
                "test_runner_sha256": test_plan["runner_sha256"],
                "test_receipt_hash": full["receipt_hash"],
                "test_output_artifact_hash": full_output["artifact_hash"],
            },
            runner_descriptor=runner_descriptor,
        ),
        *[
            qualified_execution_artifact(
                kind="migration",
                profile_id=f"migration-{index}",
                source_commit=source_commit,
                source_tree=source_tree,
                base_commit=base_commit,
                base_tree=base_tree,
                plan_commit=plan_commit,
                inputs={
                    "migration_path": receipt["migration_path"],
                    "migration_sha256": digest(
                        subprocess.check_output(
                            ["git", "show", f"{source_commit}:{receipt['migration_path']}"],
                            cwd=candidate_repo,
                        )
                    ),
                    "schema_snapshot_path": receipt["schema_snapshot_path"],
                    "schema_snapshot_sha256": (
                        digest(
                            subprocess.check_output(
                                ["git", "show", f"{source_commit}:{receipt['schema_snapshot_path']}"],
                                cwd=candidate_repo,
                            )
                        )
                        if receipt["schema_snapshot_path"] is not None
                        else None
                    ),
                    "runner_path": "/qualified/migration-runner",
                    "runner_sha256": "sha256:" + "6" * 64,
                    "migration_receipt_hash": receipt["receipt_hash"],
                },
                runner_descriptor=runner_descriptor,
            )
            for index, receipt in enumerate(migrations, start=1)
        ],
    ]
    return {
        "candidate_manifest": candidate_manifest,
        "luet_conformance_receipt": luet_receipt,
        "focused_test_receipt": focused,
        "focused_test_output": focused_output,
        "full_suite_test_receipt": full,
        "full_suite_test_output": full_output,
        "migration_receipts": migrations,
        "release_manifest": release,
        "rollback_manifest": rollback,
        "qualified_execution_catalog": execution_catalog,
        "qualified_execution_runner_descriptor": runner_descriptor,
        "execution_proofs": execution_proofs,
    }


def independent_review_evidence(candidate_manifest, independent_review_receipt):
    """Build X-only review output after cards/roles have been established."""

    review_packet_unsigned = {
        "schema": PACKET_SCHEMA,
        "status": "EXECUTABLE",
        "candidate_manifest_hash": candidate_manifest["manifest_hash"],
        "candidate_source": {key: candidate_manifest["source"][key] for key in ("commit", "tree", "archive_sha256")},
        "plan": candidate_manifest["plan"],
        "snapshot": {"ref": "sink:candidate-snapshot", "hash": digest(b"candidate snapshot")},
        "required_dimensions": ["semantic", "security"],
        "review_contract": {
            "schema": RESULT_SCHEMA,
            "pass_requires": "both dimensions PASS with zero findings",
            "source_mutation": "forbidden",
            "authority_broadening": "forbidden",
        },
        "selected_provider": independent_review_receipt["selected_provider"],
        "receiver_profile": {"id": "reviewer", "version": 1},
        "runner_argv": ["reviewer"],
        "hold": None,
    }
    review_packet = {**review_packet_unsigned, "packet_hash": object_hash(review_packet_unsigned)}
    review_result_unsigned = {
        "schema": RESULT_SCHEMA,
        "packet_hash": review_packet["packet_hash"],
        "candidate_manifest_hash": candidate_manifest["manifest_hash"],
        "selected_provider": independent_review_receipt["selected_provider"],
        "governed_review_receipt": independent_review_receipt,
        "dimensions": {
            "semantic": {"verdict": "PASS", "findings": []},
            "security": {"verdict": "PASS", "findings": []},
        },
        "overall": "PASS",
    }
    review_result = {**review_result_unsigned, "result_hash": object_hash(review_result_unsigned)}
    source = candidate_manifest["source"]
    runner_descriptor = qualified_runner_descriptor(candidate_manifest["plan"]["commit"])
    execution_proof = qualified_execution_artifact(
        kind="review",
        profile_id="independent-review",
        source_commit=source["commit"],
        source_tree=source["tree"],
        base_commit=source["commit"],
        base_tree=source["tree"],
        plan_commit=candidate_manifest["plan"]["commit"],
        inputs={
            "review_packet_content_sha256": object_hash(review_packet),
            "review_packet_hash": review_packet["packet_hash"],
            "review_result_content_sha256": object_hash(review_result),
            "review_result_hash": review_result["result_hash"],
            "runner_path": "/qualified/review-runner",
            "runner_sha256": "sha256:" + "7" * 64,
        },
        runner_descriptor=runner_descriptor,
    )
    return {
        "review_packet": review_packet,
        "review_result": review_result,
        "qualified_execution_catalog": qualified_execution_catalog(candidate_manifest["plan"]["commit"], runner_descriptor),
        "qualified_execution_runner_descriptor": runner_descriptor,
        "review_execution_proof": execution_proof["proof"],
        "review_execution_transcript": execution_proof["transcript"],
    }


def _new_sink(path: Path, *, email: str, name: str):
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", email], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", name], cwd=path, check=True)


def _commit_sink(sink: Path, *, sink_id: str, artifacts: list[dict], message: str):
    manifest_unsigned = {
        "schema": RECEIPT_SINK_MANIFEST_SCHEMA,
        "sink_id": sink_id,
        "artifacts": artifacts,
    }
    manifest = {**manifest_unsigned, "manifest_hash": object_hash(manifest_unsigned)}
    manifest_raw = write_json(sink / "manifest.json", manifest)
    subprocess.run(["git", "add", "."], cwd=sink, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=sink, check=True)
    return {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": sink_id,
        "repository": str(sink.resolve()),
        "commit": git(sink, "rev-parse", "HEAD"),
        "tree": git(sink, "rev-parse", "HEAD^{tree}"),
        "manifest_path": "manifest.json",
        "manifest_content_sha256": digest(manifest_raw),
    }


def _candidate_evidence_sink(
    tmp_path,
    *,
    candidate_repo,
    source_commit,
    source_tree,
    plan_commit,
    w06_materialization,
    corrupt_w08=None,
):
    sink = tmp_path / "candidate-evidence-sink"
    _new_sink(sink, email="candidate-evidence@example.invalid", name="Candidate evidence S")
    evidence = candidate_evidence(
        candidate_repo,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
        w06_materialization=w06_materialization,
    )
    if corrupt_w08 in {"focused", "full"}:
        key = "focused_test_receipt" if corrupt_w08 == "focused" else "full_suite_test_receipt"
        receipt = dict(evidence[key])
        receipt["command"] = ["pytest", "tests/not-the-retained-command"]
        receipt_unsigned = {field: item for field, item in receipt.items() if field != "receipt_hash"}
        evidence[key] = {**receipt_unsigned, "receipt_hash": object_hash(receipt_unsigned)}
    if corrupt_w08 == "focused-output":
        output = dict(evidence["focused_test_output"])
        output["stdout_base64"] = "dGFtcGVyZWQ="
        output_unsigned = {field: item for field, item in output.items() if field != "artifact_hash"}
        evidence["focused_test_output"] = {**output_unsigned, "artifact_hash": object_hash(output_unsigned)}
    if corrupt_w08 == "release":
        evidence["release_manifest"] = {**evidence["release_manifest"], "archive_sha256": "0" * 64}
    if corrupt_w08 == "rollback":
        rollback = {**evidence["rollback_manifest"], "candidate_commit": "0" * 40}
        rollback_unsigned = {key: value for key, value in rollback.items() if key != "manifest_hash"}
        evidence["rollback_manifest"] = {**rollback_unsigned, "manifest_hash": object_hash(rollback_unsigned)}
    if corrupt_w08 == "execution-proof":
        proof = dict(evidence["execution_proofs"][0]["proof"])
        proof["inputs"] = {**proof["inputs"], "scope": "forged"}
        evidence["execution_proofs"][0] = {**evidence["execution_proofs"][0], "proof": proof}
    if corrupt_w08 == "runner-descriptor":
        evidence["qualified_execution_runner_descriptor"] = {
            **evidence["qualified_execution_runner_descriptor"],
            "namespace_id": "forged-runner-namespace",
        }
    if corrupt_w08 == "signer-descriptor":
        catalog = dict(evidence["qualified_execution_catalog"])
        catalog["services"] = [{**catalog["services"][0], "descriptor_hash": "sha256:" + "0" * 64}]
        evidence["qualified_execution_catalog"] = catalog
    if corrupt_w08 == "luet-receipt":
        receipt = dict(evidence["luet_conformance_receipt"])
        receipt["source_tree"] = "0" * 40
        evidence["luet_conformance_receipt"] = receipt
    if corrupt_w08 in {"luet-provider", "luet-binary"}:
        receipt = dict(evidence["luet_conformance_receipt"])
        field = "provider_id" if corrupt_w08 == "luet-provider" else "binary_sha256"
        receipt[field] = "forged-provider" if field == "provider_id" else "sha256:" + "0" * 64
        unsigned = {key: value for key, value in receipt.items() if key != "receipt_hash"}
        evidence["luet_conformance_receipt"] = {**unsigned, "receipt_hash": object_hash(unsigned)}
    if corrupt_w08 == "manifest-conformance":
        manifest = dict(evidence["candidate_manifest"])
        manifest["conformance"] = {"status": "MISSING", "receipt_hash": None}
        manifest["dispatchable"] = False
        unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
        evidence["candidate_manifest"] = {**unsigned, "manifest_hash": object_hash(unsigned)}
    artifacts = []
    pointers = {}
    for name, value in evidence.items():
        if name in {"migration_receipts", "execution_proofs"}:
            continue
        raw = write_json(sink / "candidate-evidence" / f"{name}.json", value)
        ref = f"artifact:{source_commit}:candidate-evidence:{name}"
        artifacts.append(
            {
                "ref": ref,
                "path": f"candidate-evidence/{name}.json",
                "content_sha256": digest(raw),
            }
        )
        pointers[name] = {"ref": ref, "content_sha256": digest(raw)}
    migration_pointers = []
    for index, receipt in enumerate(evidence["migration_receipts"], start=1):
        raw = write_json(sink / "candidate-evidence" / f"migration-{index}.json", receipt)
        ref = f"artifact:{source_commit}:candidate-evidence:migration-{index}"
        artifacts.append(
            {
                "ref": ref,
                "path": f"candidate-evidence/migration-{index}.json",
                "content_sha256": digest(raw),
            }
        )
        migration_pointers.append({"ref": ref, "content_sha256": digest(raw)})
    if corrupt_w08 == "migration":
        migration_pointers = migration_pointers[:-1]
    execution_proof_pointers = []
    for index, proof_artifact in enumerate(evidence["execution_proofs"], start=1):
        pair = {}
        for name, value in proof_artifact.items():
            raw = write_json(sink / "candidate-evidence" / f"execution-{index}-{name}.json", value)
            ref = f"artifact:{source_commit}:candidate-evidence:execution-{index}-{name}"
            artifacts.append(
                {
                    "ref": ref,
                    "path": f"candidate-evidence/execution-{index}-{name}.json",
                    "content_sha256": digest(raw),
                }
            )
            pair[name] = {"ref": ref, "content_sha256": digest(raw)}
        execution_proof_pointers.append(pair)
    bundle_unsigned = {
        "schema": CANDIDATE_EVIDENCE_BUNDLE_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        **pointers,
        "migration_receipts": migration_pointers,
        "execution_proofs": execution_proof_pointers,
    }
    if corrupt_w08 == "luet-missing":
        bundle_unsigned.pop("luet_conformance_receipt")
    bundle = {**bundle_unsigned, "bundle_hash": object_hash(bundle_unsigned)}
    if corrupt_w08 != "missing":
        bundle_raw = write_json(sink / "bundles" / "candidate-evidence.json", bundle)
        artifacts.append(
            {
                "ref": candidate_evidence_bundle_ref(source_commit),
                "path": "bundles/candidate-evidence.json",
                "content_sha256": digest(bundle_raw),
            }
        )
    descriptor = _commit_sink(
        sink,
        sink_id="candidate-evidence-sink",
        artifacts=artifacts,
        message="immutable candidate evidence S",
    )
    return sink, descriptor, evidence


def _pinned_candidate_evidence_descriptor(tmp_path, candidate_repo, candidate_sink_descriptor, w06_materialization_pin):
    authority = tmp_path / "candidate-evidence-descriptor-authority"
    _new_sink(authority, email="descriptor@example.invalid", name="Candidate evidence descriptor D")
    descriptor_content = {
        "schema": CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
        "candidate_evidence_sink": candidate_sink_descriptor,
        "w06_plan_materialization": w06_materialization_pin,
    }
    raw = write_json(authority / "descriptor.json", descriptor_content)
    subprocess.run(["git", "add", "."], cwd=authority, check=True)
    subprocess.run(["git", "commit", "-qm", "pin candidate evidence S"], cwd=authority, check=True)
    pin = {
        "schema": PINNED_CANDIDATE_EVIDENCE_DESCRIPTOR_SCHEMA,
        "repository": str(authority.resolve()),
        "commit": git(authority, "rev-parse", "HEAD"),
        "tree": git(authority, "rev-parse", "HEAD^{tree}"),
        "path": "descriptor.json",
        "content_sha256": digest(raw),
    }
    config = tmp_path / "candidate-evidence-descriptor-config.json"
    write_json(config, pin)
    return authority, config


def _execution_evidence_sink(
    tmp_path,
    *,
    source_commit,
    source_tree,
    plan_commit,
    candidate_manifest,
    candidate_evidence_descriptor,
    corrupt_role=None,
    corrupt_sink_binding_role=None,
    corrupt_w08=None,
):
    sink = tmp_path / "execution-evidence-sink"
    _new_sink(sink, email="execution-evidence@example.invalid", name="Execution evidence X")
    artifacts = []
    evidence_by_role = {}
    for role in ("implementation", "independent-review", "controller-verification"):
        evidence = role_evidence(
            role=role,
            source_commit=source_commit,
            source_tree=source_tree,
            plan_commit=plan_commit,
            receipt_sink_binding=({"ref": "receipt-sink:legacy:descriptor:v1", "hash": "sha256:" + "f" * 64} if role == corrupt_sink_binding_role else candidate_evidence_descriptor.card_binding()),
        )
        evidence_by_role[role] = evidence
        pointers = {}
        for name, value in evidence.items():
            raw = write_json(sink / "governed-execution" / role / f"{name}.json", value)
            ref = f"artifact:{source_commit}:governed-execution:{role}:{name}"
            artifacts.append({"ref": ref, "path": f"governed-execution/{role}/{name}.json", "content_sha256": digest(raw)})
            pointers[name] = {"ref": ref, "content_sha256": digest(raw)}
        bundle_unsigned = {
            "schema": GOVERNED_EXECUTION_BUNDLE_SCHEMA,
            "source_commit": source_commit,
            "source_tree": source_tree,
            "plan_commit": plan_commit,
            "role": role,
            **pointers,
        }
        bundle = {**bundle_unsigned, "bundle_hash": object_hash(bundle_unsigned)}
        if role == corrupt_role:
            bundle["role_receipt"] = {**bundle["role_receipt"], "content_sha256": "sha256:" + "0" * 64}
            bundle["bundle_hash"] = object_hash({key: value for key, value in bundle.items() if key != "bundle_hash"})
        raw = write_json(sink / "bundles" / f"governed-{role}.json", bundle)
        artifacts.append(
            {
                "ref": governed_execution_bundle_ref(source_commit, role),
                "path": f"bundles/governed-{role}.json",
                "content_sha256": digest(raw),
            }
        )
    review = independent_review_evidence(candidate_manifest, evidence_by_role["independent-review"]["role_receipt"])
    if corrupt_w08 == "review":
        result = review["review_result"]
        unsigned = {
            **{key: value for key, value in result.items() if key != "result_hash"},
            "dimensions": {
                "semantic": {"verdict": "PASS", "findings": []},
                "security": {
                    "verdict": "FAIL",
                    "findings": [
                        {
                            "severity": "high",
                            "path": "app.py",
                            "line": 1,
                            "message": "review finding",
                        }
                    ],
                },
            },
            "overall": "FAIL",
        }
        review["review_result"] = {**unsigned, "result_hash": object_hash(unsigned)}
    if corrupt_w08 == "review-pair":
        # Pair a later, syntactically valid PASS review with the first proof.
        # Only the proof's exact packet/result-byte bindings reject this.
        packet_unsigned = {
            **{key: value for key, value in review["review_packet"].items() if key != "packet_hash"},
            "snapshot": {"ref": "sink:later-snapshot", "hash": digest(b"later snapshot")},
        }
        packet = {**packet_unsigned, "packet_hash": object_hash(packet_unsigned)}
        result_unsigned = {
            **{key: value for key, value in review["review_result"].items() if key != "result_hash"},
            "packet_hash": packet["packet_hash"],
        }
        review["review_packet"] = packet
        review["review_result"] = {**result_unsigned, "result_hash": object_hash(result_unsigned)}
    review_pointers = {}
    for name, value in review.items():
        raw = write_json(sink / "independent-review" / f"{name}.json", value)
        ref = f"artifact:{source_commit}:independent-review:{name}"
        artifacts.append({"ref": ref, "path": f"independent-review/{name}.json", "content_sha256": digest(raw)})
        review_pointers[name] = {"ref": ref, "content_sha256": digest(raw)}
    review_bundle_unsigned = {
        "schema": INDEPENDENT_REVIEW_EVIDENCE_BUNDLE_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        **review_pointers,
    }
    review_bundle = {**review_bundle_unsigned, "bundle_hash": object_hash(review_bundle_unsigned)}
    raw = write_json(sink / "bundles" / "independent-review.json", review_bundle)
    artifacts.append(
        {
            "ref": independent_review_evidence_bundle_ref(source_commit),
            "path": "bundles/independent-review.json",
            "content_sha256": digest(raw),
        }
    )
    descriptor = _commit_sink(
        sink,
        sink_id="execution-evidence-sink",
        artifacts=artifacts,
        message="immutable governed execution and review X",
    )
    config = tmp_path / "execution-evidence-sink-config.json"
    write_json(config, descriptor)
    return sink, config


def pinned_sinks(
    tmp_path,
    *,
    candidate_repo,
    source_commit,
    source_tree,
    plan_commit,
    corrupt_role=None,
    corrupt_sink_binding_role=None,
    corrupt_w08=None,
    corrupt_w06_materialization=None,
):
    _w06_repository, w06_materialization_pin, materialization = w06_plan_materialization(
        tmp_path,
        plan_commit=plan_commit,
        corrupt=corrupt_w06_materialization,
    )
    candidate_sink, candidate_sink_descriptor, evidence = _candidate_evidence_sink(
        tmp_path,
        candidate_repo=candidate_repo,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
        w06_materialization=materialization,
        corrupt_w08=corrupt_w08,
    )
    descriptor_authority, candidate_descriptor_config = _pinned_candidate_evidence_descriptor(
        tmp_path,
        candidate_repo,
        candidate_sink_descriptor,
        w06_materialization_pin,
    )
    descriptor = load_pinned_candidate_evidence_descriptor(
        candidate_descriptor_config,
        candidate_repository=candidate_repo,
    )
    execution_sink, execution_config = _execution_evidence_sink(
        tmp_path,
        source_commit=source_commit,
        source_tree=source_tree,
        plan_commit=plan_commit,
        candidate_manifest=evidence["candidate_manifest"],
        candidate_evidence_descriptor=descriptor,
        corrupt_role=corrupt_role,
        corrupt_sink_binding_role=corrupt_sink_binding_role,
        corrupt_w08=corrupt_w08,
    )
    return candidate_sink, descriptor_authority, execution_sink, candidate_descriptor_config, execution_config


def _gate(candidate, plan_repository, descriptor_config, execution_config):
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    execution_sink = PinnedGitReceiptSink(
        load_receipt_sink_descriptor(execution_config, candidate_repository=candidate),
        candidate_repository=candidate,
    )
    return candidate_admission_gate(
        candidate,
        candidate="HEAD",
        plan_repository=plan_repository,
        plan_approved_ref=PLAN_APPROVED_REF,
        candidate_evidence_descriptor=descriptor,
        execution_sink=execution_sink,
    )


def test_acyclic_two_store_gate_reads_exact_committed_artifacts_and_cli_admits(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    candidate_sink, authority, execution_sink, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    (execution_sink / "governed-execution" / "implementation" / "role_receipt.json").write_text("{}")
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    binding = descriptor.card_binding()
    assert binding["ref"] == "candidate-evidence:candidate-evidence-sink:descriptor:v3"
    assert binding["hash"].startswith("sha256:")
    assert descriptor.identity["repository"] == str(authority.resolve())
    assert descriptor.candidate_evidence_sink_descriptor["repository"] == str(candidate_sink.resolve())
    gate = _gate(candidate, plan_repository, descriptor_config, execution_config)

    assert gate["allowed"] is True
    assert gate["source_commit"] == commit
    assert gate["plan_commit"] == plan_commit
    assert gate["candidate_evidence"] is not None
    assert gate["luet_conformance_receipt_hash"] == gate["candidate_evidence"]["luet_conformance_receipt_hash"]
    assert gate["w06_plan_materialization_hash"] == gate["candidate_evidence"]["w06_plan_materialization_hash"]
    assert len(gate["candidate_evidence"]["migration_receipt_hashes"]) == 2
    assert gate["independent_review_evidence"]["review_result_hash"].startswith("sha256:")
    assert gate["candidate_evidence_descriptor"]["descriptor_hash"] == descriptor.identity["descriptor_hash"]
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "admit_governed_candidate.py"),
            "--repo",
            str(candidate),
            "--candidate",
            "HEAD",
            "--plan-repository",
            str(plan_repository),
            "--plan-approved-ref",
            PLAN_APPROVED_REF,
            "--candidate-evidence-descriptor-config",
            str(descriptor_config),
            "--execution-evidence-sink-config",
            str(execution_config),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["gate_hash"] == gate["gate_hash"]


def test_gate_holds_when_x_bundle_artifact_does_not_match(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
        corrupt_role="independent-review",
    )
    gate = _gate(candidate, plan_repository, descriptor_config, execution_config)
    assert gate["allowed"] is False
    assert gate["reasons"] == [
        "missing-or-invalid-governed-evidence:independent-review",
        "missing-or-invalid-independent-review-evidence",
    ]


def test_gate_rejects_a_legacy_or_substituted_card_receipt_sink_binding(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
        corrupt_sink_binding_role="controller-verification",
    )
    gate = _gate(candidate, plan_repository, descriptor_config, execution_config)
    assert gate["allowed"] is False
    assert gate["reasons"] == ["missing-or-invalid-governed-evidence:controller-verification"]


@pytest.mark.parametrize(
    "corruption",
    [
        "missing",
        "focused",
        "full",
        "focused-output",
        "migration",
        "review",
        "review-pair",
        "release",
        "rollback",
        "execution-proof",
        "runner-descriptor",
        "signer-descriptor",
        "luet-receipt",
        "luet-provider",
        "luet-binary",
        "luet-missing",
        "manifest-conformance",
    ],
)
def test_gate_requires_every_retained_s_or_x_evidence_artifact(tmp_path, corruption):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
        corrupt_w08=corruption,
    )
    gate = _gate(candidate, plan_repository, descriptor_config, execution_config)
    assert gate["allowed"] is False
    expected = (
        ["missing-or-invalid-independent-review-evidence"]
        if corruption in {"review", "review-pair"}
        else [
            "missing-or-invalid-candidate-evidence",
            "missing-or-invalid-independent-review-evidence",
        ]
    )
    assert gate["reasons"] == expected


@pytest.mark.parametrize("corruption", ["solution", "graph", "missing"])
def test_gate_requires_a_valid_d_pinned_w06_plan_materialization(tmp_path, corruption):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
        corrupt_w06_materialization=corruption,
    )
    gate = _gate(candidate, plan_repository, descriptor_config, execution_config)
    assert gate["allowed"] is False
    assert gate["luet_conformance_receipt_hash"] is None
    assert gate["reasons"] == [
        "missing-or-invalid-candidate-evidence",
        "missing-or-invalid-independent-review-evidence",
    ]


def test_gate_rejects_a_one_store_cycle_even_when_both_pins_are_individually_valid(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, _execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    with pytest.raises(CandidateReceiptSinkError, match="must be disjoint"):
        candidate_admission_gate(
            candidate,
            candidate="HEAD",
            plan_repository=plan_repository,
            plan_approved_ref=PLAN_APPROVED_REF,
            candidate_evidence_descriptor=descriptor,
            execution_sink=PinnedGitReceiptSink(
                descriptor.candidate_evidence_sink_descriptor,
                candidate_repository=candidate,
            ),
        )


def test_pinned_candidate_evidence_descriptor_rejects_a_tampered_dynamic_s_pin(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    _plans, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, _execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    pin = json.loads(descriptor_config.read_text())
    pin["content_sha256"] = "sha256:" + "0" * 64
    descriptor_config.write_bytes(canonical(pin))
    with pytest.raises(CandidateReceiptSinkError, match="content hash mismatch"):
        load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)


def test_candidate_local_descriptor_configuration_or_repository_is_refused(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    _plans, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, _x, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    local = candidate / "candidate-evidence-descriptor-config.json"
    local.write_bytes(descriptor_config.read_bytes())
    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        load_pinned_candidate_evidence_descriptor(local, candidate_repository=candidate)
    execution_descriptor = json.loads(execution_config.read_text())
    execution_descriptor["repository"] = str(candidate)
    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        PinnedGitReceiptSink(execution_descriptor, candidate_repository=candidate)


def test_cli_requires_both_operator_configured_evidence_roots(tmp_path):
    candidate, _commit, _tree = candidate_repository(tmp_path)
    plan_repository, _plan_commit = approved_plan_repository(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "admit_governed_candidate.py"),
            "--repo",
            str(candidate),
            "--candidate",
            "HEAD",
            "--plan-repository",
            str(plan_repository),
            "--plan-approved-ref",
            PLAN_APPROVED_REF,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "--candidate-evidence-descriptor-config" in completed.stderr


def _bootstrap_deployment_declaration():
    return {
        "schema": "tgw-bootstrap-deployment-declaration/v1",
        "target": {"host": "tgw-prod", "flake_repository_id": "tgw-flake"},
        "expected_prior_closure": "/nix/store/" + "a" * 32 + "-nixos-system-tgw-prod-previous",
        "intended_next_closure": "/nix/store/" + "b" * 32 + "-nixos-system-tgw-prod-candidate",
        "health_probes": ["broker", "egress", "namespace"],
    }


def _append_bootstrap_deployment_contract(_execution_sink, *, source_commit, contract, tmp_path):
    contract_sink = tmp_path / "bootstrap-contract-sink"
    _new_sink(contract_sink, email="bootstrap-contract@example.invalid", name="Bootstrap contract Y")
    ref = bootstrap_deployment_contract_ref(source_commit)
    raw = write_json(contract_sink / "bootstrap-deployment" / "contract.json", contract)
    descriptor = _commit_sink(
        contract_sink,
        sink_id="bootstrap-contract-sink",
        artifacts=[
            {
                "ref": ref,
                "path": "bootstrap-deployment/contract.json",
                "content_sha256": digest(raw),
            },
        ],
        message="retain immutable W09 bootstrap deployment contract Y",
    )
    config = tmp_path / "w09-bootstrap-contract-sink-config.json"
    write_json(config, descriptor)
    return ref, config


def _pinned_execution_evidence_sink(candidate, execution_config):
    return PinnedGitReceiptSink(
        load_receipt_sink_descriptor(execution_config, candidate_repository=candidate),
        candidate_repository=candidate,
    )


def _bootstrap_contract_resolver(candidate, plan_repository, descriptor_config, execution_config, contract_config):
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    execution_sink = _pinned_execution_evidence_sink(candidate, execution_config)
    contract_sink = _pinned_execution_evidence_sink(candidate, contract_config)
    return PinnedBootstrapDeploymentContractResolver(
        candidate,
        plan_repository=plan_repository,
        plan_approved_ref=PLAN_APPROVED_REF,
        candidate_evidence_descriptor=descriptor,
        execution_evidence_sink=execution_sink,
        bootstrap_contract_sink=contract_sink,
    )


def test_bootstrap_contract_is_derived_from_exact_w08_s_d_x_evidence_and_retained_in_disjoint_y(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, execution_sink, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    contract = derive_bootstrap_deployment_contract(
        candidate,
        candidate="HEAD",
        plan_repository=plan_repository,
        plan_approved_ref=PLAN_APPROVED_REF,
        candidate_evidence_descriptor=descriptor,
        execution_evidence_sink=_pinned_execution_evidence_sink(candidate, execution_config),
        deployment_declaration=_bootstrap_deployment_declaration(),
    )
    ref, contract_config = _append_bootstrap_deployment_contract(
        execution_sink,
        source_commit=commit,
        contract=contract,
        tmp_path=tmp_path,
    )

    verified = _bootstrap_contract_resolver(
        candidate,
        plan_repository,
        descriptor_config,
        execution_config,
        contract_config,
    ).resolve(ref, contract["contract_hash"])

    assert ref == bootstrap_deployment_contract_ref(commit)
    assert contract["candidate"]["commit"] == commit
    assert contract["candidate"]["tree"] == tree
    assert contract["plan"]["commit"] == plan_commit
    assert contract["release"]["manifest_hash"].startswith("sha256:")
    assert contract["rollback"]["manifest_hash"].startswith("sha256:")
    admission = contract["candidate"]["governed_admission"]
    assert admission["gate_hash"].startswith("sha256:")
    assert admission["independent_review"]["qualified_execution_proof_hash"].startswith("sha256:")
    assert contract["deployment"]["expected_prior"]["generation"] == "previous"
    assert contract["deployment"]["intended_next"]["generation"] == "candidate"
    assert verified.provider_binding() == {
        "bootstrap_contract_ref": ref,
        "bootstrap_contract_hash": contract["contract_hash"],
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "candidate",
        "sink",
        "legacy-static-only",
        "admission-gate",
        "review-bundle",
        "symbolic-closure",
        "typed-effect",
        "rollback-contract",
    ],
)
def test_bootstrap_contract_rejects_widened_or_mismatched_w08_and_closure_bindings(tmp_path, corruption):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _s, _d, execution_sink, descriptor_config, execution_config = pinned_sinks(
        tmp_path,
        candidate_repo=candidate,
        source_commit=commit,
        source_tree=tree,
        plan_commit=plan_commit,
    )
    descriptor = load_pinned_candidate_evidence_descriptor(descriptor_config, candidate_repository=candidate)
    contract = derive_bootstrap_deployment_contract(
        candidate,
        candidate="HEAD",
        plan_repository=plan_repository,
        plan_approved_ref=PLAN_APPROVED_REF,
        candidate_evidence_descriptor=descriptor,
        execution_evidence_sink=_pinned_execution_evidence_sink(candidate, execution_config),
        deployment_declaration=_bootstrap_deployment_declaration(),
    )
    contract = json.loads(json.dumps(contract))
    if corruption == "candidate":
        contract["candidate"]["commit"] = "0" * 40
    elif corruption == "sink":
        contract["candidate"]["candidate_evidence"]["sink"]["commit"] = "0" * 40
    elif corruption == "legacy-static-only":
        contract["candidate"].pop("governed_admission")
    elif corruption == "admission-gate":
        contract["candidate"]["governed_admission"]["gate_hash"] = "sha256:" + "0" * 64
    elif corruption == "review-bundle":
        contract["candidate"]["governed_admission"]["independent_review"]["bundle_hash"] = "sha256:" + "0" * 64
    elif corruption == "symbolic-closure":
        contract["deployment"]["intended_next"]["closure"] = "current-system"
    elif corruption == "typed-effect":
        contract["typed_effects"]["install"]["target_host"] = "other-host"
    else:
        contract["rollback_contract"]["generation"] = "unbound-rollback"
    unsigned = {key: value for key, value in contract.items() if key != "contract_hash"}
    contract["contract_hash"] = object_hash(unsigned)
    ref, contract_config = _append_bootstrap_deployment_contract(
        execution_sink,
        source_commit=commit,
        contract=contract,
        tmp_path=tmp_path,
    )

    with pytest.raises(BootstrapDeploymentContractError):
        _bootstrap_contract_resolver(
            candidate,
            plan_repository,
            descriptor_config,
            execution_config,
            contract_config,
        ).resolve(ref, contract["contract_hash"])
