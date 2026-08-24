"""Executable routing and receipt tests for coding treatments."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from tgw.config import load_config
from tgw.development.coding_snapshot import _CHECKERS, build_coding_snapshot
from tgw.development.profiles import CODING_READY_FOR_IMPLEMENTATION
from tgw.development.treatments import CODING_TREATMENTS
from tgw.queue.worker_base import HardFailure
from tgw.workers.coding import (
    CodingWorker,
    _run_bounded_process_group,
    receipt_path_for_treatment,
)
from tgw.workflow_kernel.contracts import (
    EffectClass,
    FingerprintResult,
    GoalProfile,
    Requirement,
    TreatmentContract,
)
from tgw.workflow_kernel.evaluator import evaluate


def _treatment(identity: str, required: str) -> TreatmentContract:
    return TreatmentContract(
        identity=identity,
        version="1",
        requires=(Requirement(required, (FingerprintResult.TRUE,)),),
        may_establish=(required,),
        must_preserve=(),
        ownership=(identity,),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt/tgw-development/v1",
    )


def _git_worktree(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"], cwd=path, check=True, capture_output=True)


def _worker(treatment_id: str, root: Path, launcher, repository_root: Path | None = None):
    return CodingWorker(
        treatment_id,
        {
            "coding": {
                "worktree_root": str(root),
                "repository_root": str(repository_root or root),
            }
        },
        launcher=launcher,
    )


def test_coding_worker_lease_outlives_bounded_launcher_timeout(tmp_path):
    worker = CodingWorker(
        "claude-review",
        {
            "queue": {"lease_seconds": 60},
            "coding": {
                "timeout_s": 2400,
                "worktree_root": str(tmp_path),
                "repository_root": str(tmp_path),
            },
        },
        launcher=lambda *_args: {},
    )

    assert worker.lease_seconds == 2700


def test_git_identity_trusts_only_the_exact_validated_path(tmp_path, monkeypatch):
    path = tmp_path / "shared-worktree"
    path.mkdir()
    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"{path}\n{path / '.git-common'}\n",
            stderr="",
        )

    monkeypatch.setattr("tgw.workers.coding.subprocess.run", run)

    assert CodingWorker._git_identity(path) == (
        path.resolve(),
        (path / ".git-common").resolve(),
    )
    assert observed["command"] == [
        "git",
        "-c",
        f"safe.directory={path.resolve()}",
        "rev-parse",
        "--show-toplevel",
        "--git-common-dir",
    ]
    assert observed["cwd"] == path


def test_bounded_launcher_timeout_kills_descendant_process_group(tmp_path):
    marker = tmp_path / "descendant-survived"
    child = (
        "import pathlib,sys,time; "
        "time.sleep(0.8); "
        "pathlib.Path(sys.argv[1]).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]]); "
        "time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded_process_group(
            [sys.executable, "-c", parent, str(marker), child],
            cwd=tmp_path,
            env={},
            timeout=0.2,
        )

    time.sleep(1)
    assert not marker.exists()


def test_bounded_launcher_timeout_kills_term_ignoring_descendant_with_closed_stdio(tmp_path):
    marker = tmp_path / "term-ignoring-descendant-survived"
    child = (
        "import os,pathlib,signal,sys,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "os.close(0); os.close(1); os.close(2); "
        "time.sleep(0.8); "
        "pathlib.Path(sys.argv[1]).write_text('survived')"
    )
    parent = (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1]]); "
        "time.sleep(30)"
    )
    with pytest.raises(subprocess.TimeoutExpired):
        _run_bounded_process_group(
            [sys.executable, "-c", parent, str(marker), child],
            cwd=tmp_path,
            env={},
            timeout=0.2,
        )

    time.sleep(1)
    assert not marker.exists()


@pytest.mark.parametrize("queue_name", ("workflow_evaluate", "ebay_publish", "ai_identify"))
def test_coding_worker_rejects_business_queues(queue_name):
    with pytest.raises(ValueError, match="unsupported coding queue"):
        CodingWorker(queue_name, {"coding": {}})


@pytest.mark.parametrize(
    "treatment_id",
    ("codex-implement", "claude-review", "controller-verify", "hermes-stitch"),
)
def test_coding_worker_claims_and_runs_dispatched_treatment(tmp_path, treatment_id):
    """Every dispatched coding treatment has a worker that claims and executes it."""
    _git_worktree(tmp_path)
    before = build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("reviewed",)))
    job = {
        "job_id": f"job-{treatment_id}",
        "payload_json": {
            "treatment_id": treatment_id,
            "treatment_version": "1",
            "graph_id": "graph-review-1",
            "worktree": str(tmp_path),
            "object_id": str(tmp_path.resolve()),
            "object_generation": before.generation,
        },
    }
    established = {
        "codex-implement": ["implemented"],
        "claude-review": ["reviewed"],
        "controller-verify": ["controller_verified"],
        "hermes-stitch": ["committed"],
    }[treatment_id]
    launcher = MagicMock(
        return_value={
            "outcome": "satisfied",
            "established_conditions": established,
            "artifacts": ["review.md"],
        }
    )
    worker = _worker(treatment_id, tmp_path.parent, launcher, tmp_path)

    with patch("tgw.queue.worker_base.state_machine") as state_machine:
        state_machine.claim_queue_jobs.return_value = [job]
        claimed = worker._claim_one()
        assert claimed == job
        receipt = worker.handle(claimed)

    launcher.assert_called_once_with(treatment_id, job["payload_json"], tmp_path)
    assert receipt["outcome"] == "satisfied"
    assert receipt_path_for_treatment(tmp_path, treatment_id).is_file()


def test_review_receipt_changes_snapshot_and_selects_next_treatment(tmp_path):
    """Completing review persists evidence that makes the next evaluation legal."""
    _git_worktree(tmp_path)
    profile = GoalProfile("test.reviewed", "1", ("reviewed",))
    next_treatment = TreatmentContract(
        identity="next-legal-treatment",
        version="1",
        requires=(Requirement("reviewed", (FingerprintResult.TRUE,)),),
        may_establish=("next",),
        must_preserve=(),
        ownership=("next",),
        effect_class=EffectClass.LOCAL,
        receipt_schema_id="receipt/tgw-development/v1",
    )
    before = build_coding_snapshot(tmp_path, profile)
    assert before.assertions[0].result is FingerprintResult.FALSE

    worker = _worker(
        "claude-review",
        tmp_path.parent,
        lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["reviewed"],
        },
        tmp_path,
    )
    worker.handle(
        {
            "payload_json": {
                "treatment_id": "claude-review",
                "treatment_version": "1",
                "graph_id": "graph-review-2",
                "worktree": str(tmp_path),
                "object_id": str(tmp_path.resolve()),
                "object_generation": before.generation,
            }
        }
    )

    after = build_coding_snapshot(tmp_path, profile)
    assert after.generation == before.generation
    assert after.assertions[0].result is FingerprintResult.TRUE
    graph = evaluate(
        snapshot=after,
        goal=profile,
        treatments=(next_treatment,),
        evaluator_version="test",
    )
    assert [item.treatment_id for item in graph.eligible_treatments] == ["next-legal-treatment"]


def test_real_coding_profile_receipt_advances_the_real_treatment_graph(tmp_path, monkeypatch):
    """A review receipt changes the foreman's shipped graph, not a toy profile."""
    _git_worktree(tmp_path)
    subprocess.run(["git", "checkout", "-b", "feature/review"], cwd=tmp_path, check=True)
    (tmp_path / "feature.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "feature.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "feature"], cwd=tmp_path, check=True, capture_output=True)

    def passing(_worktree):
        return FingerprintResult.TRUE, ("passed",), ()

    monkeypatch.setitem(_CHECKERS, "tested", passing)
    monkeypatch.setitem(_CHECKERS, "linted", passing)
    before = build_coding_snapshot(tmp_path, CODING_READY_FOR_IMPLEMENTATION, CODING_TREATMENTS)
    before_graph = evaluate(snapshot=before, goal=CODING_READY_FOR_IMPLEMENTATION, treatments=CODING_TREATMENTS, evaluator_version="test")
    assert "claude-review" in {item.treatment_id for item in before_graph.eligible_treatments}
    worker = _worker(
        "claude-review",
        tmp_path.parent,
        lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["reviewed"],
        },
        tmp_path,
    )
    worker.handle(
        {
            "payload_json": {
                "treatment_id": "claude-review",
                "graph_id": "graph-real",
                "worktree": str(tmp_path),
                "object_id": str(tmp_path.resolve()),
                "object_generation": before.generation,
            }
        }
    )
    after = build_coding_snapshot(tmp_path, CODING_READY_FOR_IMPLEMENTATION, CODING_TREATMENTS)
    results = {item.condition_id: item.result for item in after.assertions}
    assert after.generation == before.generation
    assert {"reviewed", "controller_verified"}.issubset(results)
    assert results["reviewed"] is FingerprintResult.TRUE
    after_graph = evaluate(snapshot=after, goal=CODING_READY_FOR_IMPLEMENTATION, treatments=CODING_TREATMENTS, evaluator_version="test")
    assert "claude-review" not in {item.treatment_id for item in after_graph.eligible_treatments}
    assert "controller-verify" in {item.treatment_id for item in after_graph.eligible_treatments}


