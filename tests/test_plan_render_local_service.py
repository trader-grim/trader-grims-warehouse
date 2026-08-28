from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tgw import doctor_cli, local_plan_cli

ROOT = Path(__file__).parents[1]


def test_local_plan_render_config_is_exact_and_independent() -> None:
    config = json.loads((ROOT / "config/tgw-plan-render-local.json").read_text(encoding="utf-8"))
    assert config["postgres_dsn"] == "dbname=tgw_lib_dev_state_machine"
    assert config["standalone_plan_root"] == ("/opt/TGW/library/approved/058e2f980201cc78245358e4901cf007063f2c29")
    assert config["plan_repository_root"] == "/opt/TGW/library/plans"
    assert config["plan_approved_commit"] == ("058e2f980201cc78245358e4901cf007063f2c29")
    assert config["plan_approved_solution_hash"] == ("sha256:ecce15aad2699492c0c5577bff1af7005ffbbec6ae6166b325b34c1cc7e70e9f")
    assert config["plan_render_root"] == "/opt/TGW/var/plan-render"
    text = json.dumps(config).lower()
    assert "tgw-prod" not in text
    assert "ssh" not in text
    assert "provider" not in text


def test_local_plan_render_unit_reuses_existing_worker_as_db_user() -> None:
    unit = (ROOT / "systemd/tgw-plan-render-local.service").read_text(encoding="utf-8")
    assert "User=db\n" in unit
    assert "SupplementaryGroups=tgw-coders" in unit
    assert "WorkingDirectory=/opt/TGW/tgw-lib/coding-runtime/current" in unit
    assert "PYTHONPATH=/opt/TGW/tgw-lib/coding-runtime/current/src" in unit
    assert "-m tgw.workers.plan_render --config " in unit
    assert "/opt/TGW/tgw-lib/config/tgw-plan-render-local.json" in unit
    assert "ReadOnlyPaths=/opt/TGW/library/approved/058e2f" in unit
    assert "ReadWritePaths=/opt/TGW/var/plan-render" in unit


def test_doctor_exposes_separately_named_plan_render_repair() -> None:
    assert doctor_cli._PLAN_RENDER_UNIT == "tgw-plan-render-local.service"
    assert doctor_cli._REPAIRS["plan-render-worker"] is (doctor_cli.repair_plan_render_worker)
    parser = doctor_cli._parser()
    args = parser.parse_args(["repair", "plan-render-worker"])
    assert args.target == "plan-render-worker"


def test_operator_launcher_routes_plan_to_local_tgw_lib() -> None:
    launcher = (ROOT / "bin/tgw-operator").read_text(encoding="utf-8")
    local = (ROOT / "bin/tgw-plan-local-operator").read_text(encoding="utf-8")

    assert "plan)" in launcher
    assert (
        "exec /opt/TGW/tgw-lib/coding-runtime/current/bin/tgw-plan-local-operator"
        in launcher
    )
    assert "tgw.local_plan_cli" in local
    assert "/opt/TGW/tgw-lib/config/tgw-plan-render-local.json" in local
    assert "tgw-prod" not in local
    assert "production-client" not in local


def test_local_plan_cli_render_uses_configured_renderer(monkeypatch, tmp_path, capsys) -> None:
    config_path = tmp_path / "local.json"
    config_path.write_text("{}\n", encoding="utf-8")
    configured = {"standalone_plan_root": "/local/approved"}
    monkeypatch.setattr(local_plan_cli, "load_operational_config", lambda path: configured)
    monkeypatch.setattr(
        local_plan_cli,
        "render_taskboard",
        lambda config: {
            "ok": True,
            "path": "/local/TGW-Taskboard.md",
            "open": 1,
            "done_week": 2,
            "plan_identity": {"plan_commit": "a" * 40, "solution_hash": "sha256:test"},
        } if config is configured else pytest.fail("wrong local config"),
    )
    monkeypatch.setattr(sys, "argv", ["tgw plan", "--config", str(config_path), "render"])

    assert local_plan_cli.main() == 0
    assert "Taskboard rendered: /local/TGW-Taskboard.md" in capsys.readouterr().out


