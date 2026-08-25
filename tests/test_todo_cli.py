import subprocess
from pathlib import Path

import pytest

from tgw import todo_cli
from tgw.development.local_workflow import LocalCodingWorkflowError


def test_initialize_enforces_actor_then_binds_todo_and_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    monkeypatch.setattr(todo_cli, "load_config", lambda path: {"postgres_dsn": "dbname=local"})
    monkeypatch.setattr(todo_cli, "require_coder_account", lambda: events.append("actor") or "codex")
    monkeypatch.setattr(todo_cli.todo, "init", lambda dsn: events.append(("todo", dsn)))
    monkeypatch.setattr(todo_cli.state_machine, "init", lambda dsn: events.append(("queue", dsn)))

    assert todo_cli._initialize(Path("fixture.json"))["postgres_dsn"] == "dbname=local"
    assert events == ["actor", ("todo", "dbname=local"), ("queue", "dbname=local")]


def test_run_initializes_before_list_or_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    events = []
    monkeypatch.setattr(todo_cli, "_initialize", lambda path: events.append("init") or {"x": 1})
    monkeypatch.setattr(todo_cli.todo, "cmd_todo", lambda cfg, args: events.append("command") or {"ok": True})

    args = todo_cli.parser().parse_args(["--add", "local item"])
    assert todo_cli.run(args) == 0
    assert events == ["init", "command"]


def test_run_rejects_non_coder_without_touching_todo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(todo_cli, "_initialize", lambda path: (_ for _ in ()).throw(LocalCodingWorkflowError("not in tgw-coders")))
    touched = []
    monkeypatch.setattr(todo_cli.todo, "cmd_todo", lambda cfg, args: touched.append(True))

    assert todo_cli.run(todo_cli.parser().parse_args([])) == 1
    assert touched == []


def test_parser_supports_local_list_add_note_and_done() -> None:
    assert todo_cli.parser().parse_args([]).agent is None
    assert todo_cli.parser().parse_args(["codex", "--add", "x"]).add == "x"
    assert todo_cli.parser().parse_args(["--note", "17", "working"]).note == ["17", "working"]
    assert todo_cli.parser().parse_args(["--done", "17"]).done == 17


def test_parser_does_not_accept_a_dsn_or_config_override() -> None:
    with pytest.raises(SystemExit):
        todo_cli.parser().parse_args(["--config", "/tmp/not-the-installed-config.json"])


def test_run_uses_only_the_installed_local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    monkeypatch.setattr(todo_cli, "_initialize", lambda path: observed.append(path) or {})
    monkeypatch.setattr(todo_cli.todo, "cmd_todo", lambda cfg, args: {"ok": True})

    assert todo_cli.run(todo_cli.parser().parse_args([])) == 0
    assert observed == [todo_cli.DEFAULT_CONFIG]


def test_operator_routes_are_narrow_and_help_is_offline() -> None:
    source = (Path(__file__).parents[1] / "bin/tgw-operator").read_text(encoding="utf-8")
    assert "todo)" in source
    assert "/opt/TGW/tgw-lib/bin/tgw-todo" in source
    assert "--help|-h|help" in source
    assert "local Todo store" in source
    assert "exec /usr/local/libexec/tgw-production-client" in source
    for forbidden in ("ssh ", "sudo ", "remote-provision", "approval", "admission", "dispatch"):
        assert forbidden not in source.lower()


@pytest.mark.parametrize("argument", ["--help", "help"])
def test_operator_help_exits_before_production_delegation(argument: str) -> None:
    launcher = Path(__file__).parents[1] / "bin/tgw-operator"
    result = subprocess.run([launcher, argument], text=True, capture_output=True, check=False)

    assert result.returncode == 0
    assert "Local tgw-lib controls" in result.stdout
    assert result.stderr == ""
