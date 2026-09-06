"""CLI entry for the continual-harness orchestrator — Todo 1916 leaf 11.1."""

from __future__ import annotations

import pytest

from tgw.development import harness_cli


def test_task_body_reads_todo(monkeypatch):
    import tgw.todo as todo

    monkeypatch.setattr(todo, "todo_get", lambda i: {"body": "  implement the widget  "})
    monkeypatch.setattr(todo, "init", lambda dsn: None)
    task_id, body = harness_cli._task_body("1931", None, "dbname=x")
    assert task_id == "todo-1931"
    assert body == "  implement the widget  "


def test_task_body_override_wins(monkeypatch):
    task_id, body = harness_cli._task_body("my-task", "do the thing", "dbname=x")
    assert (task_id, body) == ("my-task", "do the thing")


def test_task_body_non_numeric_without_override_exits():
    with pytest.raises(SystemExit):
        harness_cli._task_body("free-text", None, "dbname=x")


def test_task_body_empty_todo_exits(monkeypatch):
    import tgw.todo as todo

    monkeypatch.setattr(todo, "todo_get", lambda i: {"body": "   "})
    monkeypatch.setattr(todo, "init", lambda dsn: None)
    with pytest.raises(SystemExit):
        harness_cli._task_body("42", None, "dbname=x")


def test_sudo_ref_publisher_raises_on_failure(monkeypatch, tmp_path):
    import subprocess

    class _Fail:
        returncode = 1
        stderr = "not the sanctioned publisher"
        stdout = ""

    seen = {}
    monkeypatch.setattr(subprocess, "run", lambda a, **k: seen.update(argv=a) or _Fail())
    publish = harness_cli._sudo_ref_publisher(tmp_path, "tgw-harness")
    with pytest.raises(harness_cli.harness_git.HarnessGitError):
        publish("refs/heads/main", "a" * 40, "b" * 40)
    assert seen["argv"][:5] == ["sudo", "-n", "-u", "tgw-harness", "/usr/bin/git"]


def test_main_wires_orchestrator(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()  # resolve(strict=True) only needs the dir to exist
    seen = {}

    monkeypatch.setattr(harness_cli.harness_runners, "build_runners",
                        lambda repo, wtr, **k: seen.update(body=k["task_body"]) or "RUNNERS")

    def fake_run_task(task_id, **kw):
        seen.update(task_id=task_id, message=kw["commit_message"], runners=kw["runners"],
                    has_publisher=kw["ref_publisher"] is not None)
        return {"outcome": "landed", "commit": "c" * 40, "rounds": 1, "base_ref": "refs/heads/main"}

    monkeypatch.setattr(harness_cli.harness_orchestrator, "run_task", fake_run_task)
    monkeypatch.setattr(harness_cli.harness_ledger, "init", lambda dsn: None)

    rc = harness_cli.main([
        "my-task", "--body", "do X", "--message", "my-task: do X",
        "--repository", str(tmp_path), "--postgres-dsn", "dbname=x",
    ])
    assert rc == 0
    assert seen["task_id"] == "my-task"
    assert seen["body"] == "do X"
    assert seen["message"] == "my-task: do X"
    assert seen["runners"] == "RUNNERS"
    assert seen["has_publisher"] is True  # default: sudo -u db publisher


def test_resolve_executor_bins_is_catalogue_driven(tmp_path, monkeypatch):
    import json

    from tgw import coding_executor_catalog

    acme = tmp_path / "acme"
    acme.write_text("#!/bin/sh\n")
    acme.chmod(0o755)
    cat = tmp_path / "executors.json"
    cat.write_text(json.dumps({"executors": {
        "acme": {"binary_name": "acme", "install_source": None,
                 "install_target_path": str(acme), "runtime_deps": [], "verify_cmd": None,
                 "credential_env": ["ACME_API_KEY"], "auth_file": None, "enabled": True},
        "beta": {"binary_name": "beta-missing", "install_source": None,
                 "install_target_path": None, "runtime_deps": [], "verify_cmd": None,
                 "credential_env": [], "auth_file": None, "enabled": True},
    }}))
    monkeypatch.setenv("TGW_CODING_EXECUTORS", str(cat))
    monkeypatch.delenv(coding_executor_catalog.binary_env_var("acme"), raising=False)
    monkeypatch.setattr(coding_executor_catalog.shutil, "which", lambda _n: None)
    resolved = harness_cli._resolve_executor_bins({})
    assert resolved == {"acme": str(acme.resolve())}  # 'beta' binary not found -> omitted


def test_main_self_publish_drops_the_db_publisher(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(harness_cli.harness_runners, "build_runners", lambda *a, **k: "R")
    monkeypatch.setattr(harness_cli.harness_ledger, "init", lambda dsn: None)
    captured = {}
    monkeypatch.setattr(
        harness_cli.harness_orchestrator, "run_task",
        lambda tid, **kw: captured.update(publisher=kw["ref_publisher"]) or {"outcome": "landed", "commit": "c", "rounds": 1},
    )
    harness_cli.main(["t", "--body", "b", "--message", "m", "--repository", str(tmp_path),
                      "--self-publish", "--postgres-dsn", "dbname=x"])
    assert captured["publisher"] is None
