import hashlib
import json
import subprocess

import pytest

from tgw.candidate_manifest import CandidateManifestError, build_candidate_manifest, graph_hash
from tgw.plan_luet import LUET_REVISION, LUET_VERSION, PROVIDER_ID


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "source").write_text("candidate")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "candidate"], cwd=repo, check=True)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    return repo, commit, tree


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
    return build_candidate_manifest(
        repo,
        commit=commit,
        base_commit=commit,
        plan_commit="plan",
        solution_hash="sha256:solution",
        closure_hash="sha256:closure",
        focused_receipt={"status": "passed"},
        full_suite=("pytest", "-q"),
        graph=graph,
        conformance_receipt=receipt,
    )


def test_persisted_pinned_receipt_enables_candidate_without_live_luet(tmp_path):
    repo, commit, tree = _repo(tmp_path)
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    receipt = _receipt(graph, commit, tree)
    manifest = _manifest(repo, commit, graph, receipt)
    assert manifest["conformance"] == {"status": "VERIFIED", "receipt_hash": receipt["receipt_hash"]}
    assert manifest["dispatchable"] is True


@pytest.mark.parametrize("field", ["plan_commit", "graph_hash", "closure_hash", "source_commit", "source_tree", "luet_revision"])
def test_stale_or_unpinned_receipt_is_rejected(tmp_path, field):
    repo, commit, tree = _repo(tmp_path)
    graph = {"schema": "tgw-plan/v2", "plan_commit": "plan"}
    receipt = _receipt(graph, commit, tree)
    receipt[field] = "stale"
    with pytest.raises(CandidateManifestError, match="binding mismatch"):
        _manifest(repo, commit, graph, receipt)


def test_missing_receipt_prepares_held_candidate(tmp_path):
    repo, commit, _ = _repo(tmp_path)
    manifest = _manifest(repo, commit, {"schema": "tgw-plan/v2"})
    assert manifest["conformance"]["status"] == "MISSING"
    assert manifest["dispatchable"] is False
    assert manifest["tests"]["full_suite"]["status"] == "DEFINED_NOT_RUN"
