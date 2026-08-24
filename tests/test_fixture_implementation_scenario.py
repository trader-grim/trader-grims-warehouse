"""Fixture-only clean-baseline scenario for the W07 implementation path."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from tgw.development.coding_snapshot import build_coding_snapshot
from tgw.development.foreman import ForemanConfig, TodoRecord, tick
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.development.treatments import CODING_TREATMENTS
from tgw.workflow_kernel.contracts import GoalProfile, RuntimeWorkGraph
from tgw.workflow_kernel.evaluator import evaluate


def _git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def _clean_fixture_worktree(path: Path) -> str:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    (path / "fixture.txt").write_text("baseline\n")
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Fixture", "-c", "user.email=fixture@tgw.local", "add", "fixture.txt"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Fixture", "-c", "user.email=fixture@tgw.local", "commit", "-m", "baseline"],
        check=True, capture_output=True,
    )
    return _git(path, "rev-parse", "HEAD")


def test_clean_fixture_source_baseline_makes_only_implementation_eligible(tmp_path):
    baseline = _clean_fixture_worktree(tmp_path / "worktree")
    snapshot = build_coding_snapshot(
        tmp_path / "worktree", GoalProfile("fixture", "1", ("implemented",)),
        CODING_TREATMENTS, implementation_baseline_commit=baseline,
    )
    implemented = next(item for item in snapshot.assertions if item.condition_id == "implemented")
    assert implemented.result.value == "false"
    graph = evaluate(snapshot=snapshot, goal=CODING_READY_FOR_IMPLEMENTATION, treatments=CODING_TREATMENTS, evaluator_version="fixture")
    assert [(item.treatment_id, item.treatment_version) for item in graph.eligible_treatments] == [("codex-implement", "1")]


def test_fixture_baseline_is_never_applied_to_an_ordinary_todo(tmp_path):
    observed = []
    ordinary = TodoRecord(1, "codex", 1, "ordinary", str(tmp_path / "ordinary"))
    graph = RuntimeWorkGraph("runtime-work-graph/v1", "g", ordinary.worktree, "v", "fixture", "1", "fixture", "e", "c", "r", (), (), (), (), (), (), (), (), ())

    def snapshot(*_args, implementation_baseline_commit=None, **_kwargs):
        observed.append(implementation_baseline_commit)
        return object()

    with patch("tgw.development.foreman.validated_coding_worktree", return_value=Path(ordinary.worktree)), patch("tgw.development.foreman.build_coding_snapshot", side_effect=snapshot), patch("tgw.development.foreman.evaluate", return_value=graph):
        assert tick(ForemanConfig(), fetch_todos=lambda: [ordinary], check_active_fn=lambda _: False).skipped_waiting == 1
    assert observed == [None]


def test_fixture_baseline_must_equal_the_todo_bound_source_commit(tmp_path):
    fixture = TodoRecord(2, "codex", 1, "fixture", str(tmp_path / "fixture"), {"fixture_run_id": "fixture-example", "source_commit": "a" * 40})
    config = ForemanConfig(fixture_implementation_baseline_commit="b" * 40)
    result = tick(config, fetch_todos=lambda: [fixture], check_active_fn=lambda _: False)
    assert result.errors == 1
