"""Semi-automatic executor selection (tgw.model_selector)."""

from __future__ import annotations

import json

import pytest

from tgw import model_selector as ms


def _write(tmp_path, data):
    path = tmp_path / "model-availability.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _no_env_pin(monkeypatch):
    monkeypatch.delenv("TGW_IMPLEMENT_EXECUTOR", raising=False)
    monkeypatch.delenv("TGW_REVIEW_EXECUTOR", raising=False)
    monkeypatch.delenv("TGW_MODEL_AVAILABILITY", raising=False)


_BASE = {
    "updated": "2026-09-04",
    "executors": {
        "codex": {"available": False, "reason": "subscription lapsed"},
        "claude": {"available": True},
        "manual": {"available": True},
    },
    "roles": {
        "implementation": {"prefer": ["codex", "claude", "manual"]},
        "review": {"prefer": ["claude", "codex", "manual"]},
    },
}


def test_picks_first_available_in_policy_and_skips_the_unavailable():
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.status == "SELECTED"
    assert sel.executor == "claude"  # codex is first but unavailable
    assert "policy" in sel.reason
    assert sel.considered == ("codex", "claude", "manual")
    assert sel.availability_updated == "2026-09-04"


def test_receipt_shape():
    r = ms.select_executor("review", availability=_BASE).receipt()
    assert r["schema"] == ms.SCHEMA
    assert r["role"] == "review"
    assert r["executor"] == "claude"
    assert set(r) >= {"observed_at", "status", "reason", "considered", "availability_updated"}


def test_abstains_when_nothing_in_policy_is_available_no_silent_fallback():
    data = json.loads(json.dumps(_BASE))
    data["executors"]["claude"]["available"] = False
    data["executors"]["manual"]["available"] = False
    sel = ms.select_executor("implementation", availability=data)
    assert sel.status == "ABSTAIN"
    assert sel.executor is None
    assert "subscription lapsed" in sel.reason  # carries the held reasons


def test_env_pin_wins_only_if_available(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "claude")
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.executor == "claude" and "pinned" in sel.reason


def test_env_pin_wins_but_reason_flags_when_file_says_unavailable(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "codex")
    sel = ms.select_executor("implementation", availability=_BASE)
    assert sel.executor == "codex"
    assert "subscription lapsed" in sel.reason


def test_env_pin_to_a_name_that_is_not_a_known_executor_is_rejected(monkeypatch):
    monkeypatch.setenv("TGW_IMPLEMENT_EXECUTOR", "bogus")
    with pytest.raises(ms.ModelSelectorError, match="not a known executor"):
        ms.select_executor("implementation", availability=_BASE)


def test_unknown_executor_in_file_is_rejected(tmp_path, monkeypatch):
    path = _write(tmp_path, {**_BASE, "executors": {"gpt5": {"available": True}}})
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(path))
    with pytest.raises(ms.ModelSelectorError, match="unknown executor"):
        ms.load_availability()


def test_malformed_file_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "model-availability.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(path))
    with pytest.raises(ms.ModelSelectorError):
        ms.load_availability()


def test_missing_file_falls_back_to_manual_bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("TGW_MODEL_AVAILABILITY", str(tmp_path / "nope.json"))
    sel = ms.select_executor("implementation")
    assert sel.executor == "manual"


def test_committed_default_file_is_valid_and_selects_something():
    data = ms.load_availability(ms._REPO_DEFAULT)
    for role in ("implementation", "review"):
        sel = ms.select_executor(role, availability=data)
        assert sel.status == "SELECTED"
        assert sel.executor in ms.KNOWN_EXECUTORS
