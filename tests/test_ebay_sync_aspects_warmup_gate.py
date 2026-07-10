"""Tests for EbaySyncWorker._aspects_warmup_due (session 41).

Session 39 wired the opportunistic aspects-cache warm-up to fire on every 6h
ebay_sync cycle instead of Dave's actual spec — "crawl it at the end of every day,
then our limit resets" — which let it drain the Taxonomy API quota at arbitrary
times (confirmed firing at 04:50am and hitting a 429, hours before the operator's
day started). This restricts it to the 2h window before the 00:00 PST reset, once
per calendar day.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from tgw.workers.ebay_sync import _RESET_TZ, EbaySyncWorker


def _worker(cfg: Dict[str, Any]) -> EbaySyncWorker:
    w = EbaySyncWorker.__new__(EbaySyncWorker)
    w.config = cfg
    return w


def _cfg(tmp_path) -> Dict[str, Any]:
    return {"catalog_root": tmp_path}


def _freeze(monkeypatch, iso: str) -> None:
    """Freeze datetime.now(_RESET_TZ) inside ebay_sync module to a fixed PST time."""
    fixed = datetime.fromisoformat(iso).replace(tzinfo=_RESET_TZ)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is not None else fixed.replace(tzinfo=None)

    import tgw.workers.ebay_sync as mod
    monkeypatch.setattr(mod, "datetime", _FrozenDatetime)


def test_not_due_outside_window(tmp_path, monkeypatch):
    _freeze(monkeypatch, "2026-07-02T14:00:00")  # 2pm PST — mid-day
    worker = _worker(_cfg(tmp_path))
    assert worker._aspects_warmup_due() is False


def test_due_inside_window_first_time(tmp_path, monkeypatch):
    _freeze(monkeypatch, "2026-07-02T22:30:00")  # 10:30pm PST — inside window
    worker = _worker(_cfg(tmp_path))
    assert worker._aspects_warmup_due() is True


def test_not_due_again_same_day_after_marked(tmp_path, monkeypatch):
    _freeze(monkeypatch, "2026-07-02T22:30:00")
    worker = _worker(_cfg(tmp_path))
    worker._mark_aspects_warmup_run()
    assert worker._aspects_warmup_due() is False


def test_due_again_next_day_in_window(tmp_path, monkeypatch):
    _freeze(monkeypatch, "2026-07-02T22:30:00")
    worker = _worker(_cfg(tmp_path))
    worker._mark_aspects_warmup_run()

    _freeze(monkeypatch, "2026-07-03T23:00:00")
    assert worker._aspects_warmup_due() is True


def test_state_file_written_with_pst_date(tmp_path, monkeypatch):
    _freeze(monkeypatch, "2026-07-02T22:30:00")
    worker = _worker(_cfg(tmp_path))
    worker._mark_aspects_warmup_run()
    state = json.loads((tmp_path / "ebay-sync-aspects-warmup-state.json").read_text())
    assert state["last_run_date"] == "2026-07-02"
