from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tgw.workers import codex_implement


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _job(**overrides):
    value = {
        "todo_id": 1745,
        "treatment_id": "codex-implement",
        "treatment_version": "1",
        "task_spec": {"schema": "coding-task/v1", "todo_id": 1745, "agent": "codex", "body": "implement the bounded feature"},
    }
    value.update(overrides)
    return value


def _invoke(report, edit=None, returncode=0):
    def invoke(command, *, cwd, **_kwargs):
        if edit:
            edit(Path(cwd))
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps(report), encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode, "", "failure" if returncode else "")

    return invoke


def test_satisfied_requires_real_uncommitted_source_change(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke(
            {"status": "implemented", "summary": "added feature", "tests": ["focused tests passed"]},
            edit=lambda path: (path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8"),
        ),
    )
    assert result["outcome"] == "satisfied"
    assert result["established_conditions"] == ["implemented"]


def test_runner_uses_noninteractive_workspace_write_without_approval_gate(
    tmp_path,
    monkeypatch,
):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    captured = []

    def invoke(command, *, cwd, env, **_kwargs):
        captured.extend(command)
        ephemeral_home = Path(env["CODEX_HOME"])
        assert ephemeral_home.parent.name.startswith(".tgw-codex-implement-")
        assert ephemeral_home.parent.parent == repo
        assert (ephemeral_home / "auth.json").is_file()
        assert (ephemeral_home / "auth.json").stat().st_mode & 0o777 == 0o600
        config = ephemeral_home / "config.toml"
        assert config.stat().st_mode & 0o777 == 0o600
        assert config.read_text(encoding="utf-8") == (
            "[mcp_servers.tgw-context]\n"
            'command = "/opt/TGW/tgw-lib/bin/tgw-context-mcp"\n'
            "args = []\n"
        )
        Path(command[command.index("-o") + 1]).write_text(
            json.dumps({"status": "blocked", "summary": "bounded", "tests": []}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    codex_implement.run(_job(), repo, invoke=invoke)

    assert "--approve-for-me" not in captured
    assert captured[captured.index("--ask-for-approval") + 1] == "never"
    assert captured[captured.index("--sandbox") + 1] == "workspace-write"
    assert "--ignore-user-config" not in captured


def test_runner_fails_closed_when_local_context_mcp_is_missing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_CONTEXT_MCP", tmp_path / "missing-context-mcp")
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    try:
        codex_implement.run(
            _job(),
            repo,
            invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")),
        )
    except Exception as exc:
        assert "tgw-context MCP is unavailable" in str(exc)
    else:
        raise AssertionError("runner accepted a missing context MCP")


def test_model_success_without_diff_is_partial(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")
    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke({"status": "implemented", "summary": "nothing changed", "tests": []}),
    )
    assert result["outcome"] == "partial"
    assert result["established_conditions"] == []


def test_runner_rejects_another_actor_before_codex(tmp_path):
    repo = _repo(tmp_path)
    job = _job(task_spec={"schema": "coding-task/v1", "todo_id": 1745, "agent": "claude", "body": "task"})
    try:
        codex_implement.run(job, repo, invoke=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("called")))
    except Exception as exc:
        assert "task specification" in str(exc)
    else:
        raise AssertionError("wrong actor was accepted")


def test_runner_detects_model_commit_as_conflict(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setattr(codex_implement, "_codex_binary", lambda: "/bin/true")

    def commit(path: Path):
        (path / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.py"], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "forbidden"], cwd=path, check=True, capture_output=True)

    result = codex_implement.run(
        _job(),
        repo,
        invoke=_invoke({"status": "implemented", "summary": "committed", "tests": []}, edit=commit),
    )
    assert result["outcome"] == "conflict"
    assert result["established_conditions"] == []


def test_prompt_forbids_deploy_commit_config_secrets_and_satellites():
    prompt = codex_implement._prompt(_job()["task_spec"])
    for word in ("commit", "deploy", "configuration", "secrets", "satellite"):
        assert word in prompt
    assert "CLAUDE.md does not govern Codex" in prompt
