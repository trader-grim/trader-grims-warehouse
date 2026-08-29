"""The coding MCP is a thin adapter over the installed local coding workflow."""

from __future__ import annotations

import json
from pathlib import Path

from tgw import coding_mcp_server


def test_start_calls_the_same_local_cli_implementation(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setenv("TGW_CODING_CONFIG", str(tmp_path / "coding.json"))

    def local_start(todo_id, *, config_path, source_commit):
        observed.update(
            todo_id=todo_id,
            config_path=config_path,
            source_commit=source_commit,
        )
        return {
            "schema": "tgw-local-coding-start/v1",
            "ok": True,
            "todo_id": todo_id,
            "worktree": "/opt/TGW/var/worktrees/todo-1732-plan-test",
            "session": {
                "cwd": "/opt/TGW/var/worktrees/todo-1732-plan-test",
                "codex": [
                    "codex",
                    "-C",
                    "/opt/TGW/var/worktrees/todo-1732-plan-test",
                ],
            },
        }

    monkeypatch.setattr(coding_mcp_server.coding_cli, "start", local_start)

    result = json.loads(coding_mcp_server.tgw_coding_start(1732, "a" * 40))

    assert result["schema"] == "tgw-local-coding-start/v1"
    assert result["session"]["cwd"] == result["worktree"]
    assert observed == {
        "todo_id": 1732,
        "config_path": tmp_path / "coding.json",
        "source_commit": "a" * 40,
    }


def test_start_normalizes_an_empty_source_commit(monkeypatch):
    observed = {}
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "start",
        lambda todo_id, **kwargs: observed.update(todo_id=todo_id, **kwargs)
        or {"ok": True},
    )

    assert json.loads(coding_mcp_server.tgw_coding_start(1732))["ok"] is True
    assert observed["source_commit"] is None


def test_read_and_control_tools_share_cli_functions(monkeypatch, tmp_path):
    config = tmp_path / "coding.json"
    calls = []
    monkeypatch.setenv("TGW_CODING_CONFIG", str(config))
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "status",
        lambda todo_id, *, config_path: calls.append(
            ("status", todo_id, config_path)
        )
        or {"ok": True, "todo_id": todo_id},
    )
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "access_status",
        lambda todo_id, *, config_path, full_jobs: calls.append(
            ("access-status", todo_id, config_path, full_jobs)
        )
        or {"ok": True, "todo_id": todo_id, "jobs_included": full_jobs},
    )
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "job_log",
        lambda job_id, *, config_path: calls.append(
            ("log", job_id, config_path)
        )
        or {"job_id": job_id},
    )
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "stop",
        lambda job_id, *, config_path: calls.append(
            ("stop", job_id, config_path)
        )
        or {"job_id": job_id, "state": "cancelled"},
    )

    assert json.loads(coding_mcp_server.tgw_coding_status(1732))["todo_id"] == 1732
    assert json.loads(coding_mcp_server.tgw_coding_access_status())["ok"] is True
    assert json.loads(coding_mcp_server.tgw_coding_log("job-1"))["job_id"] == "job-1"
    assert json.loads(coding_mcp_server.tgw_coding_stop("job-1"))["state"] == (
        "cancelled"
    )
    assert calls == [
        ("status", 1732, config),
        ("access-status", None, config, False),
        ("log", "job-1", config),
        ("stop", "job-1", config),
    ]


def test_errors_are_explicit_and_do_not_become_authority_gates(monkeypatch):
    monkeypatch.setattr(
        coding_mcp_server.coding_cli,
        "start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            coding_mcp_server.coding_cli.CodingCLIError("Todo is unavailable")
        ),
    )

    result = json.loads(coding_mcp_server.tgw_coding_start(9999))

    assert result == {
        "schema": "tgw-local-coding-mcp-error/v1",
        "ok": False,
        "operation": "start",
        "error": "Todo is unavailable",
        "error_type": "CodingCLIError",
    }


def test_main_runs_stdio_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        coding_mcp_server.mcp,
        "run",
        lambda **kwargs: calls.append(kwargs),
    )

    coding_mcp_server.main()

    assert calls == [{"transport": "stdio"}]


def test_launcher_is_local_and_has_no_obsolete_backend():
    launcher = (
        Path(__file__).resolve().parents[1] / "bin" / "tgw-coding-mcp"
    ).read_text(encoding="utf-8")
    source = Path(coding_mcp_server.__file__).read_text(encoding="utf-8")

    assert "/opt/TGW/tgw-lib/coding-runtime/current" in launcher
    assert "TGW_CODING_CONFIG=/opt/TGW/tgw-lib/config/tgw-coding-local.json" in launcher
    assert "tgw.coding_mcp_server" in launcher
    assert "coding_cli.start" in source
    assert "/api/coding/requests" not in source
    assert "ssh" not in source.lower()
    assert "sudo" not in source.lower()
