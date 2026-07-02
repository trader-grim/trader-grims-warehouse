"""Tests for EbaySyncWorker._record_fallback_state (session-39 API audit finding #2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from tgw.workers.ebay_sync import EbaySyncWorker


def _worker(cfg: Dict[str, Any]) -> EbaySyncWorker:
    w = EbaySyncWorker.__new__(EbaySyncWorker)
    w.config = cfg
    return w


def _state(tmp_path: Path) -> Dict[str, Any]:
    return json.loads((tmp_path / "ebay-sync-fallback-state.json").read_text(encoding="utf-8"))


def test_first_fallback_records_consecutive_one(tmp_path):
    worker = _worker({"catalog_root": tmp_path})
    consecutive = worker._record_fallback_state(used_fallback=True)
    assert consecutive == 1
    assert _state(tmp_path)["consecutive_fallback_runs"] == 1


def test_repeated_fallback_increments(tmp_path):
    worker = _worker({"catalog_root": tmp_path})
    worker._record_fallback_state(used_fallback=True)
    worker._record_fallback_state(used_fallback=True)
    consecutive = worker._record_fallback_state(used_fallback=True)
    assert consecutive == 3


def test_successful_bulk_run_resets_to_zero(tmp_path):
    worker = _worker({"catalog_root": tmp_path})
    worker._record_fallback_state(used_fallback=True)
    worker._record_fallback_state(used_fallback=True)
    consecutive = worker._record_fallback_state(used_fallback=False)
    assert consecutive == 0
    assert _state(tmp_path)["consecutive_fallback_runs"] == 0


def test_no_catalog_root_does_not_crash(tmp_path):
    worker = _worker({})
    assert worker._record_fallback_state(used_fallback=True) == 1
    assert worker._record_fallback_state(used_fallback=False) == 0
