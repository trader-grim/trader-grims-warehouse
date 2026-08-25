from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from dataclasses import replace
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
    context_runtime_source = root / "context-runtime/src"
    context_module = context_runtime_source / "tgw/current_context_snapshot.py"
    context_module.parent.mkdir(parents=True)
    context_module.write_text("# fixture\n", encoding="utf-8")
    context_module.chmod(0o444)
    context_module.parent.chmod(0o555)
    context_runtime_source.chmod(0o555)
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
        context_runtime_source=context_runtime_source,
        context_catalog=catalog_path,
        receipts=root / "doctor-receipts",
        cleanup_archive_root=root / "recovery-archive",
        cleanup_system_bin=tmp_path / "usr-local-bin",
        cleanup_actor_home=tmp_path / "actor-home",
        cleanup_reference_roots=(tmp_path / "active-config",),
        trusted_release_owners=(os.getuid(),),
        systemd_unit_roots=(tmp_path / "systemd-units",),
        archive_discovery_roots=(tmp_path / "archive-discovery",),
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


def test_root_post_repair_checks_use_the_invoking_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "codex")

    assert doctor_cli._operator_actor() == "codex"


def test_root_database_postcheck_runs_as_the_invoking_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    observed = {}

    def run(command, **_kwargs):
        observed["command"] = command
        result = {
            "actor": "codex",
            "database_connect": True,
            "schema_usage": True,
            "role_member": True,
            "todo_access": True,
            "queue_access": True,
            "history_access": True,
            "todo_sequence_access": True,
            "history_sequence_access": True,
            "claim_function_access": True,
            "recovery_function_access": True,
            "active_jobs": 0,
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result) + "\n", "")

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "codex")
    monkeypatch.setattr(doctor_cli, "_run", run)

    result = doctor_cli.check_database(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["actor"] == "codex"
    assert observed["command"][:5] == [
        "sudo",
        "-n",
        "-u",
        "codex",
        "/usr/bin/psql",
    ]


def test_context_repair_updates_only_stale_source_binding_and_writes_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, head, tree = _fixture(tmp_path)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    original_run = doctor_cli._run
    publisher_env = {}

    def run(command, **kwargs):
        if command[0] == str(paths.context_publisher):
            publisher_env.update(kwargs["env"])
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
    assert publisher_env == {
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(paths.context_runtime_source.resolve()),
    }
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


def test_context_repair_refuses_writable_context_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    paths.context_runtime_source.chmod(0o777)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="not trusted-owner immutable"):
        doctor_cli.repair_context(paths)


def test_context_repair_refuses_symlinked_context_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    configured_source = paths.context_runtime_source
    real_source = configured_source.parent / "real-src"
    configured_source.rename(real_source)
    configured_source.symlink_to(real_source)
    paths = replace(paths, context_runtime_source=configured_source)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="not trusted-owner immutable"):
        doctor_cli.repair_context(paths)


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


def test_unit_definition_rejects_loaded_exec_start_with_extra_argument(
    tmp_path: Path,
) -> None:
    paths, head, _tree = _fixture(tmp_path)
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o644)
    expected = doctor_cli._UNIT_ARGV[unit]
    state = {
        "FragmentPath": str(fragment),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": (
            f"{{ path={expected[0]} ; argv[]={' '.join(expected)} --extra ; "
            "ignore_errors=no ; start_time=[n/a] ; }}"
        ),
        "ActiveState": "inactive",
        "MainPID": "0",
    }

    result = doctor_cli._unit_definition(paths, unit, state)

    assert result["exact"] is False
    assert "loaded ExecStart differs" in result["reasons"]


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


