"""Production runner factories for the orchestrator — Todo 1916 leaf 11.1.

No LLM here: the external session is proven with a fake runner script, the
test gate with a scratch worktree carrying green / red tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tgw.development import harness_runners
from tgw.development.harness_runners import (
    external_session,
    git_worktree_prepare,
    implement_outcome,
    pytest_gate,
    review_findings,
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@e", *args],
        cwd=cwd, check=True, text=True, capture_output=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
             "PATH": "/usr/bin:/bin", "HOME": str(cwd)},
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "src" / "m.py").write_text("def f():\n    return 1\n")
    (root / "tests" / "test_m.py").write_text("from m import f\n\ndef test_f():\n    assert f() == 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "initial")
    return root


# --------------------------------------------------------------------------- #
# prepare_worktree
# --------------------------------------------------------------------------- #

def test_git_worktree_prepare_creates_and_reuses(repo, tmp_path):
    prepare = git_worktree_prepare(repo, tmp_path / "wts", actor="harness")
    base = _git(repo, "rev-parse", "main")

    wt = prepare("todo-9", base)
    assert wt.exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD") == "coding/harness/todo-9"
    assert _git(wt, "rev-parse", "HEAD") == base

    # idempotent — same identity reused even after a round commit
    (wt / "x").write_text("1\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "round 1")
    again = prepare("todo-9", base)
    assert again == wt


# --------------------------------------------------------------------------- #
# pytest_gate — the mechanical completion gate
# --------------------------------------------------------------------------- #

def test_pytest_gate_passes_on_green_worktree(repo, tmp_path):
    prepare = git_worktree_prepare(repo, tmp_path / "wts")
    wt = prepare("todo-green", _git(repo, "rev-parse", "main"))
    (wt / "tests" / "test_new.py").write_text("def test_ok():\n    assert True\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "add test")

    gate = pytest_gate(python=sys.executable, run_ruff=False)
    result = gate("todo-green", wt)
    assert result["passed"] is True


def test_pytest_gate_fails_on_red_test(repo, tmp_path):
    prepare = git_worktree_prepare(repo, tmp_path / "wts")
    wt = prepare("todo-red", _git(repo, "rev-parse", "main"))
    (wt / "tests" / "test_bad.py").write_text("def test_bad():\n    assert False, 'nope'\n")
    _git(wt, "add", "-A")
    _git(wt, "commit", "-q", "-m", "add failing test")

    gate = pytest_gate(python=sys.executable, run_ruff=False)
    result = gate("todo-red", wt)
    assert result["passed"] is False
    assert "nope" in result["detail"] or "exit" in result["detail"]


# --------------------------------------------------------------------------- #
# external_session — implement / review via a configured runner
# --------------------------------------------------------------------------- #

def _fake_runner(tmp_path: Path, receipt_name: str, receipt_body: dict) -> Path:
    script = tmp_path / f"fake-{receipt_name}.py"
    script.write_text(
        "import json, os, pathlib, sys\n"
        "job = json.loads(os.environ['TGW_CODING_JOB'])\n"
        # the runner is launched with cwd = <worktree>/src; the receipt goes to
        # the worktree root, exactly like harness_session._emit does.
        "wt = pathlib.Path(os.environ['TGW_CODING_WORKTREE_SRC']).parent\n"
        f"(wt / {receipt_name!r}).write_text(json.dumps({json.dumps(receipt_body)}))\n"
        "(wt / '.job-seen.json').write_text(json.dumps(job))\n"
        "sys.exit(0)\n"
    )
    return script


def test_external_session_runs_runner_and_maps_receipt(repo, tmp_path):
    prepare = git_worktree_prepare(repo, tmp_path / "wts")
    wt = prepare("todo-impl", _git(repo, "rev-parse", "main"))

    script = _fake_runner(
        tmp_path, "implementation-receipt.json",
        {"outcome": "satisfied", "artifacts": [{"kind": "summary", "detail": "did the thing"}]},
    )

    def payload_builder(task_id, worktree, context):
        return {"todo_id": task_id, "round": context.get("round"), "body": "do X"}

    implement = external_session(
        runner_argv=(sys.executable, str(script)),
        receipt_name="implementation-receipt.json",
        payload_builder=payload_builder,
        map_receipt=implement_outcome,
    )
    result = implement("todo-impl", wt, round=1, prior_findings=[])
    assert result == {"outcome": "ok", "summary": "did the thing"}

    seen = json.loads((wt / ".job-seen.json").read_text())
    assert seen == {"todo_id": "todo-impl", "round": 1, "body": "do X"}


def test_external_session_raises_when_no_receipt(repo, tmp_path):
    prepare = git_worktree_prepare(repo, tmp_path / "wts")
    wt = prepare("todo-noreceipt", _git(repo, "rev-parse", "main"))
    script = tmp_path / "silent.py"
    script.write_text("import sys; sys.exit(3)\n")

    session = external_session(
        runner_argv=(sys.executable, str(script)),
        receipt_name="review-receipt.json",
        payload_builder=lambda t, w, c: {},
        map_receipt=review_findings,
    )
    with pytest.raises(harness_runners.RunnerError):
        session("todo-noreceipt", wt)


# --------------------------------------------------------------------------- #
# receipt mappers
# --------------------------------------------------------------------------- #

def test_implement_outcome_mapping():
    assert implement_outcome({"outcome": "satisfied"})["outcome"] == "ok"
    assert implement_outcome({"outcome": "resumable_partial"})["outcome"] == "partial"
    assert implement_outcome({"outcome": "blocked"})["outcome"] == "failed"
    assert implement_outcome({"outcome": "failed", "artifacts": [{"detail": "boom\nmore"}]}) == {
        "outcome": "failed", "summary": "boom",
    }


def test_session_argv_wraps_in_sudo_for_the_coder_user():
    argv = harness_runners._session_argv("/py", "implement", coder_user="tgw-coder")
    assert argv[:4] == ("sudo", "-n", "-u", "tgw-coder")
    assert argv[-2:] == ("tgw.development.harness_session", "implement")
    assert harness_runners._session_argv("/py", "review", coder_user=None) == (
        "/py", "-m", "tgw.development.harness_session", "review",
    )


def test_build_runners_uses_the_coder_user_and_writes_a_job_file(repo, tmp_path, monkeypatch):
    import subprocess as _sp

    from tgw.development.harness_runners import build_runners

    wt = git_worktree_prepare(repo, tmp_path / "wts")("todo-x", _git(repo, "rev-parse", "main"))
    (wt / "implementation-receipt.json").write_text('{"outcome": "satisfied", "artifacts": []}')

    seen = {}
    monkeypatch.setattr(_sp, "run", lambda argv, **kw: seen.update(argv=list(argv))
                        or _sp.CompletedProcess(argv, 0, "", ""))

    r = build_runners(repo, tmp_path / "wts", task_body="do it",
                      coder_user="tgw-coder", executor_preference=("claude", "codex"),
                      executor_bin={"claude": "/opt/x/claude", "codex": ""})
    r.implement("todo-x", wt, round=1, prior_findings=[])

    assert seen["argv"][:4] == ["sudo", "-n", "-u", "tgw-coder"]
    job = json.loads((wt / ".tgw-harness" / "job.json").read_text())
    assert job["executor_preference"] == ["claude", "codex"] and job["body"] == "do it"
    assert job["executor_bin"] == {"claude": "/opt/x/claude"}  # empty paths dropped


def test_review_findings_mapping():
    got = review_findings({
        "verdict": "FAIL",
        "findings": [
            {"message": "off-by-one", "severity": "high"},
            {"message": "style nit", "blocking": False},
            "bare string finding",
        ],
    })
    assert got["findings"][0] == {"message": "off-by-one", "blocking": True}
    assert got["findings"][1] == {"message": "style nit", "blocking": False}
    assert got["findings"][2]["blocking"] is True  # verdict FAIL
    assert review_findings({"verdict": "PASS", "findings": []}) == {"findings": [], "verdict": "PASS"}
