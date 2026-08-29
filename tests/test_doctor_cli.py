from __future__ import annotations

import errno
import grp
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import pwd
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

from tgw import doctor_cli
from tgw.current_context_snapshot import publish_bytes

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def btrfs_tmp_path() -> Iterator[Path]:
    root = Path(tempfile.mkdtemp(prefix="pytest-doctor-reflink-", dir="/opt/TGW/var/tmp"))
    try:
        yield root
    finally:
        shutil.rmtree(root)


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=path, check=True, text=True, capture_output=True)
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
    prior_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    if prior_mode is not None and not prior_mode & stat.S_IWUSR:
        path.chmod(prior_mode | stat.S_IWUSR)
    try:
        path.write_text(json.dumps(value), encoding="utf-8")
    finally:
        if prior_mode is not None:
            path.chmod(prior_mode)


def test_reconciliation_publication_is_history_first_and_failure_atomic(tmp_path, monkeypatch):
    receipt = tmp_path / "implementation-receipt.json"
    receipt.write_bytes(b"legacy\n")
    attempt = {"attempt_hash": "sha256:" + "a" * 64}

    monkeypatch.setattr(
        "tgw.development.partial_resume.append_attempt",
        lambda *_args: (_ for _ in ()).throw(OSError("append failed")),
    )
    with pytest.raises(OSError, match="append failed"):
        doctor_cli._publish_reconciled_implementation(
            tmp_path,
            attempt,
            receipt,
            {"new": True},
            mode=0o640,
        )
    assert receipt.read_bytes() == b"legacy\n"

    history_path = tmp_path / ".tgw-coding-history/implementation/attempt.json"
    order = []
    monkeypatch.setattr(
        "tgw.development.partial_resume.append_attempt",
        lambda *_args: order.append("history") or history_path,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_atomic_json",
        lambda *_args, **_kwargs: order.append("top-level") or (_ for _ in ()).throw(OSError("projection failed")),
    )
    with pytest.raises(OSError, match="projection failed"):
        doctor_cli._publish_reconciled_implementation(
            tmp_path,
            attempt,
            receipt,
            {"new": True},
            mode=0o640,
        )
    assert order == ["history", "top-level"]
    assert receipt.read_bytes() == b"legacy\n"


def _fixture(tmp_path: Path) -> tuple[doctor_cli.DoctorPaths, str, str]:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    snapshot_fixture_path = tmp_path / "tgw-lib/config/tgw-context-current.json"
    source_files = {
        "README": "source\n",
        "bin/tgw-coding-local-operator": "#!/bin/sh\nexit 0\n",
        "bin/tgw-todo-local-operator": "#!/bin/sh\nexit 0\n",
        "bin/tgw-coding-mcp": "#!/bin/sh\nexit 0\n",
        "bin/tgw-doctor": "#!/bin/sh\nexit 0\n",
        "bin/tgw-operator": (ROOT / "bin/tgw-operator").read_text(),
        "bin/tgw-coding-bootstrap": (ROOT / "bin/tgw-coding-bootstrap").read_text(),
        "config/tgw-coding-local-roles.sql": "SELECT 1;\n",
        "systemd/tgw-codex-implement-worker.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-claude-review-worker.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-controller-verify-worker.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-coding-lifecycle-supervisor.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-coding-root-effect.service": "[Service]\nExecStart=/bin/true\n",
        "systemd/tgw-coding-runtime-restart.path": "[Path]\nPathChanged=/tmp/restart-request\n",
        "systemd/tgw-coding-runtime-restart.service": (
            "[Service]\nType=oneshot\n"
            "ExecStart=/bin/systemctl restart "
            "tgw-codex-implement-worker.service "
            "tgw-claude-review-worker.service "
            "tgw-controller-verify-worker.service "
            "tgw-coding-lifecycle-supervisor.service "
            "tgw-plan-render-local.service\n"
            "ExecStart=/bin/systemctl restart tgw-coding-root-effect.service\n"
        ),
        "systemd/tgw-context-snapshot-promote.path": "[Path]\nPathChanged=/tmp/context-pending\n",
        "systemd/tgw-context-snapshot-promote.service": "[Service]\nType=oneshot\nExecStart=/bin/true\n",
        "systemd/tgw-coding-local-foreman.timer": "[Timer]\nOnBootSec=1s\n",
        "systemd/tgw-coding-local-foreman.service": "[Service]\nType=oneshot\nExecStart=/bin/true\n",
        "scripts/tgw_context_debian_stdio.py": (f"#!/bin/sh\n# runtime snapshot: {snapshot_fixture_path}\nexit 0\n"),
        "scripts/tgw_context_publish.py": (ROOT / "scripts/tgw_context_publish.py").read_text(),
        "src/tgw/context_mcp_server.py": "# context server fixture\n",
        "src/tgw/current_context_snapshot.py": (ROOT / "src/tgw/current_context_snapshot.py").read_text(),
        "src/tgw/local_context_runtime.py": "# local runtime fixture\n",
    }
    for relative, content in source_files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if relative.startswith("bin/") or relative.startswith("scripts/"):
            path.chmod(0o755)
        else:
            path.chmod(0o644)
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "source")
    head = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")

    root = tmp_path / "tgw-lib"
    root.mkdir()
    root.chmod(0o755)
    runtime_root = root / "coding-runtime"
    release = runtime_root / "releases" / head
    local_bin = root / "bin"
    operator_cli = tmp_path / "usr-local-bin-tgw"
    coding_bootstrap = tmp_path / "usr-local-sbin-tgw-coding-bootstrap"
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    support_root = tmp_path / "tgw-coders"
    archive_root = support_root / "archive"
    runner_root = support_root / "runner"
    lifecycle_root = support_root / "lifecycles"
    root_effect_root = support_root / "root-effects"
    for directory in (archive_root, runner_root, lifecycle_root, root_effect_root):
        directory.mkdir(parents=True, exist_ok=True)
        root_effect = directory == root_effect_root
        directory.chmod(0o2750 if root_effect else 0o2770)
        os.chown(directory, -1, grp.getgrnam("tgw-coders").gr_gid)
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
    (local_bin / "tgw-coding").symlink_to(runtime_root / "current/bin/tgw-coding-local-operator")
    (local_bin / "tgw-todo").symlink_to(runtime_root / "current/bin/tgw-todo-local-operator")
    (local_bin / "tgw-coding-mcp").symlink_to(runtime_root / "current/bin/tgw-coding-mcp")
    (local_bin / "tgw-doctor").symlink_to(runtime_root / "current/bin/tgw-doctor")
    shutil.copyfile(release / "bin/tgw-operator", operator_cli)
    operator_cli.chmod(0o555)
    shutil.copyfile(release / "bin/tgw-coding-bootstrap", coding_bootstrap)
    coding_bootstrap.chmod(0o555)

    config = root / "config/tgw-coding-local.json"
    _write_json(
        config,
        {
            "schema": "tgw-local-coding-workflow/v1",
            "postgres_dsn": "dbname=tgw_lib_dev_state_machine",
            "coding": {
                "repository_root": str(repository),
                "worktree_root": str(worktrees),
                "preservation_archive_root": str(archive_root),
                "runner_state_root": str(runner_root),
                "lifecycle_root": str(lifecycle_root),
                "root_effect_root": str(root_effect_root),
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
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_bytes(publish_bytes(task, cursor))
    snapshot_path.chmod(0o444)
    launcher = local_bin / "tgw-context-mcp"
    shutil.copyfile(release / "scripts/tgw_context_debian_stdio.py", launcher)
    launcher.chmod(0o555)
    publisher = local_bin / "tgw-context-publish"
    shutil.copyfile(release / "scripts/tgw_context_publish.py", publisher)
    publisher.chmod(0o555)
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
        coding_bootstrap=coding_bootstrap,
        context_snapshot=snapshot_path,
        context_task=task_path,
        context_cursor=cursor_path,
        context_launcher=launcher,
        context_publisher=publisher,
        context_generation_root=root / "context-entrypoints/generations",
        context_generation_pointer=root / "context-entrypoints/current",
        context_runtime_source=context_runtime_source,
        context_catalog=catalog_path,
        receipts=root / "doctor-receipts",
        cleanup_archive_root=root / "recovery-archive",
        cleanup_system_bin=tmp_path / "usr-local-bin",
        cleanup_actor_home=tmp_path / "actor-home",
        cleanup_reference_roots=(tmp_path / "active-config",),
        trusted_release_owners=(os.getuid(),),
        context_install_uid=os.getuid(),
        context_install_gid=os.getgid(),
        coding_root_effect_uid=os.getuid(),
        systemd_unit_roots=(tmp_path / "systemd-units",),
        archive_discovery_roots=(tmp_path / "archive-discovery",),
    )
    generation = paths.context_generation_root / "context-fixture"
    generation.mkdir(parents=True)
    shutil.copyfile(launcher, generation / "tgw-context-mcp")
    shutil.copyfile(publisher, generation / "tgw-context-publish")
    for entry in generation.iterdir():
        entry.chmod(0o555)
    generation.chmod(0o555)
    paths.context_generation_pointer.parent.mkdir(parents=True, exist_ok=True)
    paths.context_generation_pointer.parent.chmod(0o755)
    paths.context_generation_pointer.symlink_to(Path("generations/context-fixture"))
    paths.context_generation_root.chmod(0o555)
    launcher.unlink()
    publisher.unlink()
    launcher.write_bytes(doctor_cli._CONTEXT_DISPATCH_SHIM)
    publisher.write_bytes(doctor_cli._CONTEXT_DISPATCH_SHIM)
    launcher.chmod(0o555)
    publisher.chmod(0o555)
    return paths, head, tree


def test_coding_bootstrap_is_explicit_and_context_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "bootstrap")
    _git(repository, "config", "user.email", "bootstrap@example.invalid")
    candidate_config = {
        "schema": "tgw-local-coding-workflow/v1",
        "postgres_dsn": "dbname=tgw_lib_dev_state_machine",
        "coding": {
            "repository_root": str(repository),
            "worktree_root": str(tmp_path / "worktrees"),
            "runtime_root": str(tmp_path / "runtime"),
            "preservation_archive_root": str(tmp_path / "archive"),
            "runner_state_root": str(tmp_path / "runner"),
            "lifecycle_root": str(tmp_path / "lifecycles"),
            "root_effect_root": str(tmp_path / "effects"),
            "commands": {},
            "allowed_runners": [],
        },
    }
    config_source = repository / "config/tgw-coding-local.json"
    config_source.parent.mkdir(parents=True)
    config_source.write_text(json.dumps(candidate_config))
    operator_source = repository / "bin/tgw-operator"
    operator_source.parent.mkdir(parents=True)
    operator_source.write_text(
        "#!/bin/sh\n[ \"$(/usr/bin/id -u)\" -ne 0 ] || exit 126\nexit 0\n",
        encoding="utf-8",
    )
    operator_source.chmod(0o755)
    bootstrap_source = repository / "bin/tgw-coding-bootstrap"
    bootstrap_source.write_text(
        "#!/usr/bin/python3.13 -IS\n# exact fixture bootstrap\n",
        encoding="utf-8",
    )
    bootstrap_source.chmod(0o755)
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "candidate")
    commit = _git(repository, "rev-parse", "HEAD")
    tree = _git(repository, "rev-parse", "HEAD^{tree}")
    root = tmp_path / "tgw-lib"
    context_input = root / "context-input"
    context_input.mkdir(parents=True)
    task = context_input / "current-task.json"
    cursor = context_input / "plan-cycle-cursor.json"
    task.write_text("{}")
    cursor.write_text("{}")
    paths = doctor_cli.DoctorPaths(
        repository=repository,
        worktrees=tmp_path / "worktrees",
        coding_config=root / "config/tgw-coding-local.json",
        runtime_root=tmp_path / "runtime",
        local_bin=tmp_path / "bin",
        operator_cli=tmp_path / "tgw",
        coding_bootstrap=tmp_path / "tgw-coding-bootstrap",
        context_task=task,
        context_cursor=cursor,
        systemd_install_root=tmp_path / "systemd",
        receipts=tmp_path / "receipts",
        context_install_uid=os.getuid(),
        context_install_gid=os.getgid(),
    )
    paths.worktrees.mkdir()
    paths.systemd_install_root.mkdir()
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_desired_runtime", lambda *_args: pytest.fail("Context consulted")
    )
    monkeypatch.setattr(
        doctor_cli, "_source_identity", lambda _paths: (commit, tree, "")
    )
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: type("Group", (), {"gr_gid": os.getegid()})(),
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda _name: type(
            "Account", (), {"pw_uid": os.geteuid(), "pw_name": "db"}
        )(),
    )
    monkeypatch.setattr(
        doctor_cli, "_provision_coding_support_roots", lambda *_args: []
    )
    monkeypatch.setattr(
        doctor_cli, "_close_mutation_journal", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        doctor_cli,
        "_repair_managed_directory",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_atomic_bytes",
        lambda path, value, **_kwargs: (
            path.parent.mkdir(parents=True, exist_ok=True),
            path.write_bytes(value),
            path.chmod(_kwargs["mode"]),
        ),
    )

    def materialize(command, **_kwargs):
        assert command[:6] == [
            "/usr/sbin/runuser", "-u", "db", "-g", "tgw-coders", "--",
        ]
        assert "--bootstrap-commit" in command
        release = paths.runtime_root / "releases" / commit
        (release / "bin").mkdir(parents=True)
        (release / "source-byte").write_text("exact\n", encoding="utf-8")
        (release / "source-byte").chmod(0o444)
        for name in ("tgw-operator", "tgw-coding-bootstrap"):
            shutil.copyfile(repository / "bin" / name, release / "bin" / name)
            (release / "bin" / name).chmod(0o555)
        (release / "bin").chmod(0o555)
        release.chmod(0o555)
        paths.runtime_root.mkdir(exist_ok=True)
        paths.runtime_root.chmod(0o750)
        (paths.runtime_root / "releases").chmod(0o750)
        (paths.runtime_root / "current").symlink_to(Path("releases") / commit)
        materialization = {
            "schema": "tgw-local-coding-bootstrap-materialization/v1",
            "actor": "db",
            "commit": commit,
            "tree": tree,
        }
        materialization["receipt_hash"] = doctor_cli._hash(materialization)
        return subprocess.CompletedProcess(
            command, 0, json.dumps(materialization), ""
        )

    monkeypatch.setattr(doctor_cli, "_run", materialize)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args, **_kwargs: {"verified": True}
    )
    monkeypatch.setattr(doctor_cli, "_launcher_links", lambda _paths: {})
    monkeypatch.setattr(
        doctor_cli,
        "repair_plan_render_worker",
        lambda *_args, **_kwargs: {"ok": True},
    )
    monkeypatch.setattr(
        doctor_cli, "repair_workers", lambda *_args, **_kwargs: {"ok": True}
    )
    monkeypatch.setattr(
        doctor_cli,
        "check_units",
        lambda *_args, **_kwargs: {"state": "PASS"},
    )
    monkeypatch.setattr(
        doctor_cli,
        "check_plan_render_worker",
        lambda *_args, **_kwargs: {"state": "PASS"},
    )
    monkeypatch.setattr(doctor_cli, "_receipt", lambda *_args, **_kwargs: "receipt")
    result = doctor_cli.repair_coding_bootstrap(commit, paths)
    assert result["ok"] is True
    assert result["context_required"] is False
    assert result["review_authority"] is False
    assert paths.coding_config.read_bytes() == config_source.read_bytes()
    release = paths.runtime_root / "releases" / commit
    assert all(
        path.stat(follow_symlinks=False).st_uid == paths.context_install_uid
        and path.stat(follow_symlinks=False).st_gid == paths.context_install_gid
        for path in (release, *release.rglob("*"))
    )


def test_source_bootstrap_launcher_does_not_depend_on_selected_runtime() -> None:
    launcher = (ROOT / "bin/tgw-coding-bootstrap").read_text(encoding="utf-8")
    assert launcher.splitlines()[0] == "#!/usr/bin/python3.13 -IS"
    assert "coding-runtime/current" not in launcher
    assert "/opt/TGW/.venvs" not in launcher
    assert "tgw_context" not in launcher.lower()
    assert "protected-review" not in launcher.lower()
    assert "admission" not in launcher.lower()
    assert "onboarding" not in launcher.lower()
    assert "/usr/local/sbin/tgw-coding-bootstrap" in launcher
    assert "st_uid != 0" in launcher
    assert "PYTHONPATH" in launcher
    assert "PYTHONSAFEPATH" in launcher
    assert 'Path("/usr/bin/python3.13")' in launcher
    assert 'Path("/usr/sbin")' in launcher
    assert 'Path("/usr/sbin/runuser")' in launcher
    assert 'Path("/var/tmp")' in launcher
    assert '"tgw.doctor_cli"' in launcher
    assert "_extract_exact" in launcher
    assert '"--repair"' in launcher
    assert "_unprivileged_status()" in launcher
    assert '_git("status"' not in launcher


def test_source_bootstrap_routes_privileged_repair_through_exact_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launcher = ROOT / "bin/tgw-coding-bootstrap"
    loader = importlib.machinery.SourceFileLoader(
        "tgw_exact_repair_bootstrap_test", str(launcher)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    commit = "a" * 40
    tree = "b" * 40
    staging = tmp_path / "private-exact-candidate"
    staging.mkdir()
    observed: list[str] = []

    monkeypatch.setattr(module, "_require_installed_root_copy", lambda: None)
    monkeypatch.setattr(module, "_unprivileged_status", lambda: b"")
    monkeypatch.setattr(
        module,
        "_git",
        lambda *_args: (commit + "\n").encode(),
    )
    monkeypatch.setattr(module, "_extract_exact", lambda *_args: tree)
    monkeypatch.setattr(module.tempfile, "mkdtemp", lambda **_kwargs: str(staging))
    monkeypatch.setattr(module.shutil, "rmtree", lambda *_args: None)

    def run(command, **_kwargs):
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)

    assert module.main(["--commit", commit, "--repair", "context"]) == 0
    assert observed == [
        str(module._PYTHON),
        "-S",
        "-P",
        "-m",
        "tgw.doctor_cli",
        "repair",
        "context",
        "--commit",
        commit,
        "--json",
    ]


def test_repair_cli_succeeds_with_non_failing_diagnostic_attention(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        doctor_cli,
        "repair",
        lambda *_args, **_kwargs: {
            "ok": True,
            "diagnosis": {"ok": True, "state": "ATTENTION", "exit_code": 1},
        },
    )

    result = doctor_cli.main(
        ["repair", "context", "--commit", "a" * 40, "--json"]
    )

    assert result == 0
    assert '"state": "ATTENTION"' in capsys.readouterr().out


def test_unix_access_probes_support_roots_and_inflight_worktrees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = doctor_cli.DoctorPaths(
        repository=Path("/repo"), worktrees=Path("/worktrees")
    )
    active = Path("/worktrees/todo-1921-plan-abc123")
    support = {
        "preservation_archive_root": {"path": "/support/preservation", "exact": True},
        "runner_state_root": {"path": "/support/runner", "exact": True},
        "lifecycle_root": {"path": "/support/lifecycle", "exact": True},
        "root_effect_root": {"path": "/support/root-effect", "exact": True},
    }
    support_calls = []
    path_calls = []
    monkeypatch.setattr(doctor_cli, "_operator_actor", lambda: "codex")
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=77, gr_mem=["codex", "db"]),
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_gid=77),
    )
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda _name, _gid: [77])
    monkeypatch.setattr(
        doctor_cli,
        "_actor_path_access",
        lambda actor, path: path_calls.append((actor, str(path))) or True,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_actor_path_access_flags",
        lambda actor, path, flags: support_calls.append(
            (actor, str(path), tuple(flags))
        )
        or True,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_shared_git_directory",
        lambda path, _gid: {"path": str(path), "exact": True},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_inspect_shared_git_trees",
        lambda *_args, **_kwargs: {
            "exact": True,
            "trees": {},
            "outside_configured_root_untouched": [],
            "linked_worktrees_inspected": False,
            "linked_worktree_count": 1,
        },
    )
    monkeypatch.setattr(doctor_cli, "_coding_support_roots", lambda *_args: support)
    monkeypatch.setattr(doctor_cli, "_active_coding_worktrees", lambda _paths: [active])

    result = doctor_cli.check_unix_access(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["active_coding_worktrees"] == [str(active)]
    assert ("codex", str(active)) in path_calls
    assert ("db", str(active)) in path_calls
    assert (
        "codex",
        "/support/root-effect",
        ("-r", "-x"),
    ) in support_calls
    assert (
        "db",
        "/support/root-effect",
        ("-r", "-w", "-x"),
    ) in support_calls


def test_active_worktree_rows_require_matching_managed_absolute_paths(
    tmp_path: Path,
) -> None:
    root = tmp_path / "worktrees"
    worktree = root / "todo-1921-plan-abc123"
    worktree.mkdir(parents=True)
    paths = doctor_cli.DoctorPaths(worktrees=root)
    value = str(worktree)

    result = doctor_cli._validate_active_coding_worktree_rows(
        paths,
        [
            {
                "job_id": "job-1",
                "payload": {
                    "worktree": value,
                    "task_spec": {"worktree": value},
                },
                "entity_id": value,
            }
        ],
    )

    assert result == [worktree]


@pytest.mark.parametrize("value", ["", ".", "todo-1921-plan-abc123"])
def test_active_worktree_rows_reject_empty_or_relative_paths(
    tmp_path: Path, value: str
) -> None:
    root = tmp_path / "worktrees"
    (root / "todo-1921-plan-abc123").mkdir(parents=True)
    paths = doctor_cli.DoctorPaths(worktrees=root)

    with pytest.raises(doctor_cli.DoctorError, match="malformed|managed absolute"):
        doctor_cli._validate_active_coding_worktree_rows(
            paths,
            [
                {
                    "job_id": "job-1",
                    "payload": {"worktree": value},
                    "entity_id": None,
                }
            ],
        )


def test_active_worktree_rows_reject_conflicting_references(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    first = root / "todo-1921-plan-abc123"
    second = root / "todo-1922-plan-def456"
    first.mkdir(parents=True)
    second.mkdir()
    paths = doctor_cli.DoctorPaths(worktrees=root)

    with pytest.raises(doctor_cli.DoctorError, match="conflicting"):
        doctor_cli._validate_active_coding_worktree_rows(
            paths,
            [
                {
                    "job_id": "job-1",
                    "payload": {
                        "worktree": str(first),
                        "task_spec": {"worktree": str(second)},
                    },
                    "entity_id": str(first),
                }
            ],
        )


@pytest.mark.parametrize("entity_id", [None, "", ".", "todo-1921-plan-abc123"])
def test_active_worktree_rows_reject_malformed_entity_reference(
    tmp_path: Path, entity_id: str | None
) -> None:
    root = tmp_path / "worktrees"
    worktree = root / "todo-1921-plan-abc123"
    worktree.mkdir(parents=True)
    paths = doctor_cli.DoctorPaths(worktrees=root)

    with pytest.raises(doctor_cli.DoctorError, match="entity_id"):
        doctor_cli._validate_active_coding_worktree_rows(
            paths,
            [
                {
                    "job_id": "job-1",
                    "payload": {"worktree": str(worktree)},
                    "entity_id": entity_id,
                }
            ],
        )


def test_active_worktree_rows_reject_nested_matching_directory(tmp_path: Path) -> None:
    root = tmp_path / "worktrees"
    nested = root / "container" / "todo-1921-plan-abc123"
    nested.mkdir(parents=True)
    paths = doctor_cli.DoctorPaths(worktrees=root)

    with pytest.raises(doctor_cli.DoctorError, match="managed absolute"):
        doctor_cli._validate_active_coding_worktree_rows(
            paths,
            [
                {
                    "job_id": "job-1",
                    "payload": {"worktree": str(nested)},
                    "entity_id": None,
                }
            ],
        )


def test_unix_access_fails_when_operator_cannot_reach_support_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = doctor_cli.DoctorPaths(
        repository=Path("/repo"), worktrees=Path("/worktrees")
    )
    monkeypatch.setattr(doctor_cli, "_operator_actor", lambda: "codex")
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=77, gr_mem=["codex", "db"]),
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_gid=77),
    )
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda _name, _gid: [77])
    monkeypatch.setattr(doctor_cli, "_actor_path_access", lambda *_args: True)
    monkeypatch.setattr(
        doctor_cli,
        "_actor_path_access_flags",
        lambda actor, path, _flags: not (
            actor == "codex" and str(path) == "/support/lifecycle"
        ),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_shared_git_directory",
        lambda path, _gid: {"path": str(path), "exact": True},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_inspect_shared_git_trees",
        lambda *_args, **_kwargs: {"exact": True, "trees": {}},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_coding_support_roots",
        lambda *_args: {
            "lifecycle_root": {"path": "/support/lifecycle", "exact": True}
        },
    )
    monkeypatch.setattr(doctor_cli, "_active_coding_worktrees", lambda _paths: [])

    result = doctor_cli.check_unix_access(paths)

    assert result["state"] == "FAIL"
    assert result["evidence"]["actors"]["codex"]["exact"] is False


def test_bootstrap_release_ownership_promotion_rejects_symlink(
    tmp_path: Path,
) -> None:
    release = tmp_path / "runtime" / "releases" / "release"
    release.mkdir(parents=True)
    release.parent.parent.chmod(0o750)
    release.parent.chmod(0o750)
    (release / "linked").symlink_to("missing")
    release.chmod(0o555)

    try:
        with pytest.raises(
            doctor_cli.DoctorError,
            match="bootstrap release cannot be promoted safely",
        ):
            doctor_cli._promote_bootstrap_release_ownership(
                release,
                uid=os.getuid(),
                gid=os.getgid(),
            )
    finally:
        release.chmod(0o755)
        (release / "linked").unlink(missing_ok=True)


def test_bootstrap_release_ownership_promotion_rejects_hard_link(
    tmp_path: Path,
) -> None:
    release = tmp_path / "runtime" / "releases" / "release"
    release.mkdir(parents=True)
    release.parent.parent.chmod(0o750)
    release.parent.chmod(0o750)
    outside = tmp_path / "outside"
    outside.write_text("bound bytes", encoding="utf-8")
    outside.chmod(0o444)
    os.link(outside, release / "linked")
    release.chmod(0o555)
    before = outside.stat(follow_symlinks=False)

    try:
        with pytest.raises(
            doctor_cli.DoctorError,
            match="bootstrap release cannot be promoted safely",
        ):
            doctor_cli._promote_bootstrap_release_ownership(
                release,
                uid=os.getuid(),
                gid=os.getgid(),
            )
        after = outside.stat(follow_symlinks=False)
        assert (after.st_uid, after.st_gid, after.st_ino) == (
            before.st_uid,
            before.st_gid,
            before.st_ino,
        )
    finally:
        release.chmod(0o755)
        outside.chmod(0o644)


