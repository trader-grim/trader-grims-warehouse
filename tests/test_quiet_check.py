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


_EMPTY_SUMMARY = {"queued": 0, "processing": 0, "dead_letter": 0}


def test_quiet_when_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
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
    monkeypatch.setattr(sm, "queue_state_summary", lambda: {"queued": 3, "processing": 1, "dead_letter": 0})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["quiet"] is False
    assert out["active_total"] == 4
    assert "message" not in out  # no idle nudge when busy


def test_missing_suggestions_file_counts_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    cfg = {"postgres_dsn": "x", "plan_vault_path": tmp_path}  # no suggestions dir
    out = api.cmd_quiet_check(cfg)
    assert out["pending_suggestions"] == 0


def test_todo_db_failure_is_graceful(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)

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
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)

    # idle + --notify -> notify fires
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    api.cmd_quiet_check(_cfg(tmp_path), notify_on_idle=True)
    assert len(calls) == 1

    # busy + --notify -> no notify
    monkeypatch.setattr(sm, "active_depths", lambda: {"ebay_draft": 1})
    api.cmd_quiet_check(_cfg(tmp_path), notify_on_idle=True)
    assert len(calls) == 1  # unchanged


# ---------------------------------------------------------------------------
# Phase 2 — KDE Connect push
# ---------------------------------------------------------------------------

def test_kdc_push_when_idle(tmp_path, monkeypatch):
    """KDE Connect send_text is called when idle and kdc_device is set."""
    import tgw.apis.kdeconnect as kdc_mod

    sent = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    monkeypatch.setattr(kdc_mod, "get_device_id", lambda name, **k: "deadbeef" * 4)
    monkeypatch.setattr(kdc_mod, "send_text", lambda dev_id, text: sent.append((dev_id, text)) or True)

    out = api.cmd_quiet_check(_cfg(tmp_path), kdc_device="my-phone")
    assert out["kdeconnect_pushed"] is True
    assert len(sent) == 1
    assert "idle" in sent[0][1].lower()


def test_kdc_not_pushed_when_busy(tmp_path, monkeypatch):
    """KDE Connect is NOT called when the pipeline is busy."""
    import tgw.apis.kdeconnect as kdc_mod

    sent = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {"ebay_draft": 2})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: {"queued": 2, "processing": 0, "dead_letter": 0})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    monkeypatch.setattr(kdc_mod, "send_text", lambda *a, **k: sent.append(a) or True)

    out = api.cmd_quiet_check(_cfg(tmp_path), kdc_device="my-phone")
    assert "kdeconnect_pushed" not in out
    assert sent == []


def test_kdc_error_captured_does_not_raise(tmp_path, monkeypatch):
    """KDE Connect failure is captured in kdeconnect_error; result is still ok."""
    import tgw.apis.kdeconnect as kdc_mod

    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    monkeypatch.setattr(kdc_mod, "get_device_id", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no kdeconnect-cli")))

    out = api.cmd_quiet_check(_cfg(tmp_path), kdc_device="my-phone")
    assert out["ok"] is True
    assert out["kdeconnect_pushed"] is False
    assert "no kdeconnect-cli" in out["kdeconnect_error"]


def test_kdc_not_set_no_push(tmp_path, monkeypatch):
    """No kdc_device → kdeconnect_pushed key absent from result."""
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert "kdeconnect_pushed" not in out


# ---------------------------------------------------------------------------
# Task 77 — queued/processing/dead_letter fields in result
# ---------------------------------------------------------------------------

def test_state_summary_fields_in_result(tmp_path, monkeypatch):
    """Result includes queued, processing, dead_letter from queue_state_summary."""
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary",
                        lambda: {"queued": 5, "processing": 2, "dead_letter": 1})
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["queued"] == 5
    assert out["processing"] == 2
    assert out["dead_letter"] == 1


def test_state_summary_zero_when_idle(tmp_path, monkeypatch):
    """All three counts are 0 when pipeline is empty."""
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "active_depths", lambda: {})
    monkeypatch.setattr(sm, "queue_state_summary", lambda: _EMPTY_SUMMARY)
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])

    out = api.cmd_quiet_check(_cfg(tmp_path))
    assert out["queued"] == 0
    assert out["processing"] == 0
    assert out["dead_letter"] == 0
    assert out["ok"] is True