@pytest.mark.parametrize("outcome", ["failed", "partial", "conflict"])
def test_unsatisfied_launcher_outcomes_never_establish_review(tmp_path, outcome):
    _git_worktree(tmp_path)
    before = build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("reviewed",)))
    worker = _worker(
        "claude-review",
        tmp_path.parent,
        lambda *_args: {
            "outcome": outcome,
            "established_conditions": [],
        },
        tmp_path,
    )
    with pytest.raises(HardFailure, match=f"reported {outcome}"):
        worker.handle(
            {
                "payload_json": {
                    "treatment_id": "claude-review",
                    "graph_id": "g",
                    "worktree": str(tmp_path),
                    "object_id": str(tmp_path.resolve()),
                    "object_generation": before.generation,
                }
            }
        )
    assert json.loads(receipt_path_for_treatment(tmp_path, "claude-review").read_text())["status"] == "FAIL"
    assert build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("reviewed",))).assertions[0].result is FingerprintResult.FALSE


def test_unsatisfied_launcher_outcome_dead_letters_instead_of_succeeding(tmp_path):
    """A FAIL receipt is durable evidence, but the queue delivery also fails."""
    _git_worktree(tmp_path)
    before = build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("reviewed",)))
    worker = _worker(
        "claude-review",
        tmp_path.parent,
        lambda *_args: {
            "outcome": "failed",
            "established_conditions": [],
        },
        tmp_path,
    )
    job = {
        "job_id": "failed-review",
        "lease_token": "11111111-1111-4111-8111-111111111111",
        "payload_json": {
            "treatment_id": "claude-review",
            "graph_id": "same-graph",
            "worktree": str(tmp_path),
            "object_id": str(tmp_path.resolve()),
            "object_generation": before.generation,
        },
    }
    with (
        patch("tgw.queue.worker_base.state_machine") as state_machine,
        patch("tgw.notify.notify"),
    ):
        worker._process(job)
    state_machine.mark_dead_letter.assert_called_once()
    state_machine.mark_succeeded.assert_not_called()


