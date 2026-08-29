from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tgw import coding_cli


class _FakeStore:
    def __init__(self, root: Path, prior):
        self.root = root
        self.prior = prior

    def find(self, identifier):
        return self.prior

    def path(self, identity: str) -> Path:
        target = self.root / (identity.removeprefix("coding:") + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("{}", encoding="utf-8")
        return target


class _FakeTodo:
    def __init__(self, item):
        self.item = item
        self.cleared: list[tuple[int, str]] = []

    def todo_get(self, identifier):
        return self.item

    def todo_set_status_note(self, identifier, note, *, suppress_plan_render=False):
        self.cleared.append((identifier, note))


def _git_repo(path: Path) -> Path:
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / "README").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=path, check=True, capture_output=True)
    return path


def _setup(monkeypatch, tmp_path, *, item, prior):
    lifecycles = tmp_path / "lifecycles"
    lifecycles.mkdir()
    store = _FakeStore(lifecycles, prior)
    fake_todo = _FakeTodo(item)
    monkeypatch.setattr(
        coding_cli, "_initialize",
        lambda config_path: {"coding": {"lifecycle_root": str(lifecycles)}},
    )
    monkeypatch.setattr(coding_cli, "require_coder_account", lambda: "deepseek")
    monkeypatch.setattr(coding_cli, "LifecycleStore", lambda root: store)
    monkeypatch.setattr(coding_cli.todo, "todo_get", fake_todo.todo_get)
    monkeypatch.setattr(coding_cli.todo, "todo_set_status_note", fake_todo.todo_set_status_note)
    return store, fake_todo


def _open_item(todo_id=1922):
    return {"id": todo_id, "agent": "deepseek", "done_at": None, "status_note": None}


def test_rebind_refuses_missing_lifecycle(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, item=_open_item(), prior=None)
    with pytest.raises(coding_cli.CodingCLIError, match="no coding lifecycle"):
        coding_cli.rebind(1922, config_path=Path("/nonexistent"))


def test_rebind_refuses_non_failed_state(tmp_path, monkeypatch):
    prior = {"state": "QUEUED", "root_id": "coding:abc", "binding": {"worktree": None}}
    _setup(monkeypatch, tmp_path, item=_open_item(), prior=prior)
    with pytest.raises(coding_cli.CodingCLIError, match="not rebindable"):
        coding_cli.rebind(1922, config_path=Path("/nonexistent"))


def test_rebind_refuses_dirty_worktree(tmp_path, monkeypatch):
    worktree = _git_repo(tmp_path / "wt")
    (worktree / "dirty.py").write_text("x = 1\n", encoding="utf-8")
    prior = {"state": "FAILED", "root_id": "coding:abc", "binding": {"worktree": str(worktree)}}
    _setup(monkeypatch, tmp_path, item=_open_item(), prior=prior)
    with pytest.raises(coding_cli.CodingCLIError, match="not clean"):
        coding_cli.rebind(1922, config_path=Path("/nonexistent"))


def test_rebind_archives_journal_and_clears_binding(tmp_path, monkeypatch):
    worktree = _git_repo(tmp_path / "wt")
    prior = {"state": "FAILED", "root_id": "coding:abc", "binding": {"worktree": str(worktree)}}
    store, fake_todo = _setup(monkeypatch, tmp_path, item=_open_item(), prior=prior)
    result = coding_cli.rebind(1922, config_path=Path("/nonexistent"))
    assert result["ok"] is True
    assert result["archived_lifecycle"].startswith("abc.json.rebind-")
    assert (tmp_path / "lifecycles" / result["archived_lifecycle"]).is_file()
    assert not (tmp_path / "lifecycles" / "abc.json").exists()
    assert fake_todo.cleared == [(1922, "")]
    assert result["worktree"] == str(worktree)
    assert "tgw coding start" in result["note"]


def test_rebind_archives_remediation_required_state(tmp_path, monkeypatch):
    worktree = _git_repo(tmp_path / "wt")
    prior = {"state": "REMEDIATION_REQUIRED", "root_id": "coding:def", "binding": {"worktree": str(worktree)}}
    store, fake_todo = _setup(monkeypatch, tmp_path, item=_open_item(), prior=prior)
    result = coding_cli.rebind(1922, config_path=Path("/nonexistent"))
    assert result["ok"] is True
    assert result["archived_lifecycle"].startswith("def.json.rebind-")
    assert fake_todo.cleared == [(1922, "")]


def test_rebind_refuses_closed_todo(tmp_path, monkeypatch):
    item = _open_item()
    item["done_at"] = "2026-08-29T00:00:00+00:00"
    _setup(monkeypatch, tmp_path, item=item, prior=None)
    with pytest.raises(coding_cli.CodingCLIError, match="not an open Todo"):
        coding_cli.rebind(1922, config_path=Path("/nonexistent"))