def test_plan_render_process_runtime_detects_stale_and_current_worker(tmp_path: Path) -> None:
    selected = tmp_path / "releases" / ("a" * 40)
    previous = tmp_path / "releases" / ("b" * 40)
    selected.mkdir(parents=True)
    previous.mkdir(parents=True)
    proc = tmp_path / "proc"
    (proc / "2374584").mkdir(parents=True)
    (proc / "2374584" / "cwd").symlink_to(previous)
    state = {"MainPID": "2374584"}

    stale = doctor_cli._plan_render_process_runtime_identity(state, selected, proc_root=proc)
    (proc / "2374584" / "cwd").unlink()
    (proc / "2374584" / "cwd").symlink_to(selected)
    current = doctor_cli._plan_render_process_runtime_identity(state, selected, proc_root=proc)

    assert stale["exact"] is False
    assert stale["reason"] == "loaded process predates selected immutable runtime"
    assert current["exact"] is True


def test_doctor_reports_stopped_plan_render_service(tmp_path: Path, monkeypatch) -> None:
    release = tmp_path / "release"
    (release / "config").mkdir(parents=True)
    source_config = ROOT / "config/tgw-plan-render-local.json"
    installed_config = tmp_path / "tgw-plan-render-local.json"
    installed_config.write_bytes(source_config.read_bytes())
    (release / "config/tgw-plan-render-local.json").write_bytes(source_config.read_bytes())
    paths = doctor_cli.DoctorPaths(plan_render_config=installed_config)
    monkeypatch.setattr(doctor_cli, "_desired_runtime", lambda _paths: ("a" * 40, release, {}))
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda _unit: {"LoadState": "loaded", "ActiveState": "inactive"},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_definition",
        lambda *_args: {"exact": True, "reasons": []},
    )
    result = doctor_cli.check_plan_render_worker(paths)
    assert result["state"] == "FAIL"
    assert result["operator_action"].endswith("repair plan-render-worker")


def _plan_render_check_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[doctor_cli.DoctorPaths, Path]:
    desired = "a" * 40
    runtime_root = tmp_path / "runtime"
    release = runtime_root / "releases" / desired
    (release / "config").mkdir(parents=True)
    source_config = ROOT / "config/tgw-plan-render-local.json"
    (release / "config/tgw-plan-render-local.json").write_bytes(
        source_config.read_bytes()
    )
    (runtime_root / "current").symlink_to(Path("releases") / desired)
    installed_config = tmp_path / "tgw-plan-render-local.json"
    installed_config.write_bytes(source_config.read_bytes())
    plan_render_root = tmp_path / "plan-render"
    plan_render_log_root = plan_render_root / "log"
    plan_render_log_root.mkdir(parents=True)
    plan_render_root.chmod(0o2770)
    plan_render_log_root.chmod(0o2770)
    paths = doctor_cli.DoctorPaths(
        runtime_root=runtime_root,
        plan_render_config=installed_config,
        plan_render_root=plan_render_root,
        plan_render_log_root=plan_render_log_root,
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(pw_uid=os.getuid())
        if name == "db"
        else None,
    )
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=os.getgid())
        if name == "tgw-coders"
        else None,
    )
    monkeypatch.setattr(
        doctor_cli,
        "_desired_runtime",
        lambda _paths: (desired, release, {}),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_state",
        lambda _unit: {"LoadState": "loaded", "ActiveState": "active"},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_unit_definition",
        lambda *_args: {"exact": True, "reasons": []},
    )
    monkeypatch.setattr(
        doctor_cli,
        "_plan_render_process_runtime_identity",
        lambda *_args, **_kwargs: {"exact": True},
    )
    monkeypatch.setattr(
        doctor_cli,
        "read_exact_tree_file",
        lambda _repository, *, path, **_kwargs: (
            0o644,
            (release / path).read_bytes(),
        ),
    )
    return paths, release


def test_doctor_requires_exact_plan_render_runtime_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _release = _plan_render_check_fixture(tmp_path, monkeypatch)
    (paths.runtime_root / "current").unlink()
    (paths.runtime_root / "current").symlink_to("releases/" + "b" * 40)

    result = doctor_cli.check_plan_render_worker(paths)

    assert result["state"] == "FAIL"
    assert "immutable runtime selector differs" in result["detail"]


def test_doctor_rejects_matching_plan_render_config_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _release = _plan_render_check_fixture(tmp_path, monkeypatch)
    target = tmp_path / "mutable-plan-render.json"
    target.write_bytes(paths.plan_render_config.read_bytes())
    paths.plan_render_config.unlink()
    paths.plan_render_config.symlink_to(target)

    result = doctor_cli.check_plan_render_worker(paths)

    assert result["state"] == "FAIL"
    assert "immutable config path or bytes differ" in result["detail"]


