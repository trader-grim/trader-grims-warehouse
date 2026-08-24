from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from tgw.development import local_workflow


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, check=True, text=True, capture_output=True,
    )
    return result.stdout.strip()


def _config(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    worktrees = tmp_path / "worktrees"
    repository.mkdir()
    worktrees.mkdir()
    path = tmp_path / "coding.json"
    path.write_text(
        json.dumps(
            {
                "schema": "tgw-local-coding-workflow/v1",
                "postgres_dsn": "dbname=tgw_lib_dev_state_machine",
                "queue": {"poll_interval_s": 2},
                "coding": {
                    "repository_root": str(repository),
                    "worktree_root": str(worktrees),
                    "commands": {"codex-implement": ["/bin/true"]},
                    "allowed_runners": ["/bin/true"],
                },
            }
        )
    )
    return path


def test_local_config_has_no_remote_or_authority_dependency(tmp_path: Path) -> None:
    config = local_workflow.load_config(_config(tmp_path))

    assert config["postgres_dsn"] == "dbname=tgw_lib_dev_state_machine"
    assert "api_endpoint" not in config["coding"]
    assert "worker_identity" not in config["coding"]


def test_allocate_worktree_uses_one_group_workshop_and_is_idempotent(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    worktrees = tmp_path / "worktrees"
    repository.mkdir()
    worktrees.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README").write_text("base\n")
    _git(repository, "add", "README")
    _git(repository, "commit", "-m", "base")
    head = _git(repository, "rev-parse", "HEAD")

    first = local_workflow.allocate_worktree(
        repository, worktrees, "codex", 17, "plan-" + "a" * 24, head,
    )
    second = local_workflow.allocate_worktree(
        repository, worktrees, "codex", 17, "plan-" + "a" * 24, head,
    )

    assert first["created"] is True
    assert second["created"] is False
    assert first["worktree"] == second["worktree"]
    assert first["branch"] == "coding/codex/todo-17-plan-" + "a" * 24
    assert first["group"] == "tgw-coders"


def test_solution_projection_loads_without_card_or_envelope(monkeypatch, tmp_path: Path) -> None:
    projection = tmp_path / "solution.json"
    solution = {
        "schema": "tgw-plan-solution/v1",
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
    }
    projection.write_text(
        json.dumps(
            {
                "schema": "tgw-plan-runtime-projection/v1",
                "solution": solution,
            }
        )
    )
    observed = []
    monkeypatch.setattr(
        local_workflow,
        "verify_direct_development_solution",
        lambda value: observed.append(value),
    )

    assert local_workflow.load_solution(projection) == solution
    assert observed == [solution]


def test_status_reports_only_local_dependencies(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(local_workflow, "require_coder_account", lambda: "codex")
    monkeypatch.setattr(
        local_workflow,
        "__import__",
        __import__,
        raising=False,
    )
    monkeypatch.setattr(
        __import__("tgw.plan_luet", fromlist=["load_direct_development_luet_binding"]),
        "load_direct_development_luet_binding",
        lambda: SimpleNamespace(
            executable_path=Path("/opt/TGW/tgw-lib/development-tools/luet"),
            sha256="sha256:" + "a" * 64,
            version="0.9.26",
            plan_commit="b" * 40,
            plan_solution_hash="sha256:" + "c" * 64,
        ),
    )

    result = local_workflow.status_command(argparse.Namespace(config=config))

    assert result["ok"] is True
    assert result["dependencies"] == {
        "remote_provision_api": False,
        "actor_fleet": False,
        "execution_card": False,
        "tgw_prod": False,
    }
