"""PP-MCP-001 — tests for all 13 TGW MCP tools.

The MCP layer is what Claude itself uses to query live queue/item/health state
and re-enqueue actions mid-session; the wrapper-to-internal contract drifted
once (9 vs 10 tools), so it's worth locking down.

FastMCP's @mcp.tool() returns the original function, so each tool is called
directly. _get_cfg() is short-circuited by setting the module-level _cfg, and
every internal (api.*, state_machine.*, health.check_all, worker_base.*) is
monkeypatched so no real PostgreSQL / eBay / Ollama is touched. tgw_health is
invoked with include_ebay=False inside the tool, so the dead token is off-path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import pytest

import tgw.mcp_server as mcp_server
from tgw import resolver
from tgw.queue import state_machine as sm


def test_sse_binding_uses_service_environment(monkeypatch):
    monkeypatch.setenv("TGW_MCP_HOST", "100.107.99.66")
    monkeypatch.setenv("TGW_MCP_PORT", "8765")

    assert mcp_server._sse_binding() == ("100.107.99.66", 8765)


@pytest.mark.parametrize("port", ["not-a-port", "0", "65536"])
def test_sse_binding_rejects_invalid_port(monkeypatch, port):
    monkeypatch.setenv("TGW_MCP_PORT", port)

    with pytest.raises(ValueError, match="TGW_MCP_PORT"):
        mcp_server._sse_binding()


def test_sse_entrypoint_applies_service_binding(monkeypatch):
    calls = []
    monkeypatch.setenv("TGW_MCP_HOST", "100.107.99.66")
    monkeypatch.setenv("TGW_MCP_PORT", "8765")
    monkeypatch.setattr(sys, "argv", ["tgw-mcp", "--sse"])
    monkeypatch.setattr(mcp_server.mcp, "run", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(mcp_server.mcp.settings, "host", "127.0.0.1")
    monkeypatch.setattr(mcp_server.mcp.settings, "port", 8000)
    monkeypatch.setattr(mcp_server.mcp.settings.transport_security, "allowed_hosts", [])
    monkeypatch.setattr(mcp_server.mcp.settings.transport_security, "allowed_origins", [])

    mcp_server.main()

    assert calls == [{"transport": "sse"}]
    assert mcp_server.mcp.settings.host == "100.107.99.66"
    assert mcp_server.mcp.settings.port == 8765
    assert mcp_server.mcp.settings.transport_security.allowed_hosts == ["100.107.99.66:8765"]
    assert mcp_server.mcp.settings.transport_security.allowed_origins == [
        "http://100.107.99.66:8765",
    ]

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeCur:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return list(self._rows)


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, *a, **k):
        return _FakeCur(self._rows)


def _install_conn(monkeypatch, rows):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "_conn", lambda: _FakeConn(rows))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    c = {"itemdata_root": tmp_path, "postgres_dsn": "postgresql://fake/db"}
    monkeypatch.setattr(mcp_server, "_cfg", c)
    # resolver.find_current_sku caches a process-level {sku_old: current}
    # index the first time it's called; reset it per-test so a renamed
    # fixture written under this test's tmp_path is actually picked up.
    monkeypatch.setattr(resolver, "_sku_old_index", None)
    return c


def _write_item(cfg, sku, doc):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")


# ---------------------------------------------------------------------------
# Registration / drift guard — all 10 tools present and callable
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "tgw_get_item", "tgw_search_items", "tgw_search_full", "tgw_queue_status",
    "tgw_health", "tgw_enqueue", "tgw_get_todo", "tgw_add_suggest",
    "tgw_dead_letter", "tgw_hint_trail", "tgw_catalog_verify",
    "tgw_mailbox_send", "tgw_get_plan_brief", "tgw_clip_deliver",
    "tgw_simple_llm_jobs", "tgw_get_plan_graph",
}


def test_exactly_ten_tools_present():
    present = {n for n in dir(mcp_server)
              if n.startswith("tgw_") and callable(getattr(mcp_server, n))}
    assert present == EXPECTED_TOOLS
    assert len(present) == 16


# ---------------------------------------------------------------------------
# tgw_get_item
# ---------------------------------------------------------------------------

def test_get_item_found(cfg):
    _write_item(cfg, "tgw001", {"sku": "tgw001", "title": "Widget"})
    out = json.loads(mcp_server.tgw_get_item("tgw001"))
    assert out["ok"] is True
    assert out["item"]["title"] == "Widget"


def test_get_item_missing(cfg):
    out = json.loads(mcp_server.tgw_get_item("tgw404"))
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_get_item_resolves_renamed_sku_via_alias_fallback(cfg):
    # Item now lives under tgw002 but was previously tgw001; the doc
    # records sku_old so resolver.find_current_sku() can map it forward.
    _write_item(cfg, "tgw002", {"sku": "tgw002", "sku_old": "tgw001",
                                "title": "Renamed Widget"})
    out = json.loads(mcp_server.tgw_get_item("tgw001"))
    assert out["ok"] is True
    assert out["item"]["title"] == "Renamed Widget"
    assert out["item"]["sku"] == "tgw002"


def test_get_item_accepts_capitalized_sku_argument(cfg):
    # FastMCP-boundary coverage (item 4/5): tgw_get_item's `sku` parameter
    # gets the shared alias_field() helper (todo #1528).
    _write_item(cfg, "tgw001", {"sku": "tgw001", "title": "Widget"})
    tool = mcp_server.mcp._tool_manager._tools["tgw_get_item"]
    out = json.loads(asyncio.run(tool.run({"Sku": "tgw001"})))
    assert out["ok"] is True
    assert out["item"]["title"] == "Widget"


# ---------------------------------------------------------------------------
# tgw_search_items
# ---------------------------------------------------------------------------

def test_search_items_passes_through_and_clamps_limit(cfg, monkeypatch):
    calls = {}

    def fake_list_items(cfg, **kwargs):
        calls.update(kwargs)
        return {"ok": True, "count": 0, "items": []}

    monkeypatch.setattr("tgw.api.list_items", fake_list_items)
    out = json.loads(mcp_server.tgw_search_items(search="hat", limit=500))
    assert out["ok"] is True
    assert calls["search"] == "hat"
    assert calls["limit"] == 100  # clamped to max 100


def test_search_items_limit_floor(cfg, monkeypatch):
    calls = {}
    monkeypatch.setattr("tgw.api.list_items",
                        lambda cfg, **kw: calls.update(kw) or {"ok": True})
    mcp_server.tgw_search_items(limit=0)
    assert calls["limit"] == 1  # clamped to min 1


def test_search_items_error_is_caught(cfg, monkeypatch):
    monkeypatch.setattr("tgw.api.list_items",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = json.loads(mcp_server.tgw_search_items(search="x"))
    assert out["ok"] is False and "boom" in out["error"]


def test_search_items_accepts_capitalized_arguments(cfg, monkeypatch):
    calls = {}
    monkeypatch.setattr("tgw.api.list_items",
                        lambda cfg, **kw: calls.update(kw) or {"ok": True, "items": []})
    tool = mcp_server.mcp._tool_manager._tools["tgw_search_items"]
    out = json.loads(asyncio.run(tool.run({
        "Search": "hat", "Location": "A1", "Status": "In Stock", "Limit": 5,
    })))
    assert out["ok"] is True
    assert calls["search"] == "hat"
    assert calls["location"] == "A1"
    assert calls["status"] == "In Stock"
    assert calls["limit"] == 5


# ---------------------------------------------------------------------------
# tgw_search_full (PP-KNOWLEDGE-001 R2, todo #1147)
# ---------------------------------------------------------------------------

def test_search_full_passes_through(cfg, monkeypatch):
    calls = {}

    def fake_run(query, limit=20):
        calls["query"] = query
        calls["limit"] = limit
        return {"ok": True, "query": query, "count": 1, "elapsed_ms": 5.0,
                "results": [{"url": "file:///x", "title": "x", "mtype": "text/plain",
                             "fbytes": "10", "abstract": ""}]}

    monkeypatch.setattr("tgw.search_full.run_full_text_search", fake_run)
    out = json.loads(mcp_server.tgw_search_full("tgw20260101000000000", limit=50))
    assert out["ok"] is True
    assert calls["query"] == "tgw20260101000000000"
    assert calls["limit"] == 50
    assert out["results"][0]["url"] == "file:///x"


def test_search_full_error_is_caught(cfg, monkeypatch):
    monkeypatch.setattr("tgw.search_full.run_full_text_search",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = json.loads(mcp_server.tgw_search_full("x"))
    assert out["ok"] is False and "boom" in out["error"]


def test_search_full_propagates_ok_false(cfg, monkeypatch):
    monkeypatch.setattr("tgw.search_full.run_full_text_search",
                        lambda *a, **k: {"ok": False, "error": "recollq not found on PATH"})
    out = json.loads(mcp_server.tgw_search_full("x"))
    assert out["ok"] is False
    assert "recollq" in out["error"]


def test_search_full_accepts_capitalized_arguments(cfg, monkeypatch):
    calls = {}

    def fake_run(query, limit=20):
        calls["query"] = query
        calls["limit"] = limit
        return {"ok": True, "query": query, "count": 0, "elapsed_ms": 1.0, "results": []}

    monkeypatch.setattr("tgw.search_full.run_full_text_search", fake_run)
    tool = mcp_server.mcp._tool_manager._tools["tgw_search_full"]
    out = json.loads(asyncio.run(tool.run({"Query": "widget", "Limit": 7})))
    assert out["ok"] is True
    assert calls["query"] == "widget"
    assert calls["limit"] == 7


# ---------------------------------------------------------------------------
# tgw_queue_status
# ---------------------------------------------------------------------------

def test_queue_status_aggregates_dead_letter(cfg, monkeypatch):
    rows = [
        ("ebay_draft", "queued", 5),
        ("ebay_draft", "dead_letter", 2),
        ("ebay_price", "dead_letter", 1),
    ]
    _install_conn(monkeypatch, rows)
    monkeypatch.setattr(sm, "dead_letter_errors", lambda: [
        {"queue_name": "ebay_draft", "error_detail": "token is expired"},
        {"queue_name": "ebay_draft", "error_detail": "HardFailure: rejected"},
        {"queue_name": "ebay_price", "error_detail": "HardFailure: rejected"},
    ])
    monkeypatch.setattr(sm, "zero_work_queues", lambda hours: [])
    out = json.loads(mcp_server.tgw_queue_status())
    assert out["ok"] is True
    assert out["dead_letter_total"] == 3
    assert out["dead_letter_by_queue"] == {"ebay_draft": 2, "ebay_price": 1}
    assert out["dead_letter_classified"] == {
        "ebay_draft": {"transient": 1, "hard": 1},
        "ebay_price": {"transient": 0, "hard": 1},
    }
    assert out["dead_letter_transient"] == 1
    assert out["dead_letter_hard"] == 2
    assert out["zero_work_stalls"] == []
    assert len(out["queues"]) == 3


# ---------------------------------------------------------------------------
# tgw_health
# ---------------------------------------------------------------------------

def test_health_passes_include_flags_false(cfg, monkeypatch):
    seen = {}

    def fake_check_all(cfg, **kwargs):
        seen.update(kwargs)
        return {"all_ok": True, "checks": []}

    monkeypatch.setattr("tgw.health.check_all", fake_check_all)
    out = json.loads(mcp_server.tgw_health())
    assert out["all_ok"] is True
    assert seen["include_ebay"] is False  # dead token kept off-path
    assert seen["include_ollama"] is False


# ---------------------------------------------------------------------------
# tgw_enqueue
# ---------------------------------------------------------------------------

def test_enqueue_invalid_action(cfg):
    out = json.loads(mcp_server.tgw_enqueue("tgw001", "not_a_stage"))
    assert out["ok"] is False
    assert "invalid action" in out["error"]


def test_enqueue_item_not_found(cfg):
    out = json.loads(mcp_server.tgw_enqueue("tgw404", "ai_identify"))
    assert out["ok"] is False
    assert "not found" in out["error"]


def test_enqueue_resolves_renamed_sku_via_alias_fallback(cfg, monkeypatch):
    _write_item(cfg, "tgw002", {"sku": "tgw002", "sku_old": "tgw001"})
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "enqueue_job", lambda **kw: "job-88")
    out = json.loads(mcp_server.tgw_enqueue("tgw001", "ebay_draft"))
    assert out["ok"] is True
    assert out["job_id"] == "job-88"


def test_enqueue_success(cfg, monkeypatch):
    _write_item(cfg, "tgw001", {"sku": "tgw001"})
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "enqueue_job", lambda **kw: "job-77")
    out = json.loads(mcp_server.tgw_enqueue("tgw001", "ebay_draft"))
    assert out["ok"] is True
    assert out["job_id"] == "job-77"
    assert out["queue"] == "ebay_draft"


def test_enqueue_duplicate_is_ok(cfg, monkeypatch):
    import psycopg2.errors
    _write_item(cfg, "tgw001", {"sku": "tgw001"})
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)

    def _dupe(**kw):
        raise psycopg2.errors.UniqueViolation("dup")

    monkeypatch.setattr(sm, "enqueue_job", _dupe)
    out = json.loads(mcp_server.tgw_enqueue("tgw001", "ebay_draft"))
    assert out["ok"] is True
    assert "already queued" in out["note"]


def test_enqueue_accepts_capitalized_arguments(cfg, monkeypatch):
    _write_item(cfg, "tgw001", {"sku": "tgw001"})
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "enqueue_job", lambda **kw: "job-99")
    tool = mcp_server.mcp._tool_manager._tools["tgw_enqueue"]
    out = json.loads(asyncio.run(tool.run({"Sku": "tgw001", "Action": "ebay_draft"})))
    assert out["ok"] is True
    assert out["job_id"] == "job-99"


# ---------------------------------------------------------------------------
# tgw_get_todo
# ---------------------------------------------------------------------------

def test_get_todo_all(cfg, monkeypatch):
    rows = [{"id": 1, "agent": "claude", "priority": 1, "body": "do x",
             "source": "plan", "added_at": "2026-06-07"}]
    _install_conn(monkeypatch, rows)
    out = json.loads(mcp_server.tgw_get_todo())
    assert out["ok"] is True
    assert out["agent"] == "all"
    assert out["items"][0]["body"] == "do x"


def test_get_todo_filtered_by_agent(cfg, monkeypatch):
    _install_conn(monkeypatch, [])
    out = json.loads(mcp_server.tgw_get_todo(agent="gemini"))
    assert out["ok"] is True
    assert out["agent"] == "gemini"


def test_get_todo_accepts_capitalized_agent_argument(cfg, monkeypatch):
    _install_conn(monkeypatch, [])
    tool = mcp_server.mcp._tool_manager._tools["tgw_get_todo"]
    out = json.loads(asyncio.run(tool.run({"Agent": "gemini"})))
    assert out["ok"] is True
    assert out["agent"] == "gemini"


# ---------------------------------------------------------------------------
# tgw_add_suggest
# ---------------------------------------------------------------------------

def test_add_suggest_delegates_to_cmd_suggest(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr("tgw.api.cmd_suggest",
                        lambda cfg, text: seen.update(text=text) or {"ok": True, "path": "/x"})
    out = json.loads(mcp_server.tgw_add_suggest("remember this"))
    assert out["ok"] is True
    assert seen["text"] == "remember this"


def test_add_suggest_accepts_capitalized_text_argument(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr("tgw.api.cmd_suggest",
                        lambda cfg, text: seen.update(text=text) or {"ok": True})
    tool = mcp_server.mcp._tool_manager._tools["tgw_add_suggest"]
    out = json.loads(asyncio.run(tool.run({"Text": "remember this"})))
    assert out["ok"] is True
    assert seen["text"] == "remember this"


# ---------------------------------------------------------------------------
# tgw_clip_deliver (todo #1563/PP-CLIP-001 clipboard-agent-delivery Phase 0)
# ---------------------------------------------------------------------------

def test_clip_deliver_delegates_to_deliver_clip(tmp_path, monkeypatch):
    seen = {}

    def fake_deliver(content, label=None, db_path=None):
        seen.update(content=content, label=label)
        return {"ok": True, "id": 7, "origin": "agent", "label": label}

    monkeypatch.setattr("tgw.clip.deliver_clip", fake_deliver)
    out = json.loads(mcp_server.tgw_clip_deliver("prepared text", "a label"))
    assert out["ok"] is True
    assert out["id"] == 7
    assert seen["content"] == "prepared text"
    assert seen["label"] == "a label"


def test_clip_deliver_no_label_passes_none(monkeypatch):
    seen = {}

    def fake_deliver(content, label=None, db_path=None):
        seen.update(label=label)
        return {"ok": True, "id": 1}

    monkeypatch.setattr("tgw.clip.deliver_clip", fake_deliver)
    mcp_server.tgw_clip_deliver("prepared text")
    assert seen["label"] is None


def test_clip_deliver_accepts_capitalized_arguments(monkeypatch):
    monkeypatch.setattr("tgw.clip.deliver_clip",
                        lambda content, label=None, db_path=None: {"ok": True, "id": 3})
    tool = mcp_server.mcp._tool_manager._tools["tgw_clip_deliver"]
    out = json.loads(asyncio.run(tool.run({"Content": "prepared text", "Label": "x"})))
    assert out["ok"] is True


def test_clip_deliver_surfaces_error_on_exception(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("db locked")

    monkeypatch.setattr("tgw.clip.deliver_clip", _raise)
    out = json.loads(mcp_server.tgw_clip_deliver("prepared text"))
    assert out["ok"] is False
    assert "db locked" in out["error"]


def test_clip_deliver_registered_when_not_readonly():
    """Mirrors the same-shape READONLY-registration behavior as tgw_enqueue/
    tgw_add_suggest (todo #1563) — reload the module with TGW_MCP_READONLY
    unset/0 and confirm tgw_clip_deliver IS registered."""
    import importlib
    import os as _os

    old = _os.environ.pop("TGW_MCP_READONLY", None)
    try:
        reloaded = importlib.reload(mcp_server)
        assert "tgw_clip_deliver" in reloaded.mcp._tool_manager._tools
        assert hasattr(reloaded, "tgw_clip_deliver")
    finally:
        if old is not None:
            _os.environ["TGW_MCP_READONLY"] = old
        importlib.reload(mcp_server)  # restore module state for later tests


def test_clip_deliver_not_registered_when_readonly():
    """Same class of write as tgw_enqueue/tgw_add_suggest — must inherit
    their READONLY exclusion so Tigwa's current training-mode restriction
    (TGW_MCP_READONLY=1) covers it automatically, not by omission."""
    import importlib
    import os as _os

    old = _os.environ.get("TGW_MCP_READONLY")
    _os.environ["TGW_MCP_READONLY"] = "1"
    try:
        reloaded = importlib.reload(mcp_server)
        assert "tgw_clip_deliver" not in reloaded.mcp._tool_manager._tools
        # the function itself still exists (module-level def), it's just not
        # registered as an MCP tool — same pattern as tgw_enqueue/tgw_add_suggest.
        assert "tgw_enqueue" not in reloaded.mcp._tool_manager._tools
        assert "tgw_add_suggest" not in reloaded.mcp._tool_manager._tools
    finally:
        if old is None:
            _os.environ.pop("TGW_MCP_READONLY", None)
        else:
            _os.environ["TGW_MCP_READONLY"] = old
        importlib.reload(mcp_server)  # restore module state for later tests


# ---------------------------------------------------------------------------
# tgw_mailbox_send (PP-RUNNERCOMMS-001)
# ---------------------------------------------------------------------------

def test_mailbox_send_delegates_to_cmd_mailbox_send(cfg, monkeypatch):
    seen = {}

    def fake_send(cfg, to_actor, text, from_actor="claude", msg_type="NOTE",
                  subject=None, todo_id=None):
        seen.update(to_actor=to_actor, text=text, from_actor=from_actor,
                    msg_type=msg_type, subject=subject, todo_id=todo_id)
        return {"ok": True, "file": "/x"}

    monkeypatch.setattr("tgw.api.cmd_mailbox_send", fake_send)
    out = json.loads(mcp_server.tgw_mailbox_send(
        "claude", "please review", from_actor="tigwa", msg_type="REVIEW",
        subject="review please", todo_id=1484,
    ))
    assert out["ok"] is True
    assert seen == {
        "to_actor": "claude", "text": "please review", "from_actor": "tigwa",
        "msg_type": "REVIEW", "subject": "review please", "todo_id": 1484,
    }


def test_mailbox_send_defaults_omit_subject_and_todo(cfg, monkeypatch):
    seen = {}

    def fake_send(cfg, to_actor, text, from_actor="claude", msg_type="NOTE",
                  subject=None, todo_id=None):
        seen.update(subject=subject, todo_id=todo_id)
        return {"ok": True, "file": "/x"}

    monkeypatch.setattr("tgw.api.cmd_mailbox_send", fake_send)
    json.loads(mcp_server.tgw_mailbox_send("dave", "hi"))
    assert seen["subject"] is None
    assert seen["todo_id"] is None


def test_mailbox_send_accepts_capitalized_arguments(cfg, monkeypatch):
    seen = {}

    def fake_send(cfg, to_actor, text, from_actor="claude", msg_type="NOTE",
                  subject=None, todo_id=None):
        seen.update(to_actor=to_actor, text=text)
        return {"ok": True}

    monkeypatch.setattr("tgw.api.cmd_mailbox_send", fake_send)
    tool = mcp_server.mcp._tool_manager._tools["tgw_mailbox_send"]
    out = json.loads(asyncio.run(tool.run({"To": "claude", "Text": "hello"})))
    assert out["ok"] is True
    assert seen == {"to_actor": "claude", "text": "hello"}


def test_mailbox_send_accepts_all_extra_aliases_after_alias_field_refactor(cfg, monkeypatch):
    # Regression: tgw_mailbox_send's params were refactored onto the shared
    # alias_field() helper (todo #1528) — this confirms its existing
    # From/Type/Todo shorthand aliases (Tigwa's own precedent, not just the
    # generic title-cased form) survived the refactor unchanged.
    seen = {}

    def fake_send(cfg, to_actor, text, from_actor="claude", msg_type="NOTE",
                  subject=None, todo_id=None):
        seen.update(to_actor=to_actor, text=text, from_actor=from_actor,
                    msg_type=msg_type, subject=subject, todo_id=todo_id)
        return {"ok": True, "file": "/x"}

    monkeypatch.setattr("tgw.api.cmd_mailbox_send", fake_send)
    tool = mcp_server.mcp._tool_manager._tools["tgw_mailbox_send"]
    out = json.loads(asyncio.run(tool.run({
        "To": "claude", "Text": "hello", "From": "tigwa", "Type": "REVIEW",
        "Subject": "hi there", "Todo": 1528,
    })))
    assert out["ok"] is True
    assert seen == {
        "to_actor": "claude", "text": "hello", "from_actor": "tigwa",
        "msg_type": "REVIEW", "subject": "hi there", "todo_id": 1528,
    }


def test_mailbox_send_error_is_caught(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no such actor")

    monkeypatch.setattr("tgw.api.cmd_mailbox_send", boom)
    out = json.loads(mcp_server.tgw_mailbox_send("nowhere", "hi"))
    assert out["ok"] is False
    assert "no such actor" in out["error"]


# ---------------------------------------------------------------------------
# tgw_dead_letter
# ---------------------------------------------------------------------------

def test_dead_letter_lists_with_verdict(cfg, monkeypatch):
    jobs = [{
        "job_id": "j1", "queue_name": "ebay_draft",
        "payload_json": {"sku": "tgw001"}, "error_detail": "timeout reading",
        "attempt_count": 3, "finished_at": None,
    }]
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "dead_letter_jobs", lambda **kw: jobs)
    monkeypatch.setattr("tgw.queue.worker_base.classify_dead_letter",
                        lambda err: ("transient", 60))
    out = json.loads(mcp_server.tgw_dead_letter())
    assert out["ok"] is True
    assert out["count"] == 1
    j = out["jobs"][0]
    assert j["sku"] == "tgw001"
    assert j["verdict"] == "transient"
    assert j["requeue_delay"] == 60


def test_dead_letter_accepts_capitalized_arguments(cfg, monkeypatch):
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    seen = {}

    def fake_jobs(**kw):
        seen.update(kw)
        return []

    monkeypatch.setattr(sm, "dead_letter_jobs", fake_jobs)
    tool = mcp_server.mcp._tool_manager._tools["tgw_dead_letter"]
    out = json.loads(asyncio.run(tool.run({"Queue": "ebay_draft", "Limit": 3})))
    assert out["ok"] is True
    assert seen["queue_name"] == "ebay_draft"
    assert seen["limit"] == 3


# ---------------------------------------------------------------------------
# tgw_hint_trail
# ---------------------------------------------------------------------------

def test_hint_trail_delegates(cfg, monkeypatch):
    monkeypatch.setattr("tgw.api.cmd_hint_trail",
                        lambda cfg, sku: {"ok": True, "sku": sku, "events": []})
    out = json.loads(mcp_server.tgw_hint_trail("tgw001"))
    assert out["ok"] is True and out["sku"] == "tgw001"


def test_hint_trail_accepts_capitalized_sku_argument(cfg, monkeypatch):
    monkeypatch.setattr("tgw.api.cmd_hint_trail",
                        lambda cfg, sku: {"ok": True, "sku": sku, "events": []})
    tool = mcp_server.mcp._tool_manager._tools["tgw_hint_trail"]
    out = json.loads(asyncio.run(tool.run({"Sku": "tgw001"})))
    assert out["ok"] is True and out["sku"] == "tgw001"


# ---------------------------------------------------------------------------
# tgw_catalog_verify
# ---------------------------------------------------------------------------

def test_catalog_verify_passes_args_through(cfg, monkeypatch):
    seen = {}

    def fake_verify(cfg, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "scanned": 10, "violations": 0}

    monkeypatch.setattr("tgw.api.cmd_catalog_verify", fake_verify)
    out = json.loads(mcp_server.tgw_catalog_verify(location="A1", limit=50,
                                                   severity="critical"))
    assert out["ok"] is True
    assert seen["location"] == "A1"
    assert seen["limit"] == 50
    assert seen["min_severity"] == "critical"
    assert seen["output"] is None


def test_catalog_verify_error_caught(cfg, monkeypatch):
    monkeypatch.setattr("tgw.api.cmd_catalog_verify",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bad")))
    out = json.loads(mcp_server.tgw_catalog_verify())
    assert out["ok"] is False and "bad" in out["error"]


def test_catalog_verify_accepts_capitalized_arguments(cfg, monkeypatch):
    seen = {}

    def fake_verify(cfg, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "scanned": 1, "violations": 0}

    monkeypatch.setattr("tgw.api.cmd_catalog_verify", fake_verify)
    tool = mcp_server.mcp._tool_manager._tools["tgw_catalog_verify"]
    out = json.loads(asyncio.run(tool.run({
        "Location": "A1", "Limit": 5, "Severity": "critical",
        "Mark_verified": True, "Force": True, "Skip_verified": True,
    })))
    assert out["ok"] is True
    assert seen["location"] == "A1"
    assert seen["limit"] == 5
    assert seen["min_severity"] == "critical"
    assert seen["mark_verified"] is True
    assert seen["force"] is True
    assert seen["skip_verified"] is True


# ---------------------------------------------------------------------------
# tgw_get_plan_brief — deterministic, source-linked Plan Vault retrieval
# ---------------------------------------------------------------------------

def _plan_cfg(tmp_path):
    vault = tmp_path / "plan-vault"
    return {
        "plan_vault_path": vault,
        "plan_master_path": vault / "plan" / "TGW-Master-Plan.md",
    }


def test_get_plan_brief_returns_exact_pp_section_with_provenance(tmp_path, monkeypatch):
    c = _plan_cfg(tmp_path)
    plan_path = c["plan_master_path"]
    plan_path.parent.mkdir(parents=True)
    plan = "# TGW Master Plan\n\n## PP-ALPHA-001 Alpha work\nalpha source\n\n## PP-BETA-002 Beta work\nbeta source\n"
    plan_path.write_text(plan, encoding="utf-8")
    detail = c["plan_vault_path"] / "plan" / "pp" / "PP-ALPHA-001.md"
    detail.parent.mkdir(parents=True)
    detail_bytes = "# PP-ALPHA-001\nDetailed canonical source.\n".encode("utf-8")
    detail.write_bytes(detail_bytes)
    monkeypatch.setattr(mcp_server, "_cfg", c)

    out = json.loads(mcp_server.tgw_get_plan_brief("pp-alpha-001"))

    assert out["ok"] is True
    assert out["query"]["pp"] == "PP-ALPHA-001"
    assert out["canonical_source"]["sha256"] == hashlib.sha256(plan.encode()).hexdigest()
    assert out["section"]["heading"] == "PP-ALPHA-001 Alpha work"
    assert out["section"]["content"] == "## PP-ALPHA-001 Alpha work\nalpha source\n\n"
    # Linked PP detail documents are metadata-only — path/status/hash/bytes,
    # never inlined content, even when small enough to fit the packet cap
    # (PP-KNOWLEDGE-001/#1439 follow-up, item 4).
    assert out["linked_pp_detail"]["status"] == "present"
    assert out["linked_pp_detail"]["sha256"] == hashlib.sha256(detail_bytes).hexdigest()
    assert out["linked_pp_detail"]["bytes"] == len(detail_bytes)
    assert "content" not in out["linked_pp_detail"]


def test_get_plan_brief_does_not_treat_a_cross_reference_heading_as_a_second_match(tmp_path, monkeypatch):
    c = _plan_cfg(tmp_path)
    plan_path = c["plan_master_path"]
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text(
        "## PP-ALPHA-001 Canonical work\nalpha source\n\n"
        "## PP-OLD-001 Folded into PP-ALPHA-001\nold source\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_server, "_cfg", c)

    out = json.loads(mcp_server.tgw_get_plan_brief("PP-ALPHA-001"))

    assert out["ok"] is True
    assert out["section"]["heading"] == "PP-ALPHA-001 Canonical work"


def test_get_plan_brief_via_tool_run_boundary(tmp_path, monkeypatch):
    # FastMCP-boundary coverage (item 6): invoke through the actual MCP tool
    # dispatch path, not just a direct Python function call.
    c = _plan_cfg(tmp_path)
    plan_path = c["plan_master_path"]
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("## PP-GAMMA-003 Gamma work\ngamma source\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_cfg", c)

    tool = mcp_server.mcp._tool_manager._tools["tgw_get_plan_brief"]
    out = json.loads(asyncio.run(tool.run({"pp": "pp-gamma-003"})))

    assert out["ok"] is True
    assert out["query"]["pp"] == "PP-GAMMA-003"
    assert out["section"]["heading"] == "PP-GAMMA-003 Gamma work"


def test_get_plan_brief_accepts_all_caps_pp_alias(tmp_path, monkeypatch):
    # `pp` is a two-letter abbreviation; a client is at least as likely to
    # present it as the all-caps abbreviation "PP" (matching how PP-* refs
    # are written everywhere in this codebase) as the mechanical
    # str.capitalize() form "Pp" — alias_field('pp', 'PP') covers both.
    c = _plan_cfg(tmp_path)
    plan_path = c["plan_master_path"]
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("## PP-DELTA-004 Delta work\ndelta source\n", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "_cfg", c)

    tool = mcp_server.mcp._tool_manager._tools["tgw_get_plan_brief"]
    out = json.loads(asyncio.run(tool.run({"PP": "pp-delta-004"})))

    assert out["ok"] is True
    assert out["query"]["pp"] == "PP-DELTA-004"
    assert out["section"]["heading"] == "PP-DELTA-004 Delta work"


# ---------------------------------------------------------------------------
# tgw_simple_llm_jobs (PP-SIMPLEJOBS-001, todo #1574)
# ---------------------------------------------------------------------------

def test_simple_llm_jobs_invalid_operation(cfg):
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="nope", text="hi"))
    assert out["ok"] is False
    assert "invalid operation" in out["error"]


def test_simple_llm_jobs_summarize_calls_call_model_and_parses_json(cfg, monkeypatch):
    calls = {}

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls.update(task=task, system_prompt=system_prompt, user_prompt=user_prompt)
        return '{"summary": "short summary", "key_points": ["a", "b"]}'

    monkeypatch.setattr("tgw.apis.llm.call_model", fake_call_model)
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="summarize", text="long text here", instructions="be brief",
    ))
    assert out["ok"] is True
    assert out["operation"] == "summarize"
    assert out["result"] == {"summary": "short summary", "key_points": ["a", "b"]}
    assert calls["task"] == "simple_llm_jobs"
    assert "long text here" in calls["user_prompt"]
    assert "be brief" in calls["user_prompt"]


def test_simple_llm_jobs_extract_fields_embeds_schema_in_system_prompt(cfg, monkeypatch):
    calls = {}

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls["system_prompt"] = system_prompt
        return '{"brand": "Kenmore", "condition": "used"}'

    monkeypatch.setattr("tgw.apis.llm.call_model", fake_call_model)
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="extract_fields", text="a description",
        schema={"brand": "string", "condition": "string"},
    ))
    assert out["ok"] is True
    assert out["result"]["brand"] == "Kenmore"
    assert '"brand": "string"' in calls["system_prompt"]


def test_simple_llm_jobs_classify_embeds_label_set(cfg, monkeypatch):
    calls = {}

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls["system_prompt"] = system_prompt
        return '{"label": "USED_GOOD", "confidence": 0.8, "reason": "worn"}'

    monkeypatch.setattr("tgw.apis.llm.call_model", fake_call_model)
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description",
        label_set=["NEW", "USED_GOOD", "USED_ACCEPTABLE"],
    ))
    assert out["ok"] is True
    assert out["result"]["label"] == "USED_GOOD"
    assert "USED_GOOD" in calls["system_prompt"]
    assert "USED_ACCEPTABLE" in calls["system_prompt"]


def test_simple_llm_jobs_rank_snippets_embeds_indexed_items(cfg, monkeypatch):
    calls = {}

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls["user_prompt"] = user_prompt
        return '{"ranked": [{"index": 1, "score": 0.9}, {"index": 0, "score": 0.4}]}'

    monkeypatch.setattr("tgw.apis.llm.call_model", fake_call_model)
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="rank_snippets", text="query text",
        items=["candidate zero", "candidate one"],
    ))
    assert out["ok"] is True
    assert out["result"]["ranked"][0]["index"] == 1
    assert "0: candidate zero" in calls["user_prompt"]
    assert "1: candidate one" in calls["user_prompt"]


def test_simple_llm_jobs_strips_markdown_fence(cfg, monkeypatch):
    monkeypatch.setattr("tgw.apis.llm.call_model",
                        lambda *a, **k: '```json\n{"summary": "x"}\n```')
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="summarize", text="hi"))
    assert out["ok"] is True
    assert out["result"] == {"summary": "x"}


def test_simple_llm_jobs_non_json_response_surfaces_raw(cfg, monkeypatch):
    monkeypatch.setattr("tgw.apis.llm.call_model", lambda *a, **k: "not json at all")
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="summarize", text="hi"))
    assert out["ok"] is False
    assert out["raw"] == "not json at all"
    assert "not valid JSON" in out["error"]


def test_simple_llm_jobs_call_model_exception_is_caught(cfg, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("deepseek unavailable")

    monkeypatch.setattr("tgw.apis.llm.call_model", boom)
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="summarize", text="hi"))
    assert out["ok"] is False
    assert "deepseek unavailable" in out["error"]


def test_simple_llm_jobs_registered_even_when_readonly():
    """Read-only-safe by nature (no ItemData/eBay/queue writes) — must stay
    registered under TGW_MCP_READONLY=1, same as tgw_search_full (packet
    step 6: match the closest analogous read-only tool, not the write-gated
    tgw_enqueue/tgw_add_suggest/tgw_clip_deliver/tgw_mailbox_send group)."""
    import importlib
    import os as _os

    old = _os.environ.get("TGW_MCP_READONLY")
    _os.environ["TGW_MCP_READONLY"] = "1"
    try:
        reloaded = importlib.reload(mcp_server)
        assert "tgw_simple_llm_jobs" in reloaded.mcp._tool_manager._tools
    finally:
        if old is None:
            _os.environ.pop("TGW_MCP_READONLY", None)
        else:
            _os.environ["TGW_MCP_READONLY"] = old
        importlib.reload(mcp_server)  # restore module state for later tests


def test_simple_llm_jobs_classify_valid_label_still_ok(cfg, monkeypatch):
    """Regression check: normal case (model picks a label in label_set)
    still returns ok: True (todo #1576)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"label": "USED_GOOD", "confidence": 0.8, "reason": "worn"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description",
        label_set=["NEW", "USED_GOOD", "USED_ACCEPTABLE"],
    ))
    assert out["ok"] is True
    assert out["result"]["label"] == "USED_GOOD"