def test_satisfied_local_coding_job_completes_without_item_evaluation_queue(tmp_path):
    _git_worktree(tmp_path)
    before = build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("implemented",)))
    worker = _worker(
        "codex-implement",
        tmp_path.parent,
        lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["implemented"],
            "artifacts": [],
        },
        tmp_path,
    )
    job = {
        "job_id": "satisfied-local-coding",
        "lease_token": "11111111-1111-4111-8111-111111111111",
        "payload_json": {
            "treatment_id": "codex-implement",
            "graph_id": "local-graph",
            "worktree": str(tmp_path),
            "object_id": str(tmp_path.resolve()),
            "object_generation": before.generation,
        },
    }
    with patch("tgw.queue.worker_base.state_machine") as state_machine:
        worker._process(job)
    state_machine.mark_succeeded.assert_called_once()
    state_machine.complete_treatment_and_enqueue_evaluation.assert_not_called()


def test_malformed_launcher_outcome_fails_closed(tmp_path):
    _git_worktree(tmp_path)
    worker = _worker("claude-review", tmp_path.parent, lambda *_args: {}, tmp_path)
    with pytest.raises(HardFailure, match="invalid outcome"):
        worker.handle({"payload_json": {"treatment_id": "claude-review", "graph_id": "g", "worktree": str(tmp_path), "object_id": str(tmp_path.resolve()), "object_generation": "gen"}})


