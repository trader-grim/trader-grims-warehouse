from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts/tgw_context_debian_stdio.py"

import tgw  # noqa: E402

tgw.__path__.insert(0, str(ROOT / "src/tgw"))
_DOCTOR_SPEC = importlib.util.spec_from_file_location(
    "todo_1738_doctor_cli", ROOT / "src/tgw/doctor_cli.py"
)
assert _DOCTOR_SPEC and _DOCTOR_SPEC.loader
doctor_cli = importlib.util.module_from_spec(_DOCTOR_SPEC)
sys.modules[_DOCTOR_SPEC.name] = doctor_cli
_DOCTOR_SPEC.loader.exec_module(doctor_cli)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _snapshot() -> dict[str, Any]:
    plan_commit = "a" * 40
    source_commit = "b" * 40
    source_tree = "c" * 40
    capability = "PP-WORKFLOW-001"
    task = {
        "schema": "tgw-current-task/v1",
        "plan": {"approved_commit": plan_commit},
        "implementation": {
            "development_source": {
                "commit": source_commit,
                "next_leaf": capability,
            }
        },
    }
    cursor = {
        "schema": "tgw-plan-execution-cycle-cursor/v1",
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "resolved": {"next_treatment": f"todo:{capability}"},
    }
    value = {
        "schema": "tgw-current-context-snapshot/v1",
        "plan_commit": plan_commit,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "active_capability": capability,
        "active_treatment": f"todo:{capability}",
        "task": task,
        "cursor": cursor,
    }
    value["snapshot_sha256"] = "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()
    return value


def _launcher_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    snapshot = _snapshot()
    releases = tmp_path / "releases"
    runtime = releases / snapshot["source_commit"] / "src/tgw"
    runtime.mkdir(parents=True)
    for name in (
        "context_mcp_server.py",
        "current_context_snapshot.py",
        "local_context_runtime.py",
    ):
        path = runtime / name
        path.write_text("# immutable test runtime\n", encoding="utf-8")
        path.chmod(0o444)
    for directory in (runtime, runtime.parent, runtime.parent.parent):
        directory.chmod(0o555)
    current = tmp_path / "tgw-context-current.json"
    current.write_text(json.dumps(snapshot), encoding="utf-8")
    current.chmod(0o444)
    context_source = tmp_path / "source"
    context_source.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{}", encoding="utf-8")
    source = LAUNCHER.read_text(encoding="utf-8")
    replacements = {
        'RUNTIME_RELEASES = Path("/opt/TGW/tgw-lib/coding-runtime/releases")': (
            f"RUNTIME_RELEASES = Path({str(releases)!r})"
        ),
        'CONTEXT_SOURCE = Path("/opt/TGW/tgw-lib/src/trader-grims-warehouse")': (
            f"CONTEXT_SOURCE = Path({str(context_source)!r})"
        ),
        'CATALOG = Path("/opt/TGW/tgw-lib/config/tgw-context-debian-v1.json")': (
            f"CATALOG = Path({str(catalog)!r})"
        ),
        'CURRENT_CONTEXT = Path("/opt/TGW/tgw-lib/config/tgw-context-current.json")': (
            f"CURRENT_CONTEXT = Path({str(current)!r})"
        ),
        "TRUSTED_RUNTIME_OWNERS = frozenset({0, 65534})": (
            f"TRUSTED_RUNTIME_OWNERS = frozenset({{{os.getuid()}}})"
        ),
    }
    for old, new in replacements.items():
        assert old in source
        source = source.replace(old, new)
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid())
    module = ModuleType("context_launcher_test")
    module.__file__ = str(LAUNCHER)
    exec(compile(source, str(LAUNCHER), "exec"), module.__dict__)
    return module


