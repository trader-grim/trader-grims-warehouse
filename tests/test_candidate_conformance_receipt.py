import hashlib
import json
import subprocess

import pytest

from tgw.candidate_manifest import (
    CandidateManifestError,
    build_candidate_manifest,
    create_test_receipt,
    graph_hash,
)
from tgw.plan_luet import LUET_REVISION, LUET_VERSION, PROVIDER_ID


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source").write_text("baseline")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=repo, check=True)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "source").write_text("candidate")
    subprocess.run(["git", "commit", "-qam", "candidate"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    return repo, base, commit, tree


def _receipt(graph, commit, tree):
    value = {
        "schema": "tgw-luet-conformance-receipt/v1",
        "provider_id": PROVIDER_ID,
        "luet_version": LUET_VERSION,
        "luet_revision": LUET_REVISION,
        "binary_sha256": "sha256:" + "b" * 64,
        "plan_commit": "plan",
        "graph_hash": graph_hash(graph),
        "closure_hash": "sha256:closure",
        "source_commit": commit,
        "source_tree": tree,
        "status": "AGREEMENT",
        "selected_providers": ["a"],
    }
    value["receipt_hash"] = "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return value


def _manifest(repo, commit, graph, receipt=None):
    tree = subprocess.check_output(["git", "rev-parse", f"{commit}^{{tree}}"], cwd=repo, text=True).strip()
    base = subprocess.check_output(["git", "rev-parse", f"{commit}^"], cwd=repo, text=True).strip()
    base_tree = subprocess.check_output(["git", "rev-parse", f"{base}^{{tree}}"], cwd=repo, text=True).strip()
    return build_candidate_manifest(
        repo,
        commit=commit,
        base_commit=base,
        predecessor_release={
            "schema": "tgw-release-manifest-v1", "generation": "previous",
            "commit": base, "git_tree": base_tree, "archive_sha256": "a" * 64,
        },
        plan_commit="plan",
        solution_hash="sha256:solution",
        closure_hash="sha256:closure",
        focused_receipt=create_test_receipt(
            scope="focused", command=("pytest", "tests/selected"), source_commit=commit,
            source_tree=tree, returncode=0,
        ),
        full_suite_receipt=create_test_receipt(
            scope="full", command=("pytest", "-q"), source_commit=commit,
            source_tree=tree, returncode=0,
        ),
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


@pytest.mark.parametrize("field", ["plan_commit", "graph_hash", "closure_hash", "source_commit", "source_tree", "luet_revision"])
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
    with pytest.raises(CandidateManifestError, match="must run pytest"):
        create_test_receipt(
            scope="full", command=("true",), source_commit=commit, source_tree=tree,
            returncode=0,
        )


@pytest.mark.parametrize("scope", ["focused", "full"])
def test_missing_failing_or_wrong_candidate_test_proof_is_rejected(tmp_path, scope):
    repo, base, commit, tree = _repo(tmp_path)
    receipt = create_test_receipt(
        scope=scope, command=("pytest",), source_commit=commit, source_tree=tree,
        returncode=1,
    )
    kwargs = {
        "focused_receipt": create_test_receipt(
            scope="focused", command=("pytest",), source_commit=commit, source_tree=tree,
            returncode=0,
        ),
        "full_suite_receipt": create_test_receipt(
            scope="full", command=("pytest", "-q"), source_commit=commit, source_tree=tree,
            returncode=0,
        ),
    }
    kwargs[f"{scope}_receipt" if scope == "focused" else "full_suite_receipt"] = receipt
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
