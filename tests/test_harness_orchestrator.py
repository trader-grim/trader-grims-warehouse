"""The continual-harness orchestrator loop — Todo 1916 leaf 11.1.

Proves the loop drives a task to a mechanical completion (tests pass + the
change fast-forwards main), records every round to the ledger, and resumes
from the ledger after a mid-loop crash. DB-gated like the ledger tests.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

import psycopg2
import pytest

from tgw.development import harness_ledger, harness_orchestrator
from tgw.development.harness_orchestrator import Runners

_CANDIDATE_DSNS = (
    os.environ.get("TGW_TEST_STATE_MACHINE_DSN"),
    "dbname=tgw_lib_dev_state_machine user=tgw_coding",
    "dbname=state_machine_test user=tgw",
)


def _reachable_dsn() -> str | None:
    for dsn in _CANDIDATE_DSNS:
        if not dsn:
            continue
        try:
            with psycopg2.connect(dsn) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            return dsn
        except Exception:
            continue
    return None


DSN = _reachable_dsn()
pytestmark = pytest.mark.skipif(DSN is None, reason="no PostgreSQL reachable for the harness ledger")


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    ).stdout.strip()


@pytest.fixture
def env(tmp_path):
    harness_ledger.init(DSN)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "seed").write_text("0\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")

    task_id = f"test-orch-{uuid.uuid4()}"
    created: list[str] = [task_id]

    def prepare_worktree(tid: str, base_commit: str):
        wt = tmp_path / f"wt-{tid[-8:]}"
        _git(repo, "worktree", "add", "-q", "-b", f"coding/{tid}", str(wt), base_commit)
        return wt

    yield {"repo": repo, "task_id": task_id, "prepare_worktree": prepare_worktree}

    with psycopg2.connect(DSN) as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM harness_ledger_task WHERE task_id = ANY(%s)", (created,))


def _impl_writes(text):
    def implement(task_id, worktree, *, round, prior_findings):
        (worktree / "impl.py").write_text(text.format(round=round))
        return {"outcome": "ok", "summary": f"wrote impl round {round}"}
    return implement


def _run(env, **overrides):
    runners = Runners(
        prepare_worktree=env["prepare_worktree"],
        implement=overrides.get("implement", _impl_writes("v = {round}\n")),
        run_tests=overrides.get("run_tests", lambda t, w: {"passed": True, "detail": ""}),
        review=overrides.get("review", lambda t, w: {"findings": []}),
    )
    return harness_orchestrator.run_task(
        env["task_id"], repository=env["repo"], runners=runners,
        commit_message=overrides.get("message", f"{env['task_id']}: done"),
        max_rounds=overrides.get("max_rounds", 3),
        owner=overrides.get("owner", "harness"),
        lease_seconds=120,
    )


# --------------------------------------------------------------------------- #

def test_happy_path_lands_in_one_round(env):
    result = _run(env)
    assert result["outcome"] == "landed"
    assert result["rounds"] == 1
    assert _git(env["repo"], "rev-parse", "main") == result["commit"]
    assert _git(env["repo"], "log", "--oneline", "--format=%s").splitlines() == [
        f"{env['task_id']}: done", "initial",
    ]
    assert _git(env["repo"], "branch", "--list", "coding/*") == ""

    story = [e["kind"] for e in harness_ledger.history(env["task_id"])]
    assert story == ["attempt", "attempt", "next_action"]  # implement, test, land
    assert harness_ledger.read_task(env["task_id"])["status"] == "done"


def test_tests_fail_then_pass_lands_in_round_two(env):
    calls = {"n": 0}

    def run_tests(task_id, worktree):
        calls["n"] += 1
        return {"passed": calls["n"] >= 2, "detail": "boom" if calls["n"] < 2 else ""}

    result = _run(env, run_tests=run_tests)
    assert result["outcome"] == "landed"
    assert result["rounds"] == 2

    kinds = [e["kind"] for e in harness_ledger.history(env["task_id"])]
    assert "failed_approach" in kinds
    assert kinds[-1] == "next_action"


def test_blocking_findings_exhaust_budget_then_handoff(env):
    def review(task_id, worktree):
        return {"findings": [{"message": "still wrong", "blocking": True}]}

    result = _run(env, review=review, max_rounds=2)
    assert result["outcome"] == "blocked"
    assert result["rounds"] == 2
    assert "still wrong" in str(result["supervisor_handoff"]["blocking_findings"])

    task = harness_ledger.read_task(env["task_id"])
    assert task["status"] == "blocked"
    # worktree preserved for a supervised look; main untouched
    assert (env["repo"].parent / f"wt-{env['task_id'][-8:]}").exists()
    assert len(_git(env["repo"], "log", "--oneline").splitlines()) == 1
    assert [e["kind"] for e in harness_ledger.history(env["task_id"])][-1] == "next_action"


def test_resume_after_crash_continues_and_lands(env):
    boom = {"raise": True}

    def run_tests(task_id, worktree):
        if boom["raise"]:
            boom["raise"] = False
            raise RuntimeError("session died mid-test")
        return {"passed": True, "detail": ""}

    with pytest.raises(RuntimeError):
        _run(env, run_tests=run_tests)

    # the cursor is held by the crashed owner with a live lease; force-expire it
    # the way a real supervisor would after the heartbeat window.
    with psycopg2.connect(DSN) as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE harness_ledger_task SET lease_expires_at = now() - interval '1 s' "
                "WHERE task_id = %s", (env["task_id"],),
            )

    result = _run(env, run_tests=run_tests)
    assert result["outcome"] == "landed"
    assert _git(env["repo"], "rev-parse", "main") == result["commit"]
    # exactly one commit despite the crash + resume
    assert len(_git(env["repo"], "log", "--oneline").splitlines()) == 2


def test_already_satisfied_when_no_net_change(env):
    def implement(task_id, worktree, *, round, prior_findings):
        return {"outcome": "ok", "summary": "deliverable already present"}

    result = _run(env, implement=implement)
    assert result["outcome"] == "already_satisfied"
    assert len(_git(env["repo"], "log", "--oneline").splitlines()) == 1  # main untouched
    assert _git(env["repo"], "branch", "--list", "coding/*") == ""
    assert harness_ledger.read_task(env["task_id"])["status"] == "done"


def test_busy_cursor_refuses(env):
    harness_ledger.ensure_task(env["task_id"])
    harness_ledger.acquire_cursor(env["task_id"], "someone-else", lease_seconds=120)
    with pytest.raises(harness_orchestrator.OrchestratorBusy):
        _run(env)


def test_rerun_after_done_is_idempotent(env):
    first = _run(env)
    assert first["outcome"] == "landed"
    again = _run(env)
    assert again["outcome"] == "already_done"
    assert again["commit"] == first["commit"]


def test_build_runners_stack_lands_end_to_end(env, tmp_path):
    """The full production stack minus the LLM: build_runners wires
    git_worktree_prepare + pytest_gate + external_session, driven by fake
    implement/review scripts, through run_task to a landed commit."""
    from tgw.development import harness_runners
    from tgw.development.harness_runners import build_runners

    impl = tmp_path / "fake-impl.py"
    impl.write_text(
        "import json, os, pathlib\n"
        "job = json.loads(os.environ['TGW_CODING_JOB'])\n"
        "wt = pathlib.Path(job['worktree'])\n"
        "(wt / 'src').mkdir(exist_ok=True)\n"
        "(wt / 'src' / 'feature.py').write_text('VALUE = 1\\n')\n"
        "(wt / 'tests').mkdir(exist_ok=True)\n"
        "(wt / 'tests' / 'test_feature.py').write_text('from feature import VALUE\\n\\ndef test_v():\\n    assert VALUE == 1\\n')\n"
        "(wt / 'implementation-receipt.json').write_text(json.dumps({'outcome': 'satisfied', 'artifacts': [{'kind': 'summary', 'detail': 'added feature'}]}))\n"
    )
    review = tmp_path / "fake-review.py"
    review.write_text(
        "import json, os, pathlib\n"
        "job = json.loads(os.environ['TGW_CODING_JOB'])\n"
        "pathlib.Path(job['worktree'], 'review-receipt.json').write_text(json.dumps({'verdict': 'pass', 'findings': []}))\n"
    )

    runners = build_runners(
        env["repo"], tmp_path / "wts", task_body="add the feature",
        python=sys.executable,
        implement_argv=(sys.executable, str(impl)),
        review_argv=(sys.executable, str(review)),
    )
    # pytest_gate runs ruff too; the fake source is clean but skip ruff for speed
    runners = runners.__class__(
        prepare_worktree=runners.prepare_worktree,
        implement=runners.implement,
        run_tests=harness_runners.pytest_gate(python=sys.executable, run_ruff=False),
        review=runners.review,
    )

    result = harness_orchestrator.run_task(
        env["task_id"], repository=env["repo"], runners=runners,
        commit_message=f"{env['task_id']}: add the feature", lease_seconds=120,
    )
    assert result["outcome"] == "landed"
    assert _git(env["repo"], "show", "main:src/feature.py").strip() == "VALUE = 1"
    assert _git(env["repo"], "branch", "--list", "coding/*") == ""
    assert [e["kind"] for e in harness_ledger.history(env["task_id"])][-1] == "next_action"


def test_stub_executor_lands_offline_end_to_end(env, tmp_path):
    """LEAF-11-9 W0: the offline bootstrap canary. build_runners wires the REAL
    harness_session (no fake script), executor 'stub' — no binary, no network,
    no credential — and the orchestrator lands one commit through the real
    harness_git + ledger path."""
    from tgw.development import harness_runners
    from tgw.development.harness_runners import build_runners

    runners = build_runners(
        env["repo"], tmp_path / "wts", task_body="prove the pipe",
        python=sys.executable, coder_user=None,
        executor_preference=("stub",),
    )
    runners = runners.__class__(
        prepare_worktree=runners.prepare_worktree,
        implement=runners.implement,
        run_tests=harness_runners.pytest_gate(python=sys.executable, run_ruff=False),
        review=runners.review,
    )

    result = harness_orchestrator.run_task(
        env["task_id"], repository=env["repo"], runners=runners,
        commit_message=f"{env['task_id']}: offline canary", lease_seconds=120,
    )
    assert result["outcome"] == "landed"
    assert result["rounds"] == 1
    assert _git(env["repo"], "show", "main:.tgw-canary").startswith("harness offline canary")
    assert _git(env["repo"], "branch", "--list", "coding/*") == ""