def test_simple_llm_jobs_classify_label_outside_label_set_fails(cfg, monkeypatch):
    """todo #1576: a JSON-shaped response with a label outside label_set is
    a contract violation, not ok: True."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"label": "REFURBISHED", "confidence": 0.6, "reason": "unclear"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description",
        label_set=["NEW", "USED_GOOD", "USED_ACCEPTABLE"],
    ))
    assert out["ok"] is False
    assert "REFURBISHED" in out["error"]
    assert "not in label_set" in out["error"]
    assert out["raw"]["label"] == "REFURBISHED"


def test_simple_llm_jobs_classify_without_label_set_skips_check(cfg, monkeypatch):
    """No label_set supplied → open-ended classification, nothing to
    validate against — any label is accepted (todo #1576)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"label": "ANYTHING", "confidence": 0.5}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="classify", text="a description"))
    assert out["ok"] is True
    assert out["result"]["label"] == "ANYTHING"


def test_simple_llm_jobs_classify_empty_label_set_rejected_before_model_call(cfg, monkeypatch):
    """todo #1577 (Tigwa peer review of #1576): an explicit label_set=[] must
    be rejected fail-loud, before the model is even called — an empty
    allowed-label domain can never yield a valid classification, so the
    prior 'if label_set:' truthiness check silently skipped validation in
    exactly this case instead of failing loud."""
    calls = []
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: calls.append(1) or '{"label": "ANYTHING", "confidence": 0.5}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description", label_set=[],
    ))
    assert out["ok"] is False
    assert "empty" in out["error"]
    assert calls == []  # model must never be called for an impossible request