def test_bootstrap_release_ownership_promotion_never_path_chowns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "runtime" / "releases" / "release"
    nested = release / "nested"
    nested.mkdir(parents=True)
    release.parent.parent.chmod(0o750)
    release.parent.chmod(0o750)
    payload = nested / "payload"
    payload.write_text("exact bytes", encoding="utf-8")
    payload.chmod(0o444)
    nested.chmod(0o555)
    release.chmod(0o555)
    runtime_parent_before = release.parent.parent.stat(follow_symlinks=False)
    releases_parent_before = release.parent.stat(follow_symlinks=False)
    real_open = os.open
    fresh_directory_scans = 0

    def track_open(path, *args, **kwargs):
        nonlocal fresh_directory_scans
        if path == "." and kwargs.get("dir_fd") is not None:
            fresh_directory_scans += 1
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(
        os,
        "chown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("pathname chown must not be used")
        ),
    )
    monkeypatch.setattr(doctor_cli.os, "open", track_open)
    doctor_cli._promote_bootstrap_release_ownership(
        release,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    assert payload.read_text(encoding="utf-8") == "exact bytes"
    assert payload.stat(follow_symlinks=False).st_nlink == 1
    assert fresh_directory_scans >= 4
    runtime_parent_after = release.parent.parent.stat(follow_symlinks=False)
    releases_parent_after = release.parent.stat(follow_symlinks=False)
    assert (
        runtime_parent_after.st_uid,
        runtime_parent_after.st_gid,
        stat.S_IMODE(runtime_parent_after.st_mode),
    ) == (
        runtime_parent_before.st_uid,
        runtime_parent_before.st_gid,
        stat.S_IMODE(runtime_parent_before.st_mode),
    )
    assert (
        releases_parent_after.st_uid,
        releases_parent_after.st_gid,
        stat.S_IMODE(releases_parent_after.st_mode),
    ) == (
        releases_parent_before.st_uid,
        releases_parent_before.st_gid,
        stat.S_IMODE(releases_parent_before.st_mode),
    )


def test_source_bootstrap_shebang_ignores_hostile_python_startup(
    tmp_path: Path,
) -> None:
    launcher = ROOT / "bin/tgw-coding-bootstrap"
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    import_marker = tmp_path / "imported"
    site_marker = tmp_path / "site-loaded"
    (hostile / "argparse.py").write_text(
        f"from pathlib import Path\nPath({str(import_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    (hostile / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath({str(site_marker)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(hostile)
    completed = subprocess.run(
        [str(launcher), "--commit", "0" * 40],
        cwd=hostile,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "tgw-coding-bootstrap:" in completed.stderr
    assert not import_marker.exists()
    assert not site_marker.exists()


def test_release_verification_imports_in_isolated_bootstrap_interpreter() -> None:
    environment = {
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(ROOT / "src"),
        "PYTHONSAFEPATH": "1",
    }
    completed = subprocess.run(
        [
            "/usr/bin/python3.13",
            "-S",
            "-P",
            "-c",
            "from tgw.release_installer import verify; assert callable(verify)",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_source_bootstrap_reconstructs_only_exact_regular_git_files(
    tmp_path: Path,
) -> None:
    launcher = ROOT / "bin/tgw-coding-bootstrap"
    loader = importlib.machinery.SourceFileLoader("tgw_exact_bootstrap_test", str(launcher))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "bootstrap")
    _git(repository, "config", "user.email", "bootstrap@example.invalid")
    (repository / "regular").write_bytes(b"exact\n")
    executable = repository / "executable"
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "exact")
    commit = _git(repository, "rev-parse", "HEAD")
    module._REPOSITORY = repository
    destination = tmp_path / "exact"
    destination.mkdir()

    tree = module._extract_exact(commit, destination)

    assert tree == _git(repository, "rev-parse", "HEAD^{tree}")
    assert (destination / "regular").read_bytes() == b"exact\n"
    assert stat.S_IMODE((destination / "regular").stat().st_mode) == 0o644
    assert stat.S_IMODE((destination / "executable").stat().st_mode) == 0o755

    link = repository / "link"
    link.symlink_to("regular")
    _git(repository, "add", "link")
    _git(repository, "commit", "-m", "unsafe link")
    linked_commit = _git(repository, "rev-parse", "HEAD")
    with pytest.raises(module.BootstrapError, match="link or unsupported"):
        module._extract_exact(linked_commit, tmp_path / "refused")


def test_source_bootstrap_imports_only_from_private_exact_tree(tmp_path: Path) -> None:
    launcher = ROOT / "bin/tgw-coding-bootstrap"
    loader = importlib.machinery.SourceFileLoader("tgw_safe_bootstrap_test", str(launcher))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    canonical = tmp_path / "canonical"
    exact = tmp_path / "exact"
    for root, marker in ((canonical, "mutable"), (exact / "src", "exact")):
        package = root / "tgw"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "bootstrap_probe.py").write_text(
            f"MARKER = {marker!r}\n", encoding="utf-8"
        )
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-P",
            "-c",
            "import tgw.bootstrap_probe as p; print(p.MARKER); print(p.__file__)",
        ],
        cwd=canonical,
        env=module._candidate_environment(exact),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    output = completed.stdout.splitlines()
    assert output[0] == "exact"
    assert Path(output[1]).is_relative_to(exact / "src")


def test_context_managed_parents_allow_a_non_install_group(tmp_path: Path) -> None:
    managed_root = tmp_path / "tgw-lib"
    generations = managed_root / "context-entrypoints/generations"
    generations.mkdir(parents=True)
    for directory in (managed_root, generations.parent, generations):
        directory.chmod(0o755)
    paths = doctor_cli.DoctorPaths(
        context_generation_root=generations,
        context_generation_pointer=generations.parent / "current",
        context_install_uid=os.getuid(),
        context_install_gid=os.getgid() + 1,
    )

    doctor_cli._validate_context_parent(generations, paths)


def test_context_snapshot_binds_task_cursor_and_canonical_source(tmp_path: Path) -> None:
    paths, head, tree = _fixture(tmp_path)

    result = doctor_cli.check_context_snapshot(paths)

    assert result["state"] == "PASS"
    assert result["evidence"]["source_commit"] == head
    assert result["evidence"]["source_tree"] == tree


def test_context_snapshot_detects_cursor_drift_with_exact_repair(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)

    result = doctor_cli.check_context_snapshot(paths)

    assert result["state"] == "FAIL"
    assert result["operator_action"] == (
        "sudo -n /usr/local/sbin/tgw-coding-bootstrap "
        f"--commit {head} --repair context"
    )


@pytest.mark.parametrize("wrong_mode", [0o400, 0o440, 0o644])
def test_context_snapshot_detects_metadata_drift(
    tmp_path: Path, wrong_mode: int
) -> None:
    paths, head, _tree = _fixture(tmp_path)
    paths.context_snapshot.chmod(wrong_mode)

    result = doctor_cli.check_context_snapshot(paths)

    assert result["state"] == "FAIL"
    assert "install uid/gid 0444" in result["detail"]
    assert result["operator_action"] == (
        "sudo -n /usr/local/sbin/tgw-coding-bootstrap "
        f"--commit {head} --repair context"
    )


def test_doctor_selected_parser_has_exact_inline_non_ascii_byte_parity() -> None:
    task = {
        "schema": "tgw-current-task/v1",
        "plan": {"approved_commit": "a" * 40},
        "implementation": {"development_source": {"commit": "b" * 40, "next_leaf": "leaf"}},
        "operator_note": "café 東京",
    }
    cursor = {
        "schema": "tgw-plan-execution-cycle-cursor/v1",
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "resolved": {"next_treatment": "todo:leaf"},
    }
    value = {
        "schema": "tgw-current-context-snapshot/v1",
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "active_capability": "leaf",
        "active_treatment": "todo:leaf",
        "task": task,
        "cursor": cursor,
    }
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    parser = ROOT / "src/tgw/current_context_snapshot.py"

    assert doctor_cli._validate_snapshot(value, raw, parser_path=parser)["task"] == task
    with pytest.raises(doctor_cli.DoctorError, match="not canonical"):
        doctor_cli._validate_snapshot(value, json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n", parser_path=parser)


def _real_99416_legacy_parser(tmp_path: Path, suffix: str = "") -> Path:
    parser = tmp_path / "legacy_snapshot.py"
    source = subprocess.run(
        ["git", "show", "99416bfb:src/tgw/current_context_snapshot.py"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    parser.write_text(source + suffix, encoding="utf-8")
    return parser


def _legacy_fixture_value(paths: doctor_cli.DoctorPaths) -> dict[str, Any]:
    task = json.loads(paths.context_task.read_text(encoding="utf-8"))
    cursor = json.loads(paths.context_cursor.read_text(encoding="utf-8"))
    value = {
        "schema": "tgw-current-context-snapshot/v1",
        "plan_commit": cursor["plan_commit"],
        "source_commit": cursor["source_commit"],
        "source_tree": cursor["source_tree"],
        "active_capability": task["implementation"]["development_source"]["next_leaf"],
        "active_treatment": cursor["resolved"]["next_treatment"],
        "task": task,
        "cursor": cursor,
    }
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return value


def test_doctor_real_99416_legacy_parser_accepts_canonical_non_ascii(
    tmp_path: Path,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    value = _legacy_fixture_value(paths)
    value["task"]["operator_note"] = "café 東京"
    body = dict(value)
    body.pop("snapshot_sha256")
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode() + b"\n"
    assert doctor_cli._validate_snapshot(
        value, raw, parser_path=_real_99416_legacy_parser(tmp_path)
    )["task"]["operator_note"] == "café 東京"


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda raw: b" " + raw, "wire format is invalid"),
        (lambda raw: raw[:-1] + b" \n", "wire format is invalid"),
        (lambda raw: raw[:-1], "wire format is invalid"),
        (lambda _raw: b"{malformed}\n", "snapshot is invalid"),
        (lambda raw: raw + b"{}\n", "snapshot is invalid"),
        (lambda _raw: b"x" * (256 * 1024 + 1), "wire format is invalid"),
        (
            lambda raw: json.dumps(
                json.loads(raw),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode() + b"\n",
            "wire format is invalid",
        ),
    ],
    ids=[
        "leading", "trailing", "missing-lf", "malformed", "extra-stream",
        "oversized", "alternate-non-ascii-escaping",
    ],
)
def test_doctor_real_99416_legacy_parser_rejects_invalid_wire(
    tmp_path: Path, mutate: Any, error: str
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    value = _legacy_fixture_value(paths)
    value["task"]["operator_note"] = "café 東京"
    body = dict(value)
    body.pop("snapshot_sha256")
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        .encode()
    ).hexdigest()
    raw = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode() + b"\n"
    with pytest.raises(doctor_cli.DoctorError, match=error):
        doctor_cli._validate_snapshot(
            value, mutate(raw), parser_path=_real_99416_legacy_parser(tmp_path)
        )


@pytest.mark.parametrize(
    ("suffix", "error"),
    [
        ("\nparse_bytes = None\n", "parser API is invalid"),
        ("\nparse_bytes = parse\n", "parser API is invalid"),
        ("\nparse_bytes = parse\nMAX_SNAPSHOT_BYTES = 1\n", "size bounds differ"),
        ("\ndel parse\n", "parser API is invalid"),
        ("\nparse = None\n", "parser API is invalid"),
    ],
    ids=[
        "non-callable-parse-bytes", "parse-bytes-without-maximum",
        "wrong-maximum", "missing-legacy-parse", "non-callable-legacy-parse",
    ],
)
def test_doctor_rejects_invalid_real_99416_parser_api_shape(
    tmp_path: Path, suffix: str, error: str
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    value = _legacy_fixture_value(paths)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with pytest.raises(doctor_cli.DoctorError, match=error):
        doctor_cli._validate_snapshot(
            value, raw, parser_path=_real_99416_legacy_parser(tmp_path, suffix)
        )


def test_context_process_match_ignores_parent_shell_command_text() -> None:
    assert doctor_cli._is_context_process(["python3", "/opt/TGW/tgw-lib/bin/tgw-context-mcp"])
    assert doctor_cli._is_context_process(["python3", "-m", "tgw.context_mcp_server"])
    assert not doctor_cli._is_context_process(["bash", "-c", "/opt/TGW/tgw-lib/bin/tgw-context-mcp"])


def test_context_cold_probe_keeps_stdin_open_until_eof_sensitive_fourth_response(
    tmp_path: Path,
) -> None:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    expected = {
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "snapshot_sha256": "sha256:" + "d" * 64,
        "active_capability": "capability@1",
        "active_treatment": "treatment@1",
        "task": {"durable_history": {"receipts": "bound"}},
    }
    schema_fields = {
        "tgw_context_status": (), "tgw_context_current_task": (),
        "tgw_context_bundle": ("task", "limit"),
        "tgw_context_code_graph": ("operation", "query", "limit"),
        "tgw_context_plan_graph": ("task", "receiver", "operation", "limit"),
        "tgw_context_plan_source": ("path", "start_line", "max_lines", "authority"),
        "tgw_context_onboarding": ("actor",),
        "tgw_context_runbooks": ("query", "path", "start_line", "max_lines", "limit", "authority"),
    }
    schemas = {
        name: {
            "type": "object",
            "properties": {
                field: {"type": "integer" if field in {"limit", "start_line", "max_lines"} else "string"}
                for field in fields
            },
            **({"required": [next(iter(fields))]} if name in {"tgw_context_plan_graph", "tgw_context_plan_source", "tgw_context_onboarding"} else {}),
        }
        for name, fields in schema_fields.items()
    }
    output_schemas = {
        name: {
            "properties": {"result": {"title": "Result", "type": "string"}},
            "required": ["result"],
            "title": f"{name}Output",
            "type": "object",
        }
        for name in schema_fields
    }
    launcher = tmp_path / "tgw-context-mcp"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,select,sys,time\n"
        "if not os.environ.get('TGW_OLD_BATCH'): time.sleep(16.1)\n"
        "actor=" + repr(actor) + "\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); i=r.get('id'); m=r.get('method')\n"
        " if i is None: continue\n"
        " if m=='tools/list': result={'tools':[{'name':n,'description':'read only','inputSchema':s,'outputSchema':o} for (n,s),(_,o) in zip("
        + repr(sorted(schemas.items())) + "," + repr(sorted(output_schemas.items())) + ")]}\n"
        " elif m=='tools/call':\n"
        "  name=r['params']['name']\n"
        "  if name=='tgw_context_current_task':\n"
        "   readable,_,_=select.select([sys.stdin],[],[],0.1)\n"
        "   if readable and sys.stdin.read()=='': continue\n"
        "  if name=='tgw_context_current_task': value={'actor':actor,"
        "'receiver':actor,'plan':{'approved_commit':'a'*40},"
        "'implementation':{'development_source':{'commit':'b'*40}},"
            "'context':{'plan_commit':'a'*40,'source_commit':'b'*40,"
            "'source_tree':'c'*40,'snapshot_sha256':'sha256:'+'d'*64,'active_capability':'capability@1','active_treatment':'treatment@1'},'durable_history':{'receipts':'bound'}}\n"
        "  else: value={'ok':True,'actor':actor,"
        "'generation_status':{'state':'CURRENT'},"
        "'current_context':{'plan_commit':'a'*40,'source_commit':'b'*40,"
            "'source_tree':'c'*40,'snapshot_sha256':'sha256:'+'d'*64,'active_capability':'capability@1','active_treatment':'treatment@1'}}\n"
        "  result={'isError':False,'content':[{'type':'text','text':json.dumps(value)}]}\n"
        " else: result={'protocolVersion':'2025-03-26','capabilities':{},'serverInfo':{'name':'fixture','version':'1'}}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':i,'result':result}),flush=True)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    old_requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "tgw_context_status", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "tgw_context_current_task", "arguments": {}}},
    ]
    old_batch = subprocess.run(
        [str(launcher)],
        input="".join(json.dumps(item) + "\n" for item in old_requests),
        text=True,
        capture_output=True,
        check=False,
        env={"TGW_OLD_BATCH": "1"},
        timeout=2,
    )
    assert {json.loads(line)["id"] for line in old_batch.stdout.splitlines()} == {1, 2, 3}

    result = doctor_cli._probe_context_stdio(launcher, actor, expected)

    assert result["actor"] == actor
    assert result["methods"] == [
        "initialize",
        "tools/list",
        *sorted(schema_fields),
    ]
    assert result["generation"] == "CURRENT"
    assert result["timeout_seconds"] == doctor_cli._CONTEXT_COLD_PROBE_BUDGET_SECONDS


def test_context_cold_probe_response_beyond_budget_is_terminated(tmp_path: Path) -> None:
    launcher = tmp_path / "tgw-context-mcp"
    pid_file = tmp_path / "probe.pid"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import os,time\n"
        f"open({str(pid_file)!r},'w').write(str(os.getpid()))\n"
        f"time.sleep({doctor_cli._CONTEXT_COLD_PROBE_BUDGET_SECONDS + 1})\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    with pytest.raises(
        doctor_cli.DoctorError,
        match=r"timed out after 30s and was terminated",
    ):
        doctor_cli._probe_context_stdio(
            launcher,
            pwd.getpwuid(os.geteuid()).pw_name,
            {},
        )
    probe_pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(probe_pid, 0)


def test_context_cold_probe_reports_early_exit_and_reaps_child(tmp_path: Path) -> None:
    launcher = tmp_path / "tgw-context-mcp"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import sys\n"
        "sys.stderr.write('fixture stopped early\\n')\n"
        "raise SystemExit(7)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    with pytest.raises(
        doctor_cli.DoctorError,
        match=r"cold probe exited 7: fixture stopped early",
    ):
        doctor_cli._probe_context_stdio(
            launcher,
            pwd.getpwuid(os.geteuid()).pw_name,
            {},
            timeout=2,
        )


@pytest.mark.parametrize(
    ("trailing", "error"),
    [
        (b'{"jsonrpc":"2.0"', "incomplete trailing output"),
        (b"not-json\n", "returned invalid JSON"),
    ],
)
def test_context_cold_probe_rejects_trailing_output_after_all_responses(
    tmp_path: Path, trailing: bytes, error: str
) -> None:
    launcher = tmp_path / "tgw-context-mcp"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import json,sys,time\n"
        f"trailing={trailing!r}\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); request_id=request.get('id')\n"
        " if request_id is not None:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':request_id,'result':{}}),flush=True)\n"
        " if request_id == 4: break\n"
        "sys.stdin.read()\n"
        "time.sleep(0.45)\n"
        "sys.stdout.buffer.write(trailing); sys.stdout.buffer.flush()\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    started = time.monotonic()
    with pytest.raises(doctor_cli.DoctorError, match=error):
        doctor_cli._probe_context_stdio(
            launcher,
            pwd.getpwuid(os.geteuid()).pw_name,
            {},
            timeout=2,
        )
    assert time.monotonic() - started >= 0.4


def test_context_cold_probe_cleanup_orders_signals_before_leader_reap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    launcher.write_text(
        "#!" + sys.executable + "\nimport sys,time\nfor _ in sys.stdin: pass\ntime.sleep(10)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)
    events: list[tuple[str, int]] = []
    real_killpg = doctor_cli.os.killpg
    real_waitpid = doctor_cli.os.waitpid

    def killpg(pgid: int, sig: int) -> None:
        events.append(("killpg", sig))
        real_killpg(pgid, sig)

    def waitpid(pid: int, options: int):
        events.append(("waitpid", pid))
        return real_waitpid(pid, options)

    monkeypatch.setattr(doctor_cli.os, "killpg", killpg)
    monkeypatch.setattr(doctor_cli.os, "waitpid", waitpid)
    monkeypatch.setattr(
        doctor_cli, "_CONTEXT_COLD_PROBE_TERMINATE_GRACE_SECONDS", 0.01
    )
    with pytest.raises(doctor_cli.DoctorError):
        doctor_cli._probe_context_stdio(
            launcher, pwd.getpwuid(os.geteuid()).pw_name, {}, timeout=1
        )
    term = events.index(("killpg", signal.SIGTERM))
    kill = events.index(("killpg", signal.SIGKILL))
    leader_wait = next(index for index, event in enumerate(events) if event[0] == "waitpid" and event[1] > 0)
    assert term < kill < leader_wait
    assert not any(event[0] == "killpg" for event in events[leader_wait + 1 :])


def test_context_cold_probe_cleanup_faults_continue_and_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    launcher.write_text(
        "#!" + sys.executable + "\nimport sys,time\nfor _ in sys.stdin: pass\ntime.sleep(10)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)
    events: list[str] = []
    real_popen = doctor_cli.subprocess.Popen
    real_killpg = doctor_cli.os.killpg
    real_waitpid = doctor_cli.os.waitpid
    real_restore = doctor_cli._restore_linux_child_subreaper

    class FaultingStdin:
        def __init__(self, stream) -> None:
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def close(self) -> None:
            events.append("stdin-close")
            self.stream.close()
            raise OSError("injected stdin close failure")

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        process.stdin = FaultingStdin(process.stdin)
        return process

    def killpg(pgid: int, sig: int) -> None:
        events.append(signal.Signals(sig).name)
        real_killpg(pgid, sig)
        raise OSError(f"injected {signal.Signals(sig).name} failure")

    def waitpid(pid: int, options: int):
        events.append("leader-reap" if pid > 0 else "group-reap")
        return real_waitpid(pid, options)

    def restore(value: int) -> None:
        events.append("restore")
        real_restore(value)

    monkeypatch.setattr(doctor_cli.subprocess, "Popen", popen)
    monkeypatch.setattr(doctor_cli.os, "killpg", killpg)
    monkeypatch.setattr(doctor_cli.os, "waitpid", waitpid)
    monkeypatch.setattr(doctor_cli, "_restore_linux_child_subreaper", restore)
    monkeypatch.setattr(
        doctor_cli, "_CONTEXT_COLD_PROBE_TERMINATE_GRACE_SECONDS", 0.01
    )
    with pytest.raises(doctor_cli.DoctorError, match="stdin close failure.*SIGTERM.*SIGKILL"):
        doctor_cli._probe_context_stdio(
            launcher, pwd.getpwuid(os.geteuid()).pw_name, {}, timeout=1
        )
    assert events.index("SIGTERM") < events.index("SIGKILL") < events.index("leader-reap")
    assert "group-reap" in events
    assert events[-1] == "restore"


def test_context_staged_probe_fstat_failure_leaves_caller_owned_snapshot_fd_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.write_text("{}", encoding="utf-8")
    descriptor = os.open(snapshot, os.O_RDONLY)
    real_fstat = doctor_cli.os.fstat
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli.os, "open", lambda *args, **kwargs: descriptor)
    monkeypatch.setattr(
        doctor_cli.os,
        "fstat",
        lambda fd: (_ for _ in ()).throw(OSError("injected fstat failure")),
    )
    with pytest.raises(OSError, match="injected fstat failure"):
        doctor_cli._probe_context_stdio_locked(
            Path("/launcher"), "actor", {}, staged_snapshot_descriptor=descriptor
        )
    real_fstat(descriptor)
    os.close(descriptor)


def test_real_context_launcher_distinguishes_preflight_0400_from_live_0444(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_bytes(b"{}")
    launcher = ROOT / "scripts/tgw_context_debian_stdio.py"
    # Exercise the real launcher while making only the unavailable root-owned
    # fixture metadata explicit. The descriptor's real mode and bytes remain
    # kernel-backed, so 0444 versus 0400 reaches the production preflight check.
    wrapper = (
        "import os,runpy,sys;"
        "real_fstat=os.fstat;"
        "os.fstat=lambda fd: os.stat_result((*real_fstat(fd)[:4],0,0,*real_fstat(fd)[6:]));"
        "runpy.run_path(sys.argv[1],run_name='__main__')"
    )

    errors: dict[int, str] = {}
    for mode in (0o444, 0o400):
        snapshot.chmod(mode)
        descriptor = os.open(snapshot, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-c", wrapper, str(launcher)],
                input=b"",
                capture_output=True,
                check=False,
                pass_fds=(descriptor,),
                env={
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TGW_CONTEXT_PREFLIGHT_SNAPSHOT_FD": str(descriptor),
                },
            )
        finally:
            os.close(descriptor)
        assert result.returncode != 0
        errors[mode] = result.stderr.decode("utf-8", errors="replace")

    assert "not stable protected root data" in errors[0o444]
    assert "not stable protected root data" not in errors[0o400]
    assert "current TGW context snapshot schema is invalid" in errors[0o400]


def test_context_cold_probe_kills_real_forked_descendant(tmp_path: Path) -> None:
    descendant_pid = tmp_path / "descendant.pid"
    launcher = tmp_path / "tgw-context-mcp"
    launcher.write_text(
        "#!" + sys.executable + "\n"
        "import json,os,signal,sys,time\n"
        f"pid_path={str(descendant_pid)!r}\n"
        "child=os.fork()\n"
        "if child == 0:\n"
        " os.close(0)\n"
        " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        " open(pid_path,'w').write(str(os.getpid()))\n"
        " while True: time.sleep(1)\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); request_id=request.get('id')\n"
        " if request_id is not None:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':request_id,'result':{}}),flush=True)\n"
        " if request_id == 4: break\n"
        "sys.stdin.read()\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    with pytest.raises(doctor_cli.DoctorError, match="timed out after 2s"):
        doctor_cli._probe_context_stdio(
            launcher,
            pwd.getpwuid(os.geteuid()).pw_name,
            {},
            timeout=2,
        )
    pid = int(descendant_pid.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_context_cold_probe_serializes_concurrent_subreaper_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    observations: list[str] = []

    def locked(*args, **kwargs):
        observations.append("enter")
        entered.set()
        assert release.wait(2)
        observations.append("leave")
        return {"actor": args[1]}

    monkeypatch.setattr(doctor_cli, "_probe_context_stdio_locked", locked)
    actor = pwd.getpwuid(os.geteuid()).pw_name
    first = threading.Thread(
        target=doctor_cli._probe_context_stdio,
        args=(Path("/first"), actor, {}),
    )
    second = threading.Thread(
        target=doctor_cli._probe_context_stdio,
        args=(Path("/second"), actor, {}),
    )
    first.start()
    assert entered.wait(2)
    second.start()
    assert observations == ["enter"]
    release.set()
    first.join(2)
    second.join(2)
    assert observations == ["enter", "leave", "enter", "leave"]


def test_context_cold_probe_restore_failure_is_reported_after_state_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = 1
    calls = 0

    def set_state(value: int) -> None:
        nonlocal state, calls
        calls += 1
        state = value
        if calls == 1:
            raise OSError("reported restore failure")

    monkeypatch.setattr(doctor_cli, "_set_linux_child_subreaper", set_state)
    monkeypatch.setattr(doctor_cli, "_linux_child_subreaper", lambda: state)
    with pytest.raises(doctor_cli.DoctorError, match="cannot restore"):
        doctor_cli._restore_linux_child_subreaper(0)
    assert state == 0
    assert calls == 2


def test_context_staged_probe_invalid_actor_leaves_caller_owned_snapshot_fd_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    descriptor = os.open(snapshot, os.O_RDONLY)
    real_open = doctor_cli.os.open
    real_fstat = doctor_cli.os.fstat
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli.os, "open", lambda *args, **kwargs: descriptor)
    monkeypatch.setattr(
        doctor_cli.os,
        "fstat",
        lambda fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0),
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda actor: (_ for _ in ()).throw(KeyError(actor)),
    )
    with pytest.raises(KeyError):
        doctor_cli._probe_context_stdio_locked(
            Path("/launcher"), "invalid-actor", {}, staged_snapshot_descriptor=descriptor
        )
    real_fstat(descriptor)
    os.close(descriptor)
    monkeypatch.setattr(doctor_cli.os, "open", real_open)


def test_context_staged_probe_rejects_nonmember_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot.chmod(0o444)
    target = SimpleNamespace(pw_name="actor", pw_uid=1234, pw_gid=1234)
    coding_group = SimpleNamespace(gr_name="tgw-coders", gr_gid=983)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        doctor_cli.os,
        "fstat",
        lambda _fd: SimpleNamespace(
                st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0
        ),
    )
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(doctor_cli.grp, "getgrnam", lambda _name: coding_group)
    monkeypatch.setattr(doctor_cli.grp, "getgrgid", lambda _gid: coding_group)
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda *_args: [1234])
    monkeypatch.setattr(
        doctor_cli.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("non-member child was spawned"),
    )

    descriptor = os.open(snapshot, os.O_RDONLY)
    try:
        with pytest.raises(doctor_cli.DoctorError, match="not a tgw-coders member"):
            doctor_cli._probe_context_stdio(
                Path("/launcher"), "actor", {}, staged_snapshot_descriptor=descriptor
            )
    finally:
        os.close(descriptor)


def test_read_only_shared_tree_inventory_falls_back_when_noatime_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preservation = tmp_path / ".tgw-coding-preservation"
    preservation.mkdir(mode=0o700)
    (preservation / "evidence.json").write_text('{"preserved":true}\n')
    noatime = getattr(os, "O_NOATIME", 0)
    assert noatime
    real_open = os.open

    def denying_open(path, flags, *args, **kwargs):
        if flags & noatime and path in {preservation.name, "evidence.json"}:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "open", denying_open)

    result = doctor_cli._scan_shared_git_tree(
        tmp_path,
        os.getgid(),
        mutate=False,
        immutable_directories=[Path(), Path(preservation.name)],
    )

    assert result["directories"] == 2
    assert result["files"] == 1


