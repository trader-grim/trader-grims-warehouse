from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tgw.plan_solver import solve
from tgw.plan_transition import (
    DetachedMaterialization,
    PlanBinding,
    PlanTransitionController,
    PlanTransitionError,
    inspect_detached_materialization,
    prepare_detached_materialization,
)

ROOT = Path(__file__).resolve().parents[1]


def git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, text=True, check=True, timeout=30,
    )
    return result.stdout.strip()


def commit_repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "plans"
    git(tmp_path, "init", str(repo))
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "test")
    (repo / "plan.md").write_text("first\n")
    git(repo, "add", "plan.md")
    git(repo, "commit", "-m", "first")
    first = git(repo, "rev-parse", "HEAD")
    (repo / "plan.md").write_text("second\n")
    git(repo, "commit", "-am", "second")
    return repo, first, git(repo, "rev-parse", "HEAD")


def solution(commit: str) -> tuple[dict, dict]:
    graph = {
        "schema": "tgw-plan/v2", "plan_commit": commit,
        "capabilities": ["a@1"], "providers": [{"id": "a", "provides": ["a@1"]}],
        "target": {"id": "fixture", "profile": "implementation", "minimum_state": "admitted", "required_capabilities": ["a@1"]},
    }
    first = solve(graph, expected_plan_commit=commit)
    return graph, solve(graph, expected_plan_commit=commit, conformance_result={"available": True, "closure_hash": first["closure_hash"]})


def test_prepares_handoff_with_atomic_activation_and_rollback_boundaries(tmp_path):
    repo, prior_commit, successor_commit = commit_repo(tmp_path)
    _, prior_solution = solution(prior_commit)
    successor_graph, successor_solution = solution(successor_commit)
    prior = prepare_detached_materialization(repo, successor_commit=prior_commit, destination=tmp_path / "prior")
    successor = prepare_detached_materialization(repo, successor_commit=successor_commit, destination=tmp_path / "successor")

    controller = PlanTransitionController()
    successor_binding = controller.build_successor(
        predecessor=PlanBinding(prior_commit, prior_solution), successor_commit=successor_commit,
        successor_graph=successor_graph, solver=lambda _graph, **_kwargs: successor_solution,
    )
    receipt = controller.handoff(
        predecessor=PlanBinding(prior_commit, prior_solution), successor=successor_binding,
        predecessor_materialization=prior, successor_materialization=successor,
        amendment_id="AMENDMENT-TEST-001",
    )

    assert receipt["status"] == "prepared-not-activated"
    assert receipt["predecessor"]["plan_commit"] == prior_commit
    assert receipt["successor"]["plan_commit"] == successor_commit
    assert receipt["activation_boundary"]["atomic_updates"] == ["approved_ref", "approved_materialization", "context_plan_commit", "context_solution_hash"]
    assert receipt["rollback_boundary"]["target"]["plan_commit"] == prior_commit
    for boundary, binding, materialization in (
        (receipt["activation_boundary"], receipt["successor"], successor),
        (receipt["rollback_boundary"], receipt["predecessor"], prior),
    ):
        assert boundary["plan_commit"] == binding["plan_commit"]
        assert boundary["solution_hash"] == binding["solution_hash"]
        assert boundary["materialization_path"] == str(materialization.path)
        assert boundary["materialization_tree"] == materialization.tree
    assert receipt["receipt_hash"].startswith("sha256:")
    assert git(repo, "rev-parse", "HEAD") == successor_commit  # preparation did not alter repository HEAD


def test_rejects_graph_or_materialization_that_does_not_match_binding(tmp_path):
    repo, prior_commit, successor_commit = commit_repo(tmp_path)
    graph, prior_solution = solution(prior_commit)
    controller = PlanTransitionController()
    with pytest.raises(PlanTransitionError, match="successor graph"):
        controller.build_successor(
            predecessor=PlanBinding(prior_commit, prior_solution), successor_commit=successor_commit,
            successor_graph=graph, solver=solve,
        )
    branch = tmp_path / "branch"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "not-detached", str(branch), successor_commit], check=True)
    with pytest.raises(PlanTransitionError, match="detached"):
        inspect_detached_materialization(branch, expected_commit=successor_commit)


