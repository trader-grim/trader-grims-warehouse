import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tgw.candidate_manifest import (
    build_candidate_manifest,
    create_migration_safety_receipt,
    create_test_output_artifact,
    create_test_receipt,
    load_candidate_test_plan,
)
from tgw.candidate_receipt_sink import (
    GOVERNED_EXECUTION_BUNDLE_SCHEMA,
    RECEIPT_SINK_MANIFEST_SCHEMA,
    RECEIPT_SINK_SCHEMA,
    REVIEWED_CANDIDATE_EVIDENCE_BUNDLE_SCHEMA,
    ROLLBACK_MANIFEST_SCHEMA,
    CandidateReceiptSinkError,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    governed_execution_bundle_ref,
    load_receipt_sink_descriptor,
    receipt_sink_card_binding,
    receipt_sink_card_binding_content,
    resolve_approved_plan_authority,
    reviewed_candidate_evidence_bundle_ref,
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

ROOT = Path(__file__).resolve().parents[1]
PLAN_APPROVED_REF = "refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM"
TEST_ATTESTATION_KEY_ID = "candidate-sink-attestation-key-1"
TEST_ATTESTATION_PRIVATE_KEY = Ed25519PrivateKey.generate()


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
    plan.write_text(json.dumps({
        "schema": "tgw-candidate-test-plan/v1", "plan_id": "candidate-sink-fixture", "version": 1,
        "runner": {
            "path": "scripts/candidate-test-runner.py",
            "sha256": "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest(),
            "argv_prefix": ["-m", "pytest"],
        },
        "scopes": {
            "focused": {"argv": ["-q", "tests/test_governed_resource_service.py"]},
            "full": {"argv": ["-q"]},
        },
    }, sort_keys=True))


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


def service_catalog(plan_commit):
    service = {
        "schema": "tgw-registered-resource-service/v1",
        "id": "candidate-receipt-service",
        "endpoint": "https://receipts.invalid",
        "credential_env": None,
        "timeout_seconds": 5,
    }
    catalog = {
        "schema": "tgw-registered-resource-service-catalog/v2",
        "catalog_ref": "catalog:candidate-receipt-service@1",
        "plan_commit": plan_commit,
        "services": [{
            "id": service["id"],
            "descriptor_hash": resource_service_descriptor_hash(service),
            "capabilities": sorted(RESOURCE_SERVICE_CAPABILITIES),
            "attestation_key_id": TEST_ATTESTATION_KEY_ID,
            "attestation_public_key": ed25519_public_key(TEST_ATTESTATION_PRIVATE_KEY),
        }],
    }
    return service, catalog


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
        "schema": "tgw-registered-resource-retrieval-attestation/v2",
        "service_id": service["id"],
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
        attestation_payload, signing_private_key=TEST_ATTESTATION_PRIVATE_KEY,
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
    files = {
        path: hashlib.sha256(
            subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo)
        ).hexdigest()
        for path in git(repo, "ls-tree", "-r", "--name-only", commit).splitlines()
    }
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit], cwd=repo)
    content_manifest = hashlib.sha256(
        (json.dumps(dict(sorted(files.items())), sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
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


def reviewed_candidate_evidence(
    candidate_repo, *, source_commit, source_tree, plan_commit, independent_review_receipt,
):
    base_commit = git(candidate_repo, "rev-parse", f"{source_commit}^")
    base_tree = git(candidate_repo, "rev-parse", f"{base_commit}^{{tree}}")
    predecessor = release_manifest(candidate_repo, base_commit, "previous")
    test_plan = load_candidate_test_plan(candidate_repo, source_commit=source_commit)
    focused_command = test_plan["commands"]["focused"]
    full_command = test_plan["commands"]["full"]
    focused_output = create_test_output_artifact(
        scope="focused", command=focused_command,
        source_commit=source_commit, source_tree=source_tree,
        stdout=b"focused passed\n", stderr=b"",
    )
    full_output = create_test_output_artifact(
        scope="full", command=full_command,
        source_commit=source_commit, source_tree=source_tree,
        stdout=b"full suite passed\n", stderr=b"",
    )
    focused = create_test_receipt(
        scope="focused", command=focused_command,
        source_commit=source_commit, source_tree=source_tree, returncode=0,
        test_plan=test_plan, output_artifact=focused_output,
    )
    full = create_test_receipt(
        scope="full", command=full_command, source_commit=source_commit,
        source_tree=source_tree, returncode=0, test_plan=test_plan, output_artifact=full_output,
    )
    migrations = [
        migration_receipt(
            candidate_repo, path=path, source_commit=source_commit, source_tree=source_tree,
            base_commit=base_commit, base_tree=base_tree,
        )
        for path in ("src/migrations/001.sql", "src/migrations/002.sql")
    ]
    candidate_manifest = build_candidate_manifest(
        candidate_repo,
        commit=source_commit,
        base_commit=base_commit,
        predecessor_release=predecessor,
        plan_commit=plan_commit,
        solution_hash="sha256:" + "1" * 64,
        closure_hash="sha256:" + "2" * 64,
        focused_receipt=focused,
        full_suite_receipt=full,
        focused_output_artifact=focused_output,
        full_suite_output_artifact=full_output,
        migration_receipts=migrations,
    )
    review_packet_unsigned = {
        "schema": PACKET_SCHEMA,
        "status": "EXECUTABLE",
        "candidate_manifest_hash": candidate_manifest["manifest_hash"],
        "candidate_source": {
            key: candidate_manifest["source"][key]
            for key in ("commit", "tree", "archive_sha256")
        },
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
    return {
        "candidate_manifest": candidate_manifest,
        "focused_test_receipt": focused,
        "focused_test_output": focused_output,
        "full_suite_test_receipt": full,
        "full_suite_test_output": full_output,
        "migration_receipts": migrations,
        "review_packet": review_packet,
        "review_result": review_result,
        "release_manifest": release,
        "rollback_manifest": rollback,
    }


def pinned_sink(
    tmp_path, *, candidate_repo, source_commit, source_tree, plan_commit,
    corrupt_role=None, corrupt_sink_binding_role=None, corrupt_w08=None,
):
    sink = tmp_path / "receipt-sink"
    sink.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=sink, check=True)
    subprocess.run(["git", "config", "user.email", "sink@example.invalid"], cwd=sink, check=True)
    subprocess.run(["git", "config", "user.name", "Receipt sink"], cwd=sink, check=True)
    descriptor_seed = {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": "test-receipt-sink",
        "repository": str(sink.resolve()),
        "commit": "0" * 40,
        "tree": "0" * 40,
        "manifest_path": "manifest.json",
        "manifest_content_sha256": "sha256:" + "0" * 64,
    }
    card_sink_binding = receipt_sink_card_binding(descriptor_seed)
    artifacts = []
    evidence_by_role = {}
    for role in ("implementation", "independent-review", "controller-verification"):
        evidence = role_evidence(
            role=role, source_commit=source_commit, source_tree=source_tree,
            plan_commit=plan_commit,
            receipt_sink_binding=(
                {"ref": "receipt-sink:substituted:descriptor:v1", "hash": "sha256:" + "f" * 64}
                if role == corrupt_sink_binding_role else card_sink_binding
            ),
        )
        evidence_by_role[role] = evidence
        pointers = {}
        for name, value in evidence.items():
            raw = write_json(sink / "artifacts" / role / f"{name}.json", value)
            ref = f"artifact:{source_commit}:{role}:{name}"
            artifacts.append({"ref": ref, "path": f"artifacts/{role}/{name}.json", "content_sha256": digest(raw)})
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
        bundle_raw = write_json(sink / "bundles" / f"{role}.json", bundle)
        artifacts.append({
            "ref": governed_execution_bundle_ref(source_commit, role),
            "path": f"bundles/{role}.json",
            "content_sha256": digest(bundle_raw),
        })
    w08 = reviewed_candidate_evidence(
        candidate_repo, source_commit=source_commit, source_tree=source_tree,
        plan_commit=plan_commit,
        independent_review_receipt=evidence_by_role["independent-review"]["role_receipt"],
    )
    if corrupt_w08 == "review":
        review = w08["review_result"]
        review_unsigned = {
            **{key: value for key, value in review.items() if key != "result_hash"},
            "dimensions": {
                "semantic": {"verdict": "PASS", "findings": []},
                "security": {
                    "verdict": "FAIL",
                    "findings": [{
                        "severity": "high", "path": "app.py", "line": 1,
                        "message": "review finding",
                    }],
                },
            },
            "overall": "FAIL",
        }
        w08["review_result"] = {**review_unsigned, "result_hash": object_hash(review_unsigned)}
    if corrupt_w08 in {"focused", "full"}:
        key = "focused_test_receipt" if corrupt_w08 == "focused" else "full_suite_test_receipt"
        receipt = dict(w08[key])
        receipt["command"] = ["pytest", "tests/not-the-retained-command"]
        receipt_unsigned = {field: item for field, item in receipt.items() if field != "receipt_hash"}
        w08[key] = {**receipt_unsigned, "receipt_hash": object_hash(receipt_unsigned)}
    if corrupt_w08 == "focused-output":
        output = dict(w08["focused_test_output"])
        output["stdout_base64"] = "dGFtcGVyZWQ="
        output_unsigned = {field: item for field, item in output.items() if field != "artifact_hash"}
        w08["focused_test_output"] = {**output_unsigned, "artifact_hash": object_hash(output_unsigned)}
    if corrupt_w08 == "release":
        release = dict(w08["release_manifest"])
        release["archive_sha256"] = "0" * 64
        w08["release_manifest"] = release
    if corrupt_w08 == "rollback":
        rollback = dict(w08["rollback_manifest"])
        rollback["candidate_commit"] = "0" * 40
        rollback_unsigned = {key: value for key, value in rollback.items() if key != "manifest_hash"}
        w08["rollback_manifest"] = {**rollback_unsigned, "manifest_hash": object_hash(rollback_unsigned)}
    pointers = {}
    for name, value in w08.items():
        if name == "migration_receipts":
            continue
        raw = write_json(sink / "reviewed-candidate" / f"{name}.json", value)
        ref = f"artifact:{source_commit}:w08:{name}"
        artifacts.append({
            "ref": ref, "path": f"reviewed-candidate/{name}.json", "content_sha256": digest(raw),
        })
        pointers[name] = {"ref": ref, "content_sha256": digest(raw)}
    migration_pointers = []
    for index, receipt in enumerate(w08["migration_receipts"], start=1):
        raw = write_json(sink / "reviewed-candidate" / f"migration-{index}.json", receipt)
        ref = f"artifact:{source_commit}:w08:migration-{index}"
        artifacts.append({
            "ref": ref, "path": f"reviewed-candidate/migration-{index}.json", "content_sha256": digest(raw),
        })
        migration_pointers.append({"ref": ref, "content_sha256": digest(raw)})
    if corrupt_w08 == "migration":
        migration_pointers = migration_pointers[:-1]
    w08_bundle_unsigned = {
        "schema": REVIEWED_CANDIDATE_EVIDENCE_BUNDLE_SCHEMA,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "plan_commit": plan_commit,
        **pointers,
        "migration_receipts": migration_pointers,
    }
    w08_bundle = {**w08_bundle_unsigned, "bundle_hash": object_hash(w08_bundle_unsigned)}
    if corrupt_w08 != "missing":
        bundle_raw = write_json(sink / "bundles" / "reviewed-candidate.json", w08_bundle)
        artifacts.append({
            "ref": reviewed_candidate_evidence_bundle_ref(source_commit),
            "path": "bundles/reviewed-candidate.json",
            "content_sha256": digest(bundle_raw),
        })
    manifest_unsigned = {
        "schema": RECEIPT_SINK_MANIFEST_SCHEMA,
        "sink_id": "test-receipt-sink",
        "artifacts": artifacts,
    }
    manifest = {**manifest_unsigned, "manifest_hash": object_hash(manifest_unsigned)}
    manifest_raw = write_json(sink / "manifest.json", manifest)
    subprocess.run(["git", "add", "."], cwd=sink, check=True)
    subprocess.run(["git", "commit", "-qm", "immutable candidate evidence"], cwd=sink, check=True)
    descriptor = {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": "test-receipt-sink",
        "repository": str(sink.resolve()),
        "commit": git(sink, "rev-parse", "HEAD"),
        "tree": git(sink, "rev-parse", "HEAD^{tree}"),
        "manifest_path": "manifest.json",
        "manifest_content_sha256": digest(manifest_raw),
    }
    config = tmp_path / "receipt-sink-config.json"
    write_json(config, descriptor)
    return sink, config


def test_pinned_sink_gate_reads_exact_committed_artifacts_and_cli_admits(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    sink_repository, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree,
        plan_commit=plan_commit,
    )
    # A mutable worktree file is not evidence: the gate reads the descriptor's
    # exact commit object, not this post-commit edit.
    (sink_repository / "artifacts" / "implementation" / "role_receipt.json").write_text("{}")
    descriptor = load_receipt_sink_descriptor(config, candidate_repository=candidate)
    assert receipt_sink_card_binding_content(descriptor) == {
        "schema": "tgw-pinned-git-candidate-receipt-sink-card-binding/v1",
        "sink_id": "test-receipt-sink",
        "repository": str(sink_repository.resolve()),
        "manifest_path": "manifest.json",
    }
    assert receipt_sink_card_binding(descriptor) == {
        "ref": "receipt-sink:test-receipt-sink:descriptor:v1",
        "hash": digest(canonical(receipt_sink_card_binding_content(descriptor))),
    }
    gate = candidate_admission_gate(
        candidate, candidate="HEAD", plan_repository=plan_repository, plan_approved_ref=PLAN_APPROVED_REF,
        sink=PinnedGitReceiptSink(descriptor, candidate_repository=candidate),
    )

    assert gate["allowed"] is True
    assert gate["source_commit"] == commit
    assert gate["plan_commit"] == plan_commit
    assert gate["plan_authority"] == {
        "schema": "tgw-governed-candidate-plan-authority/v1",
        "repository": str(plan_repository.resolve()),
        "approved_ref": PLAN_APPROVED_REF,
        "approved_commit": plan_commit,
    }
    assert len(gate["governed_execution_receipt_hashes"]) == 3
    assert gate["reviewed_candidate_evidence"] is not None
    assert len(gate["reviewed_candidate_evidence"]["migration_receipt_hashes"]) == 2
    assert gate["reviewed_candidate_evidence"]["candidate_manifest_hash"].startswith("sha256:")
    assert gate["reviewed_candidate_evidence"]["review_result_hash"].startswith("sha256:")
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "admit_governed_candidate.py"),
            "--repo", str(candidate), "--candidate", "HEAD",
            "--plan-repository", str(plan_repository), "--plan-approved-ref", PLAN_APPROVED_REF,
            "--receipt-sink-config", str(config),
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["gate_hash"] == gate["gate_hash"]


def test_gate_holds_when_a_pinned_bundle_artifact_does_not_match(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _sink, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree,
        plan_commit=plan_commit, corrupt_role="independent-review",
    )
    descriptor = load_receipt_sink_descriptor(config, candidate_repository=candidate)
    gate = candidate_admission_gate(
        candidate, candidate="HEAD", plan_repository=plan_repository, plan_approved_ref=PLAN_APPROVED_REF,
        sink=PinnedGitReceiptSink(descriptor, candidate_repository=candidate),
    )

    assert gate["allowed"] is False
    assert gate["reasons"] == [
        "missing-or-invalid-governed-evidence:independent-review",
        "missing-or-invalid-reviewed-candidate-evidence",
    ]


def test_gate_holds_when_any_card_uses_a_substituted_receipt_sink_binding(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _sink, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree,
        plan_commit=plan_commit, corrupt_sink_binding_role="controller-verification",
    )
    descriptor = load_receipt_sink_descriptor(config, candidate_repository=candidate)

    gate = candidate_admission_gate(
        candidate, candidate="HEAD", plan_repository=plan_repository, plan_approved_ref=PLAN_APPROVED_REF,
        sink=PinnedGitReceiptSink(descriptor, candidate_repository=candidate),
    )

    assert gate["allowed"] is False
    assert gate["reasons"] == ["missing-or-invalid-governed-evidence:controller-verification"]


def test_gate_holds_when_the_external_approved_plan_ref_moves(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _sink, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree, plan_commit=plan_commit,
    )
    (plan_repository / "plan.txt").write_text("a newer but unbound approved Plan\n")
    subprocess.run(["git", "add", "."], cwd=plan_repository, check=True)
    subprocess.run(["git", "commit", "-qm", "new approved Plan"], cwd=plan_repository, check=True)
    subprocess.run(
        ["git", "update-ref", PLAN_APPROVED_REF, git(plan_repository, "rev-parse", "HEAD")],
        cwd=plan_repository, check=True,
    )
    descriptor = load_receipt_sink_descriptor(config, candidate_repository=candidate)

    gate = candidate_admission_gate(
        candidate, candidate="HEAD", plan_repository=plan_repository, plan_approved_ref=PLAN_APPROVED_REF,
        sink=PinnedGitReceiptSink(descriptor, candidate_repository=candidate),
    )

    assert gate["plan_commit"] != plan_commit
    assert gate["allowed"] is False
    assert gate["reasons"] == [
        "missing-or-invalid-governed-evidence:controller-verification",
        "missing-or-invalid-governed-evidence:implementation",
        "missing-or-invalid-governed-evidence:independent-review",
        "missing-or-invalid-reviewed-candidate-evidence",
    ]


@pytest.mark.parametrize("corruption", [
    "missing", "focused", "full", "focused-output", "migration", "review", "release", "rollback",
])
def test_gate_requires_every_sink_retained_w08_artifact(tmp_path, corruption):
    candidate, commit, tree = candidate_repository(tmp_path)
    plan_repository, plan_commit = approved_plan_repository(tmp_path)
    _sink, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree,
        plan_commit=plan_commit, corrupt_w08=corruption,
    )
    descriptor = load_receipt_sink_descriptor(config, candidate_repository=candidate)

    gate = candidate_admission_gate(
        candidate, candidate="HEAD", plan_repository=plan_repository, plan_approved_ref=PLAN_APPROVED_REF,
        sink=PinnedGitReceiptSink(descriptor, candidate_repository=candidate),
    )

    assert gate["allowed"] is False
    assert gate["reasons"] == ["missing-or-invalid-reviewed-candidate-evidence"]
    assert gate["reviewed_candidate_evidence"] is None


def test_candidate_local_sink_configuration_or_repository_is_refused(tmp_path):
    candidate, commit, tree = candidate_repository(tmp_path)
    _plans, plan_commit = approved_plan_repository(tmp_path)
    _sink, config = pinned_sink(
        tmp_path, candidate_repo=candidate, source_commit=commit, source_tree=tree, plan_commit=plan_commit,
    )
    local = candidate / "receipt-sink-config.json"
    local.write_bytes(config.read_bytes())

    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        load_receipt_sink_descriptor(local, candidate_repository=candidate)

    descriptor = json.loads(config.read_text())
    descriptor["repository"] = str(candidate)
    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        PinnedGitReceiptSink(descriptor, candidate_repository=candidate)


def test_candidate_nested_beneath_sink_or_plan_root_is_refused(tmp_path):
    sink_root = tmp_path / "receipt-sink"
    sink_root.mkdir()
    candidate = sink_root / "candidate"
    candidate.mkdir()
    descriptor = {
        "schema": RECEIPT_SINK_SCHEMA,
        "sink_id": "test-receipt-sink",
        "repository": str(sink_root),
        "commit": "0" * 40,
        "tree": "0" * 40,
        "manifest_path": "manifest.json",
        "manifest_content_sha256": "sha256:" + "0" * 64,
    }
    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        PinnedGitReceiptSink(descriptor, candidate_repository=candidate)
    with pytest.raises(CandidateReceiptSinkError, match="disjoint from the candidate repository"):
        resolve_approved_plan_authority(
            sink_root, approved_ref=PLAN_APPROVED_REF, candidate_repository=candidate,
        )


def test_cli_requires_an_operator_configured_sink(tmp_path):
    candidate, _commit, _tree = candidate_repository(tmp_path)
    plan_repository, _plan_commit = approved_plan_repository(tmp_path)
    completed = subprocess.run(
        [
            sys.executable, str(ROOT / "scripts" / "admit_governed_candidate.py"),
            "--repo", str(candidate), "--candidate", "HEAD",
            "--plan-repository", str(plan_repository), "--plan-approved-ref", PLAN_APPROVED_REF,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, text=True, capture_output=True, check=False,
    )

    assert completed.returncode == 2
    assert "--receipt-sink-config" in completed.stderr
