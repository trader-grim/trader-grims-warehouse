from __future__ import annotations

import json
import os
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
    source_files = {
        "README": "source\n",
        "bin/tgw-coding-local-operator": "#!/bin/sh\nexit 0\n",
        "bin/tgw-coding-mcp": "#!/bin/sh\nexit 0\n",
        "bin/tgw-doctor": "#!/bin/sh\nexit 0\n",
        "bin/tgw-operator": "#!/bin/sh\nexit 0\n",
        "config/tgw-coding-local-roles.sql": "SELECT 1;\n",
        "systemd/tgw-codex-implement-worker.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-controller-verify-worker.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-coding-local-foreman.timer": "[Timer]\nOnBootSec=1s\n",
        "systemd/tgw-coding-local-foreman.service": "[Service]\nType=oneshot\nExecStart=/bin/true\n",
    }
    for relative, content in source_files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if relative.startswith("bin/"):
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    _git(repository, "add", ".")
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
    for relative in _git(repository, "ls-files").splitlines():
        source = repository / relative
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, path)
        path.chmod(source.stat().st_mode & 0o777)
    for directory in [release, *[path for path in release.rglob("*") if path.is_dir()]]:
        directory.chmod(0o555)
    runtime_root.mkdir(parents=True, exist_ok=True)
    (runtime_root / "current").symlink_to(Path("releases") / head)
    local_bin.mkdir(parents=True, exist_ok=True)
    (local_bin / "tgw-coding").symlink_to(
        runtime_root / "current/bin/tgw-coding-local-operator"
    )
    (local_bin / "tgw-coding-mcp").symlink_to(
        runtime_root / "current/bin/tgw-coding-mcp"
    )
    (local_bin / "tgw-doctor").symlink_to(runtime_root / "current/bin/tgw-doctor")
    operator_cli.symlink_to(runtime_root / "current/bin/tgw-operator")

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
    launcher.write_text(
        f"#!/bin/sh\n# runtime snapshot: {snapshot_path}\nexit 0\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)
    publisher = local_bin / "tgw-context-publish"
    publisher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    catalog_path = root / "config/tgw-context-debian-v1.json"
    _write_json(catalog_path, {"schema": "fixture", "actors": {"codex": {}}})
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
        context_catalog=catalog_path,
        receipts=root / "doctor-receipts",
        trusted_release_owners=(os.getuid(),),
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
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            task = json.loads(task_path.read_text())
            updated_cursor = json.loads(cursor_path.read_text())
            _write_json(output_path, _snapshot(task, updated_cursor))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    result = doctor_cli.repair_context(paths)

    repaired = json.loads(paths.context_cursor.read_text())
    assert result["ok"] is True
    assert result["changed"] is True
    assert repaired["source_commit"] == head
    assert repaired["source_tree"] == tree
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert receipt["operation"] == "context"
    assert receipt["receipt_sha256"].startswith("sha256:")


def test_context_publisher_failure_leaves_live_inputs_unchanged_and_is_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor_before = paths.context_cursor.read_bytes()
    snapshot_before = paths.context_snapshot.read_bytes()
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if command[0] == str(paths.context_publisher):
            return subprocess.CompletedProcess(command, 1, "", "publisher rejected")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context", paths)

    assert paths.context_cursor.read_bytes() == cursor_before
    assert paths.context_snapshot.read_bytes() == snapshot_before
    assert len(list(paths.receipts.glob("*context-failed.json"))) == 1


def test_context_repair_detects_snapshot_race_and_restores_both_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor_before = paths.context_cursor.read_bytes()
    snapshot_before = paths.context_snapshot.read_bytes()
    original_run = doctor_cli._run
    original_atomic = doctor_cli._atomic_json

    def run(command, **kwargs):
        if command[0] == str(paths.context_publisher):
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            _write_json(
                output_path,
                _snapshot(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                ),
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    raced = False

    def atomic(path, value, **kwargs):
        nonlocal raced
        original_atomic(path, value, **kwargs)
        if path == paths.context_cursor and not raced:
            raced = True
            _write_json(paths.context_snapshot, {"concurrent": True})

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_atomic_json", atomic)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="changed before atomic cutover"):
        doctor_cli.repair_context(paths)

    assert paths.context_cursor.read_bytes() == cursor_before
    assert paths.context_snapshot.read_bytes() == snapshot_before


def test_runtime_check_requires_exact_release_selector_and_launchers(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)

    result = doctor_cli.check_runtime(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["desired_commit"] == head
    assert result["evidence"]["forbidden_dependencies"] == []


def test_runtime_check_rejects_mutated_immutable_release(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    (paths.runtime_root / "current/README").write_text("mutated\n", encoding="utf-8")

    result = doctor_cli.check_runtime(paths)

    assert result["state"] == "FAIL"
    assert "release tree differs from Git" in result["detail"]


def test_runtime_repair_switches_only_one_selector_behind_stable_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, head, _tree = _fixture(tmp_path)
    previous = paths.runtime_root / "releases/previous"
    previous.mkdir()
    current = paths.runtime_root / "current"
    current.unlink()
    current.symlink_to(Path("releases/previous"))
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)

    result = doctor_cli.repair_runtime(paths)

    assert result["changed"] is True
    assert current.is_symlink()
    assert current.resolve() == paths.runtime_root / "releases" / head
    for destination, target in doctor_cli._launcher_links(paths).items():
        assert destination.is_symlink()
        assert destination.readlink() == target


def test_runtime_repair_refuses_online_launcher_surface_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    stale = paths.local_bin / "tgw-coding"
    stale.unlink()
    stale.write_text("old launcher\n", encoding="utf-8")
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)

    with pytest.raises(doctor_cli.DoctorError, match="bounded bootstrap"):
        doctor_cli.repair_runtime(paths)


def test_runtime_selector_rolls_back_if_post_switch_release_check_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    previous = paths.runtime_root / "releases/previous"
    previous.mkdir()
    current = paths.runtime_root / "current"
    current.unlink()
    current.symlink_to(Path("releases/previous"))
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    original_verify = doctor_cli._verify_release_tree
    calls = 0

    def verify(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise doctor_cli.DoctorError("release changed after selector switch")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(doctor_cli, "_verify_release_tree", verify)

    with pytest.raises(doctor_cli.DoctorError, match="original selector restored"):
        doctor_cli.repair_runtime(paths)

    assert current.readlink() == Path("releases/previous")


def test_unit_definition_requires_exact_fragment_and_no_dropins(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o644)
    state = {
        "FragmentPath": str(fragment),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": " ".join(doctor_cli._UNIT_ARGV[unit]),
        "ActiveState": "inactive",
        "MainPID": "0",
    }

    exact = doctor_cli._unit_definition(paths, unit, state)
    with_dropin = doctor_cli._unit_definition(
        paths, unit, {**state, "DropInPaths": "/etc/systemd/system/x.conf"}
    )

    assert exact["exact"] is True
    assert with_dropin["exact"] is False
    assert "unexpected systemd drop-in" in with_dropin["reasons"]


def test_unit_definition_rejects_active_process_with_different_argv(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o644)
    state = {
        "FragmentPath": str(fragment),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": " ".join(doctor_cli._UNIT_ARGV[unit]),
        "ActiveState": "active",
        "MainPID": str(os.getpid()),
    }

    result = doctor_cli._unit_definition(paths, unit, state)

    assert result["exact"] is False
    assert "active process argv differs" in result["reasons"]


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


def test_inventory_derives_harnesses_from_catalog(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _write_json(paths.context_catalog, {"actors": {"future-harness": {}}})

    result = doctor_cli.inventory(paths)
    future = next(row for row in result["harnesses"] if row["name"] == "future-harness")

    assert future["catalog_actor"] is True
    assert future["tgw_coders_member"] is False


def test_database_check_source_covers_every_granted_object_and_execute_privilege() -> None:
    source = Path(doctor_cli.__file__).read_text(encoding="utf-8")

    assert "history_access" in source
    assert "history_sequence_access" in source
    assert "has_function_privilege" in source


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


def test_blanket_repair_all_is_not_exposed(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)

    with pytest.raises(doctor_cli.DoctorError, match="unknown repair"):
        doctor_cli.repair("all", paths)


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
