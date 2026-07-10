"""Tests for tgw.ebay.snapshot_backfill.cmd_ebay_backfill_snapshot (PP-EBAY-SNAPSHOT-001 #894)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from tgw.ebay.snapshot_backfill import _backfill_one, cmd_ebay_backfill_snapshot


@pytest.fixture(autouse=True)
def _mock_fence(monkeypatch):
    import tgw.ebay.snapshot_backfill as _smod
    from tests.conftest import make_fake_patch_item

    monkeypatch.setattr(_smod, "fence_patch_item", make_fake_patch_item(None))


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
        "pretty": False,
        "api_key": "test-api-key",
    }


SKU = "tgw20260101120000001"
SKU2 = "tgw20260102120000002"

_FAKE_INV_RESPONSE = {
    "sku": SKU,
    "product": {"title": "Test Widget", "imageUrls": ["https://example.com/1.jpg"]},
    "condition": "USED_GOOD",
    "availability": {"shipToLocationAvailability": {"quantity": 1}},
}

_ITEM_NEEDS_BACKFILL = {
    "sku": SKU,
    "title": "Test Widget",
    "ebay_listing": {"listing_id": "123456789", "status": "Active"},
}

_ITEM_ALREADY_HAS_SUBMITTED = {
    "sku": SKU,
    "title": "Test Widget",
    "ebay_listing": {"listing_id": "123456789", "status": "Active"},
    "ebay_submitted": {
        "inventory_item": _FAKE_INV_RESPONSE,
        "fetched_at": "2026-06-01T12:00:00+00:00",
    },
}

_ITEM_NO_LISTING_ID = {
    "sku": SKU,
    "title": "Test Widget",
    "ebay_listing": {"status": "Active"},
}


# ---------------------------------------------------------------------------
# _backfill_one unit tests
# ---------------------------------------------------------------------------


def test_backfill_one_dry_run(tmp_path, monkeypatch):
    """Dry-run returns ok=True, dry_run=True without calling ebay_get."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: called.append(a))

    result = _backfill_one(cfg, SKU, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert called == []


def test_backfill_one_saves_response(tmp_path, monkeypatch):
    """Successful GET writes ebay_submitted.inventory_item to the item JSON."""
    root = tmp_path / "ItemData"
    root.mkdir()
    path = _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr(
        "tgw.ebay.snapshot_backfill.ebay_get",
        lambda cfg, url: _FAKE_INV_RESPONSE,
    )

    result = _backfill_one(cfg, SKU)
    assert result["ok"] is True
    assert not result.get("skipped")

    saved = json.loads(path.read_text())
    assert "ebay_submitted" in saved
    assert saved["ebay_submitted"]["inventory_item"] == _FAKE_INV_RESPONSE
    assert "fetched_at" in saved["ebay_submitted"]


def test_backfill_one_skips_existing_submitted(tmp_path, monkeypatch):
    """Item that already has ebay_submitted is skipped (ok=True, skipped=True)."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_ALREADY_HAS_SUBMITTED)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: called.append(a))

    result = _backfill_one(cfg, SKU)
    assert result["ok"] is True
    assert result["skipped"] is True
    assert called == []


def test_backfill_one_skips_no_listing_id(tmp_path, monkeypatch):
    """Item without ebay_listing.listing_id is skipped."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NO_LISTING_ID)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: called.append(a))

    result = _backfill_one(cfg, SKU)
    assert result["ok"] is False
    assert result["skipped"] is True
    assert called == []


def test_backfill_one_404_is_skipped(tmp_path, monkeypatch):
    """404 from eBay (Trading-API-only item) is treated as ok=True, skipped=True."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    def _raise_404(cfg, url):
        resp = MagicMock()
        resp.status_code = 404
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", _raise_404)

    result = _backfill_one(cfg, SKU)
    assert result["ok"] is True
    assert result["skipped"] is True
    assert "404" in result["reason"]


def test_backfill_one_http_error_is_error(tmp_path, monkeypatch):
    """Non-404 HTTP errors (e.g. 500) are reported as errors (ok=False)."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    def _raise_500(cfg, url):
        resp = MagicMock()
        resp.status_code = 500
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", _raise_500)

    result = _backfill_one(cfg, SKU)
    assert result["ok"] is False
    assert not result.get("skipped")


def test_backfill_one_missing_json(tmp_path, monkeypatch):
    """Non-existent item JSON is reported as an error."""
    root = tmp_path / "ItemData"
    root.mkdir()
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: {})

    result = _backfill_one(cfg, "tgw_no_such_sku")
    assert result["ok"] is False
    assert not result.get("skipped")
    assert "not found" in result["reason"]


