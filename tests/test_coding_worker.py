"""Executable routing and receipt tests for coding treatments."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import nullcontext
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


def _install_controller_lineage(
    worktree: Path, baseline: str, head: str, tree: str,
) -> dict:
    from tgw.development.partial_resume import append_attempt, candidate_changed_paths, make_attempt

    plan_binding = {
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": baseline,
        "source_tree": subprocess.run(
            ["git", "rev-parse", f"{baseline}^{{tree}}"], cwd=worktree,
            check=True, text=True, capture_output=True,
        ).stdout.strip(),
        "worktree": str(worktree.resolve()),
    }
    closed = {
        "kind": "closed_candidate", "commit": head, "tree": tree,
        "base_commit": baseline,
        "changed_paths": candidate_changed_paths(worktree, baseline, head),
    }
    attempt = make_attempt({
        "job_id": "job", "attempt_count": 1, "todo_id": 1798,
        **plan_binding, "actor": "codex", "worktree": str(worktree.resolve()),
        "treatment_id": "codex-implement", "treatment_version": "1",
    }, worktree, outcome="satisfied", artifacts=[closed])
    append_attempt(worktree, attempt)
    (worktree / "implementation-receipt.json").write_text(json.dumps({
        "status": "PASS", "outcome": "satisfied",
        "treatment_id": "codex-implement", "object_id": str(worktree.resolve()),
        "plan_binding": plan_binding, "artifacts": [closed],
    }))
    return {
        "todo_id": 1798, "todo_agent": "codex", "plan_binding": plan_binding,
        "implementation_attempt_hash": attempt["attempt_hash"],
        "object_generation": __import__("hashlib").sha256(
            f"{head}|{tree}".encode()
        ).hexdigest()[:16],
    }


@pytest.mark.parametrize(
    "field,wrong",
    [
        ("plan_commit", "f" * 40),
        ("solution_hash", "sha256:" + "f" * 64),
        ("todo_id", 9999),
        ("source_commit", "f" * 40),
        ("source_tree", "f" * 40),
        ("actor", "legacy"),
        ("worktree", "/tmp/substituted"),
        ("treatment_id", "legacy-implement"),
        ("treatment_version", "0"),
    ],
)
def test_canonical_lineage_rejects_every_expected_binding_substitution(tmp_path, field, wrong):
    from tgw.development.partial_resume import validate_implementation_lineage

    _git_worktree(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (tmp_path / "feature.py").write_text("implemented = True\n")
    subprocess.run(["git", "add", "feature.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=tmp_path, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    job = _install_controller_lineage(tmp_path, baseline, head, tree)
    receipt = json.loads((tmp_path / "implementation-receipt.json").read_text())
    expected = {
        "todo_id": job["todo_id"], **job["plan_binding"], "actor": "codex",
        "worktree": str(tmp_path.resolve()), "treatment_id": "codex-implement",
        "treatment_version": "1",
    }
    expected[field] = wrong
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_implementation_lineage(
            tmp_path, base_commit=baseline, candidate_commit=head,
            candidate_tree=tree, receipt=receipt, expected=expected,
        )


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
    state_machine.close_local_success.assert_called_once()
    state_machine.complete_treatment_and_enqueue_evaluation.assert_not_called()


def test_success_publication_failure_removes_attempt_and_receipt(monkeypatch, tmp_path):
    worker = object.__new__(CodingWorker)
    receipt = {"outcome": "satisfied"}
    receipt_path = tmp_path / "implementation-receipt.json"
    attempt_path = tmp_path / ".tgw-coding-history" / "implementation" / "000001-test.json"
    worker._pending_success_receipt = ("job-1", receipt_path, receipt)
    worker._pending_success_attempt = (tmp_path, {}, None, [])

    def append(*_args):
        attempt_path.parent.mkdir(parents=True)
        attempt_path.write_text("attempt")
        return attempt_path

    monkeypatch.setattr(
        "tgw.workers.coding.make_attempt",
        lambda *_a, **_k: {"attempt_hash": "sha256:test"},
    )
    monkeypatch.setattr("tgw.workers.coding.history", lambda *_a, **_k: [])
    monkeypatch.setattr("tgw.workers.coding.append_attempt", append)
    monkeypatch.setattr(
        "tgw.workers.coding._write_receipt",
        lambda *_a: (_ for _ in ()).throw(OSError("publication failed")),
    )
    registered = []
    with pytest.raises(OSError, match="publication failed"):
        worker._on_direct_local_success(
            {"job_id": "job-1"}, receipt, registered.append,
        )
    assert len(registered) == 1
    registered[0]()
    assert not attempt_path.exists()
    assert not receipt_path.exists()


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
    monkeypatch.delenv("TGW_CODING_JOB", raising=False)
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


def test_controller_git_probe_trusts_only_the_exact_worktree(tmp_path, monkeypatch):
    from tgw.workers import controller_verify

    observed = {}

    def run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="head\n", stderr="")

    monkeypatch.setattr(controller_verify.subprocess, "run", run)

    assert controller_verify._git_text(tmp_path, "rev-parse", "HEAD") == "head"
    assert observed["command"] == [
        "git",
        "-c",
        f"safe.directory={tmp_path.resolve()}",
        "rev-parse",
        "HEAD",
    ]
    assert observed["cwd"] == tmp_path


def test_controller_lineage_fails_before_checks_for_missing_or_contradictory_evidence(
    tmp_path, monkeypatch,
):
    from tgw.workers import controller_verify

    job = {
        "todo_id": 1798,
        "plan_binding": {
            "plan_commit": "a" * 40,
            "solution_hash": "sha256:" + "b" * 64,
            "source_commit": "c" * 40,
            "source_tree": "d" * 40,
        },
    }
    with pytest.raises(controller_verify.ControllerVerificationError, match="lineage is absent"):
        controller_verify._assert_implementation_lineage(
            tmp_path, job, "c" * 40, "e" * 40, "f" * 40,
        )

    (tmp_path / "implementation-receipt.json").write_text(json.dumps({
        "plan_binding": {**job["plan_binding"], "solution_hash": "substituted"},
    }))
    monkeypatch.setattr(
        "tgw.development.partial_resume.validate_implementation_lineage",
        lambda *_args, **_kwargs: {
            "todo_id": 1798, "plan_commit": "a" * 40,
            "solution_hash": "sha256:" + "b" * 64,
            "source_commit": "c" * 40, "source_tree": "d" * 40,
            "worktree": str(tmp_path.resolve()),
        },
    )
    monkeypatch.setattr(controller_verify, "_git_text", lambda *_args: "d" * 40)
    with pytest.raises(controller_verify.ControllerVerificationError, match="does not match"):
        controller_verify._assert_implementation_lineage(
            tmp_path.resolve(), job, "c" * 40, "e" * 40, "f" * 40,
        )


@pytest.mark.parametrize("replacement", [None, "sha256:" + "f" * 64])
def test_controller_requires_current_implementation_attempt_hash(tmp_path, replacement):
    from tgw.workers import controller_verify

    _git_worktree(tmp_path)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (tmp_path / "feature.py").write_text("implemented = True\n")
    subprocess.run(["git", "add", "feature.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "candidate"], cwd=tmp_path, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=tmp_path, check=True, text=True, capture_output=True).stdout.strip()
    current = _install_controller_lineage(tmp_path, baseline, head, tree)

    controller_verify._assert_implementation_lineage(tmp_path.resolve(), current, baseline, head, tree)
    substituted = dict(current)
    if replacement is None:
        substituted.pop("implementation_attempt_hash")
    else:
        substituted["implementation_attempt_hash"] = replacement
    with pytest.raises(controller_verify.ControllerVerificationError, match="attempt hash"):
        controller_verify._assert_implementation_lineage(
            tmp_path.resolve(), substituted, baseline, head, tree,
        )


def test_controller_rejects_lineage_before_running_tests_or_lint(monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.setenv("TGW_CODING_JOB", "{}")
    monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", "41")
    monkeypatch.setattr(controller_verify, "inherited_worktree_lease", lambda *_args: nullcontext())
    monkeypatch.setattr(
        controller_verify, "_verification_commands",
        lambda: (_ for _ in ()).throw(
            controller_verify.ControllerVerificationError("exact implementation lineage is absent")
        ),
    )
    called = []
    monkeypatch.setattr(controller_verify, "_run_check", lambda *_args: called.append(True))

    assert controller_verify.main() == 0
    assert called == []
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    assert "lineage is absent" in result["artifacts"][0]["detail"]


def test_controller_uses_exact_inherited_worker_lease(tmp_path, monkeypatch, capsys):
    from tgw.development.worktree_lease import exclusive_worktree_lease
    from tgw.workers import controller_verify

    _git_worktree(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TGW_CODING_JOB", "{}")
    monkeypatch.setattr(controller_verify, "_verification_commands", lambda: ())
    monkeypatch.setattr(controller_verify, "_source_bound_candidate_files", lambda: ())

    with exclusive_worktree_lease(tmp_path) as descriptor:
        monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", str(descriptor))
        assert controller_verify.main() == 0

    assert json.loads(capsys.readouterr().out)["outcome"] == "satisfied"


def test_controller_fails_closed_for_untrusted_inherited_descriptor(monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.setenv("TGW_CODING_JOB", "{}")
    monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", "41")
    monkeypatch.setattr(
        controller_verify,
        "inherited_worktree_lease",
        lambda *_args: (_ for _ in ()).throw(
            HardFailure("lease metadata is not shared with this Unix actor")
        ),
    )
    monkeypatch.setattr(controller_verify, "_verification_commands", lambda: pytest.fail("checks ran"))

    assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    assert "not shared with this Unix actor" in result["artifacts"][0]["detail"]


def test_inherited_lease_uses_descriptor_bound_linux_lock_state(tmp_path):
    from tgw.development.worktree_lease import exclusive_worktree_lease, inherited_worktree_lease

    _git_worktree(tmp_path)
    with exclusive_worktree_lease(tmp_path) as descriptor:
        with inherited_worktree_lease(tmp_path, descriptor) as inherited:
            assert inherited == descriptor


def test_inherited_lease_survives_real_subprocess_fd_inheritance(tmp_path):
    from tgw.development.worktree_lease import exclusive_worktree_lease

    _git_worktree(tmp_path)
    script = (
        "import pathlib,sys; "
        "from tgw.development.worktree_lease import inherited_worktree_lease; "
        "w=pathlib.Path(sys.argv[1]); fd=int(sys.argv[2]); "
        "c=inherited_worktree_lease(w,fd); c.__enter__(); print('inherited')"
    )
    with exclusive_worktree_lease(tmp_path) as descriptor:
        completed = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), str(descriptor)],
            check=False, text=True, capture_output=True, pass_fds=(descriptor,),
        )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "inherited"


def test_inherited_lease_rejects_unlocked_descriptor_during_other_holder_release(tmp_path):
    """Reproduce the rejected probe/acquire TOCTOU without timing or polling."""
    from tgw.development.worktree_lease import inherited_worktree_lease

    _git_worktree(tmp_path)
    gitdir = tmp_path / ".git"
    unlocked = os.open(gitdir, os.O_RDONLY | os.O_DIRECTORY)
    holder = os.open(gitdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        import fcntl
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # This release is the exact rejected-candidate window between its
        # blocked probe and flock(unlocked), which would then acquire the lock.
        fcntl.flock(holder, fcntl.LOCK_UN)
        with pytest.raises(HardFailure, match="absent worktree lease state"):
            with inherited_worktree_lease(tmp_path, unlocked):
                pass
        probe = os.open(gitdir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            fcntl.flock(probe, fcntl.LOCK_UN)
            os.close(probe)
    finally:
        os.close(holder)
        os.close(unlocked)


@pytest.mark.parametrize(
    "record,detail",
    [
        ("", "ambiguous or absent"),
        (
            "lock:\t1: FLOCK ADVISORY WRITE 1 00:01:1 0 EOF\n"
            "lock:\t2: FLOCK ADVISORY WRITE 1 00:01:1 0 EOF\n",
            "ambiguous or absent",
        ),
        ("lock:\tbroken\n", "malformed"),
        ("lock:\t1: POSIX ADVISORY WRITE 1 00:01:1 0 EOF\n", "malformed"),
        ("lock:\t1: FLOCK ADVISORY READ 1 00:01:1 0 EOF\n", "malformed"),
        ("lock:\t1: FLOCK ADVISORY WRITE 1 00:01:1 1 EOF\n", "malformed"),
        ("lock:\t1: FLOCK ADVISORY WRITE 1 00:01:1 0 99\n", "malformed"),
        ("lock:\t1: FLOCK ADVISORY WRITE 1 00:01:1 0 EOF\n", "wrong inode"),
    ],
)
def test_inherited_lease_rejects_bad_descriptor_bound_lock_records(
    tmp_path, monkeypatch, record, detail,
):
    from tgw.development.worktree_lease import _validate_inherited_flock

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: record)
        with pytest.raises(HardFailure, match=detail):
            _validate_inherited_flock(descriptor)
    finally:
        os.close(descriptor)


def test_inherited_flock_accepts_namespace_remapped_device_identity(tmp_path, monkeypatch):
    from tgw.development.worktree_lease import _validate_inherited_flock

    descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        inode = os.fstat(descriptor).st_ino
        record = f"lock:\t1: FLOCK ADVISORY WRITE 1 dead:beef:{inode} 0 EOF\n"
        monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: record)
        _validate_inherited_flock(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("value", [None, "", "not-an-fd", "-1", "2147483648"])
def test_controller_fails_closed_for_invalid_inherited_lease_fd(value, monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.setenv("TGW_CODING_JOB", "{}")
    if value is None:
        monkeypatch.delenv("TGW_CODING_WORKTREE_LEASE_FD", raising=False)
    else:
        monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", value)
    monkeypatch.setattr(controller_verify, "_verification_commands", lambda: pytest.fail("checks ran"))
    assert controller_verify.main() == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "failed"


def test_controller_fails_closed_for_amplified_numeric_inherited_lease_fd(monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.setenv("TGW_CODING_JOB", "{}")
    monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", "9" * 5000)
    monkeypatch.setattr(controller_verify, "_verification_commands", lambda: pytest.fail("checks ran"))
    assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    assert "malformed lease descriptor" in result["artifacts"][0]["detail"]


def test_controller_verify_runner_does_not_establish_conditions_when_a_check_fails(monkeypatch, capsys):
    from tgw.workers import controller_verify

    monkeypatch.delenv("TGW_CODING_JOB", raising=False)

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


def test_controller_check_does_not_leak_outer_job_payload(monkeypatch):
    from tgw.workers import controller_verify

    observed = {}
    monkeypatch.setenv("TGW_CODING_JOB", '{"todo_id": 1735}')

    def run(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="passed\n", stderr="")

    monkeypatch.setattr(controller_verify.subprocess, "run", run)

    result = controller_verify._run_check("pytest", [sys.executable, "-m", "pytest"])

    assert result["status"] == "passed"
    assert "TGW_CODING_JOB" not in observed["env"]


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
    subprocess.run(["git", "add", "src/feature.py", "tests/test_feature.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "closed successor"], cwd=worktree, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps(_install_controller_lineage(worktree, baseline, head, tree)),
    )

    python_files, tests = controller_verify._source_bound_python_files()

    assert python_files == ("src/feature.py", "tests/test_feature.py")
    assert tests == ("tests/test_feature.py",)
    from tgw.development.worktree_lease import exclusive_worktree_lease
    with exclusive_worktree_lease(worktree) as descriptor:
        monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", str(descriptor))
        assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["artifacts"][0] == {
        "kind": "check",
        "name": "candidate-diff",
        "status": "passed",
        "targets": ["src/feature.py", "tests/test_feature.py"],
    }
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == [
        "tested",
        "linted",
        "controller_verified",
    ]


def test_controller_final_recheck_rejects_mutation_during_checks(
    tmp_path, monkeypatch, capsys
):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src/feature.py").write_text("implemented = True\n")
    (worktree / "tests/test_feature.py").write_text("def test_feature():\n    assert True\n")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "closed successor"], cwd=worktree, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps(_install_controller_lineage(worktree, baseline, head, tree)),
    )
    calls = 0

    def pass_then_mutate(name, command):
        nonlocal calls
        calls += 1
        if calls == 3:
            (worktree / "src/feature.py").write_text("implemented = False\n")
        return {"kind": "check", "name": name, "status": "passed", "targets": command[-1:]}

    monkeypatch.setattr(controller_verify, "_run_check", pass_then_mutate)

    from tgw.development.worktree_lease import exclusive_worktree_lease
    with exclusive_worktree_lease(worktree) as descriptor:
        monkeypatch.setenv("TGW_CODING_WORKTREE_LEASE_FD", str(descriptor))
        assert controller_verify.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "failed"
    assert "mutable or uncommitted" in result["artifacts"][0]["detail"]


def test_controller_diff_check_covers_non_python_candidate_paths(tmp_path, monkeypatch):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src/feature.py").write_text("implemented = True\n")
    (worktree / "tests/test_feature.py").write_text("def test_feature():\n    assert True\n")
    (worktree / "config.json").write_text('{"enabled": true}\n')
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "closed successor"], cwd=worktree, check=True, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, check=True, text=True, capture_output=True).stdout.strip()
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps(_install_controller_lineage(worktree, baseline, head, tree)),
    )

    commands = dict(controller_verify._verification_commands())

    assert commands["candidate-diff"][7:] == [
        "config.json", "src/feature.py", "tests/test_feature.py"
    ]


def test_controller_refuses_uncommitted_source_candidate(tmp_path, monkeypatch):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "feature.py").write_text("implemented = True\n")
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps({"plan_binding": {"source_commit": baseline}, "object_generation": "dirty"}),
    )

    with pytest.raises(controller_verify.ControllerVerificationError, match="no committed successor"):
        controller_verify._source_bound_python_files()


def test_controller_refuses_mutable_or_wrong_generation_candidate(tmp_path, monkeypatch):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src/feature.py").write_text("implemented = True\n")
    (worktree / "tests/test_feature.py").write_text("def test_feature():\n    assert True\n")
    subprocess.run(["git", "add", "src/feature.py", "tests/test_feature.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "closed successor"], cwd=worktree, check=True, capture_output=True)
    monkeypatch.chdir(worktree)

    (worktree / "src/feature.py").write_text("implemented = False\n")
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps({"plan_binding": {"source_commit": baseline}, "object_generation": "dirty"}),
    )
    with pytest.raises(controller_verify.ControllerVerificationError, match="mutable or uncommitted"):
        controller_verify._source_bound_python_files()

    subprocess.run(["git", "restore", "src/feature.py"], cwd=worktree, check=True)
    with pytest.raises(controller_verify.ControllerVerificationError, match="dispatched generation"):
        controller_verify._source_bound_python_files()


def test_controller_refuses_ignored_mutable_candidate_state(tmp_path, monkeypatch):
    from tgw.workers import controller_verify

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    (worktree / ".gitignore").write_text("ignored/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "baseline ignore"], cwd=worktree, check=True, capture_output=True)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "src.py").write_text("implemented = True\n")
    (worktree / "tests").mkdir()
    (worktree / "tests/test_feature.py").write_text("def test_feature():\n    assert True\n")
    subprocess.run(["git", "add", "src.py", "tests/test_feature.py"], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "closed successor"], cwd=worktree, check=True, capture_output=True)
    (worktree / "ignored").mkdir()
    (worktree / "ignored/value").write_text("mutable")
    monkeypatch.chdir(worktree)
    monkeypatch.setenv(
        "TGW_CODING_JOB",
        json.dumps({"plan_binding": {"source_commit": baseline}, "object_generation": "irrelevant"}),
    )

    with pytest.raises(controller_verify.ControllerVerificationError, match="mutable or uncommitted"):
        controller_verify._source_bound_python_files()


@pytest.mark.parametrize(
    "raw_status",
    [
        b"?? .tgw-coding-history/implementation/000001-"
        + (b"a" * 64)
        + b".json\0",
        b"?? implementation-receipt.json\0",
        b"!! controller-harness-receipt.json\0",
    ],
)
def test_controller_nul_status_accepts_only_owned_workflow_evidence(
    tmp_path, monkeypatch, raw_status
):
    from tgw.workers import controller_verify

    def run(command, **_kwargs):
        assert command[-4:] == [
            "--porcelain=v1", "-z", "--untracked-files=all", "--ignored=matching",
        ]
        return subprocess.CompletedProcess(command, 0, stdout=raw_status, stderr=b"")

    monkeypatch.setattr(controller_verify.subprocess, "run", run)
    controller_verify._assert_source_status_clean(tmp_path)


def test_controller_accepts_exact_preservation_manifest_without_deleting_it(tmp_path):
    from tgw.development.partial_resume import preservation_manifest, source_tree
    from tgw.workers import controller_verify

    _git_worktree(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    manifest = preservation_manifest(tmp_path, {"state": "UNSAFE_DIRTY"}, {
        "todo_id": 1792, "plan_commit": "b" * 40,
        "solution_hash": "sha256:" + "c" * 64, "source_commit": head,
        "source_tree": source_tree(tmp_path, head), "actor": "codex",
        "worktree": str(tmp_path.resolve()), "treatment_id": "codex-implement",
        "treatment_version": "1",
    })
    original = manifest.read_bytes()

    controller_verify._assert_source_status_clean(tmp_path)

    assert manifest.read_bytes() == original


@pytest.mark.parametrize(
    "raw_status",
    [
        b"?? .tgw-coding-history-evil/attempt.json\0",
        b"?? .tgw-coding-history\0",
        b"!! .tgw-coding-history/\0",
        b"?? .tgw-coding-history/controller/check.log\0",
        b"?? .tgw-coding-history/implementation/attempt.json\0",
        b"?? nested/implementation-receipt.json\0",
        b"?? implementation-receipt.json.bak\0",
        b"?? ../implementation-receipt.json\0",
        b"?? .tgw-coding-history/../src.py\0",
        b"?? .tgw-coding-history//attempt.json\0",
        b"A  src/staged.py\0",
        b"M  implementation-receipt.json\0",
        b" M controller-harness-receipt.json\0",
        b"!! ignored/value\0",
        b"R  .tgw-coding-history/new\0.tgw-coding-history/old\0",
        b"ZZ implementation-receipt.json\0",
        b"   implementation-receipt.json\0",
        b"malformed\0",
        b"?? implementation-receipt.json",
        b"?? implementation-receipt.json\0\0",
        b"?? .tgw-coding-history/implementation/000001-\xff.json\0",
    ],
)
def test_controller_nul_status_rejects_prefixes_renames_and_unsafe_paths(
    tmp_path, monkeypatch, raw_status
):
    from tgw.workers import controller_verify

    monkeypatch.setattr(
        controller_verify.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, stdout=raw_status, stderr=b""
        ),
    )

    with pytest.raises(controller_verify.ControllerVerificationError):
        controller_verify._assert_source_status_clean(tmp_path)


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


def test_configured_worker_passes_exact_worktree_lease_to_runner(tmp_path, monkeypatch):
    worker = CodingWorker(
        "codex-implement",
        {
            "coding": {
                "commands": {"codex-implement": ["/bin/true"]},
                "allowed_runners": ["/bin/true"],
            }
        },
    )
    worker._worktree_lease_fd = 37
    observed = {}

    def run(command, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "outcome": "partial",
                    "established_conditions": [],
                    "artifacts": [],
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("tgw.workers.coding._run_bounded_process_group", run)
    worker._launch_configured_command("codex-implement", {}, tmp_path)

    assert observed["pass_fds"] == (37,)
    assert observed["env"]["TGW_CODING_WORKTREE_LEASE_FD"] == "37"


def test_automatic_worker_controller_path_passes_exact_closed_candidate(tmp_path, monkeypatch):
    from tgw.development.worktree_lease import exclusive_worktree_lease
    from tgw.workers import coding

    monkeypatch.setattr(
        coding.grp,
        "getgrnam",
        lambda _name: type(
            "TestGroup", (), {"gr_gid": os.getegid(), "gr_mem": ["codex"]}
        )(),
    )

    worktree = tmp_path / "worktree"
    _git_worktree(worktree)
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    (worktree / "src").mkdir()
    (worktree / "tests").mkdir()
    (worktree / "src/feature.py").write_text("implemented = True\n", encoding="utf-8")
    (worktree / "tests/test_feature.py").write_text(
        "def test_feature():\n    assert True\n", encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(
        ["git", "commit", "-m", "exact closed candidate"], cwd=worktree,
        check=True, capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"], cwd=worktree, check=True,
        text=True, capture_output=True,
    ).stdout.strip()
    payload = _install_controller_lineage(worktree, baseline, head, tree)
    worker = CodingWorker(
        "controller-verify",
        {"coding": {
            "commands": {"controller-verify": [sys.executable, "-m", "tgw.workers.controller_verify"]},
            "allowed_runners": [sys.executable],
            "runner_state_root": str(tmp_path.parent / "runner-control"),
            "timeout_s": 60,
        }},
    )
    monkeypatch.setattr("tgw.workers.coding.worker_base.state_machine.get_job", lambda _job_id: None)

    with exclusive_worktree_lease(worktree) as descriptor:
        worker._worktree_lease_fd = descriptor
        result = worker._launch_configured_command("controller-verify", payload, worktree)

    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["tested", "linted", "controller_verified"]


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
