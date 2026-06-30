"""Tests for PP-EBAY-SNAPSHOT-001 — photo integrity checks.

#891: photo_verify block written after publish (ebay_publish).
#892: periodic integrity check + ebay_repush enqueue (ebay_sync).

All tests are offline: eBay API calls are monkeypatched.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

import tgw.workers.ebay_sync as ebay_sync_mod
from tgw.workers.ebay_sync import EbaySyncWorker


@pytest.fixture(autouse=True)
def _mock_fence(monkeypatch):
    monkeypatch.setattr(ebay_sync_mod, "fence_ebay_write", lambda *a, **k: {"ok": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(**extra: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "fulfillment_policy_id": "FC4",
        "payment_policy_id": "PAY1",
        "return_policy_id": "RET1",
        "ebay_verify_interval_days": 7,
        "raw": {},
        "api_key": "test-api-key",
    }
    cfg.update(extra)
    return cfg


def _worker(cfg: Dict[str, Any]) -> EbaySyncWorker:
    w = EbaySyncWorker.__new__(EbaySyncWorker)
    w.config = cfg
    return w


def _item_with_submitted(image_urls: list) -> Dict[str, Any]:
    return {
        "ebay_submitted": {
            "inventory_item": {"product": {"imageUrls": image_urls}},
            "staged_at": "2026-06-01T00:00:00+00:00",
        },
        "ebay_listing": {"status": "Active"},
    }


# ---------------------------------------------------------------------------
# _check_photo_integrity — interval gating
# ---------------------------------------------------------------------------


def test_photo_check_skipped_when_recently_verified(monkeypatch):
    """If verified_at is recent, no GET should be made."""
    called = []
    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: called.append(1) or {})

    item = _item_with_submitted(["u1", "u2"])
    item["ebay_listing"]["photo_verify"] = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "submitted_count": 2,
        "confirmed_count": 2,
    }
    worker = _worker(_cfg())
    result = worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert result is False
    assert called == []


def test_photo_check_runs_when_overdue(monkeypatch):
    """If verified_at is older than interval, a GET is made."""
    old_ts = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    item = _item_with_submitted(["u1", "u2"])
    item["ebay_listing"]["photo_verify"] = {
        "verified_at": old_ts,
        "submitted_count": 2,
        "confirmed_count": 2,
    }

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {"product": {"imageUrls": ["u1", "u2"]}})

    worker = _worker(_cfg())
    result = worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert result is True
    assert item["ebay_listing"]["photo_verify"]["confirmed_count"] == 2


def test_photo_check_runs_when_never_verified(monkeypatch):
    """Items with no photo_verify entry are checked immediately."""
    item = _item_with_submitted(["u1"])

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {"product": {"imageUrls": ["u1"]}})

    worker = _worker(_cfg())
    result = worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert result is True
    assert item["ebay_listing"]["photo_verify"]["submitted_count"] == 1
    assert item["ebay_listing"]["photo_verify"]["confirmed_count"] == 1


# ---------------------------------------------------------------------------
# _check_photo_integrity — repush enqueue
# ---------------------------------------------------------------------------


def test_repush_enqueued_when_photo_count_drops(monkeypatch):
    item = _item_with_submitted(["u1", "u2", "u3"])

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {"product": {"imageUrls": ["u1"]}})

    enqueued = []

    def fake_enqueue(**kwargs: Any) -> int:
        enqueued.append(kwargs)
        return 1

    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", fake_enqueue)

    worker = _worker(_cfg())
    result = worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert result is True
    assert item["ebay_listing"]["photo_verify"]["confirmed_count"] == 1
    assert len(enqueued) == 1
    assert enqueued[0]["queue_name"] == "ebay_repush"
    assert enqueued[0]["payload"] == {"sku": "tgwSKU"}


def test_repush_not_enqueued_when_counts_match(monkeypatch):
    item = _item_with_submitted(["u1", "u2"])

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {"product": {"imageUrls": ["u1", "u2"]}})

    enqueued = []
    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", lambda **k: enqueued.append(k) or 1)

    worker = _worker(_cfg())
    worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert enqueued == []


def test_repush_not_enqueued_when_no_submitted_reference(monkeypatch):
    """If no ebay_submitted and no draft imageUrls, don't cry wolf."""
    item: Dict[str, Any] = {"ebay_listing": {"status": "Active"}}

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {"product": {"imageUrls": []}})

    enqueued = []
    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", lambda **k: enqueued.append(k) or 1)

    worker = _worker(_cfg())
    worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert enqueued == []


def test_photo_check_survives_get_failure(monkeypatch):
    """A failed GET is non-fatal; returns False (no file write)."""
    item = _item_with_submitted(["u1"])

    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))

    worker = _worker(_cfg())
    result = worker._check_photo_integrity("tgwSKU", item, item["ebay_listing"])

    assert result is False