# ---------------------------------------------------------------------------
# cmd_ebay_backfill_snapshot integration tests
# ---------------------------------------------------------------------------


def test_cmd_dry_run_no_api_calls(tmp_path, monkeypatch):
    """--dry-run scans and reports without calling ebay_get."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: called.append(a))
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)

    result = cmd_ebay_backfill_snapshot(cfg, dry_run=True)
    assert result["ok"] is True
    assert result["total_candidates"] == 1
    assert result["saved_count"] == 1
    assert called == []


def test_cmd_saves_candidate(tmp_path, monkeypatch):
    """Live run saves ebay_submitted for a qualifying item."""
    root = tmp_path / "ItemData"
    root.mkdir()
    path = _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr(
        "tgw.ebay.snapshot_backfill.ebay_get",
        lambda cfg, url: _FAKE_INV_RESPONSE,
    )
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)

    # Suppress catalog_rebuild enqueue
    monkeypatch.setattr(
        "tgw.ebay.snapshot_backfill.state_machine",
        MagicMock(),
        raising=False,
    )

    result = cmd_ebay_backfill_snapshot(cfg)
    assert result["ok"] is True
    assert result["saved_count"] == 1
    assert SKU in result["saved"]

    saved = json.loads(path.read_text())
    assert saved["ebay_submitted"]["inventory_item"] == _FAKE_INV_RESPONSE


def test_cmd_skips_already_submitted(tmp_path, monkeypatch):
    """Items with existing ebay_submitted are not in candidates."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_ALREADY_HAS_SUBMITTED)
    cfg = _make_cfg(tmp_path)

    called = []
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: called.append(a))
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)

    result = cmd_ebay_backfill_snapshot(cfg)
    assert result["ok"] is True
    assert result["total_candidates"] == 0
    assert called == []


def test_cmd_limit_respected(tmp_path, monkeypatch):
    """--limit N stops after N items."""
    root = tmp_path / "ItemData"
    root.mkdir()
    for i in range(5):
        sku = f"tgw2026010112000000{i}"
        _write_item(
            root,
            sku,
            {
                "sku": sku,
                "title": f"Widget {i}",
                "ebay_listing": {"listing_id": f"ID{i}", "status": "Active"},
            },
        )
    cfg = _make_cfg(tmp_path)

    calls = []
    monkeypatch.setattr(
        "tgw.ebay.snapshot_backfill.ebay_get",
        lambda cfg, url: calls.append(url) or _FAKE_INV_RESPONSE,
    )
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.state_machine", MagicMock(), raising=False)

    result = cmd_ebay_backfill_snapshot(cfg, limit=2)
    assert result["ok"] is True
    assert result["saved_count"] == 2
    assert len(calls) == 2


def test_cmd_404_counted_as_not_in_api(tmp_path, monkeypatch):
    """Items returning 404 from eBay are counted in not_in_api, not errors."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    def _raise_404(cfg, url):
        resp = MagicMock()
        resp.status_code = 404
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", _raise_404)
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)

    result = cmd_ebay_backfill_snapshot(cfg)
    assert result["ok"] is True
    assert SKU in result["not_in_api"]
    assert result["saved_count"] == 0
    assert len(result["errors"]) == 0


def test_cmd_empty_catalog(tmp_path, monkeypatch):
    """Empty ItemData returns ok=True with 0 candidates."""
    root = tmp_path / "ItemData"
    root.mkdir()
    cfg = _make_cfg(tmp_path)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", lambda *a, **k: {})

    result = cmd_ebay_backfill_snapshot(cfg)
    assert result["ok"] is True
    assert result["total_candidates"] == 0
    assert result["saved_count"] == 0


def test_cmd_errors_set_ok_false(tmp_path, monkeypatch):
    """HTTP 500 errors set ok=False."""
    root = tmp_path / "ItemData"
    root.mkdir()
    _write_item(root, SKU, _ITEM_NEEDS_BACKFILL)
    cfg = _make_cfg(tmp_path)

    def _raise_500(cfg, url):
        resp = MagicMock()
        resp.status_code = 500
        raise requests.exceptions.HTTPError(response=resp)

    monkeypatch.setattr("tgw.ebay.snapshot_backfill.ebay_get", _raise_500)
    monkeypatch.setattr("tgw.ebay.snapshot_backfill.time.sleep", lambda s: None)

    result = cmd_ebay_backfill_snapshot(cfg)
    assert result["ok"] is False
    assert len(result["errors"]) == 1