def test_context_staged_probe_rejects_mismatched_group_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot.chmod(0o444)
    target = SimpleNamespace(pw_name="actor", pw_uid=1234, pw_gid=1234)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        doctor_cli.os,
        "fstat",
        lambda _fd: SimpleNamespace(
                st_mode=stat.S_IFREG | 0o400, st_uid=0, st_gid=0
        ),
    )
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_name="tgw-coders", gr_gid=983),
    )
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrgid",
        lambda _gid: SimpleNamespace(gr_name="replacement", gr_gid=983),
    )

    descriptor = os.open(snapshot, os.O_RDONLY)
    try:
        with pytest.raises(doctor_cli.DoctorError, match="group identity differs"):
            doctor_cli._probe_context_stdio(
                Path("/launcher"), "actor", {}, staged_snapshot_descriptor=descriptor
            )
    finally:
        os.close(descriptor)


def test_context_staged_probe_child_identity_keeps_only_coding_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    target = SimpleNamespace(pw_uid=1004, pw_gid=1004)
    monkeypatch.setattr(
        doctor_cli.os, "setgroups", lambda groups: calls.append(("groups", groups))
    )
    monkeypatch.setattr(
        doctor_cli.os, "setgid", lambda gid: calls.append(("gid", gid))
    )
    monkeypatch.setattr(
        doctor_cli.os, "setuid", lambda uid: calls.append(("uid", uid))
    )

    doctor_cli._drop_staged_probe_privileges(target, 983)

    assert calls == [("groups", [983]), ("gid", 1004), ("uid", 1004)]


def test_context_staged_probe_pins_launcher_before_privilege_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/usr/bin/python3\n", encoding="utf-8")
    launcher.chmod(0o555)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot.chmod(0o400)
    target = SimpleNamespace(pw_name="codex", pw_uid=1004, pw_gid=1004)
    coding_group = SimpleNamespace(gr_name="tgw-coders", gr_gid=983)
    observed: dict[str, object] = {}

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        doctor_cli.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_name="root")
    )
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(doctor_cli.grp, "getgrnam", lambda _group: coding_group)
    monkeypatch.setattr(doctor_cli.grp, "getgrgid", lambda _gid: coding_group)
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda *_args: [1004, 983])
    subreaper = {"value": 0}
    monkeypatch.setattr(
        doctor_cli, "_linux_child_subreaper", lambda: subreaper["value"]
    )
    monkeypatch.setattr(
        doctor_cli,
        "_set_linux_child_subreaper",
        lambda value: subreaper.__setitem__("value", value),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_restore_linux_child_subreaper",
        lambda value: subreaper.__setitem__("value", value),
    )

    def popen(command, **kwargs):
        observed["command"] = command
        observed["pass_fds"] = kwargs["pass_fds"]
        observed["preexec_fn"] = kwargs["preexec_fn"]
        observed["cwd"] = kwargs["cwd"]
        launcher_fd = int(command[0].rsplit("/", 1)[-1])
        observed["launcher_fd"] = launcher_fd
        assert os.pread(launcher_fd, 2, 0) == b"#!"
        raise RuntimeError("stop after spawn binding inspection")

    monkeypatch.setattr(doctor_cli.subprocess, "Popen", popen)
    descriptor = os.open(snapshot, os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError, match="spawn binding inspection"):
            doctor_cli._probe_context_stdio(
                launcher,
                "codex",
                {},
                staged_snapshot_descriptor=descriptor,
                staged_snapshot_uid=os.getuid(),
                staged_snapshot_gid=os.getgid(),
            )
        assert observed["command"] == [
            f"/proc/self/fd/{observed['launcher_fd']}"
        ]
        assert observed["pass_fds"] == (
            descriptor,
            observed["launcher_fd"],
        )
        assert callable(observed["preexec_fn"])
        assert observed["cwd"] == "/"
        with pytest.raises(OSError):
            os.fstat(observed["launcher_fd"])
    finally:
        os.close(descriptor)


def test_context_staged_probe_launcher_close_failure_is_not_retried_and_preserves_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/usr/bin/python3\n", encoding="utf-8")
    launcher.chmod(0o555)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot.chmod(0o400)
    target = SimpleNamespace(pw_name="codex", pw_uid=1004, pw_gid=1004)
    coding_group = SimpleNamespace(gr_name="tgw-coders", gr_gid=983)
    subreaper = {"value": 0}
    launcher_fd: int | None = None
    close_calls = 0
    real_close = doctor_cli.os.close

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        doctor_cli.pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_name="root")
    )
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(doctor_cli.grp, "getgrnam", lambda _group: coding_group)
    monkeypatch.setattr(doctor_cli.grp, "getgrgid", lambda _gid: coding_group)
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda *_args: [1004, 983])
    monkeypatch.setattr(
        doctor_cli, "_linux_child_subreaper", lambda: subreaper["value"]
    )
    monkeypatch.setattr(
        doctor_cli,
        "_set_linux_child_subreaper",
        lambda value: subreaper.__setitem__("value", value),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_restore_linux_child_subreaper",
        lambda value: subreaper.__setitem__("value", value),
    )

    def popen(command, **_kwargs):
        nonlocal launcher_fd
        launcher_fd = int(command[0].rsplit("/", 1)[-1])
        raise RuntimeError("distinct spawn failure")

    def close(descriptor: int) -> None:
        nonlocal close_calls
        if descriptor == launcher_fd:
            close_calls += 1
            real_close(descriptor)
            raise OSError("distinct launcher close failure after release")
        real_close(descriptor)

    monkeypatch.setattr(doctor_cli.subprocess, "Popen", popen)
    monkeypatch.setattr(doctor_cli.os, "close", close)
    snapshot_descriptor = os.open(snapshot, os.O_RDONLY)
    try:
        with pytest.raises(RuntimeError) as caught:
            doctor_cli._probe_context_stdio(
                launcher,
                "codex",
                {},
                staged_snapshot_descriptor=snapshot_descriptor,
                staged_snapshot_uid=os.getuid(),
                staged_snapshot_gid=os.getgid(),
            )
        assert "distinct spawn failure" in str(caught.value)
        assert "distinct launcher close failure after release" in str(caught.value)
        assert close_calls == 1
        assert launcher_fd is not None
        with pytest.raises(OSError):
            os.fstat(launcher_fd)
    finally:
        real_close(snapshot_descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="real privilege drop requires root")
def test_context_staged_probe_executes_shebang_below_root_only_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor = "codex"
    target = pwd.getpwnam(actor)
    coding_gid = grp.getgrnam("tgw-coders").gr_gid
    expected = {
        "plan_commit": "a" * 40,
        "source_commit": "b" * 40,
        "source_tree": "c" * 40,
        "snapshot_sha256": "sha256:" + "d" * 64,
        "active_capability": "context-probe-test@1",
        "active_treatment": "establish:context-probe-test@1",
        "task": {"durable_history": []},
    }
    status = {
        "ok": True,
        "actor": actor,
        "generation_status": {"state": "CURRENT"},
        "current_context": dict(expected),
    }
    task = {
        "actor": actor,
        "receiver": actor,
        "plan": {"approved_commit": expected["plan_commit"]},
        "implementation": {
            "development_source": {"commit": expected["source_commit"]}
        },
        "context": dict(expected),
        "durable_history": [],
    }
    schema_fields = {
        "tgw_context_status": (),
        "tgw_context_current_task": (),
        "tgw_context_bundle": ("task", "limit"),
        "tgw_context_code_graph": ("operation", "query", "limit"),
        "tgw_context_plan_graph": ("task", "receiver", "operation", "limit"),
        "tgw_context_plan_source": (
            "path",
            "start_line",
            "max_lines",
            "authority",
        ),
        "tgw_context_onboarding": ("actor",),
        "tgw_context_runbooks": (
            "query",
            "path",
            "start_line",
            "max_lines",
            "limit",
            "authority",
        ),
    }
    tools = []
    for name, fields in schema_fields.items():
        schema = {
            "type": "object",
            "properties": {
                field: {
                    "type": "integer"
                    if field in {"limit", "start_line", "max_lines"}
                    else "string"
                }
                for field in fields
            },
        }
        if name in {
            "tgw_context_plan_graph",
            "tgw_context_plan_source",
            "tgw_context_onboarding",
        }:
            schema["required"] = [fields[0]]
        tools.append(
            {
                "name": name,
                "description": "read only",
                "inputSchema": schema,
                "outputSchema": {
                    "properties": {"result": {"title": "Result", "type": "string"}},
                    "required": ["result"],
                    "title": f"{name}Output",
                    "type": "object",
                },
            }
        )
    responses = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "serverInfo": {"name": "fixture", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(status)}],
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "result": {
                "isError": False,
                "content": [{"type": "text", "text": json.dumps(task)}],
            },
        },
    ]
    tmp_path.chmod(0o700)
    launcher = tmp_path / "root-only-launcher"
    launcher.write_text(
        "#!/usr/bin/python3\n"
        "import json,os,sys\n"
        "from pathlib import Path\n"
        f"assert os.geteuid()=={target.pw_uid}\n"
        f"assert os.getegid()=={target.pw_gid}\n"
        f"assert os.getgroups()==[{coding_gid}]\n"
        "assert not Path('.env').is_file()\n"
        f"responses={responses!r}\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); request_id=request.get('id')\n"
        " if request_id is not None: print(json.dumps(responses[request_id-1]),flush=True)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    snapshot.chmod(0o400)
    launcher_descriptors: list[int] = []
    real_open = doctor_cli.os.open

    def recording_open(path, *args, **kwargs):
        descriptor = real_open(path, *args, **kwargs)
        if Path(path) == launcher:
            launcher_descriptors.append(descriptor)
        return descriptor

    monkeypatch.setattr(doctor_cli.os, "open", recording_open)
    snapshot_descriptor = real_open(snapshot, os.O_RDONLY)
    try:
        result = doctor_cli._probe_context_stdio(
            launcher,
            actor,
            expected,
            staged_snapshot_descriptor=snapshot_descriptor,
        )
        assert result["actor"] == actor
        assert result["generation"] == "CURRENT"
        assert len(launcher_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(launcher_descriptors[0])
    finally:
        os.close(snapshot_descriptor)


@pytest.mark.skipif(os.geteuid() != 0, reason="real privilege drop requires root")
def test_context_staged_probe_identity_traverses_real_selected_launcher_path() -> None:
    actor = "codex"
    target = pwd.getpwnam(actor)
    coding_gid = grp.getgrnam("tgw-coders").gr_gid
    selected_launcher = (
        Path("/opt/TGW/tgw-lib/coding-runtime/current").resolve(strict=True)
        / "scripts/tgw_context_debian_stdio.py"
    )
    assert selected_launcher.is_file()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,os,sys; "
                "p=sys.argv[1]; open(p,'rb').read(2); "
                "print(json.dumps([os.geteuid(),os.getegid(),os.getgroups()]))"
            ),
            str(selected_launcher),
        ],
        check=False,
        capture_output=True,
        text=True,
        preexec_fn=lambda: doctor_cli._drop_staged_probe_privileges(
            target, coding_gid
        ),
    )
    assert completed.returncode == 0, completed.stderr
    uid, primary_gid, supplementary = json.loads(completed.stdout)
    assert uid == target.pw_uid
    assert primary_gid == target.pw_gid
    assert supplementary == [coding_gid]


def test_context_generation_descriptor_rejects_hardlinks(tmp_path: Path) -> None:
    generation = tmp_path / "generation"
    generation.mkdir()
    runtime = generation / "runtime.py"
    runtime.write_text("value = 1\n", encoding="utf-8")
    os.link(runtime, tmp_path / "external-alias.py")

    with pytest.raises(doctor_cli.DoctorError, match="identity is unsafe"):
        doctor_cli._descriptor_context_tree(generation)


def test_context_generation_descriptor_rejects_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generation = tmp_path / "generation"
    generation.mkdir()
    runtime = generation / "runtime.py"
    runtime.write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement.py"
    replacement.write_text("replacement\n", encoding="utf-8")
    real_stat = doctor_cli.os.stat
    calls = 0

    def racing_stat(path, *args, **kwargs):
        nonlocal calls
        if path == "runtime.py" and kwargs.get("dir_fd") is not None:
            calls += 1
            if calls == 2:
                os.replace(replacement, runtime)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "stat", racing_stat)
    with pytest.raises(doctor_cli.DoctorError, match="descendant changed"):
        doctor_cli._descriptor_context_tree(generation)


@pytest.mark.parametrize(
    "failure",
    [
        "status_false",
        "mcp_error",
        "binding_mismatch",
        "output_schema_missing",
        "output_schema_malformed",
        "output_schema_unexpected",
    ],
)
def test_context_cold_probe_rejects_false_error_mismatch_and_output_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    actor = pwd.getpwuid(os.geteuid()).pw_name
    expected = {"plan_commit": "a" * 40, "source_commit": "b" * 40, "source_tree": "c" * 40, "snapshot_sha256": "sha256:" + "d" * 64}
    status = {"ok": failure != "status_false", "actor": actor, "generation_status": {"state": "CURRENT"}, "current_context": dict(expected)}
    if failure == "binding_mismatch":
        status["current_context"]["source_tree"] = "e" * 40
    task = {
        "actor": actor,
        "receiver": actor,
        "plan": {"approved_commit": expected["plan_commit"]},
        "implementation": {
            "development_source": {"commit": expected["source_commit"]}
        },
        "context": dict(expected),
    }
    schema_fields = {
        "tgw_context_status": (),
        "tgw_context_current_task": (),
        "tgw_context_bundle": ("task", "limit"),
        "tgw_context_code_graph": ("operation", "query", "limit"),
        "tgw_context_plan_graph": ("task", "receiver", "operation", "limit"),
        "tgw_context_plan_source": (
            "path", "start_line", "max_lines", "authority",
        ),
        "tgw_context_onboarding": ("actor",),
        "tgw_context_runbooks": (
            "query", "path", "start_line", "max_lines", "limit", "authority",
        ),
    }
    tools = []
    for name, fields in schema_fields.items():
        schema = {
            "type": "object",
            "properties": {
                field: {
                    "type": "integer"
                    if field in {"limit", "start_line", "max_lines"}
                    else "string"
                }
                for field in fields
            },
        }
        if name in {
            "tgw_context_plan_graph",
            "tgw_context_plan_source",
            "tgw_context_onboarding",
        }:
            schema["required"] = [fields[0]]
        tools.append({
            "name": name,
            "description": "read only",
            "inputSchema": schema,
            "outputSchema": {
                "properties": {
                    "result": {"title": "Result", "type": "string"}
                },
                "required": ["result"],
                "title": f"{name}Output",
                "type": "object",
            },
        })
    if failure == "output_schema_missing":
        del tools[0]["outputSchema"]
    elif failure == "output_schema_malformed":
        tools[0]["outputSchema"]["properties"]["result"]["type"] = "integer"
    elif failure == "output_schema_unexpected":
        tools[0]["outputSchema"]["properties"]["extra"] = {"type": "string"}
    responses = [
        {"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "serverInfo": {"name": "fixture", "version": "1"},
        }},
        {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}},
        {"jsonrpc": "2.0", "id": 3, "result": {"isError": failure == "mcp_error", "content": [{"type": "text", "text": json.dumps(status)}]}},
        {"jsonrpc": "2.0", "id": 4, "result": {"isError": False, "content": [{"type": "text", "text": json.dumps(task)}]}},
    ]
    environment_record = tmp_path / "environment.json"
    launcher = tmp_path / "launcher"
    launcher.write_text(
        "#!" + str(Path("/proc/self/exe").resolve()) + "\n"
        "import json,os,sys\n"
        f"open({str(environment_record)!r},'w').write(json.dumps(dict(os.environ)))\n"
        f"responses={responses!r}\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line); request_id=request.get('id')\n"
        " if request_id is not None: print(json.dumps(responses[request_id-1]),flush=True)\n",
        encoding="utf-8",
    )
    launcher.chmod(0o555)

    monkeypatch.setenv("TGW_SECRET_MUST_NOT_LEAK", "secret")
    expected_error = {
        "status_false": "status returned error content",
        "mcp_error": "status returned an MCP error",
        "binding_mismatch": "status bindings differ",
        "output_schema_missing": "tool schema differs",
        "output_schema_malformed": "tool schema differs",
        "output_schema_unexpected": "tool schema differs",
    }[failure]
    with pytest.raises(doctor_cli.DoctorError, match=expected_error):
        doctor_cli._probe_context_stdio(launcher, actor, expected)
    observed = json.loads(environment_record.read_text(encoding="utf-8"))
    assert "TGW_SECRET_MUST_NOT_LEAK" not in observed
    assert set(observed) == {"PATH", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE"}


def test_root_post_repair_checks_use_the_invoking_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "codex")

    assert doctor_cli._operator_actor() == "codex"


def test_root_context_probe_uses_durable_task_actor_without_sudo_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = tmp_path / "current-task.json"
    _write_json(task, {"actor": "codex", "receiver": "codex"})
    target = SimpleNamespace(pw_name="codex", pw_uid=1004, pw_gid=1004)
    coding_group = SimpleNamespace(gr_name="tgw-coders", gr_gid=983)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(doctor_cli.grp, "getgrnam", lambda _group: coding_group)
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda *_args: [1004, 983])

    actor = doctor_cli._context_probe_actor(
        replace(doctor_cli.DoctorPaths(), context_task=task)
    )

    assert actor == "codex"


def _bind_direct_root_coding_probe(
    paths: doctor_cli.DoctorPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = json.loads(paths.context_task.read_text(encoding="utf-8"))
    task.update({"actor": "codex", "receiver": "codex"})
    _write_json(paths.context_task, task)
    target = SimpleNamespace(pw_name="codex", pw_uid=1004, pw_gid=1004)
    coding_group = SimpleNamespace(gr_name="tgw-coders", gr_gid=983)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_name="root"),
    )
    monkeypatch.setattr(doctor_cli.pwd, "getpwnam", lambda _actor: target)
    monkeypatch.setattr(doctor_cli.grp, "getgrnam", lambda _group: coding_group)
    monkeypatch.setattr(doctor_cli.os, "getgrouplist", lambda *_args: [1004, 983])
    monkeypatch.setattr(
        doctor_cli,
        "_postgres_driver",
        lambda: (_ for _ in ()).throw(AssertionError("root driver must not load")),
    )