def test_simple_llm_jobs_classify_none_label_set_is_open_ended(cfg, monkeypatch):
    """todo #1577: label_set=None (not supplied) is the open-ended case —
    distinct from label_set=[] — and must NOT be rejected."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"label": "ANYTHING", "confidence": 0.5}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description", label_set=None,
    ))
    assert out["ok"] is True
    assert out["result"]["label"] == "ANYTHING"


def test_simple_llm_jobs_classify_nonempty_label_set_still_validates(cfg, monkeypatch):
    """todo #1577: non-empty label_set keeps the membership-check behavior
    from #1576 unchanged (this must still fail for an out-of-set label)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"label": "REFURBISHED", "confidence": 0.6}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="classify", text="a description",
        label_set=["NEW", "USED_GOOD"],
    ))
    assert out["ok"] is False
    assert "not in label_set" in out["error"]


def test_simple_llm_jobs_extract_fields_all_keys_present_still_ok(cfg, monkeypatch):
    """Regression check: normal case (model returns all requested schema
    keys) still returns ok: True (todo #1576)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"brand": "Kenmore", "condition": "used"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="extract_fields", text="a description",
        schema={"brand": "string", "condition": "string"},
    ))
    assert out["ok"] is True
    assert out["result"]["brand"] == "Kenmore"


def test_simple_llm_jobs_extract_fields_missing_key_fails(cfg, monkeypatch):
    """todo #1576: a requested schema key absent from the model's response
    is a contract violation, not ok: True."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"brand": "Kenmore"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="extract_fields", text="a description",
        schema={"brand": "string", "condition": "string"},
    ))
    assert out["ok"] is False
    assert "condition" in out["error"]
    assert "missing requested field" in out["error"]
    assert out["raw"]["brand"] == "Kenmore"


