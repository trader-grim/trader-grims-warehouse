from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tgw import doctor_cli


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=path, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


def _snapshot(task: dict, cursor: dict) -> dict:
    value = {
        "schema": "tgw-current-context-snapshot/v1",
        "plan_commit": task["plan"]["approved_commit"],
        "source_commit": cursor["source_commit"],
        "source_tree": cursor["source_tree"],
        "active_capability": task["implementation"]["development_source"]["next_leaf"],
        "active_treatment": cursor["resolved"]["next_treatment"],
        "task": task,
        "cursor": cursor,
    }
    value["snapshot_sha256"] = doctor_cli._hash(value)
    return value


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[doctor_cli.DoctorPaths, str, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README").write_text("source\n", encoding="utf-8")
    _git(repository, "add", "README")
    _git(repository, "commit", "-m", "source")
    head = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")

    root = tmp_path / "tgw-lib"
    runtime_root = root / "coding-runtime"
    release = runtime_root / "releases" / head
    local_bin = root / "bin"
    operator_cli = tmp_path / "usr-local-bin-tgw"
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    for relative, content in {
        "bin/tgw-coding-local-operator": "#!/bin/sh\nexit 0\n",
        "bin/tgw-coding-mcp": "#!/bin/sh\nexit 0\n",
        "bin/tgw-doctor": "#!/bin/sh\nexit 0\n",
        "bin/tgw-operator": "#!/bin/sh\nexit 0\n",
        "config/tgw-coding-local-roles.sql": "SELECT 1;\n",
    }.items():
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "current").symlink_to(Path("releases") / head)
    local_bin.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(release / "bin/tgw-coding-local-operator", local_bin / "tgw-coding")
    shutil.copyfile(release / "bin/tgw-coding-mcp", local_bin / "tgw-coding-mcp")
    shutil.copyfile(release / "bin/tgw-doctor", local_bin / "tgw-doctor")
    shutil.copyfile(release / "bin/tgw-operator", operator_cli)

    config = root / "config/tgw-coding-local.json"
    _write_json(
        config,
        {
            "schema": "tgw-local-coding-workflow/v1",
            "postgres_dsn": "dbname=tgw_lib_dev_state_machine",
            "coding": {
                "repository_root": str(repository),
                "worktree_root": str(worktrees),
                "commands": {"codex-implement": ["/bin/true"]},
                "allowed_runners": ["/bin/true"],
            },
        },
    )
    plan_commit = "a" * 40
    task = {
        "schema": "tgw-current-task/v1",
        "id": "recovery",
        "updated_at": "2026-08-24T00:00:00+00:00",
        "plan": {"approved_commit": plan_commit},
        "implementation": {
            "development_source": {
                "commit": head,
                "next_leaf": "tgw.context-recovery@1",
            },
            "coding_workflow": {"commit": head},
        },
    }
    cursor = {
        "schema": "tgw-plan-execution-cycle-cursor/v1",
        "updated_at": "2026-08-24T00:00:00+00:00",
        "plan_commit": plan_commit,
        "source_commit": head,
        "source_tree": tree,
        "resolved": {"next_treatment": "tgw.context-recovery@1"},
    }
    task_path = root / "context-input/current-task.json"
    cursor_path = root / "context-input/plan-cycle-cursor.json"
    snapshot_path = root / "config/tgw-context-current.json"
    _write_json(task_path, task)
    _write_json(cursor_path, cursor)
    _write_json(snapshot_path, _snapshot(task, cursor))
    launcher = local_bin / "tgw-context-mcp"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    publisher = local_bin / "tgw-context-publish"
    publisher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    paths = doctor_cli.DoctorPaths(
        repository=repository,
        worktrees=worktrees,
        coding_config=config,
        runtime_root=runtime_root,
        local_bin=local_bin,
        operator_cli=operator_cli,
        context_snapshot=snapshot_path,
        context_task=task_path,
        context_cursor=cursor_path,
        context_launcher=launcher,
        context_publisher=publisher,
        context_catalog=root / "config/tgw-context-debian-v1.json",
        receipts=root / "doctor-receipts",
    )
    return paths, head, tree


