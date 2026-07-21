"""Tests for the flake-mutation push/switch request gate — PP-FLAKEGATE-001,
todo #1621, invariant E17.

All tests are offline. DB calls are mocked (same convention as
tests/test_agent_trace.py) — no real PostgreSQL connection needed here.
Live-DB acceptance evidence (real queue_jobs rows) is captured separately
in the result manifest, not in this file.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tgw import flake_gate
from tgw.queue import state_machine as sm


def _mock_conn_cursor():
    """Build a MagicMock (con, cur) pair matching state_machine._conn()'s
    context-manager shape (same helper as test_agent_trace.py)."""
    mock_cur = MagicMock()
    mock_cur.__enter__ = MagicMock(return_value=mock_cur)
    mock_cur.__exit__ = MagicMock(return_value=False)

    mock_con = MagicMock()
    mock_con.__enter__ = MagicMock(return_value=mock_con)
    mock_con.__exit__ = MagicMock(return_value=False)
    mock_con.cursor = MagicMock(return_value=mock_cur)

    return mock_con, mock_cur


# ---------------------------------------------------------------------------
# request_push / request_switch — correct enqueue_job() call shape
# ---------------------------------------------------------------------------

def test_request_push_enqueues_with_expected_shape():
    with patch.object(sm, "enqueue_job", return_value="job-123") as mock_enqueue:
        result = flake_gate.request_push(
            "~/tgw-flake", "tgw-prod", "deadbeef1234", "test summary",
        )

    assert result == {
        "ok": True, "job_id": "job-123", "kind": "push",
        "host": "tgw-prod", "commit": "deadbeef1234",
    }
    mock_enqueue.assert_called_once()
    _, kwargs = mock_enqueue.call_args
    assert kwargs["queue_name"] == "flake_mutation"
    assert kwargs["entity_type"] == "flake_commit"
    assert kwargs["entity_id"] == "deadbeef1234"
    assert kwargs["operation"] == "push"
    assert kwargs["dedupe_key"] == "flake_mutation:push:tgw-prod:deadbeef1234"
    assert kwargs["payload"] == {
        "repo": "~/tgw-flake", "host": "tgw-prod", "kind": "push",
        "summary": "test summary",
    }


def test_request_switch_enqueues_with_expected_shape():
    with patch.object(sm, "enqueue_job", return_value="job-456") as mock_enqueue:
        result = flake_gate.request_switch("a1131", "cafebabe5678", "switch summary")

    assert result == {
        "ok": True, "job_id": "job-456", "kind": "switch",
        "host": "a1131", "commit": "cafebabe5678",
    }
    mock_enqueue.assert_called_once()
    _, kwargs = mock_enqueue.call_args
    assert kwargs["queue_name"] == "flake_mutation"
    assert kwargs["entity_id"] == "cafebabe5678"
    assert kwargs["operation"] == "switch"
    assert kwargs["dedupe_key"] == "flake_mutation:switch:a1131:cafebabe5678"


# ---------------------------------------------------------------------------
# mark_flake_mutation_executed — state transition correctness
# ---------------------------------------------------------------------------

def test_mark_flake_mutation_executed_updates_queued_to_succeeded():
    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchone = MagicMock(return_value={
        "job_id": "job-123", "entity_id": "deadbeef1234", "operation": "push",
        "payload_json": {}, "state": "succeeded", "finished_at": "2026-07-21T00:00:00Z",
    })

    with patch.object(sm, "_conn", return_value=mock_con):
        row = sm.mark_flake_mutation_executed("job-123", executed_by="Dave")

    assert row["state"] == "succeeded"
    # verify the UPDATE statement actually targets queued state + right queue
    update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE queue_jobs" in c[0][0]]
    assert len(update_calls) == 1
    sql, params = update_calls[0][0]
    assert "state = 'queued'" in sql
    assert "queue_name = %s" in sql
    assert params[0] == "job-123"
    assert params[1] == sm.FLAKE_MUTATION_QUEUE


def test_mark_flake_mutation_executed_raises_when_not_found():
    mock_con, mock_cur = _mock_conn_cursor()
    mock_cur.fetchone = MagicMock(return_value=None)

    with patch.object(sm, "_conn", return_value=mock_con):
        with pytest.raises(ValueError, match="no queued flake_mutation job found"):
            sm.mark_flake_mutation_executed("does-not-exist")


def test_flake_gate_mark_executed_wraps_valueerror_as_not_ok():
    with patch.object(sm, "mark_flake_mutation_executed", side_effect=ValueError("boom")):
        result = flake_gate.mark_executed("job-999")

    assert result == {"ok": False, "error": "boom"}


# ---------------------------------------------------------------------------
# audit — flags unmatched commits, passes matched ones
# ---------------------------------------------------------------------------

def _fake_run(returncode=0, stdout=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    return proc


def test_audit_flags_commit_with_no_matching_executed_push(tmp_path):
    repo = tmp_path / "tgw-flake"
    (repo / ".git").mkdir(parents=True)

    log_output = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 2026-07-22T10:00:00-07:00\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb 2026-07-22T09:00:00-07:00\n"
    )

    with patch("tgw.flake_gate.subprocess.run") as mock_run:
        mock_run.side_effect = [
            _fake_run(),  # git fetch
            _fake_run(stdout=log_output),  # git log
        ]
        with patch.object(sm, "list_executed_flake_push_shas",
                           return_value=["bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]):
            result = flake_gate.audit(str(repo))

    assert result["ok"] is True
    assert result["commits_checked"] == 2
    findings_shas = {f["sha"] for f in result["findings"]}
    assert findings_shas == {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}


def test_audit_passes_commit_with_matching_executed_push(tmp_path):
    repo = tmp_path / "tgw-flake"
    (repo / ".git").mkdir(parents=True)

    log_output = "cccccccccccccccccccccccccccccccccccccccc 2026-07-22T10:00:00-07:00\n"

    with patch("tgw.flake_gate.subprocess.run") as mock_run:
        mock_run.side_effect = [
            _fake_run(),
            _fake_run(stdout=log_output),
        ]
        with patch.object(sm, "list_executed_flake_push_shas",
                           return_value=["cccccccccccccccccccccccccccccccccccccccc"]):
            result = flake_gate.audit(str(repo))

    assert result["ok"] is True
    assert result["findings"] == []


def test_audit_ignores_commits_before_rollout_date(tmp_path):
    repo = tmp_path / "tgw-flake"
    (repo / ".git").mkdir(parents=True)

    log_output = "dddddddddddddddddddddddddddddddddddddddd 2026-01-01T00:00:00-07:00\n"

    with patch("tgw.flake_gate.subprocess.run") as mock_run:
        mock_run.side_effect = [
            _fake_run(),
            _fake_run(stdout=log_output),
        ]
        with patch.object(sm, "list_executed_flake_push_shas", return_value=[]):
            result = flake_gate.audit(str(repo))

    assert result["ok"] is True
    assert result["findings"] == []


def test_audit_errors_on_non_git_repo(tmp_path):
    result = flake_gate.audit(str(tmp_path / "not-a-repo"))
    assert result["ok"] is False
