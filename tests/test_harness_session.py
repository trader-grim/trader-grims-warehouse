"""Apparatus-free implement / review session runners — Todo 1916 leaf 11.1.

No real LLM: the executor is forced and the subprocess is a fake ``invoke``
returning canned model output.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tgw.development import harness_session


@pytest.fixture(autouse=True)
def _force_claude(monkeypatch):
    monkeypatch.setenv("TGW_HARNESS_EXECUTOR", "claude")
    monkeypatch.setattr(harness_session, "_claude_binary", lambda: "/usr/bin/true")


def _claude_out(report: dict) -> str:
    # Claude -p --output-format json emits JSONL; the final report is the last
    # JSON object inside the result text.
    return json.dumps({"type": "result", "result": f"done.\n{json.dumps(report)}"}) + "\n"


def _fake_invoke(stdout: str, returncode: int = 0):
    def invoke(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    return invoke


# --------------------------------------------------------------------------- #
# prompts
# --------------------------------------------------------------------------- #

def test_implement_prompt_carries_body_and_prior_findings():
    p = harness_session.implement_prompt(
        {"task_id": "t1", "body": "add a flag", "round": 2,
         "prior_findings": [{"message": "missed the edge case"}]}
    )
    assert "add a flag" in p
    assert "round 2" in p
    assert "missed the edge case" in p
    assert "Do NOT commit" in p


def test_review_prompt_is_non_admitting():
    p = harness_session.review_prompt({"task_id": "t1", "body": "add a flag"})
    assert "non-admitting" in p
    assert "add a flag" in p


# --------------------------------------------------------------------------- #
# implement
# --------------------------------------------------------------------------- #

def test_implement_session_maps_implemented(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(_claude_out({"status": "implemented", "summary": "did X", "tests": ["test_x"]})),
    )
    assert out["outcome"] == "satisfied"
    assert out["artifacts"][0]["detail"] == "did X"
    assert out["tests_reported"] == ["test_x"]


def test_implement_session_maps_blocked(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(_claude_out({"status": "blocked", "summary": "needs prod access"})),
    )
    assert out["outcome"] == "blocked"


def test_implement_session_unparseable_report_is_failed(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(json.dumps({"type": "result", "result": "I could not finish."}) + "\n"),
    )
    assert out["outcome"] == "failed"


def test_implement_session_raises_on_nonzero_exit(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    with pytest.raises(harness_session.SessionError):
        harness_session.run_implement_session(job, invoke=_fake_invoke("", returncode=1))


# --------------------------------------------------------------------------- #
# review
# --------------------------------------------------------------------------- #

def test_review_session_maps_findings(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_review_session(
        job,
        invoke=_fake_invoke(_claude_out({
            "verdict": "findings",
            "findings": [
                {"message": "off by one", "blocking": True},
                {"message": "rename this", "blocking": False},
            ],
        })),
    )
    assert out["verdict"] == "FINDINGS"
    assert out["findings"] == [
        {"message": "off by one", "blocking": True},
        {"message": "rename this", "blocking": False},
    ]


def test_review_session_pass_with_no_findings(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_review_session(
        job, invoke=_fake_invoke(_claude_out({"verdict": "pass", "findings": []})),
    )
    assert out == {"verdict": "PASS", "findings": []}


def test_review_session_unparseable_is_non_blocking_finding(tmp_path):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_review_session(
        job, invoke=_fake_invoke(json.dumps({"type": "result", "result": "looks fine to me"}) + "\n"),
    )
    assert out["verdict"] == "FINDINGS"
    assert out["findings"][0]["blocking"] is False


# --------------------------------------------------------------------------- #
# console entrypoints
# --------------------------------------------------------------------------- #

def test_implement_main_writes_receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("TGW_CODING_JOB", json.dumps(
        {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    ))
    monkeypatch.setattr(
        harness_session, "run_implement_session",
        lambda job, **k: {"outcome": "satisfied", "artifacts": []},
    )
    assert harness_session.implement_main() == 0
    receipt = json.loads((tmp_path / "implementation-receipt.json").read_text())
    assert receipt["outcome"] == "satisfied"


def test_job_from_env_rejects_incomplete(monkeypatch):
    monkeypatch.setenv("TGW_CODING_JOB", json.dumps({"task_id": "t1"}))
    with pytest.raises(harness_session.SessionError):
        harness_session._job_from_env()
