"""todo #1931 (item category backend fix): the LIVE eBay categoryId must be
mirrored to the canonical top-level ebay_category_id on every ebay_sync pass
— both the first time a newly staged item is ever synced, and every time
afterward. Previously ebay_sync wrote category only into ebay_offer, so any
item staged before the live category could differ had no canonical record of
it: the Draft Editor fell back to "unset" (category "99") and the category
picker stayed unseeded (reproduced on tgw201809090907247), and the
save-inventory staging gate (draft category OR ebay_category_id) stayed
unsatisfied. Never invented locally; always read from eBay's own offer
response (categoryId).

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
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item", lambda cfg, sku, fields: {"ok": True})


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


def _offer(sku="tgwSKU", category_id="12345") -> Dict[str, Any]:
    # No marketplaceId on purpose: the marketplace mirror (todo #1214) is
    # covered by test_ebay_sync_marketplace_id.py; these tests isolate the
    # category mirror, so the offer must not trip the marketplace patch.
    return {
        "sku": sku,
        "offerId": "O1",
        "status": "PUBLISHED",
        "listing": {},
        "pricingSummary": {"price": {"value": "9.99"}},
        "categoryId": category_id,
        "availableQuantity": 1,
    }


def test_category_id_set_on_first_sync(tmp_path, monkeypatch):
    sku = "tgwCAT1"
    _write_item(tmp_path, sku, {"ebay_listing": {}})  # no ebay_category_id yet

    patched = {}
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, category_id="12345"), sku)

    assert patched == {"ebay_category_id": "12345"}


def test_category_id_updated_when_live_differs(tmp_path, monkeypatch):
    # A live category change (e.g. via the Draft Editor's category picker, or
    # an eBay-side move) must propagate to the canonical field on the next
    # sync, not stay stuck at the stale value.
    sku = "tgwCAT2"
    _write_item(tmp_path, sku, {"ebay_category_id": "111", "ebay_offer": {"category_id": "111"}})

    patched = {}
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patched.update(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, category_id="6030"), sku)

    assert patched == {"ebay_category_id": "6030"}


def test_category_id_unchanged_does_not_trigger_patch(tmp_path, monkeypatch):
    sku = "tgwCAT3"
    _write_item(tmp_path, sku, {"ebay_category_id": "12345"})

    patch_calls = []
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patch_calls.append(fields) or {"ok": True})

    worker = _worker(_cfg(tmp_path))
    worker._sync_one(_offer(sku, category_id="12345"), sku)

    assert patch_calls == []


def test_missing_category_id_on_offer_does_not_clear_stored_value(tmp_path, monkeypatch):
    # A malformed/partial offer response must never erase a known-good value.
    sku = "tgwCAT4"
    _write_item(tmp_path, sku, {"ebay_category_id": "12345"})

    patch_calls = []
    monkeypatch.setattr(ebay_sync_mod, "fence_patch_item",
                        lambda cfg, sku, fields: patch_calls.append(fields) or {"ok": True})

    offer = _offer(sku)
    del offer["categoryId"]
    worker = _worker(_cfg(tmp_path))
    worker._sync_one(offer, sku)

    assert patch_calls == []
