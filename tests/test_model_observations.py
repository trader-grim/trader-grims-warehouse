"""Dispatch-outcome observations (tgw.model_observations): the live health half
of the model-selection feedback loop (LEAF-11-8 / Todo 1956 slice)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tgw import model_observations as mo


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv(mo.ENV_VAR, str(tmp_path / "observations.jsonl"))
    monkeypatch.delenv(mo.ENABLED_ENV_VAR, raising=False)


def _lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_record_writes_schema_line_and_recent_status_holds():
    assert mo.record("opencode", "implementation", "unavailable", detail="quota wall") is True
    status = mo.recent_status("opencode")
    assert status is not None
    state, reason = status
    assert state == "held"
    assert "unavailable" in reason

    (entry,) = [json.loads(line) for line in _lines(mo.observations_path())]
    assert entry["schema"] == mo.SCHEMA
    assert set(entry) >= {"observed_at", "executor", "role", "outcome", "detail"}
    assert entry["executor"] == "opencode"
    assert entry["role"] == "implementation"
    assert entry["outcome"] == "unavailable"
    assert entry["detail"] == "quota wall"
    datetime.fromisoformat(entry["observed_at"])  # parses as ISO-8601


def test_hold_lapses_after_the_cooldown():
    mo.record("opencode", "implementation", "unavailable", detail="quota")
    soon = datetime.now(timezone.utc) + timedelta(minutes=59)
    assert mo.recent_status("opencode", now=soon) is not None
    late = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert mo.recent_status("opencode", now=late) is None


def test_error_hold_uses_the_shorter_window():
    mo.record("claude", "review", "error", detail="exit 1")
    assert mo.recent_status("claude") is not None
    inside = datetime.now(timezone.utc) + timedelta(minutes=14)
    assert mo.recent_status("claude", now=inside) is not None
    outside = datetime.now(timezone.utc) + timedelta(minutes=16)
    assert mo.recent_status("claude", now=outside) is None


def test_a_later_available_clears_an_earlier_unavailable():
    mo.record("opencode", "implementation", "unavailable", detail="quota")
    assert mo.recent_status("opencode") is not None
    mo.record("opencode", "implementation", "available")
    assert mo.recent_status("opencode") is None


def test_unknown_executor_has_no_hold():
    mo.record("opencode", "implementation", "unavailable")
    assert mo.recent_status("claude") is None


def test_missing_file_means_no_hold_and_no_raise(tmp_path, monkeypatch):
    monkeypatch.setenv(mo.ENV_VAR, str(tmp_path / "does-not-exist.jsonl"))
    assert mo.recent_status("opencode") is None


def test_corrupt_file_means_no_hold_and_no_raise():
    path = mo.observations_path()
    path.write_text("not json\n{broken\n", encoding="utf-8")
    assert mo.recent_status("opencode") is None
    # and a fresh record on top of the corruption still works
    assert mo.record("opencode", "implementation", "available") is True
    assert mo.recent_status("opencode") is None


def test_corrupt_lines_are_skipped_but_valid_ones_count():
    path = mo.observations_path()
    path.write_text("garbage\n[1,2]\n", encoding="utf-8")
    mo.record("opencode", "implementation", "unavailable", detail="quota")
    assert mo.recent_status("opencode") is not None


def test_file_stays_bounded_after_many_writes():
    for i in range(mo.MAX_LINES + 100):
        assert mo.record("opencode", "implementation", "available", detail=f"run {i}") is True
    lines = _lines(mo.observations_path())
    # write #MAX_LINES+1 trips the bound (rewrite to the last KEEP_LINES);
    # the remaining 99 appends stay well under MAX_LINES.
    assert len(lines) == mo.KEEP_LINES + 99
    assert json.loads(lines[-1])["detail"] == f"run {mo.MAX_LINES + 99}"


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError, match="unknown model-observation outcome"):
        mo.record("opencode", "implementation", "bogus")


def test_recording_can_be_disabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv(mo.ENABLED_ENV_VAR, "off")
    assert mo.enabled() is False
    assert mo.record("opencode", "implementation", "unavailable") is False
    assert not mo.observations_path().exists()
