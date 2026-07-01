"""Tests for check_ebay_sync_fallback() — session-39 API audit finding #2.

ebay_sync's per-SKU fallback (eBay error 25707) is ~N-fold more expensive in API
calls than the bulk offer list. This check surfaces it in `tgw health` once it's
been the steady state for 2+ consecutive sync runs, instead of running invisibly.
"""

from __future__ import annotations

import json
from pathlib import Path

from tgw.health import check_ebay_sync_fallback


def _cfg(tmp_path: Path) -> dict:
    return {"catalog_root": tmp_path}


def _write_state(tmp_path: Path, **fields) -> None:
    (tmp_path / "ebay-sync-fallback-state.json").write_text(json.dumps(fields), encoding="utf-8")


def test_no_state_file_is_healthy(tmp_path):
    result = check_ebay_sync_fallback(_cfg(tmp_path))
    assert result["ok"] is True
    assert not result.get("warn")


def test_zero_consecutive_is_healthy(tmp_path):
    _write_state(tmp_path, consecutive_fallback_runs=0, last_bulk_ok_at="2026-07-01T00:00:00+00:00")
    result = check_ebay_sync_fallback(_cfg(tmp_path))
    assert result["ok"] is True
    assert not result.get("warn")


def test_single_fallback_is_warned_but_not_failed(tmp_path):
    _write_state(tmp_path, consecutive_fallback_runs=1, last_fallback_at="2026-07-01T00:00:00+00:00")
    result = check_ebay_sync_fallback(_cfg(tmp_path))
    assert result["ok"] is True
    assert result["warn"] is True


def test_persistent_fallback_fails_the_check(tmp_path):
    _write_state(tmp_path, consecutive_fallback_runs=3, last_fallback_at="2026-07-01T00:00:00+00:00")
    result = check_ebay_sync_fallback(_cfg(tmp_path))
    assert result["ok"] is False
    assert result["warn"] is True
    assert "1077" in result["detail"]


def test_unreadable_state_file_does_not_crash(tmp_path):
    (tmp_path / "ebay-sync-fallback-state.json").write_text("not json", encoding="utf-8")
    result = check_ebay_sync_fallback(_cfg(tmp_path))
    assert result["ok"] is True


def test_no_catalog_root_is_healthy(tmp_path):
    result = check_ebay_sync_fallback({})
    assert result["ok"] is True
