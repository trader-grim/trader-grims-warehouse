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

import pytest

import tgw.mcp_server as mcp_server
from tgw import resolver
from tgw.queue import state_machine as sm

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
    "tgw_mailbox_send", "tgw_get_plan_brief",
}


def test_exactly_ten_tools_present():
    present = {n for n in dir(mcp_server)
              if n.startswith("tgw_") and callable(getattr(mcp_server, n))}
    assert present == EXPECTED_TOOLS
    assert len(present) == 13


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


# ---------------------------------------------------------------------------
# tgw_hint_trail
# ---------------------------------------------------------------------------

def test_hint_trail_delegates(cfg, monkeypatch):
    monkeypatch.setattr("tgw.api.cmd_hint_trail",
                        lambda cfg, sku: {"ok": True, "sku": sku, "events": []})
    out = json.loads(mcp_server.tgw_hint_trail("tgw001"))
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