def test_plan_render_repair_refuses_matching_config_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, release = _plan_render_check_fixture(tmp_path, monkeypatch)
    (release / "systemd").mkdir()
    (release / "systemd" / doctor_cli._PLAN_RENDER_UNIT).write_text(
        "[Service]\nExecStart=/bin/true\n",
        encoding="utf-8",
    )
    target = tmp_path / "mutable-plan-render.json"
    target.write_bytes(paths.plan_render_config.read_bytes())
    paths.plan_render_config.unlink()
    paths.plan_render_config.symlink_to(target)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": "c" * 40}
    )

    with pytest.raises(doctor_cli.DoctorError, match="unsafe plan_render config"):
        doctor_cli.repair_plan_render_worker(paths)


@pytest.mark.parametrize("selector_state", ["missing", "broken", "wrong"])
def test_plan_render_repair_refuses_runtime_selector_before_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selector_state: str,
) -> None:
    paths, release = _plan_render_check_fixture(tmp_path, monkeypatch)
    current = paths.runtime_root / "current"
    current.unlink()
    if selector_state == "broken":
        current.symlink_to(Path("releases") / ("a" * 40))
        for path in sorted(release.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        release.rmdir()
    elif selector_state == "wrong":
        current.symlink_to(Path("releases") / ("b" * 40))
    effects: list[str] = []
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": "c" * 40}
    )
    monkeypatch.setattr(
        doctor_cli,
        "_atomic_bytes",
        lambda *_args, **_kwargs: effects.append("write"),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda *_args, **_kwargs: effects.append("service"),
    )

    with pytest.raises(doctor_cli.DoctorError, match="repair runtime before"):
        doctor_cli.repair_plan_render_worker(paths)

    assert effects == []


def test_doctor_reports_missing_plan_render_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _release = _plan_render_check_fixture(tmp_path, monkeypatch)
    paths.plan_render_log_root.rmdir()

    result = doctor_cli.check_plan_render_worker(paths)

    assert result["state"] == "FAIL"
    assert "plan_render output directories differ" in result["detail"]
    storage = result["evidence"]["storage"]
    assert storage["exact"] is False
    assert storage["directories"][1]["kind"] == "missing"


def test_plan_render_storage_repair_is_exact_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = doctor_cli.DoctorPaths(
        plan_render_root=tmp_path / "plan-render",
        plan_render_log_root=tmp_path / "plan-render/log",
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(pw_uid=os.getuid())
        if name == "db"
        else None,
    )
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=os.getgid())
        if name == "tgw-coders"
        else None,
    )

    assert doctor_cli._repair_plan_render_storage(paths) is True
    assert doctor_cli._repair_plan_render_storage(paths) is False
    for path in (paths.plan_render_root, paths.plan_render_log_root):
        observed = path.stat(follow_symlinks=False)
        assert stat.S_ISDIR(observed.st_mode)
        assert observed.st_uid == os.getuid()
        assert observed.st_gid == os.getgid()
        assert stat.S_IMODE(observed.st_mode) == 0o2770


def test_plan_render_storage_repair_refuses_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "plan-render"
    root.symlink_to(target, target_is_directory=True)
    paths = doctor_cli.DoctorPaths(
        plan_render_root=root,
        plan_render_log_root=root / "log",
    )
    monkeypatch.setattr(
        doctor_cli.pwd,
        "getpwnam",
        lambda _name: SimpleNamespace(pw_uid=os.getuid()),
    )
    monkeypatch.setattr(
        doctor_cli.grp,
        "getgrnam",
        lambda _name: SimpleNamespace(gr_gid=os.getgid()),
    )

    with pytest.raises(doctor_cli.DoctorError, match="unsafe managed directory"):
        doctor_cli._repair_plan_render_storage(paths)


def test_plan_render_storage_diagnosis_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "actual"
    leaf = actual / "plan-render"
    leaf.mkdir(parents=True)
    leaf.chmod(0o2770)
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    result = doctor_cli._directory_identity(
        linked / "plan-render",
        uid=os.getuid(),
        gid=os.getgid(),
        mode=0o2770,
    )

    assert result["kind"] == "unsafe"
    assert result["exact"] is False