def test_direct_root_todo_binding_uses_durable_actor_without_sudo_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _bind_direct_root_coding_probe(paths, monkeypatch)
    observed = {}
    rows = [{"id": 1921, "agent": "codex", "status_note": "bound"}]

    def run(command, **_kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(command, 0, json.dumps(rows) + "\n", "")

    monkeypatch.setattr(doctor_cli, "_run", run)

    assert doctor_cli._todo_binding_rows(paths) == rows
    assert observed["command"][:5] == [
        "sudo",
        "-n",
        "-u",
        "codex",
        "/usr/bin/psql",
    ]


def test_direct_root_database_check_uses_durable_actor_without_sudo_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _bind_direct_root_coding_probe(paths, monkeypatch)
    observed = {}
    database = {
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
        "progress_note_column": True,
        "active_jobs": 0,
    }

    def run(command, **_kwargs):
        observed["command"] = command
        return subprocess.CompletedProcess(
            command, 0, json.dumps(database) + "\n", ""
        )

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


def test_context_probe_rejects_task_actor_receiver_mismatch(tmp_path: Path) -> None:
    task = tmp_path / "current-task.json"
    _write_json(task, {"actor": "codex", "receiver": "claude"})

    with pytest.raises(doctor_cli.DoctorError, match="actor and receiver differ"):
        doctor_cli._context_probe_actor(
            replace(doctor_cli.DoctorPaths(), context_task=task)
        )


def test_root_database_postcheck_runs_as_the_invoking_operator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            "progress_note_column": True,
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


def test_shared_git_directory_requires_exact_group_and_setgid(tmp_path: Path) -> None:
    path = tmp_path / "shared-git"
    path.mkdir(mode=0o2775)
    path.chmod(0o2775)

    exact = doctor_cli._shared_git_directory(path, os.getgid())
    path.chmod(0o775)
    missing_setgid = doctor_cli._shared_git_directory(path, os.getgid())

    assert exact["exact"] is True
    assert missing_setgid["exact"] is False
    assert missing_setgid["reason"] == "group, setgid, or group access differs"


def test_descriptor_anchored_git_tree_repair_adds_group_access(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "worktree"
    directory.mkdir(mode=0o750)
    nested = directory / "src"
    nested.mkdir(mode=0o750)
    file_path = nested / "module.py"
    file_path.write_text("git index fixture", encoding="utf-8")
    file_path.chmod(0o640)

    changes = doctor_cli._scan_shared_git_tree(directory, os.getgid(), mutate=True)

    assert stat.S_IMODE(directory.stat().st_mode) == 0o2770
    assert stat.S_IMODE(nested.stat().st_mode) == 0o2770
    assert stat.S_IMODE(file_path.stat().st_mode) == 0o660
    assert changes["directories"] == 2
    assert changes["files"] == 1


def test_mutation_journal_restores_complete_ordinary_tree_metadata(tmp_path: Path) -> None:
    root = tmp_path / "tree"
    root.mkdir(mode=0o700)
    target = root / "index"
    target.write_bytes(b"unchanged")
    target.chmod(0o600)
    before_root = root.stat()
    before_target = target.stat()
    journal = []

    doctor_cli._scan_shared_git_tree(root, os.getgid(), mutate=True, journal=journal)
    assert doctor_cli._close_mutation_journal(journal, rollback=True) == []

    after_root = root.stat()
    after_target = target.stat()
    fields = ("st_dev", "st_ino", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns")
    assert all(getattr(before_root, field) == getattr(after_root, field) for field in fields)
    assert all(getattr(before_target, field) == getattr(after_target, field) for field in fields)
    assert stat.S_IMODE(before_root.st_mode) == stat.S_IMODE(after_root.st_mode)
    assert stat.S_IMODE(before_target.st_mode) == stat.S_IMODE(after_target.st_mode)
    assert target.read_bytes() == b"unchanged"


def test_mutation_journal_restores_detached_pack_hardlink(tmp_path: Path) -> None:
    git_root = tmp_path / "git"
    pack_root = git_root / "objects/pack"
    pack_root.mkdir(parents=True)
    external = tmp_path / "external.pack"
    external.write_bytes(b"pack")
    pack = pack_root / ("pack-" + "a" * 40 + ".pack")
    os.link(external, pack)
    before_inode = pack.stat().st_ino
    journal = []

    doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=True, journal=journal)
    assert pack.stat().st_ino != before_inode
    assert doctor_cli._close_mutation_journal(journal, rollback=True) == []

    assert pack.stat().st_ino == external.stat().st_ino == before_inode
    assert pack.read_bytes() == external.read_bytes() == b"pack"


def test_stable_descriptor_read_proves_two_identical_reads_and_metadata(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    target.write_bytes(b'{"exact":true}\n')
    descriptor = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        raw, before, after = doctor_cli._stable_descriptor_bytes(descriptor)
    finally:
        os.close(descriptor)

    assert raw == b'{"exact":true}\n'
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
    assert all(getattr(before, field) == getattr(after, field) for field in fields)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("st_mode", stat.S_IFREG | 0o2775),
        ("st_nlink", 2),
        ("st_uid", 1001),
        ("st_gid", 984),
        ("st_mode", stat.S_IFDIR | 0o2770),
    ],
)
def test_preservation_directory_requires_exact_pinned_attributes(field: str, value: int) -> None:
    exact = {
        "st_mode": stat.S_IFDIR | 0o2775,
        "st_nlink": 1,
        "st_uid": 1000,
        "st_gid": 983,
    }
    assert doctor_cli._trusted_preservation_directory(
        SimpleNamespace(**exact),
        db_uid=1000,
        group_gid=983,
    )
    exact[field] = value
    assert not doctor_cli._trusted_preservation_directory(
        SimpleNamespace(**exact),
        db_uid=1000,
        group_gid=983,
    )


def test_noatime_permission_fallback_reflinks_without_source_atime_or_remnant(
    btrfs_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = btrfs_tmp_path
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "manifest.json"
    expected = b'{"exact":"db-owned"}\n'
    target.write_bytes(expected)
    old_ns = 1_600_000_000_000_000_000
    os.utime(target, ns=(old_ns, old_ns))
    original_open = doctor_cli.os.open
    noatime = getattr(os, "O_NOATIME", 0)

    def deny_noatime(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "open", deny_noatime)
    parent = original_open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    before = os.stat(target, follow_symlinks=False)
    try:
        read_fd, identity_fd, source_generation = doctor_cli._open_preservation_file(
            parent,
            target.name,
            snapshot_parent=snapshot_parent,
            snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
        )
        try:
            raw, identity_before, identity_after = doctor_cli._stable_descriptor_bytes(
                read_fd,
                identity_descriptor=identity_fd,
                source_generation=source_generation,
            )
        finally:
            os.close(read_fd)
            os.close(identity_fd)
    finally:
        os.close(parent)
        os.close(snapshot_parent)

    after = os.stat(target, follow_symlinks=False)
    assert raw == expected
    assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(expected).hexdigest()
    fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_uid", "st_gid", "st_size", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
    assert all(getattr(before, field) == getattr(after, field) for field in fields)
    assert all(getattr(identity_before, field) == getattr(identity_after, field) for field in fields)
    assert list(snapshot_root.iterdir()) == []


def test_noatime_fallback_pins_original_across_path_and_content_replacement(
    btrfs_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = btrfs_tmp_path
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "manifest.json"
    target.write_bytes(b"authenticated bytes")
    original_open = doctor_cli.os.open
    noatime = getattr(os, "O_NOATIME", 0)

    def deny_noatime(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "open", deny_noatime)
    parent = original_open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        read_fd, identity_fd, source_generation = doctor_cli._open_preservation_file(
            parent,
            target.name,
            snapshot_parent=snapshot_parent,
            snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
        )
        replacement = source_root / "replacement"
        replacement.write_bytes(b"attacker replacement")
        os.replace(replacement, target)
        try:
            path_state = os.stat(target.name, dir_fd=parent, follow_symlinks=False)
            with pytest.raises(doctor_cli.DoctorError, match="changed before stable read"):
                doctor_cli._stable_descriptor_bytes(
                    read_fd,
                    identity_descriptor=identity_fd,
                    source_generation=source_generation,
                )
        finally:
            os.close(read_fd)
            os.close(identity_fd)
    finally:
        os.close(parent)
        os.close(snapshot_parent)

    assert (path_state.st_dev, path_state.st_ino) != (source_generation.st_dev, source_generation.st_ino)
    assert list(snapshot_root.iterdir()) == []


def test_noatime_fallback_fails_closed_when_reflink_is_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    target = tmp_path / "manifest.json"
    target.write_bytes(b"evidence")
    original_open = doctor_cli.os.open
    noatime = getattr(os, "O_NOATIME", 0)

    def deny_noatime(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return original_open(path, flags, *args, **kwargs)

    def reject_reflink(*args, **kwargs):
        raise OSError(errno.EOPNOTSUPP, "forced unsupported reflink")

    monkeypatch.setattr(doctor_cli.os, "open", deny_noatime)
    monkeypatch.setattr(doctor_cli.fcntl, "ioctl", reject_reflink)
    parent = original_open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    before = os.stat(target, follow_symlinks=False)
    try:
        with pytest.raises(doctor_cli.DoctorError, match="reflink is unsupported"):
            doctor_cli._open_preservation_file(
                parent,
                target.name,
                snapshot_parent=snapshot_parent,
                snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
            )
    finally:
        os.close(parent)
        os.close(snapshot_parent)
    after = os.stat(target, follow_symlinks=False)
    assert before.st_atime_ns == after.st_atime_ns
    assert list(snapshot_root.iterdir()) == []


def test_noatime_fallback_fails_closed_when_anonymous_files_are_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    target = tmp_path / "manifest.json"
    target.write_bytes(b"evidence")
    original_open = doctor_cli.os.open
    noatime = getattr(os, "O_NOATIME", 0)
    tmpfile = getattr(os, "O_TMPFILE", 0)

    def reject_anonymous(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        if path == "." and tmpfile and flags & tmpfile:
            raise OSError(errno.EOPNOTSUPP, "forced O_TMPFILE denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "open", reject_anonymous)
    parent = original_open(tmp_path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    before = os.stat(target, follow_symlinks=False)
    try:
        with pytest.raises(doctor_cli.DoctorError, match="anonymous preservation snapshots are unsupported"):
            doctor_cli._open_preservation_file(
                parent,
                target.name,
                snapshot_parent=snapshot_parent,
                snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
            )
    finally:
        os.close(parent)
        os.close(snapshot_parent)
    after = os.stat(target, follow_symlinks=False)
    assert before.st_atime_ns == after.st_atime_ns
    assert list(snapshot_root.iterdir()) == []


def test_noatime_fallback_rejects_same_size_mutation_inside_clone_window(
    btrfs_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = btrfs_tmp_path / "source"
    snapshot_root = btrfs_tmp_path / "snapshots"
    source_root.mkdir()
    snapshot_root.mkdir()
    target = source_root / "manifest.json"
    target.write_bytes(b"generation-one")
    original_open = doctor_cli.os.open
    original_ioctl = doctor_cli.fcntl.ioctl
    noatime = getattr(os, "O_NOATIME", 0)

    def deny_noatime(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return original_open(path, flags, *args, **kwargs)

    def mutate_during_clone(destination, operation, source):
        before = os.fstat(source)
        target.write_bytes(b"generation-two")
        os.utime(
            target,
            ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000),
        )
        return original_ioctl(destination, operation, source)

    monkeypatch.setattr(doctor_cli.os, "open", deny_noatime)
    monkeypatch.setattr(doctor_cli.fcntl, "ioctl", mutate_during_clone)
    parent = original_open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        with pytest.raises(doctor_cli.DoctorError, match="changed inside the reflink window"):
            doctor_cli._open_preservation_file(
                parent,
                target.name,
                snapshot_parent=snapshot_parent,
                snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
            )
    finally:
        os.close(parent)
        os.close(snapshot_parent)
    assert list(snapshot_root.iterdir()) == []


def test_noatime_fallback_uses_pinned_snapshot_directory_after_path_replacement(
    btrfs_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = btrfs_tmp_path / "source"
    snapshot_root = btrfs_tmp_path / "preservation"
    source_root.mkdir()
    snapshot_root.mkdir()
    target = source_root / "manifest.json"
    target.write_bytes(b"pinned generation")
    original_open = doctor_cli.os.open
    noatime = getattr(os, "O_NOATIME", 0)

    def deny_noatime(path, flags, *args, **kwargs):
        if path == target.name and noatime and flags & noatime:
            raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "open", deny_noatime)
    parent = original_open(source_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    snapshot_parent = original_open(snapshot_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    pinned_path = btrfs_tmp_path / "pinned-away"
    os.rename(snapshot_root, pinned_path)
    snapshot_root.mkdir()
    try:
        read_fd, identity_fd, source_generation = doctor_cli._open_preservation_file(
            parent,
            target.name,
            snapshot_parent=snapshot_parent,
            snapshot_group_gid=os.fstat(snapshot_parent).st_gid,
        )
        try:
            raw, _, _ = doctor_cli._stable_descriptor_bytes(
                read_fd,
                identity_descriptor=identity_fd,
                source_generation=source_generation,
            )
        finally:
            os.close(read_fd)
            os.close(identity_fd)
    finally:
        os.close(parent)
        os.close(snapshot_parent)
    assert raw == b"pinned generation"
    assert list(pinned_path.iterdir()) == []
    assert list(snapshot_root.iterdir()) == []


def test_git_tree_inventory_is_independent_of_directory_enumeration_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a").write_bytes(b"a")
    (root / "b").write_bytes(b"b")
    original_listdir = os.listdir
    reverse = False

    def alternating_listdir(path):
        nonlocal reverse
        reverse = not reverse
        return sorted(original_listdir(path), reverse=reverse)

    monkeypatch.setattr(doctor_cli.os, "listdir", alternating_listdir)
    first = doctor_cli._scan_shared_git_tree(root, os.getgid(), mutate=False)
    second = doctor_cli._scan_shared_git_tree(root, os.getgid(), mutate=False)

    assert first["inventory_sha256"] == second["inventory_sha256"]
    assert first["content_sha256"] == second["content_sha256"]


def test_local_operator_launchers_disable_bytecode_before_runtime_import() -> None:
    repository = Path(__file__).resolve().parents[1]
    for relative in ("bin/tgw-coding-local-operator", "bin/tgw-todo-local-operator"):
        lines = (repository / relative).read_text(encoding="utf-8").splitlines()
        export = lines.index("export PYTHONDONTWRITEBYTECODE=1")
        runtime_import = next(index for index, line in enumerate(lines) if line.startswith('exec "$python"'))
        assert export < runtime_import


@pytest.mark.skipif(os.geteuid() != 0, reason="root-owned immutable-runtime regression")
def test_root_invoked_local_launcher_cannot_create_runtime_bytecode(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[1]
    runtime = tmp_path / "runtime"
    package = runtime / "src/tgw"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "coding_cli.py").write_text("assert __cached__ is not None\n", encoding="utf-8")
    for path in (runtime, runtime / "src", package):
        path.chmod(0o555)
    for path in package.iterdir():
        path.chmod(0o444)
    launcher = tmp_path / "launcher"
    source = (repository / "bin/tgw-coding-local-operator").read_text(encoding="utf-8")
    source = source.replace("/opt/TGW/tgw-lib/coding-runtime/current", str(runtime))
    source = source.replace("/opt/TGW/.venvs/controller/bin/python3", os.environ.get("PYTHON", "/usr/bin/python3"))
    launcher.write_text(source, encoding="utf-8")
    launcher.chmod(0o555)

    completed = subprocess.run([str(launcher)], check=False, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    assert not list(runtime.rglob("*.pyc"))
    assert not list(runtime.rglob("__pycache__"))


def test_descriptor_anchored_git_tree_repair_keeps_bound_root(
    tmp_path: Path,
) -> None:
    original = tmp_path / "bound"
    original.mkdir(mode=0o750)
    bound_file = original / "index"
    bound_file.write_text("bound", encoding="utf-8")
    bound_file.chmod(0o640)
    descriptor = os.open(
        original,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    moved = tmp_path / "moved"
    original.rename(moved)
    original.mkdir(mode=0o700)
    replacement = original / "index"
    replacement.write_text("replacement", encoding="utf-8")
    replacement.chmod(0o600)
    try:
        doctor_cli._scan_shared_git_tree(descriptor, os.getgid(), mutate=True)
    finally:
        os.close(descriptor)

    assert stat.S_IMODE((moved / "index").stat().st_mode) == 0o660
    assert stat.S_IMODE(replacement.stat().st_mode) == 0o600
    assert stat.S_IMODE(original.stat().st_mode) == 0o700


def test_descriptor_anchored_git_tree_repair_never_follows_symlinks(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "git"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("preserve", encoding="utf-8")
    outside.chmod(0o600)
    (directory / "link").symlink_to(outside)

    changes = doctor_cli._scan_shared_git_tree(directory, os.getgid(), mutate=True)

    assert stat.S_IMODE(outside.stat().st_mode) == 0o600
    assert changes["symlinks_untouched"] == 1


def test_git_tree_scan_excludes_only_declared_root_entry(tmp_path: Path) -> None:
    directory = tmp_path / "worktree"
    nested_git = directory / "nested/.git"
    root_git = directory / ".git"
    nested_git.mkdir(parents=True)
    root_git.mkdir(parents=True)
    (nested_git / "index").write_text("must scan", encoding="utf-8")
    (root_git / "index").write_text("excluded", encoding="utf-8")

    counts = doctor_cli._scan_shared_git_tree(
        directory,
        os.getgid(),
        mutate=False,
        excluded_root_entries=(".git",),
    )

    assert counts["excluded_root_entries"] == 1
    assert counts["files"] == 1


def test_git_tree_repair_detaches_only_valid_pack_hardlinks(
    tmp_path: Path,
) -> None:
    git_root = tmp_path / "git"
    pack = git_root / "objects/pack"
    pack.mkdir(parents=True)
    outside = tmp_path / "pack-source"
    outside.write_text("immutable pack", encoding="utf-8")
    outside.chmod(0o644)
    canonical = pack / ("pack-" + "a" * 40 + ".pack")
    os.link(outside, canonical)
    outside_inode = outside.stat().st_ino

    preflight = doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=False)
    repaired = doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=True)
    idempotent = doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=False)

    assert preflight["pack_hardlinks_seen"] == 1
    assert preflight["pack_hardlinks_detached"] == 0
    assert repaired["pack_hardlinks_seen"] == 1
    assert repaired["pack_hardlinks_detached"] == 1
    assert idempotent["pack_components_inexact"] == 0
    assert canonical.read_bytes() == outside.read_bytes() == b"immutable pack"
    assert canonical.stat().st_ino != outside_inode
    assert outside.stat().st_ino == outside_inode
    assert stat.S_IMODE(outside.stat().st_mode) == 0o644
    assert stat.S_IMODE(canonical.stat().st_mode) == 0o444

    mutable = git_root / "index"
    os.link(outside, mutable)
    with pytest.raises(doctor_cli.DoctorError, match="mutable or unreadable hardlink"):
        doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=False)


def test_git_tree_accepts_standard_read_only_loose_objects(tmp_path: Path) -> None:
    git_root = tmp_path / "git"
    loose_directory = git_root / "objects" / "ab"
    loose_directory.mkdir(parents=True)
    for directory in (git_root, git_root / "objects", loose_directory):
        directory.chmod(0o2770)
    loose = loose_directory / ("c" * 38)
    loose.write_bytes(b"immutable loose object")
    loose.chmod(0o444)

    counts = doctor_cli._scan_shared_git_tree(git_root, os.getgid(), mutate=False)

    assert counts["loose_objects"] == 1
    assert counts["loose_objects_inexact"] == 0
    assert counts["files"] == 0
    assert doctor_cli._shared_tree_exact(counts) is True


def test_unix_git_repair_recurses_into_configured_linked_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README").write_text("source\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "source")
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    linked = worktrees / "todo"
    _git(repository, "worktree", "add", "-b", "todo", str(linked))
    nested = linked / "src"
    nested.mkdir(mode=0o700)
    file_path = nested / "module.py"
    file_path.write_text("preserve bytes\n", encoding="utf-8")
    file_path.chmod(0o600)
    paths = doctor_cli.DoctorPaths(
        repository=repository,
        worktrees=worktrees,
        coding_config=tmp_path / "coding.json",
        receipts=tmp_path / "receipts",
        coding_root_effect_uid=os.geteuid(),
        runtime_root=tmp_path / "support/runtime",
    )
    _write_json(paths.coding_config, {"schema": "tgw-local-coding-workflow/v1", "coding": {
        "preservation_archive_root": str(tmp_path / "support/archive"),
        "runner_state_root": str(tmp_path / "support/runner"),
        "lifecycle_root": str(tmp_path / "support/lifecycle"),
        "root_effect_root": str(tmp_path / "support/root-effect"),
    }})
    reports = iter(({"state": "FAIL"}, {"state": "PASS"}))

    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli.os, "chown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: type("Group", (), {"gr_gid": os.getgid()})(),
    )
    monkeypatch.setattr(doctor_cli, "check_unix_access", lambda _paths: next(reports))
    monkeypatch.setattr(doctor_cli, "_coding_quiescence", lambda _paths: nullcontext())

    result = doctor_cli.repair_unix_git_access(paths)

    assert file_path.read_text(encoding="utf-8") == "preserve bytes\n"
    assert stat.S_IMODE(nested.stat().st_mode) & 0o2070 == 0o2070
    assert stat.S_IMODE(file_path.stat().st_mode) & 0o060 == 0o060
    assert result["git_tree_changes"]["linked:todo"]["files"] > 0


def _unix_repair_transaction_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repository = tmp_path / "source"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README").write_text("source\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "source")
    worktrees = tmp_path / "worktrees"
    worktrees.mkdir()
    paths = doctor_cli.DoctorPaths(
        repository=repository,
        worktrees=worktrees,
        coding_config=tmp_path / "coding.json",
        receipts=tmp_path / "receipts",
        coding_root_effect_uid=os.geteuid(),
        runtime_root=tmp_path / "support/runtime",
    )
    _write_json(paths.coding_config, {"schema": "tgw-local-coding-workflow/v1", "coding": {
        "preservation_archive_root": str(tmp_path / "support/archive"),
        "runner_state_root": str(tmp_path / "support/runner"),
        "lifecycle_root": str(tmp_path / "support/lifecycle"),
        "root_effect_root": str(tmp_path / "support/root-effect"),
    }})
    reports = iter(({"state": "FAIL"}, {"state": "PASS"}))
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli.os, "chown", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: type("Group", (), {"gr_gid": os.getgid()})(),
    )
    monkeypatch.setattr(doctor_cli, "check_unix_access", lambda _paths: next(reports))
    monkeypatch.setattr(doctor_cli, "_coding_quiescence", lambda _paths: nullcontext())
    return paths


def _authenticated_pre_ledger_repair_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    paths = _unix_repair_transaction_fixture(tmp_path, monkeypatch)
    linked = paths.worktrees / "todo"
    _git(paths.repository, "worktree", "add", "-b", "todo", str(linked))
    preservation = linked / ".tgw-coding-preservation"
    preservation.mkdir(mode=0o2775)
    manifest = preservation / ("a" * 64 + ".json")
    manifest.write_bytes(b'{"authenticated":true}\n')
    manifest.chmod(0o460)
    before = manifest.stat()

    def authenticate(location, descriptor, _group_gid):
        assert location == linked
        preservation_descriptor = os.open(preservation, os.O_RDONLY | os.O_DIRECTORY)
        manifest_descriptor = os.open(manifest, os.O_RDONLY)
        return {
            "todo_id": 1824,
            "relative": Path(".tgw-coding-preservation") / manifest.name,
            "descriptor": manifest_descriptor,
            "before": os.fstat(manifest_descriptor),
            "preservation_descriptor": preservation_descriptor,
            "preservation_before": os.fstat(preservation_descriptor),
            "manifest_hash": "sha256:" + "a" * 64,
            "receipt_sha256": "sha256:" + "b" * 64,
        }

    monkeypatch.setattr(doctor_cli, "_authenticate_pre_ledger_preservation", authenticate)
    monkeypatch.setattr(doctor_cli, "_receipt", lambda *_args, **_kwargs: str(tmp_path / "receipt.json"))
    return paths, linked, manifest, before


def test_unix_git_repair_compares_authenticated_0460_inventory_before_0440_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _linked, manifest, before = _authenticated_pre_ledger_repair_fixture(tmp_path, monkeypatch)

    result = doctor_cli.repair_unix_git_access(paths)

    after = manifest.stat()
    stable_fields = ("st_ino", "st_uid", "st_gid", "st_nlink", "st_size", "st_atime_ns", "st_mtime_ns")
    assert stat.S_IMODE(before.st_mode) == 0o460
    assert stat.S_IMODE(after.st_mode) == 0o440
    assert all(getattr(before, field) == getattr(after, field) for field in stable_fields)
    assert manifest.read_bytes() == b'{"authenticated":true}\n'
    assert result["pre_ledger_preservation"]["todo"]["mode_repaired"] is True
    for name, preflight in result["preflight"].items():
        assert result["git_tree_changes"][name]["inventory_sha256"] == preflight["inventory_sha256"]
        assert result["git_tree_changes"][name]["content_sha256"] == preflight["content_sha256"]


def test_unix_git_repair_real_content_drift_fails_closed_before_manifest_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, linked, manifest, _before = _authenticated_pre_ledger_repair_fixture(tmp_path, monkeypatch)
    drift = linked / "README"
    repair_target = linked / "repair-target"
    repair_target.mkdir(mode=0o700)
    original_scan = doctor_cli._scan_shared_git_tree
    injected = False

    def scan(root, group_gid, *, mutate, **kwargs):
        nonlocal injected
        if mutate and not injected and os.fstat(root).st_ino == linked.stat().st_ino:
            injected = True
            drift.write_text("injected real content drift\n", encoding="utf-8")
        return original_scan(root, group_gid, mutate=mutate, **kwargs)

    monkeypatch.setattr(doctor_cli, "_scan_shared_git_tree", scan)

    with pytest.raises(doctor_cli.DoctorError, match="inventory or content changed"):
        doctor_cli.repair_unix_git_access(paths)

    assert injected is True
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o460
    assert stat.S_IMODE(repair_target.stat().st_mode) == 0o700


def test_unix_git_success_receipt_is_strictly_after_commit_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _unix_repair_transaction_fixture(tmp_path, monkeypatch)
    events = []
    close = doctor_cli._close_mutation_journal
    receipt = doctor_cli._receipt

    def ordered_close(journal, *, rollback):
        events.append("rollback" if rollback else "commit-cleanup")
        return close(journal, rollback=rollback)

    def ordered_receipt(*args, **kwargs):
        events.append("success-receipt")
        return receipt(*args, **kwargs)

    monkeypatch.setattr(doctor_cli, "_close_mutation_journal", ordered_close)
    monkeypatch.setattr(doctor_cli, "_receipt", ordered_receipt)

    result = doctor_cli.repair_unix_git_access(paths)

    assert result["ok"] is True
    assert events[-2:] == ["commit-cleanup", "success-receipt"]
    assert Path(result["receipt"]).is_file()


def test_unix_git_commit_cleanup_failure_has_no_success_receipt_and_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _unix_repair_transaction_fixture(tmp_path, monkeypatch)
    observed_journal = []

    def failed_cleanup(journal, *, rollback):
        assert rollback is False
        observed_journal.extend(journal)
        return ["injected retained cleanup evidence"]

    monkeypatch.setattr(doctor_cli, "_close_mutation_journal", failed_cleanup)
    monkeypatch.setattr(
        doctor_cli,
        "_receipt",
        lambda *_args, **_kwargs: pytest.fail("success receipt must follow successful cleanup"),
    )

    with pytest.raises(doctor_cli.DoctorError, match="transaction cleanup incomplete"):
        doctor_cli.repair_unix_git_access(paths)

    assert observed_journal
    assert not paths.receipts.exists()


def test_unix_git_receipt_publication_failure_occurs_only_after_committed_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _unix_repair_transaction_fixture(tmp_path, monkeypatch)
    events = []
    close = doctor_cli._close_mutation_journal

    def ordered_close(journal, *, rollback):
        events.append("rollback" if rollback else "commit-cleanup")
        return close(journal, rollback=rollback)

    def failed_receipt(*_args, **_kwargs):
        events.append("receipt-publication")
        raise OSError("injected receipt publication failure")

    monkeypatch.setattr(doctor_cli, "_close_mutation_journal", ordered_close)
    monkeypatch.setattr(doctor_cli, "_receipt", failed_receipt)

    with pytest.raises(OSError, match="receipt publication failure"):
        doctor_cli.repair_unix_git_access(paths)

    assert events == ["commit-cleanup", "receipt-publication"]
    assert not paths.receipts.exists()


def test_coding_quiescence_guards_and_verifies_every_local_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    active = {
        "tgw-codex-implement-worker.service",
        "tgw-coding-local-foreman.timer",
    }
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir()
    runtime_root.chmod(0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    reload_count = 0

    def run(command, **_kwargs):
        nonlocal reload_count
        commands.append(command)
        if command[1] == "daemon-reload":
            reload_count += 1
            if reload_count == 1:
                assert (quiescence_root / doctor_cli._QUIESCENCE_MARKER).is_file()
                assert all((runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN).is_file() for unit in doctor_cli._CODING_UNITS)
        elif command[1] == "stop":
            active.clear()
        elif command[1] == "start":
            active.update(command[2:])
            if command[2:] == ["tgw-coding-local-foreman.timer"]:
                active.add("tgw-coding-local-foreman.service")
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": (
                "activating"
                if unit == "tgw-coding-local-foreman.service" and unit in active
                else ("active" if unit in active else "inactive")
            ),
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with doctor_cli._coding_quiescence(paths):
        assert not active
        assert (quiescence_root / doctor_cli._QUIESCENCE_MARKER).is_file()
        assert all((runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN).is_file() for unit in doctor_cli._CODING_UNITS)

    assert commands[0] == ["systemctl", "daemon-reload"]
    assert commands[1] == [
        "systemctl",
        "stop",
        "tgw-coding-local-foreman.timer",
    ]
    assert commands[2] == [
        "systemctl",
        "stop",
        *(
            unit
            for unit in doctor_cli._CODING_UNITS
            if unit != "tgw-coding-local-foreman.timer"
        ),
    ]
    assert commands[-3] == ["systemctl", "daemon-reload"]
    assert commands[-2] == [
        "systemctl",
        "start",
        "tgw-codex-implement-worker.service",
    ]
    assert commands[-1] == [
        "systemctl",
        "start",
        "tgw-coding-local-foreman.timer",
    ]
    assert active == {
        "tgw-codex-implement-worker.service",
        "tgw-coding-local-foreman.service",
        "tgw-coding-local-foreman.timer",
    }
    assert not quiescence_root.exists()
    assert not any(runtime_root.iterdir())


def test_coding_quiescence_marker_unlink_failure_retains_recovery_and_refuses_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    unlink = doctor_cli._unlink_quiescence_file

    def run(command, **_kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def fail_marker_unlink(path, *args, **kwargs):
        if path == quiescence_root / doctor_cli._QUIESCENCE_MARKER:
            raise OSError("injected marker unlink failure")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unlink_quiescence_file", fail_marker_unlink)

    with pytest.raises(doctor_cli.DoctorError, match="injected marker unlink failure") as exc_info:
        with doctor_cli._coding_quiescence(paths):
            pass

    assert "quiescence marker remains; refusing to start local coding units" in str(exc_info.value)
    assert "quiescence guards remain" in str(exc_info.value)
    assert not any(command[1] == "start" for command in commands)
    assert (quiescence_root / doctor_cli._QUIESCENCE_STATE).is_file()
    assert (quiescence_root / doctor_cli._QUIESCENCE_MARKER).is_file()


def test_coding_quiescence_rejects_timer_triggered_failed_foreman_and_retains_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foreman = "tgw-coding-local-foreman.service"
    timer = "tgw-coding-local-foreman.timer"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[timer] = "active"
    commands = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "inactive"
        elif command[1:] == ["start", timer]:
            states[timer] = "active"
            states[foreman] = "failed"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="did not return to their initial state"):
        with doctor_cli._coding_quiescence(paths):
            pass

    starts = [command for command in commands if command[1] == "start"]
    assert starts == [["systemctl", "start", timer]]
    assert commands[-1] == ["systemctl", "start", timer]
    assert states[foreman] == "failed"
    assert (quiescence_root / doctor_cli._QUIESCENCE_STATE).is_file()


def test_coding_quiescence_stops_timer_before_timer_triggered_activating_one_shot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    states = {
        unit: ("active" if unit == "tgw-coding-local-foreman.timer" else "inactive")
        for unit in doctor_cli._CODING_UNITS
    }
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1:] == ["stop", "tgw-coding-local-foreman.timer"]:
            states["tgw-coding-local-foreman.timer"] = "inactive"
            states["tgw-coding-local-foreman.service"] = "activating"
        elif command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "inactive"
        elif command[1] == "start":
            for unit in command[2:]:
                states[unit] = "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with doctor_cli._coding_quiescence(paths):
        assert set(states.values()) == {"inactive"}

    assert commands[1] == ["systemctl", "stop", "tgw-coding-local-foreman.timer"]
    assert "tgw-coding-local-foreman.service" in commands[2]
    assert states["tgw-coding-local-foreman.timer"] == "active"


def test_coding_quiescence_proves_guards_then_recovers_failed_transient_foreman(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    state_reads = []
    foreman = "tgw-coding-local-foreman.service"
    timer = "tgw-coding-local-foreman.timer"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[foreman] = "activating"
    states[timer] = "active"
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1:] == ["stop", timer]:
            states[timer] = "inactive"
        elif command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "failed" if unit == foreman else "inactive"
        elif command[1] == "reset-failed":
            assert command == ["systemctl", "reset-failed", foreman]
            assert state_reads[-len(doctor_cli._CODING_UNITS):] == list(
                doctor_cli._CODING_UNITS
            )
            assert all(state in {"inactive", "failed"} for state in states.values())
            assert all(
                (runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN).is_file()
                for unit in doctor_cli._CODING_UNITS
            )
            states[foreman] = "inactive"
        elif command[1:] == ["start", foreman]:
            # A oneshot systemctl start is blocking; success returns it to inactive.
            assert state_reads[-len(doctor_cli._CODING_UNITS):] == list(
                doctor_cli._CODING_UNITS
            )
            assert states[timer] == "inactive"
            assert all(states[unit] == "inactive" for unit in states if unit != timer)
            states[foreman] = "inactive"
        elif command[1:] == ["start", timer]:
            assert command == ["systemctl", "start", timer]
            assert state_reads[-len(doctor_cli._CODING_UNITS):] == list(
                doctor_cli._CODING_UNITS
            )
            assert states[timer] == "inactive"
            assert all(states[unit] == "inactive" for unit in states if unit != timer)
            states[timer] = "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        state_reads.append(unit)
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with doctor_cli._coding_quiescence(paths) as evidence:
        assert evidence["initially_active"] == [timer, foreman]
        assert set(states.values()) == {"inactive"}

    assert commands[-2:] == [["systemctl", "start", foreman], ["systemctl", "start", timer]]


def test_coding_quiescence_does_not_restore_initially_inactive_timer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    foreman = "tgw-coding-local-foreman.service"
    timer = "tgw-coding-local-foreman.timer"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[foreman] = "activating"
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "failed" if unit == foreman else "inactive"
        elif command[1:] == ["reset-failed", foreman]:
            states[foreman] = "inactive"
        elif command[1:] == ["start", foreman]:
            states[foreman] = "inactive"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {"LoadState": "loaded", "ActiveState": states[unit], "DropInPaths": str(dropin) if dropin.exists() else ""}

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with doctor_cli._coding_quiescence(paths):
        pass

    assert ["systemctl", "reset-failed", foreman] in commands
    assert ["systemctl", "start", foreman] in commands
    assert ["systemctl", "start", timer] not in commands


def test_coding_quiescence_reset_failure_retains_recovery_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = "tgw-controller-verify-worker.service"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[failed] = "failed"
    state_reads = []
    commands = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1] == "reset-failed":
            assert state_reads[-len(doctor_cli._CODING_UNITS):] == list(
                doctor_cli._CODING_UNITS
            )
            return subprocess.CompletedProcess(command, 1, "", "injected reset failure")
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        state_reads.append(unit)
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {"LoadState": "loaded", "ActiveState": states[unit], "DropInPaths": str(dropin) if dropin.exists() else ""}

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="injected reset failure"):
        with doctor_cli._coding_quiescence(paths):
            pass

    units = list(doctor_cli._CODING_UNITS)
    state_path, marker, dropins, dropin_value = doctor_cli._quiescence_layout(
        paths, units
    )
    state = json.loads(state_path.read_bytes())
    assert state["units"] == units
    assert state["schema"] == doctor_cli._QUIESCENCE_SCHEMA
    assert state["boot_id"] == doctor_cli._boot_id()
    assert state["owner_pid"] == os.getpid()
    assert state["owner_start_ticks"] == doctor_cli._process_start_ticks(os.getpid())
    assert state["initially_active"] == []
    assert state["state_path"] == str(state_path)
    assert state["marker"] == str(marker)
    assert state["dropins"] == {unit: str(dropins[unit]) for unit in units}
    assert state_path.read_bytes() == doctor_cli._canonical(state) + b"\n"
    for path, mode in [(state_path, 0o400), (marker, 0o400), *((path, 0o444) for path in dropins.values())]:
        metadata = path.stat(follow_symlinks=False)
        assert stat.S_ISREG(metadata.st_mode)
        assert stat.S_IMODE(metadata.st_mode) == mode
        assert metadata.st_uid == os.getuid()
        assert metadata.st_gid == os.getgid()
        assert metadata.st_nlink == 1
    assert marker.read_bytes() == b"tgw doctor unix-git-access active\n"
    assert all(dropin.read_bytes() == dropin_value for dropin in dropins.values())
    assert doctor_cli._unexpected_quiescence_entries(
        paths, state_path=state_path, marker=marker, dropins=dropins
    ) == []
    assert states[failed] == "failed"
    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "stop", "tgw-coding-local-foreman.timer"],
        ["systemctl", "stop", *(unit for unit in units if unit != "tgw-coding-local-foreman.timer")],
        ["systemctl", "reset-failed", failed],
    ]
    assert commands[-1] == ["systemctl", "reset-failed", failed]
    assert commands.count(["systemctl", "daemon-reload"]) == 1


