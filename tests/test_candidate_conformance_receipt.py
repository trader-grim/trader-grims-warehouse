import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from tgw.candidate_manifest import (
    CandidateManifestError,
    build_candidate_manifest,
    create_luet_conformance_receipt,
    create_test_output_artifact,
    create_test_receipt,
    graph_hash,
    load_candidate_test_plan,
)
from tgw.plan_luet import LUET_REVISION, LUET_VERSION, PINNED_LUET_BINARY_SHA256, PROVIDER_ID, normalize_conformance_graph


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source").write_text("baseline")
    _install_test_contract(repo)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "source").write_text("candidate")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    return repo, base, commit, tree


def _install_test_contract(repo: Path):
    runner = repo / "scripts" / "candidate-test-runner.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("# fixture runner\n")
    plan = repo / "agent-services" / "catalogs" / "governed-candidate-test-plan-v1.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    plan.write_text(json.dumps({
        "schema": "tgw-candidate-test-plan/v1", "plan_id": "fixture-candidate-tests", "version": 1,
        "runner": {
            "path": "scripts/candidate-test-runner.py",
            "sha256": "sha256:" + hashlib.sha256(runner.read_bytes()).hexdigest(),
            "argv_prefix": ["-m", "pytest"],
        },
        "scopes": {"focused": {"argv": ["-q", "tests/selected"]}, "full": {"argv": ["-q"]}},
    }, sort_keys=True))


def _test_evidence(repo: Path, commit: str, tree: str, scope: str, *, returncode: int = 0):
    plan = load_candidate_test_plan(repo, source_commit=commit)
    command = plan["commands"][scope]
    output = create_test_output_artifact(
        scope=scope, command=command, source_commit=commit, source_tree=tree,
        stdout=b"fixture test output", stderr=b"",
    )
    return create_test_receipt(
        scope=scope, command=command, source_commit=commit, source_tree=tree,
        returncode=returncode, test_plan=plan, output_artifact=output,
    ), output


def _receipt(graph, commit, tree):
    value = {
        "schema": "tgw-luet-conformance-receipt/v1",
        "provider_id": PROVIDER_ID,
        "luet_version": LUET_VERSION,
        "luet_revision": LUET_REVISION,
        "binary_sha256": PINNED_LUET_BINARY_SHA256,
        "plan_commit": "plan",
        "input_graph_hash": graph_hash(graph),
        "graph_hash": graph_hash(normalize_conformance_graph(graph)),
        "closure_hash": "sha256:closure",
        "source_commit": commit,
        "source_tree": tree,
        "status": "AGREEMENT",
        "selected_providers": ["a"],
    }
    value["receipt_hash"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def _manifest(repo, commit, graph, receipt=None, *, plan_commit="plan"):
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}^{{tree}}"], cwd=repo, text=True).strip()
    base = subprocess.check_output(["git", "rev-parse", f"{commit}^"], cwd=repo, text=True).strip()
    base_tree = subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True).strip()
    focused, focused_output = _test_evidence(repo, commit, tree, "focused")
    full, full_output = _test_evidence(repo, commit, tree, "full")
    return build_candidate_manifest(
        repo,
        commit=commit,
        base_commit=base,
        predecessor_release={
            "schema": "tgw-release-manifest-v1", "generation": "previous",
            "commit": base, "git_tree": base_tree, "archive_sha256": "a" * 64,
        },
        plan_commit=plan_commit,
        solution_hash="sha256:solution",
        closure_hash="sha256:closure",
        focused_receipt=focused,
        full_suite_receipt=full,
        focused_output_artifact=focused_output,
        full_suite_output_artifact=full_output,
        graph=graph,
        conformance_receipt=receipt,
    )


def test_persisted_pinned_receipt_enables_candidate_without_live_luet(tmp_path):
    repo, _base, commit, tree = _repo(tmp_path)
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    receipt = _receipt(graph, commit, tree)
    manifest = _manifest(repo, commit, graph, receipt)
    assert manifest["conformance"] == {"status": "VERIFIED", "receipt_hash": receipt["receipt_hash"]}
    assert manifest["dispatchable"] is True


