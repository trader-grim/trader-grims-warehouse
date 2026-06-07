"""PP-CAPTURE-001 — tests for `tgw quiet-check`.

When the pipeline is idle, surface pending suggestions + open TODOs. Read-only;
state_machine, todo, and notify are all stubbed so no real DB/desktop is touched.
"""

import tgw.api as api
import tgw.notify as notify_mod
import tgw.todo as todo
from tgw.queue import state_machine as sm

_SUGGESTIONS = (
    "# Suggestions\n"
    "- [ ] 2026-06-07T00:00 :: idea one\n"
    "- [x] 2026-06-06T00:00 :: already processed\n"
    "- [ ] 2026-06-07T01:00 :: idea two\n"
    "just some prose, not a checkbox\n"
)


def _cfg(tmp_path, suggestions=_SUGGESTIONS):
    sdir = tmp_path / "suggestions"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "SUGGESTIONS.md").write_text(suggestions, encoding="utf-8")
    return {"postgres_dsn": "postgresql://fake/db", "plan_vault_path": tmp_path}


def test_quiet_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [{"id": 1}, {"id": 2}])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["quiet"] is True
    assert out["active_total"] == 0
    assert out["pending_suggestions"] == 2  # only [ ], not [x] or prose
    assert out["open_todos"] == 2
    assert "message" in out


def test_busy_when_active_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {"ebay_draft": 3, "ai_identify": 1})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["quiet"] is False
    assert out["active_total"] == 4
    assert "message" not in out  # no idle nudge when busy


def test_missing_suggestions_file_counts_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    cfg = {"postgres_dsn": "x", "plan_vault_path": tmp_path}  # no suggestions dir
    out = api.cmd_quiet_check(cfg)
    assert out["pending_suggestions"] == 0


def test_todo_db_failure_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})

    def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(todo, "todo_list", _boom)
    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["ok"] is True
    assert out["open_todos"] == 0


def test_notify_called_only_when_idle_and_flagged(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    monkeypatch.setattr(notify_mod, "notify", lambda *a, **k: calls.append((a, k)))

    # idle + --notify -> notify fires
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    api.cmd_quiet_check(_cfg(tmp_path), notify_on_idle=True)
    assert len(calls) == 1

    # busy + --notify -> no notify
    monkeypatch.setattr(sm, "active_depths", lambda: {"ebay_draft": 1})
    api.cmd_quiet_check(_cfg(tmp_path), notify_on_idle=True)
    assert len(calls) == 1  # unchanged