def test_coding_quiescence_fails_closed_when_activating_one_shot_cleanup_cannot_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states["tgw-coding-local-foreman.timer"] = "active"
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        if command[1:] == ["stop", "tgw-coding-local-foreman.timer"]:
            states["tgw-coding-local-foreman.timer"] = "inactive"
            states["tgw-coding-local-foreman.service"] = "activating"
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[1] == "stop":
            return subprocess.CompletedProcess(command, 1, "", "injected stop failure")
        if command[1] == "start":
            for unit in command[2:]:
                states[unit] = "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="injected stop failure"):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert states["tgw-coding-local-foreman.service"] == "activating"
    assert (quiescence_root / doctor_cli._QUIESCENCE_STATE).is_file()


def test_coding_quiescence_refuses_preexisting_runtime_mask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda unit: {
            "LoadState": ("masked" if unit == "tgw-controller-verify-worker.service" else "loaded"),
            "ActiveState": "inactive",
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("systemctl must not be called"),
    )

    with pytest.raises(doctor_cli.DoctorError, match="pre-existing coding unit masks"):
        with doctor_cli._coding_quiescence(doctor_cli.DoctorPaths()):
            pass


def test_coding_quiescence_refuses_preexisting_guard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    quiescence_root.mkdir(mode=0o755)
    marker = quiescence_root / doctor_cli._QUIESCENCE_MARKER
    marker.write_text("preserve pre-existing state\n", encoding="utf-8")
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda _unit: {"LoadState": "loaded", "ActiveState": "inactive"},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("systemctl must not be called"),
    )

    with pytest.raises(doctor_cli.DoctorError, match="pre-existing coding quiescence"):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert marker.read_text(encoding="utf-8") == "preserve pre-existing state\n"


def test_coding_quiescence_restores_active_units_after_stop_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []
    active = {"tgw-controller-verify-worker.service"}
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir()
    runtime_root.chmod(0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1] == "stop":
            active.clear()
            return subprocess.CompletedProcess(command, 1, "", "partial stop failure")
        if command[1] == "start":
            active.update(command[2:])
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": "active" if unit in active else "inactive",
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="partial stop failure"):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert commands[-2] == ["systemctl", "daemon-reload"]
    assert commands[-1] == [
        "systemctl",
        "start",
        "tgw-controller-verify-worker.service",
    ]
    assert active == {"tgw-controller-verify-worker.service"}
    assert not quiescence_root.exists()
    assert not any(runtime_root.iterdir())


@pytest.mark.parametrize("release_error", ["cleanup", "daemon-reload"])
def test_coding_quiescence_restores_through_diagnostic_release_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    release_error: str,
) -> None:
    worker = "tgw-controller-verify-worker.service"
    foreman = "tgw-coding-local-foreman.service"
    timer = "tgw-coding-local-foreman.timer"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states.update({worker: "active", foreman: "activating", timer: "active"})
    commands = []
    reload_count = 0
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        nonlocal reload_count
        commands.append(command)
        if command[1] == "daemon-reload":
            reload_count += 1
            if release_error == "daemon-reload" and reload_count == 2:
                return subprocess.CompletedProcess(command, 1, "", "injected reload failure")
        elif command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "inactive"
        elif command[1] == "start":
            for unit in command[2:]:
                states[unit] = "inactive" if unit == foreman else "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    unlink = doctor_cli._unlink_quiescence_file
    cleanup_failed = False

    def fail_one_dropin(path, value, **kwargs):
        nonlocal cleanup_failed
        if (
            release_error == "cleanup"
            and path.name == doctor_cli._QUIESCENCE_DROPIN
            and not cleanup_failed
        ):
            cleanup_failed = True
            raise OSError("injected cleanup failure")
        return unlink(path, value, **kwargs)

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)
    monkeypatch.setattr(doctor_cli, "_unlink_quiescence_file", fail_one_dropin)

    expected = (
        "injected cleanup failure"
        if release_error == "cleanup"
        else "injected reload failure"
    )
    with pytest.raises(doctor_cli.DoctorError, match=expected):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert [command for command in commands if command[1] == "start"] == [
        ["systemctl", "start", worker],
        ["systemctl", "start", foreman],
        ["systemctl", "start", timer],
    ]
    assert states[worker] == "active"
    assert states[foreman] == "inactive"
    assert states[timer] == "active"


def test_coding_quiescence_recovers_exact_stale_failed_transient_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir()
    runtime_root.chmod(0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    quiescence_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    units = list(doctor_cli._CODING_UNITS)
    state_path, marker, dropins, dropin_value = doctor_cli._quiescence_layout(paths, units)
    state = {
        "schema": doctor_cli._QUIESCENCE_SCHEMA,
        "boot_id": doctor_cli._boot_id(),
        "owner_pid": 999999999,
        "owner_start_ticks": "1",
        "units": units,
        "initially_active": [
            "tgw-controller-verify-worker.service",
            "tgw-coding-local-foreman.service",
        ],
        "state_path": str(state_path),
        "marker": str(marker),
        "dropins": {unit: str(dropins[unit]) for unit in units},
    }
    doctor_cli._create_quiescence_file(
        state_path,
        doctor_cli._canonical(state) + b"\n",
        mode=0o400,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    doctor_cli._create_quiescence_file(
        marker,
        b"tgw doctor unix-git-access active\n",
        mode=0o400,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    for dropin in dropins.values():
        dropin.parent.mkdir(mode=0o755)
        doctor_cli._create_quiescence_file(
            dropin,
            dropin_value,
            mode=0o444,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    foreman = "tgw-coding-local-foreman.service"
    states = {unit: "inactive" for unit in units}
    states[foreman] = "failed"

    def run(command, **_kwargs):
        if command[1] == "stop":
            for unit in command[2:]:
                if unit != foreman:
                    states[unit] = "inactive"
        elif command[1] == "reset-failed":
            assert command == ["systemctl", "reset-failed", foreman]
            states[foreman] = "inactive"
        elif command[1] == "start":
            for unit in command[2:]:
                states[unit] = "inactive" if unit == foreman else "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = dropins[unit]
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with doctor_cli._coding_quiescence(paths) as evidence:
        assert evidence["recovered_stale_quiescence"]["recovered"] is True
        assert set(states.values()) == {"inactive"}

    assert states["tgw-controller-verify-worker.service"] == "active"
    assert states[foreman] == "inactive"
    assert not quiescence_root.exists()
    assert not any(runtime_root.iterdir())


def test_coding_quiescence_refuses_stale_recovery_while_foreman_is_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    quiescence_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    units = list(doctor_cli._CODING_UNITS)
    state_path, marker, dropins, _dropin_value = doctor_cli._quiescence_layout(paths, units)
    state = {
        "schema": doctor_cli._QUIESCENCE_SCHEMA,
        "boot_id": doctor_cli._boot_id(),
        "owner_pid": 999999999,
        "owner_start_ticks": "1",
        "units": units,
        "initially_active": ["tgw-coding-local-foreman.timer"],
        "state_path": str(state_path),
        "marker": str(marker),
        "dropins": {unit: str(dropins[unit]) for unit in units},
    }
    state_raw = doctor_cli._canonical(state) + b"\n"
    doctor_cli._create_quiescence_file(
        state_path,
        state_raw,
        mode=0o400,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda unit: {
            "LoadState": "loaded",
            "ActiveState": ("active" if unit == "tgw-coding-local-foreman.service" else "inactive"),
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("systemctl must not be called"),
    )

    with pytest.raises(doctor_cli.DoctorError, match="one-shot is active"):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert state_path.read_bytes() == state_raw
    assert not marker.exists()
    assert not any(runtime_root.iterdir())


@pytest.mark.parametrize("extra_location", ["state-root", "drop-in-root"])
def test_coding_quiescence_retains_state_for_unexpected_runtime_remnant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_location: str,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    units = list(doctor_cli._CODING_UNITS)
    state_path, _marker, dropins, _dropin_value = doctor_cli._quiescence_layout(paths, units)

    def unit_state(unit):
        dropin = dropins[unit]
        return {
            "LoadState": "loaded",
            "ActiveState": "inactive",
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    with pytest.raises(doctor_cli.DoctorError, match="unexpected coding quiescence remnants remain"):
        with doctor_cli._coding_quiescence(paths):
            if extra_location == "state-root":
                extra = quiescence_root / "unexpected.guard"
            else:
                extra = next(iter(dropins.values())).parent / "unexpected.conf"
            extra.write_text("preserve\n", encoding="utf-8")

    assert extra.read_text(encoding="utf-8") == "preserve\n"
    assert state_path.is_file()


def test_coding_quiescence_reload_failure_restores_and_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir()
    runtime_root.chmod(0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    active = {"tgw-codex-implement-worker.service"}
    reload_count = 0
    fail_release_reload = True

    def run(command, **_kwargs):
        nonlocal reload_count
        if command[1] == "daemon-reload":
            reload_count += 1
            if fail_release_reload and reload_count == 2:
                return subprocess.CompletedProcess(command, 1, "", "reload failed")
        elif command[1] == "stop":
            active.clear()
        elif command[1] == "start":
            active.update(command[2:])
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": "active" if unit in active else "inactive",
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="reload failed"):
        with doctor_cli._coding_quiescence(paths):
            assert not active

    state_path = quiescence_root / doctor_cli._QUIESCENCE_STATE
    marker = quiescence_root / doctor_cli._QUIESCENCE_MARKER
    assert active == {"tgw-codex-implement-worker.service"}
    assert state_path.is_file()
    assert not marker.exists()
    assert not any(runtime_root.rglob(doctor_cli._QUIESCENCE_DROPIN))

    state = json.loads(state_path.read_bytes())
    state["owner_pid"] = 999999999
    state["owner_start_ticks"] = "1"
    doctor_cli._atomic_bytes(
        state_path,
        doctor_cli._canonical(state) + b"\n",
        mode=0o400,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    fail_release_reload = False

    with doctor_cli._coding_quiescence(paths) as evidence:
        assert evidence["recovered_stale_quiescence"]["recovered"] is True
        assert not active

    assert active == {"tgw-codex-implement-worker.service"}
    assert not quiescence_root.exists()
    assert not any(runtime_root.iterdir())


def test_worker_unit_exactness_includes_immutable_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.service"
    destination = tmp_path / "installed.service"
    source.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    destination.write_bytes(source.read_bytes())
    paths = doctor_cli.DoctorPaths(
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    for wrong_mode in (0o400, 0o440, 0o111, 0o555, 0o644, 0o664):
        destination.chmod(wrong_mode)
        assert doctor_cli._unit_destination_exact(paths, destination, source) is False
    destination.chmod(0o444)
    assert doctor_cli._unit_destination_exact(paths, destination, source) is True

    alias = tmp_path / "installed-alias.service"
    os.link(destination, alias)
    assert doctor_cli._unit_destination_exact(paths, destination, source) is False


def test_atomic_bytes_applies_exact_descriptor_metadata(tmp_path: Path) -> None:
    destination = tmp_path / "unit.service"

    doctor_cli._atomic_bytes(
        destination,
        b"[Service]\nExecStart=/bin/true\n",
        mode=0o444,
        uid=os.getuid(),
        gid=os.getgid(),
    )

    state = destination.stat(follow_symlinks=False)
    assert state.st_uid == os.getuid()
    assert state.st_gid == os.getgid()
    assert stat.S_IMODE(state.st_mode) == 0o444
    assert state.st_nlink == 1


def test_context_repair_updates_only_stale_source_binding_and_writes_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, head, tree = _fixture(tmp_path)
    stale_publisher = paths.context_generation_pointer.resolve() / "tgw-context-publish"
    stale_publisher.chmod(0o755)
    stale_publisher.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    stale_publisher.chmod(0o555)
    selected_publisher = (
        paths.runtime_root / "releases" / head / "scripts/tgw_context_publish.py"
    )
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    original_run = doctor_cli._run
    publisher_env = {}
    invoked_publishers = []

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            invoked_publishers.append(Path(command[0]))
            publisher_env.update(kwargs["env"])
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            task = json.loads(task_path.read_text())
            updated_cursor = json.loads(cursor_path.read_text())
            output_path.write_bytes(publish_bytes(task, updated_cursor))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    probes = []

    def probe(launcher, actor, expected, **kwargs):
        descriptor = kwargs["staged_snapshot_descriptor"]
        metadata = os.fstat(descriptor)
        assert metadata.st_uid == paths.context_install_uid
        assert metadata.st_gid == paths.context_install_gid
        assert stat.S_IMODE(metadata.st_mode) == 0o400
        os.lseek(descriptor, 0, os.SEEK_SET)
        retained_raw = b""
        while chunk := os.read(descriptor, 11):
            retained_raw += chunk
        assert json.loads(retained_raw) == {
            key: value for key, value in expected.items() if key != "task"
        }
        probes.append((launcher, actor, expected, kwargs))
        return {"generation": "CURRENT"}

    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        probe,
    )

    result = doctor_cli.repair_context(paths)

    repaired = json.loads(paths.context_cursor.read_text())
    assert result["ok"] is True
    assert result["changed"] is True
    assert repaired["source_commit"] == head
    assert repaired["source_tree"] == tree
    live_snapshot = paths.context_snapshot.stat(follow_symlinks=False)
    assert stat.S_ISREG(live_snapshot.st_mode)
    assert live_snapshot.st_nlink == 1
    assert live_snapshot.st_uid == paths.context_install_uid
    assert live_snapshot.st_gid == paths.context_install_gid
    assert stat.S_IMODE(live_snapshot.st_mode) == 0o444
    assert invoked_publishers == [selected_publisher]
    assert invoked_publishers[0] != stale_publisher
    assert publisher_env == {
        "LANG": "C.UTF-8",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(paths.runtime_root / "releases" / head / "src"),
    }
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert receipt["operation"] == "context"
    assert receipt["receipt_sha256"].startswith("sha256:")
    assert len(probes) == 1
    assert probes[0][0] == (
        paths.runtime_root / "releases" / head / "scripts/tgw_context_debian_stdio.py"
    )
    assert "staged_snapshot_descriptor" in probes[0][3]


def test_explicit_exact_context_repair_reconciles_stale_task_source_without_authority_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, head, tree = _fixture(tmp_path)
    task = json.loads(paths.context_task.read_text())
    stale_commit = "b" * 40
    stale_tree = "c" * 40
    task["source"] = {
        "repository": str(paths.repository),
        "commit": stale_commit,
        "tree": stale_tree,
        "canonical_working_tree_clean": True,
    }
    task["implementation"]["development_source"].update(
        {"commit": stale_commit, "tree": stale_tree, "state": "OLD_LIVE_STATE"}
    )
    cursor = json.loads(paths.context_cursor.read_text())
    cursor.update({"source_commit": stale_commit, "source_tree": stale_tree})
    _write_json(paths.context_task, task)
    _write_json(paths.context_cursor, cursor)
    _write_json(paths.context_snapshot, _snapshot(task, cursor))
    materialization = {
        "schema": "tgw-local-coding-bootstrap-materialization/v1",
        "actor": "db",
        "commit": head,
        "tree": tree,
    }
    materialization["receipt_hash"] = doctor_cli._hash(materialization)
    bootstrap_after = {
        "schema": "tgw-local-coding-bootstrap/v1",
        "ok": True,
        "commit": head,
        "tree": tree,
        "configuration_sha256": "sha256:" + "1" * 64,
        "materialization": materialization,
    }
    bootstrap_receipt = {
        "schema": "tgw-local-doctor-repair-receipt/v1",
        "operation": "coding-bootstrap",
        "performed_at": "2026-08-24T00:00:00+00:00",
        "actor": "root",
        "before": {},
        "after": bootstrap_after,
    }
    bootstrap_receipt["receipt_sha256"] = doctor_cli._hash(bootstrap_receipt)
    bootstrap_path = paths.receipts / "20260824T000000000000Z-coding-bootstrap.json"
    _write_json(bootstrap_path, bootstrap_receipt)
    bootstrap_path.chmod(0o444)

    selected = doctor_cli._selected_context_artifacts(paths)
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name == "tgw_context_publish.py":
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(
                publish_bytes(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                )
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_source_identity", lambda _paths: (head, tree, ""))
    monkeypatch.setattr(
        doctor_cli,
        "_repair_context_artifacts",
        lambda *_args, **_kwargs: selected,
    )
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: {"generation": "CURRENT"},
    )

    result = doctor_cli.repair_context(
        paths, desired_commit=head, source_root=tmp_path / "exact-source"
    )

    repaired_task = json.loads(paths.context_task.read_text())
    repaired_cursor = json.loads(paths.context_cursor.read_text())
    history = repaired_task["source_reconciliation_history"]
    assert result["ok"] is True
    assert repaired_task["source"]["commit"] == head
    assert repaired_task["source"]["tree"] == tree
    assert repaired_task["implementation"]["development_source"]["commit"] == head
    assert repaired_task["implementation"]["development_source"]["tree"] == tree
    assert repaired_task["implementation"]["development_source"]["state"] == (
        "CANONICAL_SOURCE_CURRENT"
    )
    workflow = repaired_task["implementation"]["coding_workflow"]
    assert workflow == {
        "state": "LIVE_EXACT_LOCAL_RUNTIME",
        "commit": head,
        "tree": tree,
        "release": str((paths.runtime_root / "releases" / head).resolve()),
        "bootstrap_receipt": str(bootstrap_path),
        "materialization_receipt_hash": materialization["receipt_hash"],
        "configuration_sha256": "sha256:" + "1" * 64,
        "context_required": False,
        "review_authority": False,
    }
    workflow_history = repaired_task["coding_workflow_reconciliation_history"]
    assert workflow_history[-1]["previous"] == {"commit": head}
    assert workflow_history[-1]["successor"] == workflow
    assert workflow_history[-1]["authority"] is False
    assert history[-1]["previous"]["commit"] == stale_commit
    assert history[-1]["previous"]["task_source"] == {
        "repository": str(paths.repository),
        "commit": stale_commit,
        "tree": stale_tree,
        "canonical_working_tree_clean": True,
    }
    assert history[-1]["successor"] == {"commit": head, "tree": tree}
    assert history[-1]["authority"] is False
    assert history[-1]["semantic_reconciliation"] == "CODING_RUNTIME_EXACT"
    reconciliation = dict(history[-1])
    claimed_hash = reconciliation.pop("reconciliation_hash")
    assert claimed_hash == doctor_cli._hash(reconciliation)
    repair_receipt = json.loads(Path(result["receipt"]).read_text())
    assert repair_receipt["before"]["task"]["source"] == {
        "repository": str(paths.repository),
        "commit": stale_commit,
        "tree": stale_tree,
        "canonical_working_tree_clean": True,
    }
    assert repaired_cursor["source_commit"] == head
    assert repaired_cursor["source_tree"] == tree


def test_bootstrap_materialization_evidence_requires_exact_self_hash() -> None:
    commit = "a" * 40
    tree = "b" * 40
    unsigned = {
        "schema": "tgw-local-coding-bootstrap-materialization/v1",
        "actor": "db",
        "commit": commit,
        "tree": tree,
    }
    valid = {**unsigned, "receipt_hash": doctor_cli._hash(unsigned)}

    assert doctor_cli._validated_bootstrap_materialization(
        valid, commit=commit, tree=tree
    ) == valid

    forged = {**valid, "receipt_hash": "sha256:" + "0" * 64}
    with pytest.raises(doctor_cli.DoctorError, match="evidence is invalid"):
        doctor_cli._validated_bootstrap_materialization(
            forged, commit=commit, tree=tree
        )


@pytest.mark.parametrize("hostile_kind", ["symlink", "hardlink"])
def test_context_repair_rejects_linked_publisher_output_before_open_without_mutating_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_kind: str
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    victim = tmp_path / "victim"
    victim.write_bytes(b"do not alter\n")
    victim.chmod(0o644)
    victim_before = victim.stat()
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name == "tgw_context_publish.py":
            output = Path(command[command.index("--output") + 1])
            if hostile_kind == "symlink":
                output.symlink_to(victim)
            else:
                os.link(victim, output)
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: pytest.fail("linked output reached cold probe"),
    )

    with pytest.raises((OSError, doctor_cli.DoctorError)):
        doctor_cli.repair_context(paths)

    victim_after = victim.stat()
    assert victim.read_bytes() == b"do not alter\n"
    assert (victim_after.st_uid, victim_after.st_gid, stat.S_IMODE(victim_after.st_mode)) == (
        victim_before.st_uid,
        victim_before.st_gid,
        stat.S_IMODE(victim_before.st_mode),
    )


@pytest.mark.parametrize("probe_fails", [False, True])
def test_context_repair_retained_descriptor_defeats_path_replacement_after_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_fails: bool
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    original_run = doctor_cli._run
    published: list[bytes] = []
    staged_path: Path | None = None

    def run(command, **kwargs):
        nonlocal staged_path
        if Path(command[0]).name == "tgw_context_publish.py":
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            staged_path = Path(command[command.index("--output") + 1])
            raw = publish_bytes(
                json.loads(task_path.read_text()), json.loads(cursor_path.read_text())
            )
            published.append(raw)
            staged_path.write_bytes(raw)
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    real_fchown = doctor_cli.os.fchown
    replacement_state: list[os.stat_result] = []

    def fchown(descriptor: int, uid: int, gid: int) -> None:
        assert staged_path is not None
        displaced = staged_path.with_suffix(".retained")
        staged_path.rename(displaced)
        staged_path.write_bytes(b"hostile replacement\n")
        staged_path.chmod(0o666)
        replacement_state.append(staged_path.stat())
        real_fchown(descriptor, uid, gid)

    observed_fd: list[int] = []
    close_count = 0
    retained_closed = False
    real_close = doctor_cli.os.close

    def close(descriptor: int) -> None:
        nonlocal close_count, retained_closed
        if observed_fd and descriptor == observed_fd[0] and not retained_closed:
            close_count += 1
            retained_closed = True
        real_close(descriptor)

    def probe(_launcher, _actor, _expected, **kwargs):
        descriptor = kwargs["staged_snapshot_descriptor"]
        observed_fd.append(descriptor)
        retained = os.fstat(descriptor)
        assert retained.st_uid == paths.context_install_uid
        assert retained.st_gid == paths.context_install_gid
        assert stat.S_IMODE(retained.st_mode) == 0o400
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks = []
        while chunk := os.read(descriptor, 7):
            chunks.append(chunk)
        assert b"".join(chunks) == published[0]
        assert staged_path is not None
        assert staged_path.read_bytes() == b"hostile replacement\n"
        assert stat.S_IMODE(staged_path.stat().st_mode) == 0o666
        if probe_fails:
            raise doctor_cli.DoctorError("injected retained probe failure")
        return {"generation": "CURRENT"}

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli.os, "fchown", fchown)
    monkeypatch.setattr(doctor_cli.os, "close", close)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", probe)

    if probe_fails:
        with pytest.raises(doctor_cli.DoctorError, match="retained probe failure"):
            doctor_cli.repair_context(paths)
    else:
        result = doctor_cli.repair_context(paths)
        assert result["ok"] is True

    assert len(observed_fd) == 1
    assert close_count == 1
    with pytest.raises(OSError):
        os.fstat(observed_fd[0])
    assert len(replacement_state) == 1


@pytest.mark.parametrize("probe_fails", [False, True])
def test_context_repair_close_failure_preserves_primary_and_reports_cleanup_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_fails: bool
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    live_paths = (
        paths.context_task,
        paths.context_cursor,
        paths.context_snapshot,
        paths.context_generation_pointer,
        paths.context_launcher,
        paths.context_publisher,
    )
    live_before = {path: doctor_cli._surface_snapshot(path) for path in live_paths}
    original_run = doctor_cli._run
    publisher_calls = 0
    probe_calls = 0
    close_calls = 0
    close_failure_injected = False
    retained_descriptor: int | None = None
    primary = doctor_cli.DoctorError("distinct injected cold-probe failure")

    def run(command, **kwargs):
        nonlocal publisher_calls
        if Path(command[0]).name == "tgw_context_publish.py":
            publisher_calls += 1
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(
                publish_bytes(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                )
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    def probe(_launcher, _actor, _expected, **kwargs):
        nonlocal probe_calls, retained_descriptor
        probe_calls += 1
        retained_descriptor = kwargs["staged_snapshot_descriptor"]
        if probe_fails:
            raise primary
        return {"generation": "CURRENT"}

    real_close = doctor_cli.os.close

    def close(descriptor: int) -> None:
        nonlocal close_calls, close_failure_injected
        if retained_descriptor is not None and descriptor == retained_descriptor:
            if close_failure_injected:
                try:
                    os.fstat(descriptor)
                except OSError:
                    # Only an actual retry of the released descriptor counts;
                    # later durable-receipt work may legitimately reuse its
                    # numeric value for a newly opened file.
                    close_calls += 1
                return real_close(descriptor)
            close_calls += 1
            close_failure_injected = True
            # Model Linux close reporting an error after it has released the
            # descriptor.  The implementation must not retry this number.
            real_close(descriptor)
            raise OSError("distinct injected close failure")
        real_close(descriptor)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli.os, "close", close)
    monkeypatch.setattr(
        doctor_cli, "_require_trusted_root_program", lambda *_args: None
    )
    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", probe)

    with pytest.raises(doctor_cli.DoctorError) as caught:
        doctor_cli.repair("context", paths)

    assert publisher_calls == 1
    assert probe_calls == 1
    assert close_calls == 1
    assert retained_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(retained_descriptor)
    assert all(
        doctor_cli._surface_snapshot(path) == surface
        for path, surface in live_before.items()
    )
    assert "distinct injected close failure" in str(caught.value)
    if probe_fails:
        assert caught.value.__cause__ is primary
        assert "distinct injected cold-probe failure" in str(caught.value)
        assert getattr(primary, "cleanup_failures") == (
            "staged Context snapshot descriptor cleanup failed: "
            "distinct injected close failure",
        )
    else:
        assert "cold-probe failure" not in str(caught.value)
    failure_receipts = list(paths.receipts.glob("*context-failed.json"))
    assert len(failure_receipts) == 1
    durable_error = json.loads(failure_receipts[0].read_text())["after"]["error"]
    assert "distinct injected close failure" in durable_error
    assert (
        "distinct injected cold-probe failure" in durable_error
    ) is probe_fails


def test_context_repair_is_semantically_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor_inode = paths.context_cursor.stat().st_ino
    snapshot_inode = paths.context_snapshot.stat().st_ino
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(publish_bytes(
                json.loads(task_path.read_text()),
                json.loads(cursor_path.read_text()),
            ))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: {"generation": "CURRENT"},
    )

    result = doctor_cli.repair_context(paths)

    assert result["changed"] is False
    assert paths.context_cursor.stat().st_ino == cursor_inode
    assert paths.context_snapshot.stat().st_ino == snapshot_inode


def test_context_repair_replaces_metadata_drift_without_changing_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    expected = paths.context_snapshot.read_bytes()
    paths.context_snapshot.chmod(0o644)
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(
                publish_bytes(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                )
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(
        doctor_cli, "_require_trusted_root_program", lambda *_args: None
    )
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: {"generation": "CURRENT"},
    )

    result = doctor_cli.repair_context(paths)

    metadata = paths.context_snapshot.stat(follow_symlinks=False)
    assert result["changed"] is True
    assert paths.context_snapshot.read_bytes() == expected
    assert metadata.st_uid == paths.context_install_uid
    assert metadata.st_gid == paths.context_install_gid
    assert stat.S_IMODE(metadata.st_mode) == 0o444


def test_context_publisher_failure_leaves_live_inputs_unchanged_and_is_receipted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor_before = paths.context_cursor.read_bytes()
    snapshot_before = paths.context_snapshot.read_bytes()
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
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


def test_context_failed_staged_cold_preflight_leaves_all_live_inputs_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    live_before = {
        path: doctor_cli._surface_snapshot(path)
        for path in (
            paths.context_task,
            paths.context_cursor,
            paths.context_snapshot,
            paths.context_generation_pointer,
            paths.context_launcher,
            paths.context_publisher,
        )
    }
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(
                publish_bytes(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                )
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            doctor_cli.DoctorError("candidate is unlaunchable")
        ),
    )

    with pytest.raises(doctor_cli.DoctorError, match="unlaunchable"):
        doctor_cli.repair_context(paths)

    assert all(
        doctor_cli._surface_snapshot(path) == surface
        for path, surface in live_before.items()
    )


def test_context_repair_bootstraps_past_stale_installed_publisher_and_failed_preflight_is_non_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, head, _tree = _fixture(tmp_path)
    selected_release = paths.runtime_root / "releases" / head
    selected_publisher = selected_release / "scripts/tgw_context_publish.py"
    selected_launcher = selected_release / "scripts/tgw_context_debian_stdio.py"
    live_generation = paths.context_generation_pointer.resolve()
    stale_publisher = live_generation / "tgw-context-publish"
    stale_publisher.chmod(0o755)
    stale_publisher.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    stale_publisher.chmod(0o555)
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    live_before = {
        path: doctor_cli._surface_snapshot(path)
        for path in (
            paths.context_snapshot,
            paths.context_generation_pointer,
            paths.context_launcher,
            paths.context_publisher,
        )
    }
    invoked: list[Path] = []
    original_run = doctor_cli._run

    def run(command, **kwargs):
        if Path(command[0]).name == "tgw_context_publish.py":
            invoked.append(Path(command[0]))
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(
                publish_bytes(
                    json.loads(task_path.read_text()),
                    json.loads(cursor_path.read_text()),
                )
            )
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    def fail_selected_preflight(launcher, _actor, _expected, **kwargs):
        assert launcher == selected_launcher
        assert isinstance(kwargs["staged_snapshot_descriptor"], int)
        raise doctor_cli.DoctorError("selected runtime preflight failed")

    monkeypatch.setattr(doctor_cli, "_probe_context_stdio", fail_selected_preflight)

    with pytest.raises(doctor_cli.DoctorError, match="selected runtime preflight failed"):
        doctor_cli.repair_context(paths)

    assert invoked == [selected_publisher]
    assert invoked[0] != stale_publisher
    assert all(
        doctor_cli._surface_snapshot(path) == surface
        for path, surface in live_before.items()
    )


def test_context_repair_refuses_writable_context_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, head, _tree = _fixture(tmp_path)
    module = paths.runtime_root / "releases" / head / "src/tgw/current_context_snapshot.py"
    module.chmod(0o666)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="release tree differs from Git"):
        doctor_cli.repair_context(paths)


def test_context_repair_requires_the_exact_context_release_group(
    tmp_path: Path,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    paths = replace(paths, context_install_gid=os.getgid() + 1)

    with pytest.raises(doctor_cli.DoctorError, match="root:root immutable"):
        doctor_cli._selected_context_artifacts(paths)


def test_context_repair_refuses_symlinked_context_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, head, _tree = _fixture(tmp_path)
    module = paths.runtime_root / "releases" / head / "src/tgw/current_context_snapshot.py"
    outside = tmp_path / "outside-context-module.py"
    outside.write_text("# outside\n", encoding="utf-8")
    module.parent.chmod(0o755)
    module.unlink()
    module.symlink_to(outside)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)

    with pytest.raises(doctor_cli.DoctorError, match="release tree differs from Git"):
        doctor_cli.repair_context(paths)


def test_context_repair_preserves_concurrent_snapshot_and_restores_its_cursor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    task = json.loads(paths.context_task.read_text())
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    _write_json(paths.context_snapshot, _snapshot(task, cursor))
    cursor_before = paths.context_cursor.read_bytes()
    original_run = doctor_cli._run
    original_cas = doctor_cli._cas_regular_file

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(publish_bytes(
                json.loads(task_path.read_text()),
                json.loads(cursor_path.read_text()),
            ))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    concurrent_snapshot = {"concurrent": "snapshot"}
    raced = False

    def cas(path, expected, replacement, **kwargs):
        nonlocal raced
        if path == paths.context_snapshot and not raced:
            raced = True
            _write_json(paths.context_snapshot, concurrent_snapshot)
        return original_cas(path, expected, replacement, **kwargs)

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_cas_regular_file", cas)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: {"generation": "CURRENT"},
    )

    with pytest.raises(doctor_cli.DoctorError, match="concurrent change"):
        doctor_cli.repair_context(paths)

    assert paths.context_cursor.read_bytes() == cursor_before
    assert json.loads(paths.context_snapshot.read_text()) == concurrent_snapshot


def test_context_repair_never_overwrites_concurrent_cursor_during_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    task = json.loads(paths.context_task.read_text())
    cursor = json.loads(paths.context_cursor.read_text())
    cursor["source_commit"] = "b" * 40
    cursor["source_tree"] = "c" * 40
    _write_json(paths.context_cursor, cursor)
    _write_json(paths.context_snapshot, _snapshot(task, cursor))
    original_run = doctor_cli._run
    original_cas = doctor_cli._cas_regular_file

    def run(command, **kwargs):
        if Path(command[0]).name in {"tgw-context-publish", "tgw_context_publish.py"}:
            task_path = Path(command[command.index("--task") + 1])
            cursor_path = Path(command[command.index("--cursor") + 1])
            output_path = Path(command[command.index("--output") + 1])
            output_path.write_bytes(publish_bytes(
                json.loads(task_path.read_text()),
                json.loads(cursor_path.read_text()),
            ))
            return subprocess.CompletedProcess(command, 0, "", "")
        return original_run(command, **kwargs)

    concurrent_cursor = {"concurrent": "cursor"}
    concurrent_snapshot = {"concurrent": "snapshot"}
    raced = False

    def cas(path, expected, replacement, **kwargs):
        nonlocal raced
        if path != paths.context_snapshot or raced:
            return original_cas(path, expected, replacement, **kwargs)
        raced = True
        _write_json(paths.context_snapshot, concurrent_snapshot)
        try:
            return original_cas(path, expected, replacement, **kwargs)
        except doctor_cli.DoctorError:
            _write_json(paths.context_cursor, concurrent_cursor)
            raise

    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_cas_regular_file", cas)
    monkeypatch.setattr(doctor_cli, "_require_trusted_root_program", lambda *_args: None)
    monkeypatch.setattr(
        doctor_cli,
        "_probe_context_stdio",
        lambda *_args, **_kwargs: {"generation": "CURRENT"},
    )

    with pytest.raises(doctor_cli.DoctorError, match="refused rollback overwrite"):
        doctor_cli.repair_context(paths)

    assert json.loads(paths.context_cursor.read_text()) == concurrent_cursor
    assert json.loads(paths.context_snapshot.read_text()) == concurrent_snapshot


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


def test_fixed_operator_launcher_survives_mutable_runtime_selector_swap(
    tmp_path: Path,
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    fixed_bytes = paths.operator_cli.read_bytes()
    hostile = paths.runtime_root / "releases/hostile/bin"
    hostile.mkdir(parents=True)
    (hostile / "tgw-operator").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    current = paths.runtime_root / "current"
    current.unlink()
    current.symlink_to(Path("releases/hostile"))

    observed = paths.operator_cli.stat(follow_symlinks=False)
    assert stat.S_ISREG(observed.st_mode)
    assert not paths.operator_cli.is_symlink()
    assert paths.operator_cli.read_bytes() == fixed_bytes
    assert b"privileged execution through the mutable coding runtime is disabled" in fixed_bytes
    launcher = fixed_bytes.decode("utf-8")
    assert launcher.index('"$(/usr/bin/id -u)" -eq 0') < launcher.index(
        "/opt/TGW/tgw-lib/coding-runtime/current"
    )


def test_runtime_repair_switches_only_one_selector_behind_stable_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_runtime_repair_refuses_online_launcher_surface_rewrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    stale = paths.local_bin / "tgw-coding"
    stale.unlink()
    stale.write_text("old launcher\n", encoding="utf-8")
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)

    with pytest.raises(doctor_cli.DoctorError, match="bounded bootstrap"):
        doctor_cli.repair_runtime(paths)


def test_runtime_selector_rolls_back_if_post_switch_release_check_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_runtime_selector_rollback_preserves_a_concurrent_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    previous = paths.runtime_root / "releases/previous"
    previous.mkdir()
    concurrent = paths.runtime_root / "releases/concurrent"
    concurrent.mkdir()
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
            current.unlink()
            current.symlink_to(Path("releases/concurrent"))
            raise doctor_cli.DoctorError("release changed after selector switch")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(doctor_cli, "_verify_release_tree", verify)

    with pytest.raises(doctor_cli.DoctorError, match="concurrent selector change"):
        doctor_cli.repair_runtime(paths)

    assert current.readlink() == Path("releases/concurrent")


def test_doctor_effective_root_source_status_is_demoted_to_ordinary_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    observed: list[tuple[list[str], object]] = []

    monkeypatch.setattr(doctor_cli, "_git", lambda *_args: commit)
    monkeypatch.setattr(doctor_cli.os, "getuid", lambda: 1004)
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(doctor_cli.os, "getresuid", lambda: (1004, 0, 0))

    def run(command, **kwargs):
        observed.append((command, kwargs.get("env")))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor_cli.subprocess, "run", run)

    assert doctor_cli._source_identity(
        replace(doctor_cli.DoctorPaths(), repository=tmp_path)
    ) == (commit, commit, "")
    command, environment = observed.pop()
    assert command[:7] == [
        "/usr/sbin/runuser",
        "-u",
        "db",
        "-g",
        "tgw-coders",
        "--",
        "/usr/bin/env",
    ]
    assert "-i" in command
    assert "/usr/bin/git" in command
    assert environment is None


