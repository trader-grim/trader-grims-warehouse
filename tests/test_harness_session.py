"""Apparatus-free implement / review session runners — Todo 1916 leaf 11.1.

No real LLM: the executor is forced and the subprocess is a fake ``invoke``
returning canned model output.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from tgw.development import harness_session


@pytest.fixture(autouse=True)
def _force_claude(monkeypatch):
    monkeypatch.setenv("TGW_HARNESS_EXECUTOR", "claude")
    monkeypatch.setattr(harness_session, "_claude_binary", lambda: "/usr/bin/true")


@pytest.fixture(autouse=True)
def _no_observations_by_default(monkeypatch):
    # hermetic by default: dispatch outcomes are not written to the durable
    # store unless a test enables recording and stubs the store.
    monkeypatch.setattr(harness_session, "OBSERVATIONS_ENABLED", False)


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


def test_implement_session_exhausted_chain_is_failed_not_raised(tmp_path, monkeypatch):
    # a nonzero exit with no credential looks like "executor unavailable" -> the
    # chain moves on; with the whole chain exhausted the task result is "failed",
    # never an unhandled raise.
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: None)
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(job, invoke=_fake_invoke("", returncode=1))
    assert out["outcome"] == "failed"
    assert out["artifacts"][0]["kind"] == "executor_chain_exhausted"


def test_implement_session_raises_on_a_real_session_failure(tmp_path, monkeypatch):
    # a nonzero exit that is NOT an availability wall, with a credential present,
    # is a genuine SessionError for that executor.
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: ("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(harness_session, "_executor_chain", lambda job=None: ["claude"])
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    with pytest.raises(harness_session.SessionError):
        harness_session.run_implement_session(
            job, invoke=_fake_invoke("boom: internal error", returncode=1))


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
        harness_session._load_job()


def test_load_job_falls_back_to_the_worktree_file(monkeypatch, tmp_path):
    monkeypatch.delenv("TGW_CODING_JOB", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".tgw-harness").mkdir()
    (tmp_path / ".tgw-harness" / "job.json").write_text(
        json.dumps({"task_id": "t9", "body": "b", "worktree": str(tmp_path), "executor": "codex"})
    )
    job = harness_session._load_job()
    assert job["task_id"] == "t9"
    assert harness_session._executor_chain(job) == ["codex"]


def test_executor_chain_prefers_the_job_preference(monkeypatch):
    monkeypatch.setenv("TGW_HARNESS_EXECUTOR", "claude")
    assert harness_session._executor_chain({"executor": "codex"}) == ["codex"]
    assert harness_session._executor_chain({"executor_preference": ["codex", "claude"]}) == ["codex", "claude"]


def test_claude_session_gets_only_its_own_credential(tmp_path, monkeypatch):
    # tgw.env sources every provider key into the process; the claude session
    # must see ONLY CLAUDE_CODE_OAUTH_TOKEN, none of the others.
    for k in ("DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(k, "leak-" + k)
    monkeypatch.setattr(harness_session, "_session_credential",
                        lambda e: ("CLAUDE_CODE_OAUTH_TOKEN", "the-token"))
    seen = {}

    def fake_invoke(cmd, **kw):
        seen["env"] = dict(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, _claude_out({"status": "implemented", "summary": "ok"}), "")

    harness_session.run_implement_session(
        {"task_id": "t", "body": "b", "worktree": str(tmp_path)}, invoke=fake_invoke)
    env = seen["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "the-token"
    for k in ("DEEPSEEK_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        assert k not in env
    assert "PATH" in env  # non-secret env survives


def test_claude_session_wires_the_context_mcp(tmp_path, monkeypatch):
    # the tgw-context MCP must reach the claude coder session: a --mcp-config
    # pointing at a file that names the launcher.
    monkeypatch.setattr(harness_session, "_context_mcp_available", lambda: True)
    monkeypatch.setattr(harness_session, "_session_credential",
                        lambda e: ("CLAUDE_CODE_OAUTH_TOKEN", "t"))
    seen = {}

    def fake_invoke(cmd, **kw):
        seen["cmd"] = list(cmd)
        i = cmd.index("--mcp-config")
        seen["mcp"] = json.loads(open(cmd[i + 1]).read())
        return subprocess.CompletedProcess(
            cmd, 0, _claude_out({"status": "implemented", "summary": "ok"}), "")

    harness_session.run_implement_session(
        {"task_id": "t", "body": "b", "worktree": str(tmp_path)}, invoke=fake_invoke)
    assert "--mcp-config" in seen["cmd"]
    assert seen["mcp"]["mcpServers"]["tgw-context"]["command"] == str(harness_session._CONTEXT_MCP)


def test_claude_session_has_no_mcp_flag_when_launcher_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_session, "_context_mcp_available", lambda: False)
    monkeypatch.setattr(harness_session, "_session_credential",
                        lambda e: ("CLAUDE_CODE_OAUTH_TOKEN", "t"))
    seen = {}

    def fake_invoke(cmd, **kw):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, _claude_out({"status": "implemented", "summary": "ok"}), "")

    harness_session.run_implement_session(
        {"task_id": "t", "body": "b", "worktree": str(tmp_path)}, invoke=fake_invoke)
    assert "--mcp-config" not in seen["cmd"]


def test_opencode_session_wires_the_context_mcp_and_is_not_pure(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_session, "_context_mcp_available", lambda: True)
    monkeypatch.setattr(harness_session, "_executor_binary", lambda n, optional=False: "/usr/bin/true")
    monkeypatch.setattr(harness_session, "_session_credential",
                        lambda e: ("OPENCODE_ZEN_API_KEY", "zk"))
    seen = {}

    def fake_invoke(cmd, **kw):
        seen["cmd"] = list(cmd)
        cfg = os.path.join(kw["env"]["HOME"], ".config", "opencode", "opencode.jsonc")
        seen["cfg"] = json.loads(open(cfg).read())
        return subprocess.CompletedProcess(
            cmd, 0, json.dumps({"type": "result", "content":
                                json.dumps({"status": "implemented", "summary": "ok"})}) + "\n", "")

    harness_session._run_opencode("do it", tmp_path, {"model": "opencode/x"}, invoke=fake_invoke)
    assert "--pure" not in seen["cmd"]
    assert seen["cfg"]["mcp"]["tgw-context"]["command"] == [str(harness_session._CONTEXT_MCP)]


def test_executor_bin_from_job_sets_the_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TGW_CLAUDE_BIN", raising=False)
    monkeypatch.delenv("TGW_CODEX_BIN", raising=False)
    monkeypatch.setattr(harness_session, "_session_credential",
                        lambda e: ("CLAUDE_CODE_OAUTH_TOKEN", "t"))

    def fake_invoke(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, _claude_out({"status": "implemented", "summary": "ok"}), "")

    harness_session.run_implement_session(
        {"task_id": "t", "body": "b", "worktree": str(tmp_path),
         "executor_bin": {"claude": "/opt/x/claude", "codex": "/opt/x/codex"}},
        invoke=fake_invoke,
    )
    assert os.environ["TGW_CLAUDE_BIN"] == "/opt/x/claude"
    assert os.environ["TGW_CODEX_BIN"] == "/opt/x/codex"


def test_executor_chain_default_is_the_full_list(monkeypatch):
    monkeypatch.delenv("TGW_HARNESS_EXECUTOR", raising=False)
    monkeypatch.setattr("tgw.model_selector.select_executor", lambda role: (_ for _ in ()).throw(Exception()))
    assert harness_session._executor_chain() == ["claude", "codex", "opencode"]


def test_stub_executor_is_offline_only_by_explicit_request(monkeypatch):
    # never in the default chain / a selector result; only when asked for
    monkeypatch.delenv("TGW_HARNESS_EXECUTOR", raising=False)
    monkeypatch.setattr("tgw.model_selector.select_executor",
                        lambda role: (_ for _ in ()).throw(Exception()))
    assert "stub" not in harness_session._executor_chain()
    assert harness_session._executor_chain({"executor_preference": ["stub"]}) == ["stub"]
    monkeypatch.setenv("TGW_HARNESS_EXECUTOR", "stub")
    assert harness_session._executor_chain() == ["stub"]


def test_executor_chain_default_is_built_from_the_catalogue(tmp_path, monkeypatch):
    # a catalogue with a different enabled set drives the default chain — no
    # hard-coded ("claude", "codex") list in harness_session.
    import json

    from tgw import coding_executor_catalog

    cat = tmp_path / "executors.json"
    cat.write_text(json.dumps({"executors": {
        "codex": {"binary_name": "codex", "install_source": None, "install_target_path": None,
                  "runtime_deps": [], "verify_cmd": None, "credential_env": ["CODEX_API_KEY"],
                  "auth_file": None, "enabled": True},
        "claude": {"binary_name": "claude", "install_source": None, "install_target_path": None,
                   "runtime_deps": [], "verify_cmd": None, "credential_env": ["ANTHROPIC_API_KEY"],
                   "auth_file": None, "enabled": False},
    }}))
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(cat))
    monkeypatch.delenv("TGW_HARNESS_EXECUTOR", raising=False)
    monkeypatch.setattr("tgw.model_selector.select_executor",
                        lambda role: (_ for _ in ()).throw(Exception()))
    # claude disabled in this catalogue -> only codex in the default chain
    assert harness_session._executor_chain() == ["codex"]
    assert coding_executor_catalog  # import used


def test_executor_chain_rejects_an_unknown_preference(monkeypatch):
    monkeypatch.delenv("TGW_CODING_EXECUTORS", raising=False)
    with pytest.raises(harness_session.SessionError, match="unknown executor"):
        harness_session._executor_chain({"executor_preference": ["not-an-executor"]})


def test_catalogue_executor_without_a_runner_is_a_warn_skip_not_a_failure(tmp_path, monkeypatch):
    # 'gemini' is in the committed catalogue but has no harness_session runner
    # yet: the chain must skip it and end 'failed', never raise.
    monkeypatch.delenv("TGW_CODING_EXECUTORS", raising=False)
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: None)
    job = {"task_id": "t", "body": "b", "worktree": str(tmp_path),
           "executor_preference": ["gemini"]}
    out = harness_session.run_implement_session(job)
    assert out["outcome"] == "failed"
    assert "no harness_session runner" in out["artifacts"][0]["detail"]


def test_missing_credential_is_a_warn_the_chain_moves_on(tmp_path, monkeypatch):
    # claude has no credential -> SessionUnavailable -> chain exhausts to
    # 'failed' (a WARN), it is never a hard SessionError.
    monkeypatch.delenv("TGW_CODING_EXECUTORS", raising=False)
    monkeypatch.setattr(harness_session, "_claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: None)
    job = {"task_id": "t", "body": "b", "worktree": str(tmp_path),
           "executor_preference": ["claude"]}
    out = harness_session.run_implement_session(job, invoke=_fake_invoke("", returncode=1))
    assert out["outcome"] == "failed"
    assert out["artifacts"][0]["kind"] == "executor_chain_exhausted"


def test_stub_executor_lands_offline(tmp_path, monkeypatch):
    # no binary, no network, no credential — a real dispatch through the chain
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: None)
    job = {"task_id": "canary-x", "body": "prove the pipe",
           "worktree": str(tmp_path), "executor_preference": ["stub"]}

    impl = harness_session.run_implement_session(job)
    assert impl["outcome"] == "satisfied"
    assert (tmp_path / ".tgw-canary").read_text().startswith("harness offline canary")

    rev = harness_session.run_review_session(job)
    assert rev == {"verdict": "PASS", "findings": []}


# --------------------------------------------------------------------------- #
# dispatch-outcome recording (LEAF-11-8 / Todo 1956 slice)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def _recorded(monkeypatch):
    """Enable recording with a stubbed store; return the captured calls as a
    list of (executor, role, outcome, detail)."""
    monkeypatch.setattr(harness_session, "OBSERVATIONS_ENABLED", True)
    calls = []

    def fake(executor, role, outcome, *, detail=""):
        calls.append((executor, role, outcome, detail))
        return True

    monkeypatch.setattr(harness_session.model_observations, "record", fake)
    return calls


def test_unavailable_attempt_is_recorded(tmp_path, monkeypatch, _recorded):
    # no credential -> SessionUnavailable -> the chain reroutes AND the
    # outcome is recorded so the next select_executor holds this executor.
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: None)
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(job, invoke=_fake_invoke("", returncode=1))
    assert out["outcome"] == "failed"
    assert len(_recorded) == 1
    executor, role, outcome, detail = _recorded[0]
    assert (executor, role, outcome) == ("claude", "implementation", "unavailable")
    assert detail  # the SessionUnavailable detail is carried


def test_error_attempt_is_recorded_then_reraised(tmp_path, monkeypatch, _recorded):
    # a genuine session failure still raises (existing behaviour) but the
    # "error" outcome is recorded first.
    monkeypatch.setattr(harness_session, "_session_credential", lambda e: ("ANTHROPIC_API_KEY", "x"))
    monkeypatch.setattr(harness_session, "_executor_chain", lambda job=None: ["claude"])
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    with pytest.raises(harness_session.SessionError):
        harness_session.run_implement_session(
            job, invoke=_fake_invoke("boom: internal error", returncode=1))
    assert [c[:3] for c in _recorded] == [("claude", "implementation", "error")]


def test_successful_report_is_recorded_as_available(tmp_path, _recorded):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(_claude_out({"status": "implemented", "summary": "did X"})),
    )
    assert out["outcome"] == "satisfied"
    assert [c[:3] for c in _recorded] == [("claude", "implementation", "available")]


def test_review_role_is_threaded_through(tmp_path, _recorded):
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_review_session(
        job, invoke=_fake_invoke(_claude_out({"verdict": "pass", "findings": []})),
    )
    assert out == {"verdict": "PASS", "findings": []}
    assert [c[:3] for c in _recorded] == [("claude", "review", "available")]


def test_recording_never_breaks_a_dispatch(tmp_path, monkeypatch):
    # a store that raises must not turn a working session into a failure.
    monkeypatch.setattr(harness_session, "OBSERVATIONS_ENABLED", True)

    def boom(executor, role, outcome, *, detail=""):
        raise RuntimeError("store on fire")

    monkeypatch.setattr(harness_session.model_observations, "record", boom)
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(_claude_out({"status": "implemented", "summary": "ok"})),
    )
    assert out["outcome"] == "satisfied"


def test_recording_gate_off_writes_nothing(tmp_path, monkeypatch):
    # OBSERVATIONS_ENABLED False (the default in this file's fixture): the
    # store is never touched even on success.
    calls = []
    monkeypatch.setattr(
        harness_session.model_observations, "record",
        lambda *a, **k: calls.append((a, k)) or True,
    )
    job = {"task_id": "t1", "body": "do X", "worktree": str(tmp_path)}
    out = harness_session.run_implement_session(
        job, invoke=_fake_invoke(_claude_out({"status": "implemented", "summary": "ok"})),
    )
    assert out["outcome"] == "satisfied"
    assert calls == []