def test_context_snapshot_binds_task_cursor_and_canonical_source(tmp_path: Path) -> None:
    paths, head, tree = _fixture(tmp_path)

    result = doctor_cli.check_context_snapshot(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["source_commit"] == head
    assert result["evidence"]["source_tree"] == tree


def test_context_snapshot_detects_cursor_drift_with_exact_repair(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)

    result = doctor_cli.check_context_snapshot(paths)

    assert result["state"] == "FAIL"
    assert result["operator_action"] == "sudo -n tgw doctor repair context"


def test_context_process_match_ignores_parent_shell_command_text() -> None:
    assert doctor_cli._is_context_process(
        ["python3", "/opt/TGW/tgw-lib/bin/tgw-context-mcp"]
    )
    assert doctor_cli._is_context_process(
        ["python3", "-m", "tgw.context_mcp_server"]
    )
    assert not doctor_cli._is_context_process(
        ["bash", "-c", "/opt/TGW/tgw-lib/bin/tgw-context-mcp"]
    )


def test_context_repair_updates_only_stale_source_binding_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, head, tree = _fixture(tmp_path)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if command[0] == str(paths.context_publisher):
            task = json.loads(paths.context_task.read_text())
            updated_cursor = json.loads(paths.context_cursor.read_text())
            _write_json(paths.context_snapshot, _snapshot(task, updated_cursor))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)

    result = doctor_cli.repair_context(paths)

    repaired = json.loads(paths.context_cursor.read_text())
    assert result["ok"] is True
    assert result["changed"] is True
    assert repaired["source_commit"] == head
    assert repaired["source_tree"] == tree
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert receipt["operation"] == "context"
    assert receipt["receipt_sha256"].startswith("sha256:")


def test_runtime_check_requires_exact_release_selector_and_launchers(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)

    result = doctor_cli.check_runtime(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["desired_commit"] == head
    assert result["evidence"]["forbidden_dependencies"] == []


def test_inventory_marks_unique_work_for_preservation(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    worktree = paths.worktrees / "candidate"
    _git(paths.repository, "worktree", "add", "-b", "candidate", str(worktree))
    (worktree / "unique").write_text("preserve me\n", encoding="utf-8")
    _git(worktree, "add", "unique")
    _git(worktree, "commit", "-m", "unique")

    result = doctor_cli.inventory(paths)
    candidate = next(row for row in result["worktrees"] if row["path"] == str(worktree))

    assert result["schema"] == "tgw-local-doctor-inventory/v1"
    assert candidate["unique_commits"] == 1
    assert candidate["merged_into_canonical"] is False
    assert candidate["preservation_required"] is True


def test_runtime_check_rejects_remote_or_authority_dependency(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    config = json.loads(paths.coding_config.read_text())
    config["coding"]["api_endpoint"] = "http://tgw-prod/coding"
    _write_json(paths.coding_config, config)

    result = doctor_cli.check_runtime(paths)

    assert result["state"] == "FAIL"
    assert "forbidden dependencies" in result["detail"]


def test_repairs_require_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 1004)

    with pytest.raises(doctor_cli.DoctorError, match="sudo -n"):
        doctor_cli.repair_context(paths)


def test_operator_launcher_routes_only_local_coding_and_doctor() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "bin/tgw-operator").read_text(encoding="utf-8")

    assert "coding)" in launcher
    assert "doctor)" in launcher
    assert "/opt/TGW/tgw-lib/bin/tgw-coding" in launcher
    assert "/opt/TGW/tgw-lib/bin/tgw-doctor" in launcher
    assert "exec /usr/local/libexec/tgw-production-client" in launcher


def test_role_sql_persists_explicit_todo_sequence_update_grant() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "config/tgw-coding-local-roles.sql").read_text(encoding="utf-8")

    assert "GRANT USAGE, SELECT, UPDATE" in sql
    assert "public.todo_items_id_seq" in sql


def test_doctor_launcher_is_local_and_provider_independent() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "bin/tgw-doctor").read_text(encoding="utf-8")

    assert "/opt/TGW/tgw-lib/coding-runtime/current" in launcher
    assert "tgw.doctor_cli" in launcher
    assert "tgw-prod" not in launcher
    assert "ssh" not in launcher.lower()