def test_plan_render_storage_diagnosis_rejects_same_target_parent_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "parent"
    leaf = parent / "plan-render"
    leaf.mkdir(parents=True)
    leaf.chmod(0o2770)
    moved = tmp_path / "moved"
    original_open = doctor_cli._open_relative_directory
    calls = 0

    def race(root_descriptor: int, relative: Path) -> int:
        nonlocal calls
        calls += 1
        descriptor = original_open(root_descriptor, relative)
        if calls == 1:
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(doctor_cli, "_open_relative_directory", race)

    result = doctor_cli._directory_identity(
        leaf,
        uid=os.getuid(),
        gid=os.getgid(),
        mode=0o2770,
    )

    assert calls == 2
    assert result["kind"] == "unsafe"
    assert result["exact"] is False


def test_plan_render_repair_receipts_storage_before_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, release = _plan_render_check_fixture(tmp_path, monkeypatch)
    (release / "systemd").mkdir()
    unit_source = release / "systemd" / doctor_cli._PLAN_RENDER_UNIT
    unit_source.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    systemd_root = tmp_path / "systemd"
    systemd_root.mkdir()
    paths = replace(
        paths,
        systemd_install_root=systemd_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    events: list[str] = []
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": "c" * 40}
    )
    monkeypatch.setattr(
        doctor_cli,
        "_receipt",
        lambda _paths, operation, *_args: events.append(f"receipt:{operation}")
        or f"/{operation}.json",
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda command, **_kwargs: events.append("run:" + " ".join(command))
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    result = doctor_cli.repair_plan_render_worker(paths)

    assert result["ok"] is True
    storage_started_index = events.index("receipt:plan-render-storage-started")
    storage_receipt_index = events.index("receipt:plan-render-storage")
    service_indexes = [
        index
        for index, event in enumerate(events)
        if event.startswith("run:systemctl") and "daemon-reload" not in event
    ]
    assert service_indexes
    assert events.index("run:systemctl daemon-reload") < storage_started_index
    assert storage_started_index < storage_receipt_index
    assert storage_receipt_index < min(service_indexes)


def test_plan_render_repair_restarts_only_stale_worker_and_then_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, release = _plan_render_check_fixture(tmp_path, monkeypatch)
    (release / "systemd").mkdir()
    unit_source = release / "systemd" / doctor_cli._PLAN_RENDER_UNIT
    unit_source.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
    systemd_root = tmp_path / "systemd"
    systemd_root.mkdir()
    destination = systemd_root / doctor_cli._PLAN_RENDER_UNIT
    destination.write_bytes(unit_source.read_bytes())
    destination.chmod(0o444)
    paths = replace(
        paths,
        systemd_install_root=systemd_root,
        systemd_unit_uid=os.getuid(),
        systemd_unit_gid=os.getgid(),
    )
    commands: list[list[str]] = []
    runtime_observations = iter([
        {"exact": False, "reason": "loaded process predates selected immutable runtime"},
        {"exact": True},
    ])
    checks = iter([
        {"state": "FAIL", "evidence": {"process_runtime": {"exact": False}}},
        {"state": "PASS", "evidence": {"process_runtime": {"exact": True}}},
        {"state": "PASS", "evidence": {"process_runtime": {"exact": True}}},
        {"state": "PASS", "evidence": {"process_runtime": {"exact": True}}},
    ])
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli, "_verify_release_tree", lambda *_args: {"tree": "c" * 40}
    )
    monkeypatch.setattr(doctor_cli, "check_plan_render_worker", lambda _paths: next(checks))
    monkeypatch.setattr(doctor_cli, "_repair_plan_render_storage", lambda _paths: False)
    monkeypatch.setattr(doctor_cli, "_receipt", lambda *_args: "receipt.json")
    monkeypatch.setattr(
        doctor_cli,
        "_plan_render_process_runtime_identity",
        lambda *_args, **_kwargs: next(runtime_observations),
    )
    monkeypatch.setattr(
        doctor_cli,
        "_run",
        lambda command, **_kwargs: commands.append(command)
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    repaired = doctor_cli.repair_plan_render_worker(paths)
    converged = doctor_cli.repair_plan_render_worker(paths)

    assert repaired["changed"] is True
    assert repaired["service_action"] == "restart"
    assert converged["changed"] is False
    assert converged["service_action"] is None
    assert commands == [
        ["systemctl", "enable", doctor_cli._PLAN_RENDER_UNIT],
        ["systemctl", "restart", doctor_cli._PLAN_RENDER_UNIT],
    ]