def test_load_config_normalizes_coding_commands_for_worker(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"coding": {"commands": {"claude-review": ["echo", "ok"]}}}))
    config = load_config(config_path)
    assert config["coding"]["commands"]["claude-review"] == ["echo", "ok"]
    assert CodingWorker("claude-review", config)._configured_command("claude-review") == ["echo", "ok"]


def test_controller_verify_runner_emits_attested_success_only_after_pytest_and_ruff(monkeypatch, capsys):
    """The local runner establishes its full authority only after both checks pass."""
    from tgw.workers import controller_verify

    calls = []
    monkeypatch.setenv("PYTHONPATH", "/immutable/worker/release/src")
    monkeypatch.setenv("TGW_CODING_WORKTREE_SRC", "/claimed/worktree/src")
    checks = (
        ("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_feature.py"]),
        ("ruff", [sys.executable, "-m", "ruff", "check", "--no-cache", "src/feature.py", "tests/test_feature.py"]),
    )
    monkeypatch.setattr(controller_verify, "_verification_commands", lambda: checks)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="passed\n", stderr="")

    monkeypatch.setattr(controller_verify.subprocess, "run", run)

    assert controller_verify.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "outcome": "satisfied",
        "established_conditions": ["tested", "linted", "controller_verified"],
        "artifacts": [
            {"kind": "check", "name": "pytest", "status": "passed", "targets": ["tests/test_feature.py"]},
            {
                "kind": "check",
                "name": "ruff",
                "status": "passed",
                "targets": ["src/feature.py", "tests/test_feature.py"],
            },
        ],
    }
    assert [command for command, _kwargs in calls] == [item[1] for item in checks]
    assert all(kwargs["env"]["PYTHONPATH"] == "/claimed/worktree/src:/immutable/worker/release/src" for _command, kwargs in calls)


def test_controller_verify_runner_does_not_establish_conditions_when_a_check_fails(monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.setattr(
        controller_verify,
        "_verification_commands",
        lambda: (("pytest", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_feature.py"]),),
    )
    monkeypatch.setattr(
        controller_verify.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, stdout="failed\n", stderr=""),
    )

    assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    assert result["established_conditions"] == []
    assert result["artifacts"] == [
        {
            "kind": "check",
            "name": "pytest",
            "status": "failed",
            "targets": ["tests/test_feature.py"],
            "detail": "failed\n",
        }
    ]


def test_controller_verify_scope_is_bound_to_changed_source_and_tests(
    tmp_path,
    monkeypatch,
    capsys,
):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src/feature.py").write_text("implemented = True\n")
    (worktree / "tests/test_feature.py").write_text("def test_feature():\n    assert True\n")
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps(
            {
                "plan_binding": {"source_commit": baseline},
            }
        ),
    )

    python_files, tests = controller_verify._source_bound_python_files()

    assert python_files == ("src/feature.py", "tests/test_feature.py")
    assert tests == ("tests/test_feature.py",)
    assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == [
        "tested",
        "linted",
        "controller_verified",
    ]


def test_configured_worker_launches_candidate_bytes_not_runtime_release(tmp_path, monkeypatch):
    worker = CodingWorker(
        "controller-verify",
        {
            "coding": {
                "commands": {"controller-verify": ["/bin/true"]},
                "allowed_runners": ["/bin/true"],
            }
        },
    )
    observed = {}

    def run(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "outcome": "satisfied",
                    "established_conditions": ["controller_verified"],
                    "artifacts": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("tgw.workers.coding._run_bounded_process_group", run)
    worker._launch_configured_command("controller-verify", {}, tmp_path)

    assert observed["env"]["TGW_CODING_WORKTREE_SRC"] == str(tmp_path / "src")


def test_coding_worker_entrypoint_loads_config_file_and_starts_allowed_local_runner(tmp_path, monkeypatch):
    """The installed queue entrypoint consumes the coding-worker config contract."""
    from tgw.workers import coding

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "coding": {
                    "commands": {"claude-review": ["local-runner", "review"]},
                    "allowed_runners": ["local-runner"],
                }
            }
        )
    )
    run = MagicMock()
    monkeypatch.setattr(coding.CodingWorker, "run", run)
    monkeypatch.setattr(
        "sys.argv",
        ["tgw-coding-worker", "--queue", "claude-review", "--config", str(config_path)],
    )

    assert coding.main() == 0
    run.assert_called_once()