def test_runtime_selector_lock_accepts_the_materializer_lock(
    tmp_path: Path,
) -> None:
    operations = tmp_path / "runtime/operations"
    operations.mkdir(parents=True)
    operations.chmod(0o750)
    lock = operations / ".selector.lock"
    lock.touch(mode=0o600)
    paths = replace(
        doctor_cli.DoctorPaths(),
        runtime_root=tmp_path / "runtime",
        coding_root_effect_uid=os.getuid(),
    )

    with doctor_cli._runtime_selector_lock(paths):
        assert lock.stat(follow_symlinks=False).st_nlink == 1


def test_unit_definition_requires_exact_fragment_and_no_dropins(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)
    paths = replace(paths, systemd_unit_uid=os.getuid(), systemd_unit_gid=os.getgid())
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o444)
    state = {
        "FragmentPath": str(fragment),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": " ".join(doctor_cli._UNIT_ARGV[unit]),
        "ActiveState": "inactive",
        "MainPID": "0",
    }

    exact = doctor_cli._unit_definition(paths, unit, state)
    with_dropin = doctor_cli._unit_definition(paths, unit, {**state, "DropInPaths": "/etc/systemd/system/x.conf"})

    assert exact["exact"] is True
    assert with_dropin["exact"] is False
    assert "unexpected systemd drop-in" in with_dropin["reasons"]


def test_unit_definition_rejects_loaded_exec_start_with_extra_argument(
    tmp_path: Path,
) -> None:
    paths, head, _tree = _fixture(tmp_path)
    paths = replace(paths, systemd_unit_uid=os.getuid(), systemd_unit_gid=os.getgid())
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o444)
    expected = doctor_cli._UNIT_ARGV[unit]
    state = {
        "FragmentPath": str(fragment),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": (f"{{ path={expected[0]} ; argv[]={' '.join(expected)} --extra ; ignore_errors=no ; start_time=[n/a] ; }}}}"),
        "ActiveState": "inactive",
        "MainPID": "0",
    }

    result = doctor_cli._unit_definition(paths, unit, state)

    assert result["exact"] is False
    assert "loaded ExecStart differs" in result["reasons"]


def test_unit_definition_rejects_active_process_with_different_argv(tmp_path: Path) -> None:
    paths, head, _tree = _fixture(tmp_path)
    paths = replace(paths, systemd_unit_uid=os.getuid(), systemd_unit_gid=os.getgid())
    unit = "tgw-codex-implement-worker.service"
    source = paths.runtime_root / "releases" / head / "systemd" / unit
    fragment = tmp_path / "installed.service"
    shutil.copyfile(source, fragment)
    fragment.chmod(0o444)
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


def test_service_process_runtime_identity_uses_resolved_working_directory(
    tmp_path: Path,
) -> None:
    release = tmp_path / "runtime/releases/current"
    stale = tmp_path / "runtime/releases/stale"
    release.mkdir(parents=True)
    stale.mkdir(parents=True)
    process = tmp_path / "proc/123"
    process.mkdir(parents=True)
    (process / "cwd").symlink_to(stale)
    state = {
        "Unit": "tgw-codex-implement-worker.service",
        "MainPID": "123",
        "InvocationID": "1" * 32,
        "ExecMainStartTimestampMonotonic": "100",
    }

    stale_result = doctor_cli._service_process_runtime_identity(
        state,
        release,
        proc_root=tmp_path / "proc",
        state_reader=lambda _unit: state,
    )
    (process / "cwd").unlink()
    (process / "cwd").symlink_to(release)
    exact_result = doctor_cli._service_process_runtime_identity(
        state,
        release,
        proc_root=tmp_path / "proc",
        state_reader=lambda _unit: state,
    )

    assert stale_result["status"] == "STALE"
    assert stale_result["restart_safe"] is True
    assert stale_result["exact"] is False
    assert stale_result["loaded_release"] == str(stale.resolve())
    assert exact_result["status"] == "EXACT"
    assert exact_result["exact"] is True
    assert exact_result["loaded_release"] == str(release.resolve())


def test_service_process_runtime_identity_retries_changed_systemd_invocation(
    tmp_path: Path,
) -> None:
    release = tmp_path / "runtime/releases" / ("a" * 40)
    stale = tmp_path / "runtime/releases" / ("b" * 40)
    release.mkdir(parents=True)
    stale.mkdir(parents=True)
    proc = tmp_path / "proc"
    for pid, cwd in (("101", stale), ("202", release)):
        process = proc / pid
        process.mkdir(parents=True)
        (process / "cwd").symlink_to(cwd)
    unit = "tgw-codex-implement-worker.service"
    first = {
        "Unit": unit,
        "MainPID": "101",
        "InvocationID": "1" * 32,
        "ExecMainStartTimestampMonotonic": "100",
    }
    replacement = {
        "Unit": unit,
        "MainPID": "202",
        "InvocationID": "2" * 32,
        "ExecMainStartTimestampMonotonic": "200",
    }
    observations = iter((replacement, replacement))

    result = doctor_cli._service_process_runtime_identity(
        first,
        release,
        proc_root=proc,
        state_reader=lambda _unit: next(observations),
    )

    assert result["status"] == "EXACT"
    assert result["pid"] == 202
    assert result["invocation_id"] == "2" * 32
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["before"] != result["attempts"][0]["after"]


def test_service_process_runtime_identity_reports_exhausted_replacement_race(
    tmp_path: Path,
) -> None:
    release = tmp_path / "runtime/releases" / ("a" * 40)
    release.mkdir(parents=True)
    proc = tmp_path / "proc"
    for pid in ("101", "202"):
        process = proc / pid
        process.mkdir(parents=True)
        (process / "cwd").symlink_to(release)
    unit = "tgw-codex-implement-worker.service"

    def state(pid: str, generation: str, started: str) -> dict[str, str]:
        return {
            "Unit": unit,
            "MainPID": pid,
            "InvocationID": generation * 32,
            "ExecMainStartTimestampMonotonic": started,
        }

    first = state("101", "1", "100")
    observations = iter((state("202", "2", "200"), state("303", "3", "300")))

    result = doctor_cli._service_process_runtime_identity(
        first,
        release,
        proc_root=proc,
        state_reader=lambda _unit: next(observations),
    )

    assert result["status"] == "RACED"
    assert result["restart_safe"] is False
    assert result["pid"] == 303
    assert len(result["attempts"]) == 2


def test_service_process_runtime_identity_classifies_stable_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = tmp_path / "runtime/releases" / ("a" * 40)
    release.mkdir(parents=True)
    state = {
        "Unit": "tgw-codex-implement-worker.service",
        "MainPID": "123",
        "InvocationID": "1" * 32,
        "ExecMainStartTimestampMonotonic": "100",
    }
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1, "", "denied"),
    )

    result = doctor_cli._service_process_runtime_identity(
        state,
        release,
        proc_root=tmp_path / "proc",
        state_reader=lambda _unit: state,
    )

    assert result["status"] == "UNREADABLE"
    assert result["restart_safe"] is False
    assert result["exact"] is False


def test_check_units_rejects_active_service_from_stale_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = "a" * 40
    paths = replace(
        doctor_cli.DoctorPaths(), runtime_root=tmp_path / "runtime"
    )
    (paths.runtime_root / "releases" / commit).mkdir(parents=True)
    stale_unit = "tgw-codex-implement-worker.service"

    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda unit: {
            "Unit": unit,
            "LoadState": "loaded",
            "ActiveState": "active" if unit in doctor_cli._ACTIVE_CODING_UNITS else "inactive",
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_definition",
        lambda *_args, **_kwargs: {
            "exact": True,
            "desired_commit": commit,
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_service_process_runtime_identity",
        lambda state, _release: {
            "exact": state["Unit"] != stale_unit,
            "reason": "loaded process predates selected immutable runtime",
        },
    )

    result = doctor_cli.check_units(paths, desired_commit=commit)

    assert result["state"] == "FAIL"
    assert stale_unit in result["detail"]
    assert result["evidence"]["units"][stale_unit]["process_runtime"]["exact"] is False


def _stub_worker_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    restart_fails: bool = False,
    initial_status: str = "STALE",
    post_check_passes: bool = True,
) -> tuple[doctor_cli.DoctorPaths, str, str, list[list[str]]]:
    commit = "a" * 40
    tree = "b" * 40
    paths = replace(
        doctor_cli.DoctorPaths(),
        repository=tmp_path / "repository",
        runtime_root=tmp_path / "runtime",
        systemd_install_root=tmp_path / "systemd",
        receipts=tmp_path / "doctor-receipts",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    (paths.runtime_root / "releases" / commit).mkdir(parents=True)
    paths.repository.mkdir()
    paths.systemd_install_root.mkdir()
    paths.receipts.mkdir()
    stale_unit = "tgw-codex-implement-worker.service"
    runtime_status = {
        unit: initial_status if unit == stale_unit else "EXACT"
        for unit in doctor_cli._ACTIVE_CODING_UNITS
    }
    generations = {unit: 1 for unit in doctor_cli._ACTIVE_CODING_UNITS}
    commands: list[list[str]] = []

    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_source_identity", lambda _paths: (commit, tree, "")
    )
    monkeypatch.setattr(doctor_cli, "_git", lambda *_args: tree)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": tree}
    )
    monkeypatch.setattr(
        doctor_cli, "read_exact_tree_file", lambda *_args, **_kwargs: (0o644, b"unit\n")
    )
    monkeypatch.setattr(
        doctor_cli, "_unit_destination_bytes_exact", lambda *_args: True
    )
    def unit_state(unit: str) -> dict[str, str]:
        generation = generations.get(unit, 0)
        return {
            "Unit": unit,
            "LoadState": "loaded",
            "ActiveState": "active" if unit in doctor_cli._ACTIVE_CODING_UNITS else "inactive",
            "SubState": "running" if unit in doctor_cli._ACTIVE_CODING_UNITS else "dead",
            "MainPID": str(1000 + generation) if unit.endswith(".service") else "0",
            "InvocationID": f"{generation:032x}" if generation else "",
            "ExecMainStartTimestampMonotonic": (
                str(10000 + generation) if unit.endswith(".service") else "0"
            ),
        }

    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)
    monkeypatch.setattr(
        doctor_cli,
        "_unit_definition",
        lambda *_args, **_kwargs: {"exact": True, "desired_commit": commit},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_service_process_runtime_identity",
        lambda state, _release: {
            "status": runtime_status[state["Unit"]],
            "exact": runtime_status[state["Unit"]] == "EXACT",
            "restart_safe": runtime_status[state["Unit"]] == "STALE",
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "check_units",
        lambda *_args, **_kwargs: {
            "state": "PASS" if post_check_passes else "FAIL"
        },
    )
    monkeypatch.setattr(doctor_cli, "_receipt", lambda *_args: "receipt")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command == ["systemctl", "restart", stale_unit]:
            if restart_fails:
                return subprocess.CompletedProcess(command, 1, "", "restart failed")
            runtime_status[stale_unit] = "EXACT"
        if (
            len(command) == 3
            and command[:2] in (["systemctl", "restart"], ["systemctl", "start"])
            and command[2] in generations
        ):
            generations[command[2]] += 1
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor_cli, "_run", run)
    return paths, commit, stale_unit, commands


def test_repair_workers_restarts_stale_generation_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, stale_unit, commands = _stub_worker_repair(tmp_path, monkeypatch)

    repaired = doctor_cli.repair_workers(paths, desired_commit=commit)
    commands.clear()
    unchanged = doctor_cli.repair_workers(paths, desired_commit=commit)

    assert repaired["service_actions"] == [f"restart:{stale_unit}"]
    assert unchanged["service_actions"] == []
    assert commands == []


def test_repair_workers_fails_closed_when_stale_generation_cannot_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, _stale_unit, _commands = _stub_worker_repair(
        tmp_path, monkeypatch, restart_fails=True
    )

    with pytest.raises(doctor_cli.DoctorError, match="restart failed"):
        doctor_cli.repair_workers(paths, desired_commit=commit)


def test_repair_workers_requires_exact_post_restart_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, stale_unit, commands = _stub_worker_repair(
        tmp_path, monkeypatch, post_check_passes=False
    )

    with pytest.raises(doctor_cli.DoctorError, match="remain unhealthy"):
        doctor_cli.repair_workers(paths, desired_commit=commit)

    assert ["systemctl", "restart", stale_unit] in commands


@pytest.mark.parametrize("status", ("RACED", "UNREADABLE"))
def test_repair_workers_never_restarts_unproven_process_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    paths, commit, stale_unit, commands = _stub_worker_repair(
        tmp_path, monkeypatch, initial_status=status
    )

    with pytest.raises(
        doctor_cli.DoctorError,
        match=rf"refusing to restart {stale_unit}: process runtime is {status.lower()}",
    ):
        doctor_cli.repair_workers(paths, desired_commit=commit)

    assert ["systemctl", "restart", stale_unit] not in commands


def test_repair_workers_exact_generation_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, _unit, commands = _stub_worker_repair(
        tmp_path, monkeypatch, initial_status="EXACT"
    )

    first = doctor_cli.repair_workers(paths, desired_commit=commit)
    second = doctor_cli.repair_workers(paths, desired_commit=commit)

    assert first["service_actions"] == []
    assert second["service_actions"] == []
    assert commands == []


def test_active_coding_service_units_bind_selected_immutable_runtime() -> None:
    for unit in doctor_cli._ACTIVE_CODING_UNITS:
        if not unit.endswith(".service"):
            continue
        text = (ROOT / "systemd" / unit).read_text(encoding="utf-8")
        assert "WorkingDirectory=/opt/TGW/tgw-lib/coding-runtime/current" in text
        assert "Environment=PYTHONPATH=src" in text
        assert (
            "Environment=PYTHONPATH=/opt/TGW/tgw-lib/coding-runtime/current/src"
            not in text
        )


def test_restart_obligation_is_root_bound_canonical_and_single_generation(
    tmp_path: Path,
) -> None:
    paths = replace(
        doctor_cli.DoctorPaths(),
        receipts=tmp_path / "receipts",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    paths.receipts.mkdir()
    unit = "tgw-codex-implement-worker.service"
    commit = "a" * 40
    tree = "b" * 40

    written = doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state={"MainPID": "123", "InvocationID": "c" * 32},
    )
    path = doctor_cli._restart_obligation_path(paths, unit)

    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) == written
    assert stat.S_IMODE(path.stat().st_mode) == 0o400
    assert path.stat().st_uid == os.getuid()
    assert path.stat().st_gid == os.getgid()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o711
    with pytest.raises(doctor_cli.DoctorError, match="binding is invalid"):
        doctor_cli._read_restart_obligation(
            paths, unit, commit="d" * 40, tree=tree
        )

    doctor_cli._clear_restart_obligation(
        paths, unit, commit=commit, tree=tree
    )
    assert not path.exists()


