"""Tests for the inventory_item refresh gate in EbaySyncWorker._sync_one (session 39,
API audit finding #1): this used to be an unconditional live GET on every offer, every
sync pass (~2,089 items x 4x/day) — the single biggest Sell Inventory API quota drain
found. It's now gated by the same ebay_verify_interval_days used by the photo-integrity
check, and reuses that check's fetch instead of hitting the endpoint twice per item.

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

import pytest

import tgw.workers.ebay_sync as ebay_sync_mod
from tgw.workers.ebay_sync import EbaySyncWorker


@pytest.fixture(autouse=True)
def _mock_fence_and_backfill(monkeypatch):
    monkeypatch.setattr(ebay_sync_mod, "fence_ebay_write", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ebay_sync_mod, "backfill_canonical_from_live", lambda item: {})
    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", lambda **k: 1)


def _cfg(tmp_path, **extra: Any) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "itemdata_root": tmp_path,
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


def _write_item(tmp_path, sku: str, item: Dict[str, Any]) -> None:
    d = tmp_path / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _offer(sku="tgwSKU", status="PUBLISHED", listing_status=None) -> Dict[str, Any]:
    listing = {"listingId": "L1", "listingStatus": listing_status} if listing_status else {}
    return {
        "sku": sku,
        "offerId": "O1",
        "status": status,
        "listing": listing,
        "pricingSummary": {"price": {"value": "9.99"}},
        "categoryId": "12345",
        "availableQuantity": 1,
    }


def test_inventory_refresh_skipped_when_recently_pulled_and_not_active(tmp_path, monkeypatch):
    sku = "tgwSKU1"
    recent = datetime.now(timezone.utc).isoformat()
    _write_item(tmp_path, sku, {"ebay_live": {"pulled_at": recent}, "ebay_listing": {}})

    calls = []
    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: calls.append(a) or {})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku), sku)

    assert calls == []


def test_inventory_refresh_runs_when_stale_and_not_active(tmp_path, monkeypatch):
    sku = "tgwSKU2"
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    _write_item(tmp_path, sku, {"ebay_live": {"pulled_at": stale}, "ebay_listing": {}})

    calls = []
    monkeypatch.setattr(
        ebay_sync_mod, "ebay_get",
        lambda *a, **k: calls.append(a) or {"product": {"imageUrls": ["u1"]}},
    )

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku), sku)

    assert len(calls) == 1


def test_inventory_refresh_runs_when_never_pulled(tmp_path, monkeypatch):
    sku = "tgwSKU3"
    _write_item(tmp_path, sku, {"ebay_listing": {}})

    calls = []
    monkeypatch.setattr(
        ebay_sync_mod, "ebay_get",
        lambda *a, **k: calls.append(a) or {"product": {"imageUrls": ["u1"]}},
    )

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku), sku)

    assert len(calls) == 1


def test_inventory_refresh_reuses_photo_integrity_fetch_for_active_listing(tmp_path, monkeypatch):
    """Active listing due for both checks in the same cycle → exactly one live GET,
    not two (this is the duplicate-call bug the audit found)."""
    sku = "tgwSKU4"
    _write_item(tmp_path, sku, {
        "ebay_listing": {"status": "Active"},
        "ebay_submitted": {"inventory_item": {"product": {"imageUrls": ["u1"]}}},
    })

    calls = []
    monkeypatch.setattr(
        ebay_sync_mod, "ebay_get",
        lambda *a, **k: calls.append(a) or {"product": {"imageUrls": ["u1"]}},
    )

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, listing_status="ACTIVE"), sku)

    assert len(calls) == 1, f"expected exactly 1 live GET, got {len(calls)}"