def test_rejects_existing_destination_without_touching_it(tmp_path):
    repo, _, successor_commit = commit_repo(tmp_path)
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    marker = occupied / "marker"
    marker.write_text("keep")
    with pytest.raises(PlanTransitionError, match="must not already exist"):
        prepare_detached_materialization(repo, successor_commit=successor_commit, destination=occupied)
    assert marker.read_text() == "keep"


def test_handoff_reinspects_and_rejects_forged_or_changed_materializations(tmp_path):
    repo, prior_commit, successor_commit = commit_repo(tmp_path)
    _, prior_solution = solution(prior_commit)
    successor_graph, successor_solution = solution(successor_commit)
    prior = prepare_detached_materialization(repo, successor_commit=prior_commit, destination=tmp_path / "prior")
    successor = prepare_detached_materialization(repo, successor_commit=successor_commit, destination=tmp_path / "successor")
    controller = PlanTransitionController()
    successor_binding = controller.build_successor(
        predecessor=PlanBinding(prior_commit, prior_solution), successor_commit=successor_commit,
        successor_graph=successor_graph, solver=lambda _graph, **_kwargs: successor_solution,
    )

    def handoff(*, before=prior, after=successor):
        return controller.handoff(
            predecessor=PlanBinding(prior_commit, prior_solution), successor=successor_binding,
            predecessor_materialization=before, successor_materialization=after,
            amendment_id="AMENDMENT-TEST-001",
        )

    with pytest.raises(PlanTransitionError):
        handoff(after=DetachedMaterialization(tmp_path / "missing", successor_commit, successor.tree))
    with pytest.raises(PlanTransitionError, match="declared Plan commit"):
        handoff(after=DetachedMaterialization(successor.path, prior_commit, successor.tree))
    with pytest.raises(PlanTransitionError, match="tree does not match"):
        handoff(after=DetachedMaterialization(successor.path, successor_commit, "forged-tree"))

    (successor.path / "untracked").write_text("dirty")
    with pytest.raises(PlanTransitionError, match="not clean"):
        handoff()
    (successor.path / "untracked").unlink()

    attached = tmp_path / "attached"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-b", "attached-branch", str(attached), successor_commit], check=True)
    with pytest.raises(PlanTransitionError, match="detached"):
        handoff(after=DetachedMaterialization(attached, successor_commit, git(attached, "rev-parse", "HEAD^{tree}")))


def test_detached_head_check_is_bounded(tmp_path):
    repo, _, successor_commit = commit_repo(tmp_path)
    materialization = prepare_detached_materialization(
        repo, successor_commit=successor_commit, destination=tmp_path / "detached",
    )
    with patch("tgw.plan_transition.subprocess.run", wraps=subprocess.run) as run:
        assert inspect_detached_materialization(
            materialization.path, expected_commit=successor_commit,
        ) == materialization
    symbolic_calls = [
        call for call in run.call_args_list
        if call.args[0][-3:] == ["symbolic-ref", "-q", "HEAD"]
    ]
    assert len(symbolic_calls) == 1
    assert symbolic_calls[0].kwargs["timeout"] == 30


def test_failed_materialization_cleanup_is_bounded(tmp_path):
    repo, _, successor_commit = commit_repo(tmp_path)
    with patch(
        "tgw.plan_transition.inspect_detached_materialization",
        side_effect=PlanTransitionError("forced inspection failure"),
    ), patch("tgw.plan_transition.subprocess.run", wraps=subprocess.run) as run:
        with pytest.raises(PlanTransitionError, match="forced inspection failure"):
            prepare_detached_materialization(
                repo, successor_commit=successor_commit, destination=tmp_path / "failed",
            )
    cleanup_calls = [
        call for call in run.call_args_list
        if call.args[0][3:6] == ["worktree", "remove", "--force"]
    ]
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0].kwargs["timeout"] == 30


def test_w12_catalog_content_bindings_cover_the_reviewed_transition_bytes():
    catalog = json.loads(
        (ROOT / "agent-services/catalogs/w12-bootstrap-transition-v1.json").read_text(),
    )
    provider = catalog["providers"][0]
    expected = provider["implementation_content"]
    observed = {
        path: "sha256:" + hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        for path in sorted(expected)
    }
    assert observed == expected
    evidence = catalog["observations"][0]["evidence"]
    assert f"source-commit:{provider['implementation_commit']}" in evidence
    for path, digest in expected.items():
        assert f"source-content:{path}:{digest}" in evidence