def test_restart_obligation_carries_debt_across_candidate_generations(
    tmp_path: Path,
) -> None:
    paths = replace(
        doctor_cli.DoctorPaths(),
        receipts=tmp_path / "receipts",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    paths.receipts.mkdir()
    unit = "tgw-codex-implement-worker.service"
    first = doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit="a" * 40,
        tree="b" * 40,
        reasons=["unit-definition-change"],
        state={"ActiveState": "inactive"},
    )

    successor = doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit="c" * 40,
        tree="d" * 40,
        reasons=["unit-definition-change"],
        state={
            "ActiveState": "active",
            "SubState": "running",
            "MainPID": "42",
            "InvocationID": "e" * 32,
            "ExecMainStartTimestampMonotonic": "4242",
        },
    )

    assert successor["predecessor_obligation_hash"] == first["obligation_hash"]
    assert "carried-forward-restart-debt" in successor["reasons"]
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit="c" * 40, tree="d" * 40
    ) == successor
    with pytest.raises(doctor_cli.DoctorError, match="binding is invalid"):
        doctor_cli._read_restart_obligation(
            paths, unit, commit="a" * 40, tree="b" * 40
        )


def test_doctor_reports_root_owned_restart_obligation_to_ordinary_actor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = replace(
        doctor_cli.DoctorPaths(),
        receipts=tmp_path / "receipts",
        runtime_root=tmp_path / "runtime",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    paths.receipts.mkdir()
    desired = "a" * 40
    unit = "tgw-codex-implement-worker.service"
    doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=desired,
        tree="b" * 40,
        reasons=["unit-definition-change"],
        state={"ActiveState": "inactive"},
    )
    monkeypatch.setattr(doctor_cli, "_privileged_repair_action", lambda *_args: "repair")
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda observed_unit: {
            "Unit": observed_unit,
            "LoadState": "loaded",
            "ActiveState": "active"
            if observed_unit in doctor_cli._ACTIVE_CODING_UNITS
            else "inactive",
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_definition",
        lambda *_args, **_kwargs: {
            "exact": True,
            "desired_commit": desired,
            "reasons": [],
        },
    )
    monkeypatch.setattr(
        doctor_cli,
        "_service_process_runtime_identity",
        lambda *_args, **_kwargs: {"exact": True, "status": "EXACT"},
    )

    result = doctor_cli.check_units(paths, desired_commit=desired)

    assert result["state"] == "FAIL"
    assert result["evidence"]["units"][unit]["restart_obligation"]["status"] == "PRESENT"


def test_repair_workers_replays_durable_restart_obligation_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, unit, commands = _stub_worker_repair(
        tmp_path,
        monkeypatch,
        restart_fails=True,
        initial_status="EXACT",
    )
    tree = "b" * 40
    doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state=doctor_cli._unit_state(unit),
    )

    with pytest.raises(doctor_cli.DoctorError, match="restart failed"):
        doctor_cli.repair_workers(paths, desired_commit=commit)
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) is not None

    def succeed(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor_cli, "_run", succeed)
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda observed_unit: {
            "Unit": observed_unit,
            "LoadState": "loaded",
            "ActiveState": "active"
            if observed_unit in doctor_cli._ACTIVE_CODING_UNITS
            else "inactive",
            "SubState": "running"
            if observed_unit in doctor_cli._ACTIVE_CODING_UNITS
            else "dead",
            "MainPID": "2002" if observed_unit.endswith(".service") else "0",
            "InvocationID": "2" * 32
            if observed_unit in doctor_cli._ACTIVE_CODING_UNITS
            else "",
            "ExecMainStartTimestampMonotonic": (
                "20002" if observed_unit.endswith(".service") else "0"
            ),
        },
    )
    commands.clear()
    result = doctor_cli.repair_workers(paths, desired_commit=commit)

    assert result["restart_obligations"] == [unit]
    assert ["systemctl", "daemon-reload"] in commands
    assert ["systemctl", "restart", unit] in commands
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) is None


def test_repair_workers_keeps_debt_when_restart_does_not_change_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, unit, commands = _stub_worker_repair(
        tmp_path,
        monkeypatch,
        initial_status="EXACT",
    )
    tree = "b" * 40
    doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state=doctor_cli._unit_state(unit),
    )

    def no_op_restart(
        command: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor_cli, "_run", no_op_restart)

    with pytest.raises(doctor_cli.DoctorError, match="did not load a new invocation"):
        doctor_cli.repair_workers(paths, desired_commit=commit)
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) is not None


def test_repair_workers_replays_debt_after_daemon_reload_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, unit, _commands = _stub_worker_repair(
        tmp_path,
        monkeypatch,
        initial_status="EXACT",
    )
    tree = "b" * 40
    doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state=doctor_cli._unit_state(unit),
    )
    successful_run = doctor_cli._run

    def fail_reload(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if command == ["systemctl", "daemon-reload"]:
            return subprocess.CompletedProcess(command, 1, "", "reload failed")
        return successful_run(command, **kwargs)

    monkeypatch.setattr(doctor_cli, "_run", fail_reload)
    with pytest.raises(doctor_cli.DoctorError, match="reload failed"):
        doctor_cli.repair_workers(paths, desired_commit=commit)
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) is not None

    monkeypatch.setattr(doctor_cli, "_run", successful_run)
    result = doctor_cli.repair_workers(paths, desired_commit=commit)

    assert f"restart:{unit}" in result["service_actions"]
    assert doctor_cli._read_restart_obligation(
        paths, unit, commit=commit, tree=tree
    ) is None


def test_repair_workers_keeps_transient_debt_until_old_invocation_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, commit, _unit, _commands = _stub_worker_repair(
        tmp_path,
        monkeypatch,
        initial_status="EXACT",
    )
    tree = "b" * 40
    transient = "tgw-coding-runtime-restart.service"
    original_state = doctor_cli._unit_state
    doctor_cli._write_restart_obligation(
        paths,
        transient,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state={"ActiveState": "activating", "SubState": "start"},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda unit: (
            {
                "Unit": unit,
                "LoadState": "loaded",
                "ActiveState": "activating",
                "SubState": "start",
            }
            if unit == transient
            else original_state(unit)
        ),
    )

    with pytest.raises(doctor_cli.DoctorError, match="transient coding unit remains active"):
        doctor_cli.repair_workers(paths, desired_commit=commit)
    assert doctor_cli._read_restart_obligation(
        paths, transient, commit=commit, tree=tree
    ) is not None

    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda unit: (
            {
                "Unit": unit,
                "LoadState": "loaded",
                "ActiveState": "inactive",
                "SubState": "dead",
            }
            if unit == transient
            else original_state(unit)
        ),
    )
    result = doctor_cli.repair_workers(paths, desired_commit=commit)

    assert transient in result["restart_obligations"]
    assert doctor_cli._read_restart_obligation(
        paths, transient, commit=commit, tree=tree
    ) is None


def test_restart_obligation_unsafe_metadata_fails_closed(tmp_path: Path) -> None:
    paths = replace(
        doctor_cli.DoctorPaths(),
        receipts=tmp_path / "receipts",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    paths.receipts.mkdir()
    unit = "tgw-codex-implement-worker.service"
    commit = "a" * 40
    tree = "b" * 40
    doctor_cli._write_restart_obligation(
        paths,
        unit,
        commit=commit,
        tree=tree,
        reasons=["unit-definition-change"],
        state={},
    )
    doctor_cli._restart_obligation_path(paths, unit).chmod(0o600)

    with pytest.raises(doctor_cli.DoctorError, match="metadata is unsafe"):
        doctor_cli._read_restart_obligation(
            paths, unit, commit=commit, tree=tree
        )


def test_relative_service_pythonpath_survives_selector_move_for_lazy_import(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    first = runtime / "releases" / ("a" * 40)
    second = runtime / "releases" / ("b" * 40)
    for release, value in ((first, "A"), (second, "B")):
        source = release / "src"
        source.mkdir(parents=True)
        (source / "lazy_generation.py").write_text(
            f"VALUE = {value!r}\n", encoding="utf-8"
        )
    current = runtime / "current"
    current.symlink_to(Path("releases") / first.name, target_is_directory=True)
    code = (
        "import json, os, sys\n"
        "print(json.dumps({'cwd': os.getcwd(), 'pythonpath': sys.path[1]}), flush=True)\n"
        "input()\n"
        "import lazy_generation\n"
        "print(lazy_generation.VALUE, flush=True)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=current,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "src",
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    started = json.loads(process.stdout.readline())
    replacement = runtime / "next"
    replacement.symlink_to(Path("releases") / second.name, target_is_directory=True)
    os.replace(replacement, current)
    try:
        stdout, stderr = process.communicate("\n", timeout=10)
    except BaseException:
        process.kill()
        process.wait(timeout=10)
        raise

    assert process.returncode == 0, stderr
    assert started == {
        "cwd": str(first.resolve()),
        "pythonpath": str((first / "src").resolve()),
    }
    assert stdout.strip() == "A"


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


def test_default_doctor_json_is_bounded_and_full_remains_explicit() -> None:
    report = {
        "schema": "diagnosis",
        "state": "FAIL",
        "counts": {"FAIL": 1},
        "exit_code": 1,
        "checks": [
            {
                "id": "context.snapshot",
                "state": "FAIL",
                "detail": "stale",
                "repairable": True,
                "operator_action": "repair context",
                "evidence": {"very_large": list(range(1000))},
            }
        ],
    }

    compact = doctor_cli._compact_diagnosis(report)

    assert compact["checks"] == [
        {
            "id": "context.snapshot",
            "state": "FAIL",
            "detail": "stale",
            "repairable": True,
            "operator_action": "repair context",
        }
    ]
    assert doctor_cli._parser().parse_args(["check", "--full"]).full is True
    assert doctor_cli._parser().parse_args(["inventory", "--full"]).full is True


def test_default_inventory_summary_limits_paths_and_counts_safe_cleanup() -> None:
    safe = {
        "path": "/worktree/safe",
        "canonical": False,
        "exists": True,
        "dirty": False,
        "unique_commits": 0,
        "merged_into_canonical": True,
        "errors": [],
        "preservation_required": False,
    }
    protected = {
        **safe,
        "path": "/worktree/protected",
        "dirty": True,
        "preservation_required": True,
    }
    report = {
        "ok": True,
        "host": "tgw-lib",
        "actor": "codex",
        "observed_at": "now",
        "canonical_source": {"commit": "a" * 40},
        "counts": {"worktrees": 2},
        "worktrees": [safe, protected],
        "cleanup_boundary": "preserve first",
    }

    compact = doctor_cli._compact_inventory(report)

    assert compact["safe_cleanup_candidates"] == {
        "count": 1,
        "sample": ["/worktree/safe"],
    }
    assert compact["preservation_required"] == {
        "count": 1,
        "sample": ["/worktree/protected"],
    }
    assert "worktrees" not in compact


def test_operator_launcher_routes_only_local_todo_coding_and_doctor() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "bin/tgw-operator").read_text(encoding="utf-8")

    assert "coding)" in launcher
    assert "doctor)" in launcher
    assert "todo)" in launcher
    assert "/opt/TGW/tgw-lib/bin/tgw-todo" in launcher
    assert "/opt/TGW/tgw-lib/bin/tgw-coding" in launcher
    assert "/opt/TGW/tgw-lib/bin/tgw-doctor" in launcher
    assert "exec /usr/local/libexec/tgw-production-client" in launcher
    assert '"$(/usr/bin/id -u)" -eq 0' in launcher
    assert "/usr/local/sbin/tgw-coding-bootstrap" in launcher


def test_role_sql_persists_explicit_todo_sequence_update_grant() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "config/tgw-coding-local-roles.sql").read_text(encoding="utf-8")

    assert "GRANT USAGE, SELECT, UPDATE" in sql
    assert "public.todo_items_id_seq" in sql


def test_database_repair_sql_idempotently_adds_progress_note() -> None:
    root = Path(__file__).resolve().parents[1]
    sql = (root / "config/tgw-coding-local-roles.sql").read_text(encoding="utf-8")

    assert "ADD COLUMN progress_note TEXT" in sql
    assert "duplicate_column" in sql


def test_database_check_fails_until_progress_note_exists(tmp_path, monkeypatch) -> None:
    paths, head, _tree = _fixture(tmp_path)
    observation = {
        key: True for key in (
            "database_connect", "schema_usage", "role_member", "todo_access",
            "queue_access", "history_access", "todo_sequence_access",
            "history_sequence_access", "claim_function_access",
            "recovery_function_access",
        )
    }
    observation.update(actor="codex", progress_note_column=False)
    monkeypatch.setattr(
        doctor_cli,
        "_database_observation",
        lambda _config, _actor: (observation, 0),
    )

    result = doctor_cli.check_database(paths)

    assert result["state"] == "FAIL"
    assert result["evidence"]["progress_note_column"] is False
    assert result["operator_action"] == (
        "sudo -n /usr/local/sbin/tgw-coding-bootstrap "
        f"--commit {head} --repair database"
    )


def test_database_repair_pipes_exact_git_sql_across_private_release_ancestry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, head, tree = _fixture(tmp_path)
    release = paths.runtime_root / "releases" / head
    migration = (paths.repository / "config/tgw-coding-local-roles.sql").read_text()
    (release / "config/tgw-coding-local-roles.sql").write_text(
        "SELECT 'mutable release path';\n", encoding="utf-8"
    )
    paths.runtime_root.chmod(0o700)
    reports = iter([
        {"state": "FAIL", "evidence": {"progress_note_column": False}},
        {
            "state": "PASS",
            "evidence": {
                "progress_note_column": True,
                "todo_access": True,
                "todo_sequence_access": True,
            },
        },
    ])
    observed: dict[str, object] = {}
    verified: list[tuple[str, Path]] = []

    def run(command, **kwargs):
        observed["command"] = command
        observed["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree",
        lambda _paths, desired, selected: (
            verified.append((desired, selected)) or {"tree": tree}
        ),
    )
    monkeypatch.setattr(doctor_cli, "check_database", lambda _paths: next(reports))
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_receipt", lambda *_args: "receipt.json")

    result = doctor_cli.repair_database(paths)

    assert result["ok"] is True
    assert verified == [(head, release)]
    assert observed["input"] == migration
    assert "--file" not in observed["command"]
    assert str(release) not in observed["command"]
    assert observed["command"][:5] == ["sudo", "-n", "-u", "postgres", "psql"]


def test_database_stdin_failure_is_receipted_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _head, tree = _fixture(tmp_path)
    receipts: list[str] = []

    def run(command, **kwargs):
        assert kwargs.get("input") == "SELECT 1;\n"
        return subprocess.CompletedProcess(command, 1, "", "migration rejected")

    def receipt(_paths, operation, *_args):
        receipts.append(operation)
        return operation + ".json"

    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": tree}
    )
    monkeypatch.setattr(
        doctor_cli, "check_database",
        lambda _paths: {"state": "FAIL", "evidence": {"progress_note_column": False}},
    )
    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_receipt", receipt)

    with pytest.raises(doctor_cli.DoctorError, match="migration rejected.*failure receipt"):
        doctor_cli.repair("database", paths)

    assert receipts == ["database-started", "database-failed"]


def test_doctor_launcher_is_local_and_provider_independent() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (root / "bin/tgw-doctor").read_text(encoding="utf-8")

    assert "/opt/TGW/tgw-lib/coding-runtime/current" in launcher
    assert "tgw.doctor_cli" in launcher
    assert "tgw-prod" not in launcher
    assert "ssh" not in launcher.lower()