def test_catalog_input_round_trips_from_luet_receipt_to_candidate_manifest(tmp_path):
    repo, _base, commit, tree = _repo(tmp_path)
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "agent-services/catalogs/governed-execution-platform-v1.json").read_text()
    )
    receipt = create_luet_conformance_receipt(
        {
            "provider_id": PROVIDER_ID, "available": True, "status": "AGREEMENT",
            "closure_hash": "sha256:closure", "selected_providers": ["fixture"],
        },
        graph=catalog, plan_commit=catalog["plan_commit"], source_commit=commit,
        source_tree=tree, binary_sha256=PINNED_LUET_BINARY_SHA256,
    )
    manifest = _manifest(
        repo, commit, catalog, receipt, plan_commit=catalog["plan_commit"],
    )
    assert receipt["input_graph_hash"] == graph_hash(catalog)
    assert receipt["graph_hash"] == graph_hash(normalize_conformance_graph(catalog))
    assert manifest["conformance"]["status"] == "VERIFIED"


@pytest.mark.parametrize("field", ["plan_commit", "input_graph_hash", "graph_hash", "closure_hash", "source_commit", "source_tree", "luet_revision"])
def test_stale_or_unpinned_receipt_is_rejected(tmp_path, field):
    repo, _base, commit, tree = _repo(tmp_path)
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    receipt = _receipt(graph, commit, tree)
    receipt[field] = "stale"
    with pytest.raises(CandidateManifestError, match="binding mismatch"):
        _manifest(repo, commit, graph, receipt)


def test_missing_conformance_receipt_prepares_held_candidate_but_never_omits_test_proof(tmp_path):
    repo, _base, commit, _ = _repo(tmp_path)
    manifest = _manifest(repo, commit, {"schema": "tgw-plan/v2"})
    assert manifest["conformance"]["status"] == "MISSING"
    assert manifest["dispatchable"] is False
    assert manifest["tests"]["full_suite"]["status"] == "PASS"


def test_noop_command_cannot_be_recorded_as_candidate_test_evidence(tmp_path):
    repo, _base, commit, tree = _repo(tmp_path)
    plan = load_candidate_test_plan(repo, source_commit=commit)
    output = create_test_output_artifact(
        scope="full", command=("true",), source_commit=commit, source_tree=tree,
        stdout=b"", stderr=b"",
    )
    with pytest.raises(CandidateManifestError, match="canonical test plan command"):
        create_test_receipt(
            scope="full", command=("true",), source_commit=commit, source_tree=tree,
            returncode=0, test_plan=plan, output_artifact=output,
        )


@pytest.mark.parametrize("scope", ["focused", "full"])
def test_missing_failing_or_wrong_candidate_test_proof_is_rejected(tmp_path, scope):
    repo, base, commit, tree = _repo(tmp_path)
    receipt, failed_output = _test_evidence(repo, commit, tree, scope, returncode=1)
    focused, focused_output = _test_evidence(repo, commit, tree, "focused")
    full, full_output = _test_evidence(repo, commit, tree, "full")
    kwargs = {
        "focused_receipt": focused,
        "full_suite_receipt": full,
        "focused_output_artifact": focused_output,
        "full_suite_output_artifact": full_output,
    }
    receipt_key = f"{scope}_receipt" if scope == "focused" else "full_suite_receipt"
    output_key = f"{scope}_output_artifact" if scope == "focused" else "full_suite_output_artifact"
    kwargs[receipt_key] = receipt
    kwargs[output_key] = failed_output
    with pytest.raises(CandidateManifestError, match="not passing"):
        build_candidate_manifest(
            repo, commit=commit, base_commit=base,
            predecessor_release={
                "schema": "tgw-release-manifest-v1", "generation": "previous",
                "commit": base,
                "git_tree": subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True).strip(),
                "archive_sha256": "a" * 64,
            },
            plan_commit="plan", solution_hash="sha256:solution", closure_hash="sha256:closure", **kwargs,
        )
