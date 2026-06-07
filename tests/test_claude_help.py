"""PP-CLAUDE-HELP-001 — tests for `tgw claude-help` command construction.

Tests the non-launch path (build + print the claude invocation). The --launch
path execs and replaces the process, so it isn't exercised here.
"""

import tgw.api as api


def test_builds_command_with_troubleshoot_doc():
    out = api.cmd_claude_help({})
    assert out["ok"] is True
    assert out["doc"].endswith("CLAUDE-TROUBLESHOOT.md")
    cmd = out["command"]
    assert "--append-system-prompt-file" in cmd
    assert out["doc"] in cmd
    assert "--add-dir" in cmd


def test_troubleshoot_doc_actually_exists():
    out = api.cmd_claude_help({})
    from pathlib import Path
    assert Path(out["doc"]).exists()


def test_issue_appended_as_initial_prompt():
    out = api.cmd_claude_help({}, issue="ebay_draft worker is stuck")
    assert out["command"][-1] == "ebay_draft worker is stuck"


def test_worker_focus_threaded():
    out = api.cmd_claude_help({}, worker="ai_identify")
    assert "Focus on the ai_identify worker." in out["command"][-1]


def test_worker_and_issue_combined():
    out = api.cmd_claude_help({}, worker="ebay_price", issue="prices look wrong")
    initial = out["command"][-1]
    assert "ebay_price" in initial
    assert "prices look wrong" in initial


def test_no_context_means_no_trailing_prompt():
    out = api.cmd_claude_help({})
    # Last arg is the repo dir from --add-dir, not a prompt string.
    assert out["command"][-2] == "--add-dir"
