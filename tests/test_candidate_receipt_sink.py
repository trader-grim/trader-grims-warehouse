import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.candidate_receipt_sink import (
    GOVERNED_EXECUTION_BUNDLE_SCHEMA,
    RECEIPT_SINK_MANIFEST_SCHEMA,
    RECEIPT_SINK_SCHEMA,
    CandidateReceiptSinkError,
    PinnedGitReceiptSink,
    candidate_admission_gate,
    governed_execution_bundle_ref,
    load_receipt_sink_descriptor,
    receipt_sink_card_binding,
    receipt_sink_card_binding_content,
    resolve_approved_plan_authority,
)
from tgw.execution_resources import (
    RESOURCE_SERVICE_CAPABILITIES,
    resource_service_catalog_hash,
    resource_service_descriptor_hash,
)
from tgw.governed_execution_receipt import create_candidate_governed_execution_receipt

ROOT = Path(__file__).resolve().parents[1]
PLAN_APPROVED_REF = "refs/tgw/approved/GOVERNED-EXECUTION-PLATFORM"


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
    (repo / "app.py").write_text("answer = 42\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    return repo, git(repo, "rev-parse", "HEAD"), git(repo, "rev-parse", "HEAD^{tree}")


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
    attestation_unsigned = {
        "schema": "tgw-registered-resource-retrieval-attestation/v1",
        "service_id": service["id"],
        "run_id": f"run-{role}",
        "card_hash": card["card_hash"],
        "role": role,
        "execution_identity": execution_identity,
        "handoff_hash": handoff_hash,
        "resource_receipt_hash": resources["receipt_hash"],
        "resources": {name: bindings[name] for name in sorted(bindings)},
    }
    attestation = {**attestation_unsigned, "attestation_hash": object_hash(attestation_unsigned)}
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


def pinned_sink(
    tmp_path, *, candidate_repo, source_commit, source_tree, plan_commit,
    corrupt_role=None, corrupt_sink_binding_role=None,
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
    for role in ("implementation", "independent-review", "controller-verification"):
        evidence = role_evidence(
            role=role, source_commit=source_commit, source_tree=source_tree,
            plan_commit=plan_commit,
            receipt_sink_binding=(
                {"ref": "receipt-sink:substituted:descriptor:v1", "hash": "sha256:" + "f" * 64}
                if role == corrupt_sink_binding_role else card_sink_binding
            ),
        )
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
    assert gate["reasons"] == ["missing-or-invalid-governed-evidence:independent-review"]


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
    ]


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
