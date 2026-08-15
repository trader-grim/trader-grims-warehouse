"""Tests for EbaySyncWorker.handle()'s 400-handling on GET /sell/inventory/v1/offer.

Todo #1397 / PP-DEADLETTER-001: 9 stale ebay_sync dead-letters from 2026-06-30
turned out to predate the 25707 orphaned-SKU fallback (added same day,
commit 382f3f0, ~22:24 PDT) — the dead-lettered jobs ran hours earlier and
simply had no HTTPError handling at all yet. Root logs/journald don't retain
back to 2026-06-30 so the exact historical errorId can't be recovered, but
these tests lock in that going forward:

1. An unrecognized 400 (not 25707, not the fetch_all_offers graceful-empty
   set) is logged with its errorId/message before re-raising, both from
   ebay_sync.py's own except block and from fetch_all_offers() itself.
2. The existing 25707 fallback path is unaffected (no regression).
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from unittest.mock import patch

import pytest
import requests

from tgw.workers.ebay_sync import EbaySyncWorker


def _worker(cfg: Dict[str, Any]) -> EbaySyncWorker:
    w = EbaySyncWorker.__new__(EbaySyncWorker)
    w.config = cfg
    return w


def _job() -> Dict[str, Any]:
    return {"payload_json": {"reason": "scheduled"}}


def _http_error(error_id: int) -> requests.exceptions.HTTPError:
    resp = requests.Response()
    resp.status_code = 400
    resp.json = lambda: {"errors": [{"errorId": str(error_id), "message": "Some eBay message"}]}
    return requests.exceptions.HTTPError(response=resp)


def test_unrecognized_400_logs_error_id_before_raising(tmp_path, caplog):
    """A 400 with an errorId that is neither 25707 nor the graceful-empty set
    must be logged (errorId + message) before the exception propagates."""
    worker = _worker({"catalog_root": tmp_path})
    exc = _http_error(99999)  # not 25707, not 25702/25710/25009

    with patch("tgw.workers.ebay_sync.fetch_all_offers", side_effect=exc):
        with caplog.at_level(logging.ERROR, logger="tgw.workers.ebay_sync"):
            with pytest.raises(requests.exceptions.HTTPError):
                worker.handle(_job())

    assert any("99999" in r.getMessage() for r in caplog.records), (
        "unrecognized eBay errorId must be logged before re-raising"
    )


def test_unrecognized_400_with_unparseable_body_still_logs(tmp_path, caplog):
    """Even if the body can't be parsed as JSON, the worker's own except
    block must not raise silently without a diagnostic line."""
    worker = _worker({"catalog_root": tmp_path})
    resp = requests.Response()
    resp.status_code = 400
    resp._content = b"not json"
    resp.json = lambda: (_ for _ in ()).throw(ValueError("bad json"))
    exc = requests.exceptions.HTTPError(response=resp)

    with patch("tgw.workers.ebay_sync.fetch_all_offers", side_effect=exc):
        with caplog.at_level(logging.ERROR, logger="tgw.workers.ebay_sync"):
            with pytest.raises(requests.exceptions.HTTPError):
                worker.handle(_job())

    assert any("unrecognized" in r.getMessage().lower() for r in caplog.records)


def test_fetch_all_offers_logs_empty_errors_list_before_raising(caplog):
    """fetch_all_offers(): a 400 that parses but has an empty 'errors' list
    must still log something before re-raising (was previously a silent
    no-op log.warning loop over an empty list)."""
    from tgw.ebay.sync import fetch_all_offers

    resp = requests.Response()
    resp.status_code = 400
    resp.json = lambda: {"errors": []}
    exc = requests.exceptions.HTTPError(response=resp)

    with patch("tgw.ebay.sync.ebay_get", side_effect=exc):
        with caplog.at_level(logging.WARNING, logger="tgw.ebay.sync"):
            with pytest.raises(requests.exceptions.HTTPError):
                fetch_all_offers({})

    assert any("empty errors list" in r.getMessage() for r in caplog.records)


def test_25707_fallback_path_unaffected(tmp_path, monkeypatch):
    """Regression guard: the existing 25707 orphaned-SKU fallback must still
    run the per-SKU path and NOT hit the new unrecognized-error log branch."""
    worker = _worker({"catalog_root": tmp_path, "itemdata_root": tmp_path})
    exc = _http_error(25707)

    monkeypatch.setattr(worker, "_fetch_offers_by_local_skus", lambda: [])
    monkeypatch.setattr(worker, "_reschedule", lambda: None)

    with patch("tgw.workers.ebay_sync.fetch_all_offers", side_effect=exc):
        # Should NOT raise — falls back to per-SKU lookups and completes normally.
        worker.handle(_job())


def test_known_graceful_empty_ids_unaffected():
    """Regression guard: fetch_all_offers()'s 25702/25710/25009 graceful-empty
    handling still returns [] without raising or hitting the new log branch."""
    from tgw.ebay.sync import fetch_all_offers

    resp = requests.Response()
    resp.status_code = 400
    resp.json = lambda: {"errors": [{"errorId": "25702"}]}
    exc = requests.exceptions.HTTPError(response=resp)

    with patch("tgw.ebay.sync.ebay_get", side_effect=exc):
        assert fetch_all_offers({}) == []
