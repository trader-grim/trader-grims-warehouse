"""PP-EBAY-MOTORS-001 follow-up (todo #1214, Dave 2026-07-09): marketplace_id
must be kept current from the LIVE offer on every ebay_sync pass — both the
first time a newly staged item is ever synced, and every time afterward,
including the ebay_sync job apply_revision() enqueues right after a live
category-change PUT. Never invented locally; always read from eBay's own
offer response (marketplaceId).

All eBay API calls are mocked — tests pass completely offline.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

import pytest

import tgw.workers.ebay_sync as ebay_sync_mod
from tgw.workers.ebay_sync import EbaySyncWorker


@pytest.fixture(autouse=True)
def _mock_fence_and_backfill(monkeypatch):
    monkeypatch.setattr(ebay_sync_mod, "fence_ebay_write", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ebay_sync_mod, "backfill_canonical_from_live", lambda item: {})
    monkeypatch.setattr(ebay_sync_mod.state_machine, "enqueue_job", lambda **k: 1)
    # inventory_item refresh gate: keep it inert for these tests
    monkeypatch.setattr(ebay_sync_mod, "ebay_get", lambda *a, **k: {})


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
    recent = datetime.now(timezone.utc).isoformat()
    item.setdefault("ebay_live", {}).setdefault("pulled_at", recent)
    (d / f"{sku}.json").write_text(json.dumps(item), encoding="utf-8")


def _offer(sku="tgwSKU", marketplace_id="EBAY_US", category_id="12345") -> Dict[str, Any]:
    return {
        "sku": sku,
        "offerId": "O1",
        "status": "PUBLISHED",
        "listing": {},
        "pricingSummary": {"price": {"value": "9.99"}},
        "categoryId": category_id,
        "availableQuantity": 1,
        "marketplaceId": marketplace_id,
    }


def test_marketplace_id_set_on_first_sync(tmp_path, monkeypatch):
    sku = "tgwSKU1"
    _write_item(tmp_path, sku, {"ebay_listing": {}})  # no marketplace_id yet

    patched = {}
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, marketplace_id="EBAY_MOTORS"), sku)

    assert patched == {"marketplace_id": "EBAY_MOTORS"}


def test_marketplace_id_updated_when_category_change_moves_it_to_motors(tmp_path, monkeypatch):
    # Simulates apply_revision(): a category edit went live, and the
    # follow-up ebay_sync (enqueued by apply_revision) picks up the new
    # marketplaceId eBay assigned as a result.
    sku = "tgwSKU2"
    _write_item(tmp_path, sku, {"marketplace_id": "EBAY_US", "ebay_offer": {"category_id": "111"}})

    patched = {}
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, marketplace_id="EBAY_MOTORS", category_id="6030"), sku)

    assert patched == {"marketplace_id": "EBAY_MOTORS"}


def test_marketplace_id_updated_when_category_change_moves_it_off_motors(tmp_path, monkeypatch):
    sku = "tgwSKU3"
    _write_item(tmp_path, sku, {"marketplace_id": "EBAY_MOTORS", "ebay_offer": {"category_id": "6030"}})

    patched = {}
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, marketplace_id="EBAY_US", category_id="111"), sku)

    assert patched == {"marketplace_id": "EBAY_US"}


def test_marketplace_id_unchanged_does_not_trigger_patch(tmp_path, monkeypatch):
    sku = "tgwSKU4"
    _write_item(tmp_path, sku, {"marketplace_id": "EBAY_MOTORS"})

    patch_calls = []
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patch_calls.append(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, marketplace_id="EBAY_MOTORS"), sku)

    assert patch_calls == []


def test_missing_marketplace_id_on_offer_does_not_clear_stored_value(tmp_path, monkeypatch):
    # A malformed/partial offer response must never erase a known-good value.
    sku = "tgwSKU5"
    _write_item(tmp_path, sku, {"marketplace_id": "EBAY_MOTORS"})

    patch_calls = []
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patch_calls.append(fields) or {"ok": True})

    offer = _offer(sku)
    del offer["marketplaceId"]
    worker = _worker(_cfg(tmp_path))
    worker._sync_one(offer, sku)

    assert patch_calls == []