def test_invalid_local_runner_writes_mechanical_failure_receipt(tmp_path):
    _git_worktree(tmp_path)
    before = build_coding_snapshot(tmp_path, GoalProfile("test", "1", ("reviewed",)))
    worker = CodingWorker(
        "claude-review",
        {
            "coding": {
                "worktree_root": str(tmp_path.parent),
                "repository_root": str(tmp_path),
                "commands": {"claude-review": ["ssh", "host", "review"]},
            }
        },
    )
    with pytest.raises(HardFailure, match="mechanical failure"):
        worker.handle(
            {
                "payload_json": {
                    "treatment_id": "claude-review",
                    "graph_id": "g",
                    "worktree": str(tmp_path),
                    "object_id": str(tmp_path.resolve()),
                    "object_generation": before.generation,
                }
            }
        )
    receipt = json.loads(receipt_path_for_treatment(tmp_path, "claude-review").read_text())
    assert receipt["outcome"] == "failed"
    assert receipt["artifacts"][0]["kind"] == "mechanical_failure"


@pytest.mark.parametrize("worktree,object_id", [("/tmp", "/tmp"), ("worktree", "other")])
def test_worker_rejects_unsafe_or_mismatched_worktree(tmp_path, worktree, object_id):
    _git_worktree(tmp_path / "worktree")
    worker = _worker("claude-review", tmp_path, lambda *_args: {})
    actual = str(tmp_path / worktree) if worktree == "worktree" else worktree
    actual_object = str(tmp_path / object_id) if object_id == "other" else object_id
    with pytest.raises(HardFailure):
        worker.handle({"payload_json": {"treatment_id": "claude-review", "graph_id": "g", "object_generation": "gen", "worktree": actual, "object_id": actual_object}})


def test_worker_rejects_symlink_escape_and_non_git_worktree(tmp_path):
    outside = tmp_path.parent / "outside-coding-worker"
    outside.mkdir(exist_ok=True)
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    worker = _worker("claude-review", tmp_path, lambda *_args: {})
    for worktree in (tmp_path / "escape", tmp_path / "plain"):
        if worktree.name == "plain":
            worktree.mkdir()
        with pytest.raises(HardFailure):
            worker.handle({"payload_json": {"treatment_id": "claude-review", "graph_id": "g", "object_generation": "gen", "worktree": str(worktree), "object_id": str(worktree)}})


def test_worker_rejects_nested_and_unrelated_git_worktrees(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    intended = root / "intended"
    _git_worktree(intended)
    nested = intended / "nested"
    nested.mkdir()
    unrelated = root / "unrelated"
    _git_worktree(unrelated)
    worker = _worker(
        "claude-review",
        root,
        lambda *_args: {
            "outcome": "satisfied",
            "established_conditions": ["reviewed"],
        },
    )
    worker.config["coding"]["repository_root"] = str(intended)
    for worktree in (nested, unrelated):
        with pytest.raises(HardFailure):
            worker.handle({"payload_json": {"treatment_id": "claude-review", "graph_id": "g", "object_generation": "gen", "worktree": str(worktree), "object_id": str(worktree)}})