def test_simple_llm_jobs_extract_fields_extra_keys_beyond_schema_still_ok(cfg, monkeypatch):
    """Extra keys beyond schema are fine — only missing keys are a
    violation (todo #1576)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"brand": "Kenmore", "condition": "used", "color": "white"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(
        operation="extract_fields", text="a description",
        schema={"brand": "string", "condition": "string"},
    ))
    assert out["ok"] is True
    assert out["result"]["color"] == "white"


def test_simple_llm_jobs_extract_fields_without_schema_skips_check(cfg, monkeypatch):
    """No schema supplied → nothing to validate against (todo #1576)."""
    monkeypatch.setattr(
        "tgw.apis.llm.call_model",
        lambda *a, **k: '{"anything": "goes"}',
    )
    out = json.loads(mcp_server.tgw_simple_llm_jobs(operation="extract_fields", text="a description"))
    assert out["ok"] is True
    assert out["result"]["anything"] == "goes"


def test_simple_llm_jobs_accepts_capitalized_arguments(cfg, monkeypatch):
    calls = {}

    def fake_call_model(task, system_prompt, user_prompt, cfg_arg):
        calls.update(user_prompt=user_prompt)
        return '{"summary": "x"}'

    monkeypatch.setattr("tgw.apis.llm.call_model", fake_call_model)
    tool = mcp_server.mcp._tool_manager._tools["tgw_simple_llm_jobs"]
    out = json.loads(asyncio.run(tool.run({"Operation": "summarize", "Text": "hello"})))
    assert out["ok"] is True
    assert "hello" in calls["user_prompt"]