def _server(snapshot: dict[str, Any]) -> SimpleNamespace:
    plan_tree = "d" * 40
    solution = "sha256:" + "e" * 64
    evidence_head = "f" * 40
    evidence_tree = "1" * 40
    freshness = "sha256:" + "2" * 64
    current_context = {
        key: snapshot[key]
        for key in (
            "active_capability",
            "active_treatment",
            "plan_commit",
            "source_commit",
            "source_tree",
            "snapshot_sha256",
        )
    }
    status = {
        "actor": "codex",
        "generation_status": {"state": "CURRENT"},
        "plan": {
            "approved_commit": snapshot["plan_commit"],
            "approved_tree": plan_tree,
            "approved_solution_hash": solution,
            "evidence_head": evidence_head,
            "evidence_tree": evidence_tree,
        },
        "source": {
            "commit": snapshot["source_commit"],
            "tree": snapshot["source_tree"],
        },
        "code_graph": {
            "commit": snapshot["source_commit"],
            "tree": snapshot["source_tree"],
            "freshness_hash": freshness,
        },
        "current_context": current_context,
    }

    class ContextError(RuntimeError):
        pass

    return SimpleNamespace(
        ContextError=ContextError,
        context_status=lambda: status,
        plan_graph=lambda task, receiver, operation, limit: {
            "task": task,
            "receiver": receiver,
            "limit": limit,
            "plan_commit": snapshot["plan_commit"],
            "plan_tree": plan_tree,
            "approved_solution_hash": solution,
            "current_context": current_context,
        },
        runbooks=lambda query, path, start, lines, limit, authority: {
            "query": query,
            "authority": authority,
            "revisions": [
                {
                    "authority": "canonical-plan-runbook",
                    "commit": evidence_head,
                    "tree": evidence_tree,
                },
                {
                    "authority": "committed-application-runbook",
                    "commit": snapshot["source_commit"],
                    "tree": snapshot["source_tree"],
                },
            ],
        },
        code_graph=lambda operation, query, limit: {
            "operation": operation,
            "limit": limit,
            "binding": {
                "commit": snapshot["source_commit"],
                "tree": snapshot["source_tree"],
                "freshness_hash": freshness,
            },
        },
        _json_call=lambda function: json.dumps(function(), sort_keys=True),
    )


def _selected(tmp_path: Path) -> dict[str, Any]:
    release = tmp_path / "release"
    launcher = release / "scripts/tgw_context_debian_stdio.py"
    runtime = release / "src/tgw"
    launcher.parent.mkdir(parents=True)
    runtime.mkdir(parents=True)
    launcher.write_bytes(b"#!/bin/sh\nexit 0\n")
    launcher.chmod(0o555)
    modules = {}
    for name in (
        "context_mcp_server",
        "current_context_snapshot",
        "local_context_runtime",
    ):
        path = runtime / f"{name}.py"
        path.write_text(f"# {name}\n", encoding="utf-8")
        path.chmod(0o444)
        modules[name] = path
    required = {"launcher": launcher, **modules}
    return {
        "commit": "a" * 40,
        "release": release,
        "release_tree": {"tree": "b" * 40},
        "launcher": launcher,
        "runtime_source": release / "src",
        "modules": modules,
        "hashes": {
            name: doctor_cli._file_hash(path) for name, path in required.items()
        },
    }


def _doctor_paths(tmp_path: Path) -> doctor_cli.DoctorPaths:
    installed = tmp_path / "bin/tgw-context-mcp"
    installed.parent.mkdir(exist_ok=True)
    installed.write_bytes(b"old\n")
    installed.chmod(0o755)
    return doctor_cli.DoctorPaths(
        context_launcher=installed,
        receipts=tmp_path / "receipts",
        context_install_uid=os.getuid(),
        context_install_gid=os.getgid(),
    )


def test_launcher_declares_only_narrow_bundle_and_immutable_release() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert (
        'RETIRED_TOOLS = ("tgw_context_bundle", "tgw_context_confirm_rebind")' in source
    )
    assert "challenge:" not in source
    assert "card_json:" not in source
    assert "grant_json:" not in source
    assert 'Path("/opt/TGW/tgw-lib/context-runtime/src")' not in source
    assert "RUNTIME_RELEASES / source_commit" in source
    assert 'def tgw_context_bundle(task: str = "current", limit: int = 12)' in source


def test_launcher_bootstrap_binds_the_atomic_snapshot_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)

    assert module.SERVER_SOURCE == tmp_path / "releases" / ("b" * 40) / "src"
    assert module._current_context()["source_commit"] == "b" * 40


def test_bundle_agrees_with_every_local_binding_and_refuses_malformed_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)

    value = json.loads(module.context_server_bundle(server, "current", 7))

    assert value["actor"] == value["receiver"] == "codex"
    assert value["task"] == "PP-WORKFLOW-001"
    assert value["plan_graph"]["receiver"] == "codex"
    assert value["current_context"]["snapshot_sha256"] == snapshot["snapshot_sha256"]
    assert all(effect is False for effect in value["dependencies"].values())
    with pytest.raises(server.ContextError, match="ordinary non-empty"):
        module.context_server_bundle(server, "", 7)
    with pytest.raises(server.ContextError, match="between 1 and 50"):
        module.context_server_bundle(server, "current", 0)


