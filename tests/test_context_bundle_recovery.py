from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _launcher_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(os, "geteuid", lambda: os.getuid())
    spec = importlib.util.spec_from_file_location("context_launcher_test", LAUNCHER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_declares_only_narrow_bundle_and_retires_rebind() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    assert 'RETIRED_TOOLS = ("tgw_context_bundle", "tgw_context_confirm_rebind")' in source
    assert "challenge:" not in source
    assert "card_json:" not in source
    assert "grant_json:" not in source
    assert "def tgw_context_bundle(task: str = \"current\", limit: int = 12)" in source


def test_bundle_agrees_with_local_bindings_and_refuses_malformed_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _launcher_module(monkeypatch)
    monkeypatch.setattr(module, "_harness_actor", lambda: "codex")
    monkeypatch.setattr(
        module,
        "_current_context",
        lambda: {
            "active_capability": "PP-WORKFLOW-001",
            "active_treatment": "todo:1738",
            "plan_commit": "a" * 40,
            "source_commit": "b" * 40,
            "source_tree": "c" * 40,
            "snapshot_sha256": "sha256:" + "d" * 64,
        },
    )

    class ContextError(RuntimeError):
        pass

    server = SimpleNamespace(
        ContextError=ContextError,
        context_status=lambda: {"actor": "codex", "plan": {"approved_commit": "a" * 40}},
        plan_graph=lambda task, receiver, operation, limit: {
            "task": task, "receiver": receiver, "limit": limit
        },
        runbooks=lambda query, path, start, lines, limit, authority: {
            "query": query, "authority": authority
        },
        code_graph=lambda operation, query, limit: {"operation": operation, "limit": limit},
        _json_call=lambda function: json.dumps(function(), sort_keys=True),
    )
    value = json.loads(module.context_server_bundle(server, "current", 7))
    assert value["actor"] == value["receiver"] == "codex"
    assert value["task"] == "PP-WORKFLOW-001"
    assert value["plan_graph"]["receiver"] == "codex"
    assert value["current_context"]["snapshot_sha256"] == "sha256:" + "d" * 64
    assert all(effect is False for effect in value["dependencies"].values())
    with pytest.raises(ContextError, match="ordinary non-empty"):
        module.context_server_bundle(server, "", 7)
    with pytest.raises(ContextError, match="between 1 and 50"):
        module.context_server_bundle(server, "current", 0)


def test_doctor_context_launcher_check_and_atomic_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    source = release / "scripts/tgw_context_debian_stdio.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"#!/bin/sh\nexit 0\n")
    source.chmod(0o555)
    installed = tmp_path / "bin/tgw-context-mcp"
    installed.parent.mkdir()
    installed.write_bytes(b"old\n")
    installed.chmod(0o555)
    paths = doctor_cli.DoctorPaths(context_launcher=installed)
    monkeypatch.setattr(doctor_cli, "_selected_context_launcher", lambda _paths: ("a" * 40, source))
    monkeypatch.setattr(doctor_cli, "_require_root", lambda: None)
    monkeypatch.setattr(doctor_cli, "_context_processes", lambda _paths: [
        {"pid": 41, "predates_launcher": True},
        {"pid": 42, "predates_launcher": False},
    ])
    assert doctor_cli.check_context_launcher(paths)["state"] == "FAIL"
    result = doctor_cli.repair_context_launcher(paths)
    assert result["changed"] is True
    assert result["restart_required"] == [41]
    assert result["client_processes_mutated"] is False
    assert installed.read_bytes() == source.read_bytes()
    assert doctor_cli.check_context_launcher(paths)["state"] == "PASS"
