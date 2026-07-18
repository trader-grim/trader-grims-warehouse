"""PP-KNOWLEDGE-001 Track R2, todo #1147 — tgw.search_full (recollq wrapper).

subprocess.run is monkeypatched throughout; no real recollq/recoll index is
touched by these tests (that's covered by the packet's live-acceptance
evidence instead, per Prime Directive 4).
"""

from __future__ import annotations

import base64
import subprocess

from tgw import search_full


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


class _FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _recollq_stdout(rows):
    """Build fake recollq -F stdout: two header lines + one line per row,
    fields joined with spaces (matching real -F output shape verified live)."""
    lines = ["Recoll query: Query(fake)", f"{len(rows)} results (printing {len(rows)} max):"]
    for row in rows:
        fields = [_b64(row.get(f, "")) for f in search_full._FIELDS]
        lines.append(" ".join(fields))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# _parse_recollq_output
# ---------------------------------------------------------------------------

def test_parse_skips_header_lines():
    stdout = _recollq_stdout([
        {"url": "file:///a.json", "title": "A", "mtype": "text/plain", "fbytes": "10", "abstract": "hi"},
    ])
    rows = search_full._parse_recollq_output(stdout)
    assert len(rows) == 1
    assert rows[0]["url"] == "file:///a.json"
    assert rows[0]["title"] == "A"
    assert rows[0]["mtype"] == "text/plain"
    assert rows[0]["fbytes"] == "10"
    assert rows[0]["abstract"] == "hi"


def test_parse_handles_empty_fields():
    stdout = _recollq_stdout([
        {"url": "file:///b.json", "title": "", "mtype": "inode/directory", "fbytes": "94", "abstract": ""},
    ])
    rows = search_full._parse_recollq_output(stdout)
    assert len(rows) == 1
    assert rows[0]["title"] == ""
    assert rows[0]["mtype"] == "inode/directory"


def test_parse_zero_results():
    stdout = "Recoll query: Query(nomatch)\n0 results\n"
    rows = search_full._parse_recollq_output(stdout)
    assert rows == []


def test_parse_multiple_rows_preserves_order():
    stdout = _recollq_stdout([
        {"url": "file:///1", "title": "one", "mtype": "text/plain", "fbytes": "1", "abstract": ""},
        {"url": "file:///2", "title": "two", "mtype": "text/plain", "fbytes": "2", "abstract": ""},
        {"url": "file:///3", "title": "three", "mtype": "text/plain", "fbytes": "3", "abstract": ""},
    ])
    rows = search_full._parse_recollq_output(stdout)
    assert [r["title"] for r in rows] == ["one", "two", "three"]


# ---------------------------------------------------------------------------
# run_full_text_search
# ---------------------------------------------------------------------------

def test_empty_query_returns_ok_false():
    result = search_full.run_full_text_search("")
    assert result["ok"] is False
    assert "empty query" in result["error"]


def test_run_full_text_search_success(monkeypatch):
    stdout = _recollq_stdout([
        {"url": "file:///opt/TGW/data/ItemData/tgw123/tgw123.json", "title": "tgw123.json",
         "mtype": "text/plain", "fbytes": "512", "abstract": "sku tgw123"},
    ])

    captured_cmd = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured_cmd["cmd"] = cmd
        return _FakeCompletedProcess(stdout=stdout, returncode=0)

    monkeypatch.setattr(search_full.subprocess, "run", fake_run)
    result = search_full.run_full_text_search("tgw123", limit=10)

    assert result["ok"] is True
    assert result["query"] == "tgw123"
    assert result["count"] == 1
    assert result["results"][0]["url"].endswith("tgw123.json")
    assert "elapsed_ms" in result
    # command shape: recollq -a -c <confdir> -n <limit> -F <fields> <query>
    cmd = captured_cmd["cmd"]
    assert cmd[0] == search_full.RECOLLQ_BIN
    assert "-c" in cmd and search_full.RECOLL_CONFDIR in cmd
    assert cmd[-1] == "tgw123"


def test_limit_is_clamped(monkeypatch):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        return _FakeCompletedProcess(stdout=_recollq_stdout([]), returncode=0)

    monkeypatch.setattr(search_full.subprocess, "run", fake_run)
    search_full.run_full_text_search("x", limit=99999)
    n_idx = captured["cmd"].index("-n")
    assert int(captured["cmd"][n_idx + 1]) == search_full.MAX_LIMIT

    search_full.run_full_text_search("x", limit=0)
    n_idx = captured["cmd"].index("-n")
    assert int(captured["cmd"][n_idx + 1]) == 1


def test_missing_binary_returns_ok_false(monkeypatch):
    def fake_run(*a, **k):
        raise FileNotFoundError()

    monkeypatch.setattr(search_full.subprocess, "run", fake_run)
    result = search_full.run_full_text_search("x")
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_timeout_returns_ok_false(monkeypatch):
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="recollq", timeout=20)

    monkeypatch.setattr(search_full.subprocess, "run", fake_run)
    result = search_full.run_full_text_search("x")
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_nonzero_exit_returns_ok_false(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        return _FakeCompletedProcess(stdout="", stderr="bad query syntax", returncode=1)

    monkeypatch.setattr(search_full.subprocess, "run", fake_run)
    result = search_full.run_full_text_search("x")
    assert result["ok"] is False
    assert "bad query syntax" in result["error"]


# ---------------------------------------------------------------------------
# format_results_text
# ---------------------------------------------------------------------------

def test_format_results_text_error():
    text = search_full.format_results_text({"ok": False, "error": "boom"})
    assert "error: boom" == text


def test_format_results_text_success():
    result = {
        "ok": True, "query": "hats", "count": 1, "elapsed_ms": 12.3,
        "results": [{"url": "file:///a", "title": "Hat", "mtype": "text/plain", "fbytes": "5", "abstract": ""}],
    }
    text = search_full.format_results_text(result)
    assert "1 result(s) for 'hats'" in text
    assert "Hat" in text
    assert "file:///a" in text
