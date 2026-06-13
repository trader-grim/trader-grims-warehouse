"""Tests for tgw.suggestions — PP-DOCFLOW-001 Phase 2."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from tgw.suggestions import (
    apply_classifications,
    classify_batch,
    format_report,
    parse_pending,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SUGGESTIONS_CONTENT = textwrap.dedent("""\
    # Suggestions processed into plan on 2026-06-03

    - [x] 2026-06-03T02:07 :: test suggestion — archived
    - [x] 2026-06-03T04:25 :: old processed suggestion

    # Unprocessed

    - [ ] 2026-06-10T10:00 :: add retry logic to browse API calls
    - [ ] 2026-06-10T11:00 :: buy a GPU for faster inference
    - [ ] 2026-06-10T12:00 :: add weight_oz to picklist output
""")


@pytest.fixture
def suggestions_file(tmp_path):
    p = tmp_path / "SUGGESTIONS.md"
    p.write_text(SUGGESTIONS_CONTENT, encoding="utf-8")
    return p


@pytest.fixture
def cfg(tmp_path):
    plan_path = tmp_path / "plan" / "TGW-Master-Plan.md"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_text("# TGW Master Plan\n## Work Tracks\n## Implementation TODO\n", encoding="utf-8")
    return {
        "plan_vault_path": tmp_path,
        "plan_master_path": plan_path,
        "models": {},
    }


# ---------------------------------------------------------------------------
# parse_pending
# ---------------------------------------------------------------------------

def test_parse_pending_finds_only_unchecked(suggestions_file):
    entries = parse_pending(suggestions_file)
    assert len(entries) == 3
    assert all(e["raw"].startswith("- [ ]") for e in entries)


def test_parse_pending_fields(suggestions_file):
    entries = parse_pending(suggestions_file)
    assert entries[0]["timestamp"] == "2026-06-10T10:00"
    assert entries[0]["text"] == "add retry logic to browse API calls"
    assert entries[0]["index"] == 0
    assert isinstance(entries[0]["line_no"], int)


def test_parse_pending_empty_file(tmp_path):
    p = tmp_path / "SUGGESTIONS.md"
    p.write_text("# no pending\n- [x] 2026-06-01T00:00 :: done\n", encoding="utf-8")
    assert parse_pending(p) == []


def test_parse_pending_missing_file(tmp_path):
    assert parse_pending(tmp_path / "nonexistent.md") == []


# ---------------------------------------------------------------------------
# classify_batch
# ---------------------------------------------------------------------------

def test_classify_batch_returns_empty_on_no_entries(cfg):
    result = classify_batch([], "## headings", cfg)
    assert result == []


def test_classify_batch_calls_model_and_parses_json(cfg):
    entries = [{"index": 0, "timestamp": "2026-06-10T10:00", "text": "add retry"}]
    mock_response = '[{"index": 0, "action": "todo", "rationale": "not done", "todo_agent": "claude", "todo_body": "add retry to browse API"}]'
    with patch("tgw.suggestions.call_model", return_value=mock_response):
        result = classify_batch(entries, "## Work Tracks", cfg)
    assert len(result) == 1
    assert result[0]["action"] == "todo"
    assert result[0]["todo_body"] == "add retry to browse API"


def test_classify_batch_handles_wrapped_json(cfg):
    entries = [{"index": 0, "timestamp": "T", "text": "x"}]
    mock_response = '{"results": [{"index": 0, "action": "already_done", "rationale": "done"}]}'
    with patch("tgw.suggestions.call_model", return_value=mock_response):
        result = classify_batch(entries, "", cfg)
    assert result[0]["action"] == "already_done"


# ---------------------------------------------------------------------------
# apply_classifications
# ---------------------------------------------------------------------------

CLASSIFIED_MIXED = [
    {"index": 0, "action": "already_done", "rationale": "Already in worker_base.py"},
    {"index": 1, "action": "review_flag", "rationale": "Capital decision", "review_agent": "admin", "review_body": "GPU purchase"},
    {"index": 2, "action": "todo", "rationale": "New feature", "todo_agent": "claude", "todo_body": "add weight_oz to picklist line"},
]


def test_apply_dry_run_does_not_modify_file(suggestions_file):
    entries = parse_pending(suggestions_file)
    original = suggestions_file.read_text(encoding="utf-8")
    apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=False)
    assert suggestions_file.read_text(encoding="utf-8") == original


def test_apply_marks_already_done(suggestions_file):
    entries = parse_pending(suggestions_file)
    with patch("tgw.suggestions.todo_add"):
        apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=True)
    content = suggestions_file.read_text(encoding="utf-8")
    # index 0 → already_done → should be [x]
    assert "- [x] 2026-06-10T10:00" in content
    # index 1, 2 → not already_done → stay [ ]
    assert "- [ ] 2026-06-10T11:00" in content
    assert "- [ ] 2026-06-10T12:00" in content


def test_apply_creates_todo_on_write(suggestions_file):
    entries = parse_pending(suggestions_file)
    with patch("tgw.suggestions.todo_add") as mock_add:
        apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=True)
    mock_add.assert_called_once_with("claude", "add weight_oz to picklist line", source="suggestions_classify", pp_ref=None)


def test_apply_passes_valid_pp_ref(suggestions_file):
    entries = parse_pending(suggestions_file)
    classified = [
        {"index": 0, "action": "review_flag"},
        {"index": 1, "action": "todo", "todo_agent": "claude",
         "todo_body": "do the thing", "pp_ref": "pp-picklist-001"},
        {"index": 2, "action": "review_flag"},
    ]
    with patch("tgw.suggestions.todo_add") as mock_add:
        apply_classifications(suggestions_file, entries, classified, write=True)
    mock_add.assert_called_once_with("claude", "do the thing",
                                     source="suggestions_classify", pp_ref="PP-PICKLIST-001")


def test_apply_drops_malformed_pp_ref(suggestions_file):
    entries = parse_pending(suggestions_file)
    classified = [
        {"index": 0, "action": "review_flag"},
        {"index": 1, "action": "todo", "todo_agent": "claude",
         "todo_body": "do the thing", "pp_ref": "the picklist project"},
        {"index": 2, "action": "review_flag"},
    ]
    with patch("tgw.suggestions.todo_add") as mock_add:
        apply_classifications(suggestions_file, entries, classified, write=True)
    mock_add.assert_called_once_with("claude", "do the thing",
                                     source="suggestions_classify", pp_ref=None)


def test_apply_no_todo_created_on_dry_run(suggestions_file):
    entries = parse_pending(suggestions_file)
    with patch("tgw.suggestions.todo_add") as mock_add:
        apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=False)
    mock_add.assert_not_called()


def test_apply_counts(suggestions_file):
    entries = parse_pending(suggestions_file)
    result = apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=False)
    assert result["ok"] is True
    assert result["total"] == 3
    assert result["already_done"] == 1
    assert result["review_flag"] == 1
    assert result["todo"] == 1
    assert result["unmatched"] == 0


def test_apply_unmatched_entry(suggestions_file):
    entries = parse_pending(suggestions_file)
    # Only classify index 0; indices 1 and 2 have no classified counterpart
    result = apply_classifications(suggestions_file, entries, [CLASSIFIED_MIXED[0]], write=False)
    assert result["unmatched"] == 2


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------

def test_format_report_dry_run_says_dry_run(suggestions_file):
    entries = parse_pending(suggestions_file)
    result = apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=False)
    report = format_report(result, applied=False)
    assert "dry-run" in report
    assert "Already reflected" in report
    assert "New todos" in report
    assert "Needs review" in report


def test_format_report_applied_says_yes(suggestions_file):
    entries = parse_pending(suggestions_file)
    with patch("tgw.suggestions.todo_add"):
        result = apply_classifications(suggestions_file, entries, CLASSIFIED_MIXED, write=True)
    report = format_report(result, applied=True)
    assert "Applied: yes" in report


def test_format_report_no_pending(tmp_path):
    result = {"ok": True, "total": 0, "already_done": 0, "todo": 0,
              "plan_append": 0, "review_flag": 0, "unmatched": 0, "details": {}}
    report = format_report(result, applied=False)
    assert "0 pending" in report
