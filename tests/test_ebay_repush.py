"""Tests for tgw.ebay.repush.cmd_ebay_repush (PP-EBAY-SNAPSHOT-001 Phase 4)."""

from __future__ import annotations

import json
from pathlib import Path

from tgw.ebay.repush import cmd_ebay_repush

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_item(root: Path, sku: str, doc: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _make_cfg(tmp_path: Path) -> dict:
    return {
        "itemdata_root": tmp_path / "ItemData",
        "postgres_dsn": "dbname=state_machine user=tgw",
    }


_INV_BODY = {
    "product": {"title": "Test Widget", "imageUrls": ["https://example.com/1.jpg"]},
    "condition": "USED_GOOD",
    "availability": {"shipToLocationAvailability": {"quantity": 1}},
}

_FULL_ITEM = {
    "sku": "tgw20260101120000001",
    "title": "Test Widget",
    "ebay_listing": {"status": "Active", "listing_id": "123456789"},
    "ebay_offer": {"offer_id": "OFF-001", "status": "PUBLISHED"},
    "ebay_submitted": {
        "inventory_item": _INV_BODY,
        "staged_at": "2026-06-01T12:00:00+00:00",
    },
}

SKU = "tgw20260101120000001"
SKU2 = "tgw20260102120000002"


# ---------------------------------------------------------------------------
# _repush_one unit tests (via cmd_ebay_repush with explicit SKU list)
# ---------------------------------------------------------------------------

def test_repush_dry_run_no_api_call(tmp_path, monkeypatch):
    """Dry-run returns ok=True without calling ebay_put."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: called.append(a))

    result = cmd_ebay_repush(cfg, [SKU], dry_run=True)
    assert result["ok"] is True
    assert result["count"] == 1
    assert SKU in result["pushed"]
    assert called == []  # no eBay API call


def test_repush_calls_ebay_put(tmp_path, monkeypatch):
    """Live run calls ebay_put with the correct path and body."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)
    cfg = _make_cfg(tmp_path)

    calls = []
    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda cfg, path, body: calls.append((path, body)))

    result = cmd_ebay_repush(cfg, [SKU])
    assert result["ok"] is True
    assert result["count"] == 1
    assert len(calls) == 1
    path, body = calls[0]
    assert path == f"/sell/inventory/v1/inventory_item/{SKU}"
    assert body == _INV_BODY


def test_repush_skips_item_without_ebay_submitted(tmp_path, monkeypatch):
    """Item with no ebay_submitted block is skipped (not an error)."""
    root = tmp_path / "ItemData"
    root.mkdir()
    item = {**_FULL_ITEM}
    del item["ebay_submitted"]
    _write_item(root, SKU, item)
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: None)

    result = cmd_ebay_repush(cfg, [SKU])
    assert result["ok"] is True
    assert result["count"] == 0
    assert len(result["skipped"]) == 1
    assert "ebay_submitted" in result["skipped"][0]["reason"]


def test_repush_skips_item_without_offer_id(tmp_path, monkeypatch):
    """Item with no offer_id is skipped."""
    root = tmp_path / "ItemData"
    root.mkdir()
    item = {**_FULL_ITEM, "ebay_offer": {}}
    _write_item(root, SKU, item)
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: None)

    result = cmd_ebay_repush(cfg, [SKU])
    assert result["ok"] is True
    assert result["count"] == 0
    assert len(result["skipped"]) == 1
    assert "offer_id" in result["skipped"][0]["reason"]


def test_repush_missing_sku_is_error(tmp_path, monkeypatch):
    """Non-existent SKU is reported as an error (not a skip)."""
    root = tmp_path / "ItemData"
    root.mkdir()
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: None)

    result = cmd_ebay_repush(cfg, ["tgw_no_such_sku"])
    assert result["ok"] is False
    assert len(result["errors"]) == 1
    assert "not found" in result["errors"][0]["reason"]


def test_repush_ebay_put_failure_is_error(tmp_path, monkeypatch):
    """When ebay_put raises, the SKU is reported as an error."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)
    cfg = _make_cfg(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("eBay 503")

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", _boom)

    result = cmd_ebay_repush(cfg, [SKU])
    assert result["ok"] is False
    assert len(result["errors"]) == 1
    assert "eBay PUT failed" in result["errors"][0]["reason"]


def test_repush_no_skus_no_all_listed_returns_empty(tmp_path):
    """No SKUs and no --all-listed returns ok=True with count=0."""
    root = tmp_path / "ItemData"
    root.mkdir()
    cfg = _make_cfg(tmp_path)

    result = cmd_ebay_repush(cfg, [])
    assert result["ok"] is True
    assert result["count"] == 0
    assert "no items matched" in result["note"]


# ---------------------------------------------------------------------------
# --all-listed flag
# ---------------------------------------------------------------------------

def test_all_listed_finds_active_items(tmp_path, monkeypatch):
    """--all-listed scans ItemData and re-pushes items with ebay_listing.status=Active."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)

    # Second item: not Active — should be excluded
    item2 = {
        "sku": SKU2,
        "title": "Inactive Widget",
        "ebay_listing": {"status": "Ended"},
        "ebay_offer": {"offer_id": "OFF-002"},
        "ebay_submitted": {"inventory_item": _INV_BODY, "staged_at": "2026-06-01T12:00:00+00:00"},
    }
    _write_item(root, SKU2, item2)
    cfg = _make_cfg(tmp_path)

    calls = []
    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda cfg, path, body: calls.append(path))

    result = cmd_ebay_repush(cfg, [], all_listed=True)
    assert result["ok"] is True
    assert result["count"] == 1
    assert SKU in result["pushed"]
    assert SKU2 not in result["pushed"]
    assert len(calls) == 1
    assert f"/sell/inventory/v1/inventory_item/{SKU}" in calls[0]


def test_all_listed_dry_run(tmp_path, monkeypatch):
    """--all-listed --dry-run finds items but makes no API calls."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: called.append(a))

    result = cmd_ebay_repush(cfg, [], all_listed=True, dry_run=True)
    assert result["ok"] is True
    assert result["count"] == 1
    assert called == []


def test_all_listed_empty_catalog(tmp_path, monkeypatch):
    """--all-listed on an empty ItemData returns ok=True with count=0."""
    root = tmp_path / "ItemData"
    root.mkdir()
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", lambda *a, **k: None)

    result = cmd_ebay_repush(cfg, [], all_listed=True)
    assert result["ok"] is True
    assert result["count"] == 0


def test_all_listed_partial_errors_ok_false(tmp_path, monkeypatch):
    """When some items error, ok=False but pushed items are still reported."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _FULL_ITEM)

    item2 = {
        "sku": SKU2,
        "title": "Widget 2",
        "ebay_listing": {"status": "Active"},
        "ebay_offer": {"offer_id": "OFF-002"},
        "ebay_submitted": {"inventory_item": _INV_BODY, "staged_at": "2026-06-01T12:00:00+00:00"},
    }
    _write_item(root, SKU2, item2)
    cfg = _make_cfg(tmp_path)

    call_count = [0]

    def _sometimes_fail(cfg, path, body):
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("eBay timeout")

    monkeypatch.setattr("tgw.ebay.repush.ebay_put", _sometimes_fail)

    result = cmd_ebay_repush(cfg, [], all_listed=True)
    assert result["ok"] is False
    assert result["count"] == 1
    assert len(result["errors"]) == 1