def test_inventory_scans_all_declared_systemd_and_archive_roots(
    tmp_path: Path,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    runtime_units = tmp_path / "run-systemd"
    vendor_units = tmp_path / "usr-lib-systemd"
    archive_parent = tmp_path / "external-root/project/archive"
    runtime_units.mkdir()
    vendor_units.mkdir()
    archive_parent.mkdir(parents=True)
    (runtime_units / "tgw-runtime.service").write_text("runtime\n")
    (vendor_units / "tgw-vendor.service").write_text("vendor\n")
    paths = replace(
        paths,
        systemd_unit_roots=(runtime_units, vendor_units),
        archive_discovery_roots=(tmp_path / "external-root",),
    )

    result = doctor_cli.inventory(paths)

    surface_paths = {row["path"] for row in result["active_surfaces"]}
    archive_paths = {row["path"] for row in result["archive_roots"]}
    assert str(runtime_units / "tgw-runtime.service") in surface_paths
    assert str(vendor_units / "tgw-vendor.service") in surface_paths
    assert str(archive_parent) in archive_paths
    assert result["archive_discovery"] == [
        {
            "path": str(tmp_path / "external-root"),
            "exists": True,
            "scanned": True,
            "complete": True,
            "max_depth": paths.archive_discovery_max_depth,
            "error": None,
        }
    ]


def test_database_check_source_covers_every_granted_object_and_execute_privilege() -> None:
    source = Path(doctor_cli.__file__).read_text(encoding="utf-8")

    assert "history_access" in source
    assert "history_sequence_access" in source
    assert "has_function_privilege" in source
    assert "has_database_privilege" in source
    assert "has_schema_privilege" in source


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


def _obsolete_fixture(
    paths: doctor_cli.DoctorPaths, monkeypatch: pytest.MonkeyPatch
) -> list[Path]:
    hashes = dict(doctor_cli._OBSOLETE_FILE_HASHES)
    created = []
    for name in hashes:
        if name.startswith("tgw-context-mcp-candidate-"):
            path = paths.local_bin / name
        else:
            path = paths.cleanup_system_bin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ("declared obsolete " + name + "\n").encode()
        path.write_bytes(raw)
        path.chmod(0o755)
        hashes[name] = "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
        created.append(path)
    actor = paths.cleanup_actor_home / ".local/bin/tgw-actor"
    actor.parent.mkdir(parents=True)
    actor.symlink_to(doctor_cli._OBSOLETE_ACTOR_TARGET)
    created.append(actor)
    paths.cleanup_reference_roots[0].mkdir(parents=True)
    monkeypatch.setattr(doctor_cli, "_OBSOLETE_FILE_HASHES", hashes)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_cleanup_process_references", lambda _rows: [])
    return created


def test_obsolete_cleanup_diagnoses_warn_and_moves_exact_surfaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    timestamp = 1_700_000_000_123_456_789
    os.utime(sources[0], ns=(timestamp, timestamp))
    os.setxattr(sources[0], "user.tgw-cleanup-test", b"preserved")

    warning = doctor_cli.check_obsolete_surfaces(paths)
    source_state = sources[0].stat(follow_symlinks=False)
    result = doctor_cli.repair_obsolete_surfaces(paths)

    assert warning["state"] == "WARN"
    assert result["changed"] is True
    assert all(not path.exists() and not path.is_symlink() for path in sources)
    manifest = json.loads(Path(result["manifest"]).read_text())
    assert manifest["schema"] == "tgw-doctor-obsolete-surface-archive/v1"
    assert {row["path"] for row in manifest["entries"]} == {str(path) for path in sources}
    for row in manifest["entries"]:
        archived = Path(row["archive_path"])
        assert archived.is_symlink() if row["kind"] == "symlink" else archived.is_file()
    regular = next(row for row in manifest["entries"] if row["path"] == str(sources[0]))
    archived_regular = Path(regular["archive_path"])
    archived_state = archived_regular.stat(follow_symlinks=False)
    assert regular["metadata"] == {
        "uid": source_state.st_uid,
        "gid": source_state.st_gid,
        "mode": stat.S_IMODE(source_state.st_mode),
        "atime_ns": source_state.st_atime_ns,
        "mtime_ns": timestamp,
        "xattrs": {"user.tgw-cleanup-test": "cHJlc2VydmVk"},
    }
    assert archived_state.st_uid == source_state.st_uid
    assert archived_state.st_gid == source_state.st_gid
    assert stat.S_IMODE(archived_state.st_mode) == stat.S_IMODE(source_state.st_mode)
    assert archived_state.st_mtime_ns == timestamp
    assert os.getxattr(archived_regular, "user.tgw-cleanup-test") == b"preserved"
    assert Path(result["prepared_receipt"]).is_file()
    assert Path(result["receipt"]).is_file()


def test_obsolete_cleanup_refuses_changed_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    sources[0].write_text("changed\n")

    diagnosis = doctor_cli.check_obsolete_surfaces(paths)
    with pytest.raises(doctor_cli.DoctorError, match="bytes changed"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert diagnosis["state"] == "FAIL"
    assert "operator_action" not in diagnosis
    assert all(path.exists() or path.is_symlink() for path in sources)
    assert not paths.cleanup_archive_root.exists()


def test_obsolete_cleanup_keeps_detecting_unbound_legacy_launchers(
    tmp_path: Path,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    legacy = paths.cleanup_system_bin / "tgw-coding"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("unknown historical bytes\n")

    diagnosis = doctor_cli.check_obsolete_surfaces(paths)

    assert diagnosis["state"] == "FAIL"
    assert diagnosis["evidence"]["unbound"] == [str(legacy)]
    assert "operator_action" not in diagnosis


def test_obsolete_cleanup_ignores_context_evidence_but_detects_active_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    paths.context_snapshot.write_text(f'{{"historical_path": "{sources[0]}"}}')
    paths = replace(
        paths,
        cleanup_reference_roots=(
            paths.context_snapshot,
            paths.cleanup_reference_roots[0],
        ),
    )
    present = [
        item
        for item in doctor_cli._declared_obsolete_surfaces(paths)
        if doctor_cli._lexists(item["path"])
    ]

    assert doctor_cli._cleanup_references(paths, present) == []

    active = paths.cleanup_reference_roots[1] / "active.service"
    active.write_text(f"ExecStart={sources[0]}\n")
    references = doctor_cli._cleanup_references(paths, present)
    assert {row["path"] for row in references} == {str(active)}
    assert {row["reference"] for row in references} == {
        str(sources[0]),
        sources[0].name,
    }


def test_obsolete_cleanup_refuses_unknown_process_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    proc_root = tmp_path / "proc"
    cmdline = proc_root / "42/cmdline"
    cmdline.parent.mkdir(parents=True)
    cmdline.write_bytes(b"/bin/true\0")
    original = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == cmdline:
            raise PermissionError("fixture process is unreadable")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    surfaces = doctor_cli._declared_obsolete_surfaces(paths)

    with pytest.raises(doctor_cli.DoctorError, match="process activity"):
        doctor_cli._cleanup_process_references(surfaces, proc_root)


def test_obsolete_cleanup_detects_path_launched_process(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    cmdline = proc_root / "42/cmdline"
    cmdline.parent.mkdir(parents=True)
    cmdline.write_bytes(b"tgw-foreman\0--help\0")
    surfaces = [
        {
            "path": Path("/usr/local/bin/tgw-foreman"),
            "kind": "file",
            "declared_sha256": "sha256:fixture",
        }
    ]

    references = doctor_cli._cleanup_process_references(surfaces, proc_root)

    assert references == [
        {
            "pid": 42,
            "command": "tgw-foreman --help",
            "references": ["tgw-foreman"],
        }
    ]


@pytest.mark.parametrize("blocker", ["configuration", "process"])
def test_obsolete_cleanup_refuses_active_reference_or_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocker: str
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    if blocker == "configuration":
        (paths.cleanup_reference_roots[0] / "active.service").write_text(
            f"ExecStart={sources[0]}\n"
        )
    else:
        monkeypatch.setattr(
            doctor_cli,
            "_cleanup_process_references",
            lambda _rows: [{"pid": 42, "command": str(sources[0])}],
        )

    with pytest.raises(doctor_cli.DoctorError, match="active references remain"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_rolls_back_active_view_on_remove_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    original_unlink = doctor_cli._unlink_bound_surface
    failed = False

    def unlink(binding, identity):
        nonlocal failed
        if binding.path == sources[1] and not failed:
            failed = True
            raise OSError("fixture removal failure")
        return original_unlink(binding, identity)

    monkeypatch.setattr(doctor_cli, "_unlink_bound_surface", unlink)
    with pytest.raises(doctor_cli.DoctorError, match="active view restored"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert all(path.exists() or path.is_symlink() for path in sources)
    assert list(paths.receipts.glob("*obsolete-surfaces-rolled-back.json"))


def test_obsolete_cleanup_rolls_back_when_parent_fsync_fails_after_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    original_fsync = doctor_cli._fsync_directory
    failed = False

    def fsync_directory(path):
        nonlocal failed
        if path == sources[0].parent and not sources[0].exists() and not failed:
            failed = True
            raise OSError("fixture parent fsync failure")
        return original_fsync(path)

    monkeypatch.setattr(doctor_cli, "_fsync_directory", fsync_directory)

    with pytest.raises(doctor_cli.DoctorError, match="active view restored"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert failed is True
    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_binding_rejects_replaced_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    declaration = next(
        item
        for item in doctor_cli._declared_obsolete_surfaces(paths)
        if item["path"] == sources[0]
    )
    original_parent = sources[0].parent.with_name(sources[0].parent.name + "-bound")

    with doctor_cli._bind_cleanup_surface(declaration) as binding:
        sources[0].parent.rename(original_parent)
        sources[0].parent.mkdir()
        with pytest.raises(doctor_cli.DoctorError, match="parent changed"):
            doctor_cli._verify_bound_cleanup_surface(binding)


def test_obsolete_cleanup_reconciles_interrupted_prepared_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    original_state = doctor_cli._write_archive_state

    def interrupt_completion(archive, name, value):
        if name == "COMPLETED":
            raise KeyboardInterrupt("fixture crash after durable removal")
        return original_state(archive, name, value)

    monkeypatch.setattr(doctor_cli, "_write_archive_state", interrupt_completion)
    with pytest.raises(KeyboardInterrupt, match="fixture crash"):
        doctor_cli.repair_obsolete_surfaces(paths)
    assert all(not path.exists() and not path.is_symlink() for path in sources)

    monkeypatch.setattr(doctor_cli, "_write_archive_state", original_state)
    reconciled = doctor_cli.repair_obsolete_surfaces(paths)
    idempotent = doctor_cli.repair_obsolete_surfaces(paths)

    assert reconciled["changed"] is True
    assert reconciled["reconciled"] is True
    assert Path(reconciled["completion_state"]).is_file()
    assert idempotent["changed"] is False


def test_obsolete_cleanup_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _obsolete_fixture(paths, monkeypatch)

    first = doctor_cli.repair_obsolete_surfaces(paths)
    second = doctor_cli.repair_obsolete_surfaces(paths)

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["archive"] is None


def test_obsolete_cleanup_has_no_production_provider_plan_business_or_worktree_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _obsolete_fixture(paths, monkeypatch)
    preserved = {}
    for name in (
        "tgw-flake-git",
        "tgw-source-git",
        "tgw-github-agent",
        "tgw-context-mcp",
        "tgw-coding",
        "tgw-coding-mcp",
        "tgw-doctor",
    ):
        path = paths.local_bin / name
        if not (path.exists() or path.is_symlink()):
            path.write_text("preserved\n")
        preserved[path] = os.readlink(path) if path.is_symlink() else path.read_bytes()
    sentinels = {}
    for name in ("production", "provider", "plan", "business-data", "git-worktree"):
        path = tmp_path / "out-of-scope" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        sentinels[path] = path.read_bytes()
    worktrees_before = _git(paths.repository, "worktree", "list", "--porcelain")

    doctor_cli.repair_obsolete_surfaces(paths)

    assert {path: path.read_bytes() for path in sentinels} == sentinels
    assert _git(paths.repository, "worktree", "list", "--porcelain") == worktrees_before
    assert {
        path: os.readlink(path) if path.is_symlink() else path.read_bytes()
        for path in preserved
    } == preserved