def test_bundle_fails_closed_on_a_mixed_source_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)
    server.context_status()["source"]["tree"] = "9" * 40

    with pytest.raises(server.ContextError, match="source identity differs"):
        module.context_server_bundle(server, "current", 7)


def test_bundle_fails_closed_when_status_changes_during_retrieval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _launcher_module(tmp_path, monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(module, "_current_context", lambda: dict(snapshot))
    server = _server(snapshot)
    stable_status = server.context_status()
    calls = 0

    def changing_status() -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return stable_status
        return {**stable_status, "context_sha256": "sha256:" + "9" * 64}

    server.context_status = changing_status

    with pytest.raises(server.ContextError, match="status changed during retrieval"):
        module.context_server_bundle(server, "current", 7)


def test_doctor_context_launcher_check_and_receipted_atomic_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(
        doctor_cli,
        "_context_processes",
        lambda _paths: [
            {"pid": 41, "predates_launcher": True},
            {"pid": 42, "predates_launcher": False},
        ],
    )

    assert doctor_cli.check_context_launcher(paths)["state"] == "FAIL"
    result = doctor_cli.repair_context_launcher(paths)

    installed = doctor_cli._surface_snapshot(paths.context_launcher)
    assert result["changed"] is True
    assert result["restart_required"] == [41]
    assert result["client_processes_mutated"] is False
    assert Path(result["receipt"]).is_file()
    assert installed["raw"] == selected["launcher"].read_bytes()
    assert installed["mode"] == 0o555
    assert installed["uid"] == os.getuid()
    assert installed["gid"] == os.getgid()
    assert set(result["runtime_hashes"]) == {
        "launcher",
        "context_mcp_server",
        "current_context_snapshot",
        "local_context_runtime",
    }
    assert doctor_cli.check_context_launcher(paths)["state"] == "PASS"


def test_doctor_context_launcher_check_rejects_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    paths.context_launcher.write_bytes(selected["launcher"].read_bytes())
    paths.context_launcher.chmod(0o555)
    paths = replace(
        paths,
        context_install_uid=os.getuid() + 1,
    )
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )

    result = doctor_cli.check_context_launcher(paths)

    assert result["state"] == "FAIL"
    assert "root:root 0555" in result["detail"]


def test_doctor_context_launcher_refuses_a_concurrent_destination_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    paths = _doctor_paths(tmp_path)
    monkeypatch.setattr(
        doctor_cli, "_selected_context_artifacts", lambda _paths: selected
    )
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    original_snapshot = doctor_cli._surface_snapshot
    calls = 0

    def raced(path: Path) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            path.write_bytes(b"concurrent\n")
            path.chmod(0o555)
        return original_snapshot(path)

    monkeypatch.setattr(doctor_cli, "_surface_snapshot", raced)

    with pytest.raises(doctor_cli.DoctorError, match="changed concurrently"):
        doctor_cli.repair_context_launcher(paths)

    assert paths.context_launcher.read_bytes() == b"concurrent\n"


def test_doctor_context_launcher_rolls_back_if_runtime_changes_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    changed_selection = {**selected, "commit": "c" * 40}
    paths = _doctor_paths(tmp_path)
    original = paths.context_launcher.read_bytes()
    calls = 0

    def selection(_paths: doctor_cli.DoctorPaths) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return changed_selection if calls == 3 else selected

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", selection)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    with pytest.raises(doctor_cli.DoctorError, match="original launcher restored"):
        doctor_cli.repair_context_launcher(paths)

    assert paths.context_launcher.read_bytes() == original
    assert stat.S_IMODE(paths.context_launcher.stat().st_mode) == 0o755


def test_doctor_context_launcher_failure_is_durably_receipted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = _selected(tmp_path)
    changed_selection = {**selected, "commit": "c" * 40}
    paths = _doctor_paths(tmp_path)
    calls = 0

    def selection(_paths: doctor_cli.DoctorPaths) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return changed_selection if calls == 3 else selected

    monkeypatch.setattr(doctor_cli, "_selected_context_artifacts", selection)
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)

    with pytest.raises(doctor_cli.DoctorError, match="failure receipt"):
        doctor_cli.repair("context-launcher", paths)

    assert list(paths.receipts.glob("*context-launcher-started.json"))
    assert list(paths.receipts.glob("*context-launcher-failed.json"))