def _obsolete_fixture(paths: doctor_cli.DoctorPaths, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
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


def test_obsolete_cleanup_default_actor_surface_is_fixed_under_direct_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(doctor_cli.os, "geteuid", lambda: 0)
    monkeypatch.delenv("SUDO_USER", raising=False)

    rows = doctor_cli._declared_obsolete_surfaces(doctor_cli.DoctorPaths())

    actor = next(row for row in rows if row["kind"] == "symlink")
    assert actor["path"] == Path("/home/codex/.local/bin/tgw-actor")


def test_obsolete_cleanup_production_scope_is_exactly_seven_pinned_surfaces() -> None:
    paths = doctor_cli.DoctorPaths()
    rows = doctor_cli._declared_obsolete_surfaces(paths)

    assert {str(row["path"]) for row in rows} == {
        "/usr/local/bin/tgw-foreman",
        "/usr/local/bin/tgw-foreman-dispatch",
        "/home/codex/.local/bin/tgw-actor",
        "/opt/TGW/tgw-lib/bin/tgw-context-mcp-candidate-3fe54df8",
        "/opt/TGW/tgw-lib/bin/tgw-context-mcp-candidate-408ee56c",
        "/opt/TGW/tgw-lib/bin/tgw-context-mcp-candidate-6813c302",
        "/opt/TGW/tgw-lib/bin/tgw-context-mcp-candidate-6865ce87",
    }
    assert doctor_cli._OBSOLETE_FILE_HASHES == {
        "tgw-foreman": "sha256:10152bcc0c7c72555a630d662e736ee827dc3edb5e3f3a0ad78ecf5b450d6332",
        "tgw-foreman-dispatch": "sha256:61fa8586dfc655685bfece1cbd71b7deed357d23833177bf9d0b6158825f66c5",
        "tgw-context-mcp-candidate-3fe54df8": "sha256:722dcfecebb23ee2dd71d8bfcf923a5275b8089edd03d47e632c51417bfc8699",
        "tgw-context-mcp-candidate-408ee56c": "sha256:3e9f57ad0a60597595e158dde60c36f8857e03b9902f0106332975fab5db4db6",
        "tgw-context-mcp-candidate-6813c302": "sha256:a1a1d637414e4afa881af03d0d0574e44733985a13b33f4acfff3bc443923e5b",
        "tgw-context-mcp-candidate-6865ce87": "sha256:9821db48b5205bbc06ccb6bd32697fbf25532c81b3292ddb5e0bae78fcda6009",
    }


def test_obsolete_cleanup_diagnoses_warn_and_moves_exact_surfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_obsolete_cleanup_preserves_posix_acl_xattr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = {"system.posix_acl_access": b"valid fixture ACL bytes"}
    restored = {}
    monkeypatch.setattr(doctor_cli.os, "listxattr", lambda _fd: list(source))
    monkeypatch.setattr(doctor_cli.os, "getxattr", lambda _fd, name: source[name])
    monkeypatch.setattr(doctor_cli.os, "setxattr", lambda _fd, name, value: restored.__setitem__(name, value))
    monkeypatch.setattr(doctor_cli.os, "removexattr", lambda _fd, _name: None)

    encoded = doctor_cli._read_xattrs(42)
    doctor_cli._replace_xattrs(43, encoded)

    assert encoded == {"system.posix_acl_access": "dmFsaWQgZml4dHVyZSBBQ0wgYnl0ZXM="}
    assert restored == source


def test_obsolete_cleanup_refuses_changed_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_obsolete_cleanup_refuses_unbound_candidate_without_expanding_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    unexpected = paths.local_bin / "tgw-context-mcp-candidate-unbound"
    unexpected.write_text("unique unknown bytes\n")

    diagnosis = doctor_cli.check_obsolete_surfaces(paths)
    with pytest.raises(doctor_cli.DoctorError, match="unbound active surfaces"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert diagnosis["state"] == "FAIL"
    assert diagnosis["evidence"]["unbound"] == [str(unexpected)]
    assert unexpected.read_text() == "unique unknown bytes\n"
    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_ignores_context_evidence_but_detects_active_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    paths.context_snapshot.chmod(0o644)
    paths.context_snapshot.write_text(f'{{"historical_path": "{sources[0]}"}}')
    paths = replace(
        paths,
        cleanup_reference_roots=(
            paths.context_snapshot,
            paths.cleanup_reference_roots[0],
        ),
    )
    present = [item for item in doctor_cli._declared_obsolete_surfaces(paths) if doctor_cli._lexists(item["path"])]

    assert doctor_cli._cleanup_references(paths, present) == []

    active = paths.cleanup_reference_roots[1] / "active.service"
    active.write_text(f"ExecStart={sources[0]}\n")
    references = doctor_cli._cleanup_references(paths, present)
    assert {row["path"] for row in references} == {str(active)}
    assert {row["reference"] for row in references} == {
        str(sources[0]),
        sources[0].name,
    }


def test_obsolete_cleanup_configuration_scan_fails_on_unreadable_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _obsolete_fixture(paths, monkeypatch)
    nested = paths.cleanup_reference_roots[0] / "nested"
    nested.mkdir()
    original_scandir = os.scandir

    def scandir(path):
        if Path(path) == nested:
            raise PermissionError("fixture unreadable configuration directory")
        return original_scandir(path)

    monkeypatch.setattr(doctor_cli.os, "scandir", scandir)
    present = [item for item in doctor_cli._declared_obsolete_surfaces(paths) if doctor_cli._lexists(item["path"])]

    with pytest.raises(doctor_cli.DoctorError, match="completely scan"):
        doctor_cli._cleanup_references(paths, present)


def test_obsolete_cleanup_refuses_unknown_process_visibility(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
def test_obsolete_cleanup_refuses_active_reference_or_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocker: str) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    if blocker == "configuration":
        (paths.cleanup_reference_roots[0] / "active.service").write_text(f"ExecStart={sources[0]}\n")
    else:
        monkeypatch.setattr(
            doctor_cli,
            "_cleanup_process_references",
            lambda _rows: [{"pid": 42, "command": str(sources[0])}],
        )

    with pytest.raises(doctor_cli.DoctorError, match="active references remain"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_rolls_back_active_view_on_remove_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_obsolete_cleanup_rolls_back_when_parent_fsync_fails_after_unlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    original_fsync = doctor_cli._fsync_directory_fd
    failed = False

    def fsync_directory(descriptor):
        nonlocal failed
        if not sources[0].exists() and not failed:
            failed = True
            raise OSError("fixture parent fsync failure")
        return original_fsync(descriptor)

    monkeypatch.setattr(doctor_cli, "_fsync_directory_fd", fsync_directory)

    with pytest.raises(doctor_cli.DoctorError, match="active view restored"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert failed is True
    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_post_unlink_parent_replacement_never_mutates_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    parent = sources[0].parent
    original_parent = parent.with_name(parent.name + "-original")
    original_fsync = doctor_cli._fsync_directory_fd
    replaced = False

    def replace_parent(descriptor):
        nonlocal replaced
        if not sources[0].exists() and not replaced:
            parent.rename(original_parent)
            parent.mkdir()
            replaced = True
        return original_fsync(descriptor)

    monkeypatch.setattr(doctor_cli, "_fsync_directory_fd", replace_parent)

    with pytest.raises(doctor_cli.DoctorError, match="rollback incomplete"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert replaced is True
    assert list(parent.iterdir()) == []
    assert (original_parent / sources[0].name).is_file()
    assert (original_parent / sources[1].name).is_file()


def test_obsolete_cleanup_binding_rejects_replaced_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)
    declaration = next(item for item in doctor_cli._declared_obsolete_surfaces(paths) if item["path"] == sources[0])
    original_parent = sources[0].parent.with_name(sources[0].parent.name + "-bound")

    with doctor_cli._bind_cleanup_surface(declaration) as binding:
        sources[0].parent.rename(original_parent)
        sources[0].parent.mkdir()
        with pytest.raises(doctor_cli.DoctorError, match="parent changed"):
            doctor_cli._verify_bound_cleanup_surface(binding)


def test_obsolete_cleanup_secure_open_rejects_prebind_ancestor_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "binding"
    ancestor = root / "ancestor"
    parent = ancestor / "bin"
    parent.mkdir(parents=True)
    source = parent / "surface"
    raw = b"exact bytes\n"
    source.write_bytes(raw)
    declared_hash = "sha256:" + __import__("hashlib").sha256(raw).hexdigest()
    item = {"path": source, "kind": "file", "declared_sha256": declared_hash}
    original_ancestor = root / "ancestor-original"
    original_open = os.open
    replaced = False

    def open_path(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        if path == "bin" and dir_fd is not None and not replaced:
            ancestor.rename(original_ancestor)
            replacement = ancestor / "bin"
            replacement.mkdir(parents=True)
            (replacement / "surface").write_bytes(raw)
            replaced = True
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(doctor_cli.os, "open", open_path)

    with pytest.raises(doctor_cli.DoctorError, match="parent changed"):
        with doctor_cli._bind_cleanup_surface(item):
            pass

    assert replaced is True


def test_obsolete_cleanup_archive_path_rejects_symlink_and_failed_intermediate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real"
    real.mkdir()
    symlink = tmp_path / "archive-link"
    symlink.symlink_to(real, target_is_directory=True)
    with pytest.raises(doctor_cli.DoctorError, match="not direct"):
        doctor_cli._durable_mkdir(symlink / "child")

    target = tmp_path / "durable" / "middle" / "leaf"
    original_mkdir = os.mkdir

    def mkdir(path, mode=0o777, *, dir_fd=None):
        if path == "middle":
            raise OSError("fixture durable mkdir failure")
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(doctor_cli.os, "mkdir", mkdir)
    with pytest.raises(doctor_cli.DoctorError, match="durably create"):
        doctor_cli._durable_mkdir(target)
    assert not target.exists()


def test_obsolete_cleanup_archive_name_collision_refuses_without_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    sources = _obsolete_fixture(paths, monkeypatch)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 25, 12, 34, 56, 789000, tzinfo=tz or UTC)

    monkeypatch.setattr(doctor_cli, "datetime", FixedDatetime)
    collision = paths.cleanup_archive_root / "2026-08-25" / "20260825T123456789000Z"
    collision.mkdir(parents=True)

    with pytest.raises(doctor_cli.DoctorError, match="archive name collision"):
        doctor_cli.repair_obsolete_surfaces(paths)

    assert all(path.exists() or path.is_symlink() for path in sources)


def test_obsolete_cleanup_reconciles_interrupted_prepared_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_obsolete_cleanup_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    _obsolete_fixture(paths, monkeypatch)

    first = doctor_cli.repair_obsolete_surfaces(paths)
    second = doctor_cli.repair_obsolete_surfaces(paths)

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["archive"] is None


def test_obsolete_cleanup_has_no_production_provider_plan_business_or_worktree_effect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert {path: os.readlink(path) if path.is_symlink() else path.read_bytes() for path in preserved} == preserved


def test_doctor_coding_resume_uses_resume_only_surface(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tgw import coding_cli

    called = []
    monkeypatch.setattr(
        coding_cli,
        "resume",
        lambda todo_id: (
            called.append(todo_id)
            or {
                "ok": True,
                "coding_state": {"state": "RESUMABLE_PARTIAL"},
            }
        ),
    )
    monkeypatch.setattr(
        coding_cli,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Doctor must not use unrestricted coding start")),
    )

    assert doctor_cli.main(["coding-resume", "1752"]) == 0
    assert called == [1752]
    assert json.loads(capsys.readouterr().out)["coding_state"]["state"] == "RESUMABLE_PARTIAL"


def test_doctor_classifies_coding_history_against_todo_plan_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tgw.development import partial_resume
    from tgw.development import plan_binding as plan_binding_module

    paths, _canonical_head, _canonical_tree = _fixture(tmp_path)
    worktree = paths.worktrees / "todo-1752-plan-deadbeef"
    worktree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=worktree, check=True)
    (worktree / "base").write_text("base\n")
    subprocess.run(["git", "add", "base"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@t", "commit", "-qm", "base"],
        cwd=worktree,
        check=True,
    )
    head = _git(worktree, "rev-parse", "HEAD")
    tree = _git(worktree, "rev-parse", "HEAD^{tree}")
    expected = {
        "job_id": "job-1",
        "attempt_count": 1,
        "todo_id": 1752,
        "plan_commit": "a" * 40,
        "solution_hash": "sha256:" + "b" * 64,
        "source_commit": head,
        "source_tree": tree,
        "actor": "codex",
        "worktree": str(worktree),
        "treatment_id": "codex-implement",
        "treatment_version": "1",
    }
    (worktree / "partial.py").write_text("partial = True\n")
    partial_resume.append_attempt(worktree, partial_resume.make_attempt(expected, worktree, outcome="partial"))
    binding = {
        "plan_commit": expected["plan_commit"],
        "solution_hash": expected["solution_hash"],
        "source_commit": head,
        "worktree": str(worktree),
    }
    monkeypatch.setattr(
        doctor_cli,
        "_todo_binding_rows",
        lambda _paths: [{"id": 1752, "agent": "codex", "status_note": "bound"}],
    )
    monkeypatch.setattr(
        plan_binding_module,
        "parse_plan_binding",
        lambda _note, todo_id=None: binding,
    )

    state = doctor_cli._bound_coding_states(paths, [worktree])[str(worktree)]
    assert state["state"] == "RESUMABLE_PARTIAL"

    monkeypatch.setattr(
        doctor_cli,
        "_todo_binding_rows",
        lambda _paths: [{"id": 1752, "agent": "claude", "status_note": "bound"}],
    )
    stale = doctor_cli._bound_coding_states(paths, [worktree])[str(worktree)]
    assert stale["state"] == "STALE_RECEIPT"

def test_preservation_clone_generation_rejects_same_inode_mutation_before_read(
    tmp_path: Path,
) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_bytes(b"original\n")
    parent = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    read_fd = identity_fd = -1
    try:
        read_fd, identity_fd, generation = doctor_cli._open_preservation_file(
            parent,
            evidence.name,
            snapshot_parent=parent,
            snapshot_group_gid=os.getgid(),
        )
        with evidence.open("r+b", buffering=0) as stream:
            stream.write(b"mutated!\n")
        os.utime(
            evidence,
            ns=(generation.st_atime_ns, generation.st_mtime_ns + 1_000_000_000),
        )
        with pytest.raises(doctor_cli.DoctorError, match="changed before stable read"):
            doctor_cli._stable_descriptor_bytes(
                read_fd,
                identity_descriptor=identity_fd,
                source_generation=generation,
            )
    finally:
        if read_fd >= 0:
            os.close(read_fd)
        if identity_fd >= 0 and identity_fd != read_fd:
            os.close(identity_fd)
        os.close(parent)


def test_preservation_directory_descriptor_metadata_mutation_is_rejected(
    tmp_path: Path,
) -> None:
    preservation = tmp_path / ".tgw-coding-preservation"
    preservation.mkdir()
    worktree_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    preservation_fd = os.open(preservation, os.O_RDONLY | os.O_DIRECTORY)
    try:
        generation = os.fstat(preservation_fd)
        os.fchmod(preservation_fd, 0o700)
        with pytest.raises(doctor_cli.DoctorError, match="directory metadata changed"):
            doctor_cli._revalidate_preservation_directory(
                worktree_fd,
                preservation_fd,
                generation,
                todo_id=1752,
            )
    finally:
        os.close(preservation_fd)
        os.close(worktree_fd)


def test_mutation_journal_closes_each_descriptor_once_when_rollbacks_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.write_text("value")
    metadata_fd = os.open(target, os.O_RDONLY)
    parent_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    before = os.fstat(metadata_fd)
    closed: list[int] = []
    real_close = os.close

    monkeypatch.setattr(
        doctor_cli.os,
        "fchown",
        lambda *_args: (_ for _ in ()).throw(OSError("fchown failed")),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_rename_exchange",
        lambda *_args: (_ for _ in ()).throw(doctor_cli.DoctorError("exchange failed")),
    )

    def tracking_close(descriptor: int) -> None:
        closed.append(descriptor)
        real_close(descriptor)

    monkeypatch.setattr(doctor_cli.os, "close", tracking_close)
    errors = doctor_cli._close_mutation_journal(
        [
            {"kind": "metadata", "descriptor": metadata_fd, "before": before},
            {"kind": "exchange", "parent": parent_fd, "name": "target", "backup": "backup"},
        ],
        rollback=True,
    )

    assert errors == ["exchange failed", "fchown failed"]
    assert closed.count(metadata_fd) == 1
    assert closed.count(parent_fd) == 1

def test_shared_tree_never_mutates_concurrent_protected_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preservation = tmp_path / ".tgw-coding-preservation"
    preservation.mkdir(mode=0o700)
    child = preservation / "concurrent"
    mutations: list[tuple[int, bool]] = []
    real_set_shared_fd = doctor_cli._set_shared_fd
    real_listdir = os.listdir
    introduced = False
    child_before: list[os.stat_result] = []
    journal: list[dict[str, object]] = []

    def introducing_listdir(descriptor: int) -> list[str]:
        nonlocal introduced
        if not introduced and os.fstat(descriptor).st_ino == preservation.stat().st_ino:
            child.write_bytes(b"untouched\n")
            child.chmod(0o600)
            child_before.append(child.stat())
            introduced = True
        return real_listdir(descriptor)

    def recording_set_shared_fd(descriptor: int, group_gid: int, *, directory: bool) -> None:
        mutations.append((os.fstat(descriptor).st_ino, directory))
        real_set_shared_fd(descriptor, group_gid, directory=directory)

    monkeypatch.setattr(doctor_cli, "_set_shared_fd", recording_set_shared_fd)
    monkeypatch.setattr(doctor_cli.os, "listdir", introducing_listdir)
    result = doctor_cli._scan_shared_git_tree(
        tmp_path,
        os.getgid(),
        mutate=True,
        immutable_directories=[Path(".tgw-coding-preservation")],
        journal=journal,
    )

    assert introduced and len(child_before) == 1
    before = child_before[0]
    monkeypatch.setattr(
        doctor_cli.os,
        "fchown",
        lambda *_args: (_ for _ in ()).throw(OSError("rollback failed")),
    )
    assert doctor_cli._close_mutation_journal(journal, rollback=True) == ["rollback failed"]
    after = child.stat()
    assert result["immutable_files"] == 1
    assert before.st_ino not in {inode for inode, _directory in mutations}
    assert (after.st_dev, after.st_ino, after.st_uid, after.st_gid) == (
        before.st_dev,
        before.st_ino,
        before.st_uid,
        before.st_gid,
    )
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert child.read_bytes() == b"untouched\n"


def test_shared_tree_noatime_denial_fails_closed_for_protected_directory_and_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preservation = tmp_path / ".tgw-coding-preservation"
    preservation.mkdir(mode=0o700)
    evidence = preservation / "evidence.json"
    evidence_bytes = b'{"preserved":true}\n'
    evidence.write_bytes(evidence_bytes)
    old_atime_ns = 1_600_000_000_123_456_789
    os.utime(evidence, ns=(old_atime_ns, evidence.stat().st_mtime_ns))
    before_names = tuple(sorted(path.name for path in preservation.iterdir()))
    before_directory = preservation.stat()
    before_evidence = evidence.stat()
    noatime = getattr(os, "O_NOATIME", 0)
    assert noatime
    real_open = os.open
    real_listdir = os.listdir
    denied_name = preservation.name
    protected_enumerations = 0
    attempts: list[tuple[str, int]] = []
    mutations: list[int] = []
    journal: list[dict[str, object]] = []

    def denying_open(path, flags, *args, **kwargs):
        if path == denied_name:
            attempts.append((str(path), flags))
            if flags & noatime:
                raise PermissionError(errno.EPERM, "forced O_NOATIME denial")
        return real_open(path, flags, *args, **kwargs)

    def tracking_listdir(path) -> list[str]:
        nonlocal protected_enumerations
        if isinstance(path, int) and os.fstat(path).st_ino == before_directory.st_ino:
            protected_enumerations += 1
        return real_listdir(path)

    monkeypatch.setattr(doctor_cli.os, "open", denying_open)
    monkeypatch.setattr(doctor_cli.os, "listdir", tracking_listdir)
    monkeypatch.setattr(
        doctor_cli,
        "_set_shared_fd",
        lambda descriptor, _group_gid, *, directory: mutations.append(os.fstat(descriptor).st_ino),
    )

    with pytest.raises(doctor_cli.DoctorError, match="without O_NOATIME"):
        doctor_cli._scan_shared_git_tree(
            tmp_path,
            os.getgid(),
            mutate=True,
            immutable_directories=[Path(), Path(preservation.name)],
            journal=journal,
        )
    assert attempts == [(preservation.name, attempts[0][1])]
    assert attempts[0][1] & noatime
    assert protected_enumerations == 0

    denied_name = evidence.name
    attempts.clear()
    with pytest.raises(doctor_cli.DoctorError, match="without O_NOATIME"):
        doctor_cli._scan_shared_git_tree(
            tmp_path,
            os.getgid(),
            mutate=True,
            immutable_directories=[Path(), Path(preservation.name)],
            journal=journal,
        )
    assert attempts == [(evidence.name, attempts[0][1])]
    assert attempts[0][1] & noatime
    assert protected_enumerations == 1
    assert mutations == []
    assert journal == []

    after_directory = preservation.stat()
    after_evidence = evidence.stat()
    for before, after in ((before_directory, after_directory), (before_evidence, after_evidence)):
        assert (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
            after.st_atime_ns,
        ) == (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_uid,
            before.st_gid,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
            before.st_atime_ns,
        )
    assert after_evidence.st_atime_ns == old_atime_ns
    assert tuple(sorted(path.name for path in preservation.iterdir())) == before_names
    descriptor = real_open(evidence, os.O_RDONLY | os.O_CLOEXEC | noatime)
    try:
        assert os.pread(descriptor, len(evidence_bytes), 0) == evidence_bytes
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("failed_start", ["worker", "foreman"])
def test_coding_quiescence_latches_nonzero_start_before_downstream_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_start: str,
) -> None:
    worker = "tgw-codex-implement-worker.service"
    foreman = "tgw-coding-local-foreman.service"
    timer = "tgw-coding-local-foreman.timer"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states.update({worker: "active", foreman: "activating", timer: "active"})
    starts = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        if command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "inactive"
        elif command[1] == "start":
            starts.append(command[2:])
            unit = command[2]
            if unit == worker:
                states[worker] = "active"
                if failed_start == "worker":
                    return subprocess.CompletedProcess(command, 1, "", "injected worker start failure")
            elif unit == foreman:
                # A correct-looking inactive oneshot state must not clear the failure.
                states[foreman] = "inactive"
                if failed_start == "foreman":
                    return subprocess.CompletedProcess(command, 1, "", "injected Foreman start failure")
            elif unit == timer:
                states[timer] = "active"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {"LoadState": "loaded", "ActiveState": states[unit],
                "DropInPaths": str(dropin) if dropin.exists() else ""}

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match=f"injected {'worker' if failed_start == 'worker' else 'Foreman'} start failure"):
        with doctor_cli._coding_quiescence(paths):
            pass

    assert [worker] in starts
    if failed_start == "worker":
        assert [foreman] not in starts
    else:
        assert [foreman] in starts
    assert [timer] not in starts


def test_coding_quiescence_latches_undesired_stop_failure_before_all_starts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = "tgw-codex-implement-worker.service"
    undesired = "tgw-controller-verify-worker.service"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[worker] = "active"
    commands = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )

    def run(command, **_kwargs):
        commands.append(command)
        if command[1] == "stop":
            for unit in command[2:]:
                states[unit] = "inactive"
        elif command[1] == "start":
            states.update({unit: "active" for unit in command[2:]})
        if command[1:] == ["stop", undesired]:
            # The unit looks correctly stopped, so only the monotonic command-failure
            # latch can prevent restoration from starting the worker again.
            states[undesired] = "inactive"
            return subprocess.CompletedProcess(
                command, 1, "", "injected undesired stop failure"
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {
            "LoadState": "loaded",
            "ActiveState": states[unit],
            "DropInPaths": str(dropin) if dropin.exists() else "",
        }

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)

    with pytest.raises(doctor_cli.DoctorError, match="injected undesired stop failure"):
        with doctor_cli._coding_quiescence(paths):
            states[undesired] = "active"

    assert [command for command in commands if command[1] == "start"] == []
    assert states["tgw-coding-local-foreman.timer"] == "inactive"
    assert (paths.quiescence_root / doctor_cli._QUIESCENCE_STATE).is_file()


def test_coding_quiescence_reset_uses_exact_pre_reset_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed = "tgw-controller-verify-worker.service"
    states = {unit: "inactive" for unit in doctor_cli._CODING_UNITS}
    states[failed] = "failed"
    reads = []
    exact_proofs = []
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root, quiescence_root=tmp_path / "tgw-doctor",
        systemd_unit_uid=os.getuid(), systemd_unit_gid=os.getgid(),
    )
    exact = doctor_cli._quiescence_file_exact

    def run(command, **_kwargs):
        if command[1] == "reset-failed":
            assert command == ["systemctl", "reset-failed", failed]
            assert reads[-len(doctor_cli._CODING_UNITS):] == list(doctor_cli._CODING_UNITS)
            assert states[failed] == "failed"
            units = list(doctor_cli._CODING_UNITS)
            _state_path, marker, dropins, dropin_value = doctor_cli._quiescence_layout(
                paths, units
            )
            assert exact_proofs == [
                (dropins[unit], dropin_value, 0o444, os.getuid(), os.getgid())
                for unit in units
            ] + [
                (
                    marker,
                    b"tgw doctor unix-git-access active\n",
                    0o400,
                    os.getuid(),
                    os.getgid(),
                )
            ]
            states[failed] = "inactive"
        return subprocess.CompletedProcess(command, 0, "", "")

    def unit_state(unit):
        reads.append(unit)
        dropin = runtime_root / f"{unit}.d" / doctor_cli._QUIESCENCE_DROPIN
        return {"LoadState": "loaded", "ActiveState": states[unit],
                "DropInPaths": str(dropin) if dropin.exists() else ""}

    def prove_exact(path, value, *, mode, uid, gid):
        exact_proofs.append((path, value, mode, uid, gid))
        return exact(path, value, mode=mode, uid=uid, gid=gid)

    monkeypatch.setattr(doctor_cli, "_run", run)
    monkeypatch.setattr(doctor_cli, "_unit_state", unit_state)
    monkeypatch.setattr(doctor_cli, "_quiescence_file_exact", prove_exact)
    with doctor_cli._coding_quiescence(paths):
        pass




@pytest.mark.parametrize(
    "unsafe_unit",
    ["tgw-unknown-coding.service", "tgw-codex-implement.service"],
)
def test_coding_quiescence_rejects_unknown_stale_initially_active_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_unit: str,
) -> None:
    runtime_root = tmp_path / "systemd"
    runtime_root.mkdir(mode=0o755)
    quiescence_root = tmp_path / "tgw-doctor"
    quiescence_root.mkdir(mode=0o755)
    paths = doctor_cli.DoctorPaths(
        systemd_runtime_root=runtime_root,
        quiescence_root=quiescence_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    units = list(doctor_cli._CODING_UNITS)
    state_path, marker, dropins, _dropin_value = doctor_cli._quiescence_layout(
        paths, units
    )
    state = {
        "schema": doctor_cli._QUIESCENCE_SCHEMA,
        "boot_id": doctor_cli._boot_id(),
        "owner_pid": 999999999,
        "owner_start_ticks": "1",
        "units": units,
        "initially_active": [unsafe_unit],
        "state_path": str(state_path),
        "marker": str(marker),
        "dropins": {unit: str(dropins[unit]) for unit in units},
    }
    doctor_cli._create_quiescence_file(
        state_path,
        doctor_cli._canonical(state) + b"\n",
        mode=0o400,
        uid=os.getuid(),
        gid=os.getgid(),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("systemctl must not be called"),
    )

    with pytest.raises(
        doctor_cli.DoctorError,
        match="pre-existing coding quiescence state fields are unsafe",
    ):
        doctor_cli._read_quiescence_state(
            state_path,
            units=units,
            marker=marker,
            dropins=dropins,
            uid=os.getuid(),
            gid=os.getgid(),
        )

    assert state_path.exists()


def test_coding_support_roots_require_protected_same_filesystem_directories(tmp_path: Path) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    archive = tmp_path / "tgw-coders/archive"
    runner = tmp_path / "tgw-coders/runner"
    shutil.rmtree(archive)
    shutil.rmtree(runner)
    config = json.loads(paths.coding_config.read_text())
    config["coding"]["preservation_archive_root"] = str(archive)
    config["coding"]["runner_state_root"] = str(runner)
    _write_json(paths.coding_config, config)
    group_gid = __import__("grp").getgrnam("tgw-coders").gr_gid

    before = doctor_cli._coding_support_roots(paths, group_gid)
    assert not all(row["exact"] for row in before.values())

    for directory in (archive, runner):
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, -1, group_gid)
        directory.chmod(0o2770)
    after = doctor_cli._coding_support_roots(paths, group_gid)
    assert all(row["exact"] for row in after.values())


def test_support_root_provisioning_journal_removes_partial_parent_tree(tmp_path: Path) -> None:
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    target = tmp_path / "new-parent/archive"
    journal: list[dict[str, object]] = []
    doctor_cli._provision_support_root(
        target,
        group_gid=group_gid,
        worktree_device=tmp_path.stat().st_dev,
        journal=journal,
    )
    assert target.is_dir()
    assert not doctor_cli._close_mutation_journal(journal, rollback=True)
    assert not target.exists()
    assert not target.parent.exists()


@pytest.mark.parametrize(
    "phase", ["creation-to-bind", "bind-to-publish", "rename-to-phase", "after-publish"],
)
def test_support_root_boundary_failure_rolls_back_bound_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    target = tmp_path / "created"
    journal: list[dict[str, object]] = []

    def fail(selected: str) -> None:
        if selected == phase:
            raise OSError(f"injected {phase}")

    monkeypatch.setattr(doctor_cli, "_support_root_checkpoint", fail)
    with pytest.raises(OSError, match=f"injected {phase}"):
        doctor_cli._provision_support_root(
            target,
            group_gid=grp.getgrnam("tgw-coders").gr_gid,
            worktree_device=tmp_path.stat().st_dev,
            journal=journal,
        )
    errors = doctor_cli._close_mutation_journal(journal, rollback=True)
    if phase == "creation-to-bind":
        assert errors == [
            "created support directory identity was never bound; retained for recovery"
        ]
        assert len(list(tmp_path.glob(".created.tgw-stage-*"))) == 1
    else:
        assert not errors
        assert not target.exists()
        assert not list(tmp_path.glob(".created.tgw-stage-*"))


def test_support_root_rollback_finds_published_inode_before_phase_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created"
    staged_replacement: Path | None = None
    journal: list[dict[str, object]] = []

    def fail_after_rename(phase: str) -> None:
        nonlocal staged_replacement
        if phase == "rename-to-phase":
            staged_name = str(journal[-1]["staging_name"])
            staged = tmp_path / staged_name
            staged.mkdir(mode=0o711)
            (staged / "sentinel").write_bytes(b"staged replacement\n")
            staged_replacement = staged
            raise OSError("injected after atomic rename")

    monkeypatch.setattr(doctor_cli, "_support_root_checkpoint", fail_after_rename)
    with pytest.raises(OSError, match="injected after atomic rename"):
        doctor_cli._provision_support_root(
            target,
            group_gid=grp.getgrnam("tgw-coders").gr_gid,
            worktree_device=tmp_path.stat().st_dev,
            journal=journal,
        )
    # The pinned inode is published even though mutable journal phase still says staging.
    errors = doctor_cli._close_mutation_journal(journal, rollback=True)
    assert errors == []
    assert staged_replacement is not None
    assert (staged_replacement / "sentinel").read_bytes() == b"staged replacement\n"
    assert not target.exists()


def test_support_root_journal_descriptor_duplication_failure_retains_unbound_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created"
    journal: list[dict[str, object]] = []
    original_dup = doctor_cli.os.dup
    calls = 0

    def fail_second_dup(descriptor: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected journal descriptor duplication")
        return original_dup(descriptor)

    monkeypatch.setattr(doctor_cli.os, "dup", fail_second_dup)
    with pytest.raises(OSError, match="journal descriptor duplication"):
        doctor_cli._provision_support_root(
            target,
            group_gid=grp.getgrnam("tgw-coders").gr_gid,
            worktree_device=tmp_path.stat().st_dev,
            journal=journal,
        )
    errors = doctor_cli._close_mutation_journal(journal, rollback=True)
    assert errors == ["created support directory identity was never bound; retained for recovery"]
    assert not target.exists()
    assert len(list(tmp_path.glob(".created.tgw-stage-*"))) == 1


def test_support_root_creation_to_bind_replacement_is_retained_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created"
    journal: list[dict[str, object]] = []
    replacement: Path | None = None

    def replace(phase: str) -> None:
        nonlocal replacement
        if phase == "creation-to-bind":
            staging = next(tmp_path.glob(".created.tgw-stage-*"))
            staging.rename(tmp_path / "created-original")
            staging.mkdir(mode=0o711)
            (staging / "sentinel").write_bytes(b"replacement\n")
            replacement = staging

    monkeypatch.setattr(doctor_cli, "_support_root_checkpoint", replace)
    with pytest.raises(doctor_cli.DoctorError, match="changed before bind"):
        doctor_cli._provision_support_root(
            target,
            group_gid=grp.getgrnam("tgw-coders").gr_gid,
            worktree_device=tmp_path.stat().st_dev,
            journal=journal,
        )
    errors = doctor_cli._close_mutation_journal(journal, rollback=True)
    assert errors == ["created support directory identity was never bound; retained for recovery"]
    assert replacement is not None
    assert (replacement / "sentinel").read_bytes() == b"replacement\n"
    assert stat.S_IMODE(replacement.stat().st_mode) == 0o711


def test_support_root_bind_to_publish_concurrent_final_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created"
    journal: list[dict[str, object]] = []

    def replace(phase: str) -> None:
        if phase == "bind-to-publish":
            target.mkdir(mode=0o711)
            (target / "sentinel").write_bytes(b"concurrent\n")

    monkeypatch.setattr(doctor_cli, "_support_root_checkpoint", replace)
    with pytest.raises(doctor_cli.DoctorError, match="appeared before"):
        doctor_cli._provision_support_root(
            target,
            group_gid=grp.getgrnam("tgw-coders").gr_gid,
            worktree_device=tmp_path.stat().st_dev,
            journal=journal,
        )
    before = target.stat()
    assert not doctor_cli._close_mutation_journal(journal, rollback=True)
    after = target.stat()
    assert (target / "sentinel").read_bytes() == b"concurrent\n"
    assert (after.st_dev, after.st_ino, after.st_mode, after.st_uid, after.st_gid) == (
        before.st_dev, before.st_ino, before.st_mode, before.st_uid, before.st_gid,
    )
    assert not list(tmp_path.glob(".created.tgw-stage-*"))


def test_support_root_replacement_before_rollback_is_not_removed(
    tmp_path: Path,
) -> None:
    target = tmp_path / "created"
    journal: list[dict[str, object]] = []
    doctor_cli._provision_support_root(
        target,
        group_gid=grp.getgrnam("tgw-coders").gr_gid,
        worktree_device=tmp_path.stat().st_dev,
        journal=journal,
    )
    target.rename(tmp_path / "bound-original")
    target.mkdir()
    (target / "sentinel").write_bytes(b"published replacement\n")
    staging = tmp_path / str(journal[-1]["staging_name"])
    staging.mkdir()
    (staging / "sentinel").write_bytes(b"staged replacement\n")
    errors = doctor_cli._close_mutation_journal(journal, rollback=True)
    assert errors == ["created support directory has no unique bound rollback name"]
    assert (target / "sentinel").read_bytes() == b"published replacement\n"
    assert (staging / "sentinel").read_bytes() == b"staged replacement\n"
    assert (tmp_path / "bound-original").is_dir()


@pytest.mark.parametrize("phase", ["open", "chown", "chmod", "file-fsync", "directory-fsync"])
def test_support_root_creation_failure_is_completely_rollbackable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    target = tmp_path / "new-parent/archive"
    journal: list[dict[str, object]] = []
    originals = {name: getattr(doctor_cli.os, name) for name in ("open", "fchown", "fchmod", "fsync")}
    calls = {"open": 0, "fsync": 0}

    def injected(name):
        def call(*args, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            if (selected == name and not phase.endswith("fsync")
                    or (phase == "file-fsync" and name == "fsync" and calls[name] == 1)
                    or (phase == "directory-fsync" and name == "fsync" and calls[name] == 2)):
                raise OSError(f"injected {phase}")
            return originals[name](*args, **kwargs)
        return call

    selected = {
        "chown": "fchown", "chmod": "fchmod",
        "file-fsync": "fsync", "directory-fsync": "fsync",
    }.get(phase, phase)
    monkeypatch.setattr(doctor_cli.os, selected, injected(selected))
    with pytest.raises(OSError, match="injected"):
        doctor_cli._provision_support_root(
            target, group_gid=group_gid,
            worktree_device=tmp_path.stat().st_dev, journal=journal,
        )
    assert not doctor_cli._close_mutation_journal(journal, rollback=True)
    assert not target.exists()
    assert not target.parent.exists()


def test_support_root_preexisting_metadata_rolls_back_after_fsync_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    target = tmp_path / "existing"
    target.mkdir(mode=0o700)
    before = target.stat()
    journal: list[dict[str, object]] = []
    monkeypatch.setattr(
        doctor_cli.os, "fsync",
        lambda _descriptor: (_ for _ in ()).throw(OSError("injected fsync")),
    )
    with pytest.raises(OSError, match="injected fsync"):
        doctor_cli._provision_support_root(
            target, group_gid=group_gid,
            worktree_device=tmp_path.stat().st_dev, journal=journal,
        )
    monkeypatch.undo()
    assert not doctor_cli._close_mutation_journal(journal, rollback=True)
    after = target.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
        before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode),
    )


def test_support_root_fsyncs_each_created_child_before_its_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    target = tmp_path / "new-parent/archive"
    journal: list[dict[str, object]] = []
    events: list[tuple[int, int]] = []
    original = doctor_cli.os.fsync

    def record(descriptor: int) -> None:
        state = os.fstat(descriptor)
        events.append((state.st_dev, state.st_ino))
        original(descriptor)

    monkeypatch.setattr(doctor_cli.os, "fsync", record)
    doctor_cli._provision_support_root(
        target, group_gid=group_gid,
        worktree_device=tmp_path.stat().st_dev, journal=journal,
    )
    target_identity = (target.stat().st_dev, target.stat().st_ino)
    parent_identity = (target.parent.stat().st_dev, target.parent.stat().st_ino)
    assert events[-3:] == [target_identity, parent_identity, target_identity]
    assert not doctor_cli._close_mutation_journal(journal, rollback=True)


@pytest.mark.parametrize("bad", [None, "", "relative/path", "duplicate"])
def test_coding_support_roots_reject_incomplete_or_aliased_configuration(tmp_path: Path, bad: str | None) -> None:
    paths, _head, _tree = _fixture(tmp_path)
    config = json.loads(paths.coding_config.read_text())
    if bad == "duplicate":
        config["coding"]["runner_state_root"] = config["coding"]["preservation_archive_root"]
    else:
        config["coding"]["preservation_archive_root"] = bad
    _write_json(paths.coding_config, config)
    group_gid = grp.getgrnam("tgw-coders").gr_gid
    assert not all(row["exact"] for row in doctor_cli._coding_support_roots(paths, group_gid).values())
    with pytest.raises(doctor_cli.DoctorError, match="distinct non-empty absolute"):
        doctor_cli._provision_coding_support_roots(paths, group_gid, [])
