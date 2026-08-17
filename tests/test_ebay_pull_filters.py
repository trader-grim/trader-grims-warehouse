"""Tests for tgw ebay-pull --sku / --location / --status filter logic.

All tests run offline — no eBay API calls, no database.

Covered:
  * sync_active_listings with sku_filter — matching listings processed,
    non-matching skipped, orphan reporting unaffected for matching items
  * listing_index SKU filtering (the sold-orders filter path)
  * sku_filter=None passes all listings through (regression guard)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Set
from unittest.mock import patch

import pytest

import tgw.ebay.pull as pull

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_item(root: Path, sku: str, doc: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _listing(listing_id: str, custom_label: str, price: str = "9.99") -> Dict[str, Any]:
    return {
        "listing_id": listing_id,
        "custom_label": custom_label,
        "listing_url": f"https://ebay.com/itm/{listing_id}",
        "status": "Active",
        "live_price": price,
    }


@pytest.fixture(autouse=True)
def _silence_log_event(tmp_path, monkeypatch):
    monkeypatch.setattr(pull.tgw_logging, "log_event", lambda *a, **k: None)
    from tests.conftest import make_fake_fence_write_tmp, make_fake_patch_item_tmp
    monkeypatch.setattr(pull, 'fence_ebay_write', make_fake_fence_write_tmp(tmp_path))
    monkeypatch.setattr(pull, 'fence_patch_item', make_fake_patch_item_tmp(tmp_path))


# ---------------------------------------------------------------------------
# sync_active_listings — sku_filter
# ---------------------------------------------------------------------------

def test_no_filter_syncs_all_listings(tmp_path):
    """sku_filter=None (default) processes every listing."""
    _write_item(tmp_path, "tgw001", {"status": "available"})
    _write_item(tmp_path, "tgw002", {"status": "available"})

    listings = [_listing("L1", "tgw001"), _listing("L2", "tgw002")]
    synced_at = "2026-06-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling", return_value=listings):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at, sku_filter=None
        )

    assert stats["matched"] == 2
    assert stats["updated"] == 2


def test_sku_filter_restricts_to_matching_skus(tmp_path):
    """Only listings whose custom_label is in sku_filter are processed."""
    _write_item(tmp_path, "tgw001", {"status": "available"})
    _write_item(tmp_path, "tgw002", {"status": "available"})
    _write_item(tmp_path, "tgw003", {"status": "available"})

    listings = [
        _listing("L1", "tgw001"),
        _listing("L2", "tgw002"),
        _listing("L3", "tgw003"),
    ]
    synced_at = "2026-06-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling", return_value=listings):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at, sku_filter={"tgw001", "tgw003"}
        )

    assert stats["matched"] == 2
    assert stats["updated"] == 2
    # tgw002 was fetched but not processed
    assert stats["fetched"] == 3


def test_sku_filter_empty_set_syncs_nothing(tmp_path):
    """An empty sku_filter means no listings are processed."""
    _write_item(tmp_path, "tgw001", {"status": "available"})

    listings = [_listing("L1", "tgw001")]
    synced_at = "2026-06-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling", return_value=listings):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at, sku_filter=set()
        )

    assert stats["matched"] == 0
    assert stats["updated"] == 0
    assert stats["fetched"] == 1


def test_sku_filter_listing_with_no_custom_label_skipped(tmp_path):
    """A listing with no custom_label is excluded when a filter is active."""
    _write_item(tmp_path, "tgw001", {"status": "available"})

    listings = [
        _listing("L1", "tgw001"),
        _listing("L2", ""),           # no custom_label
    ]
    synced_at = "2026-06-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling", return_value=listings):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at, sku_filter={"tgw001"}
        )

    assert stats["matched"] == 1
    # listing with no custom_label was skipped (not counted as orphan)
    assert stats["orphaned"] == 0


def test_sku_filter_writes_listing_data_to_json(tmp_path):
    """Matching listing data is written back to the item JSON."""
    _write_item(tmp_path, "tgw001", {"status": "available"})
    synced_at = "2026-06-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling",
                      return_value=[_listing("L99", "tgw001", price="29.99")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at, sku_filter={"tgw001"}
        )

    doc = json.loads((tmp_path / "tgw001" / "tgw001.json").read_text())
    assert doc["ebay_listing"]["listing_id"] == "L99"
    assert doc["ebay_listing"]["live_price"] == "29.99"


def test_inventory_bound_sku_records_different_active_listing_as_conflict(tmp_path):
    """The account-wide Trading feed must not overwrite an Inventory binding."""
    _write_item(tmp_path, "tgw001", {
        "ebay_listing": {
            "api": "inventory", "listing_id": "inventory-listing",
            "listing_status": "ACTIVE",
        },
        "ebay_offer": {"offer_id": "offer-1"},
    })
    with patch.object(pull, "get_my_ebay_selling", return_value=[_listing("other-listing", "tgw001")]):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path,
            "2026-08-17T15:25:00Z", sku_filter={"tgw001"},
        )

    item = json.loads((tmp_path / "tgw001" / "tgw001.json").read_text())
    assert item["ebay_listing"]["listing_id"] == "inventory-listing"
    assert item["ebay_listing_conflict"] == {
        "schema": "ebay-listing-conflict/v1",
        "kind": "active_trading_listing_differs_from_inventory_binding",
        "sync_source": "trading_getmyebayselling",
        "inventory_listing_id": "inventory-listing",
        "inventory_listing_status": "ACTIVE",
        "inventory_offer_id": "offer-1",
        "trading_listing_id": "other-listing",
        "trading_listing_status": "Active",
        "detected_at": "2026-08-17T15:25:00Z",
    }
    assert stats["listing_conflicts"] == 1


# ---------------------------------------------------------------------------
# listing_index SKU filtering (sold-orders path)
# ---------------------------------------------------------------------------

def test_listing_index_filtered_by_sku_filter(tmp_path):
    """Filtering listing_index by sku_filter retains only matching SKU paths."""
    _write_item(tmp_path, "tgw001", {"ebay_listing": {"listing_id": "L1"}})
    _write_item(tmp_path, "tgw002", {"ebay_listing": {"listing_id": "L2"}})
    _write_item(tmp_path, "tgw003", {"ebay_listing": {"listing_id": "L3"}})

    full_index = pull.build_listing_index(tmp_path)
    sku_filter: Set[str] = {"tgw001", "tgw003"}

    filtered = {lid: p for lid, p in full_index.items()
                if p.parent.name in sku_filter}

    assert set(filtered.keys()) == {"L1", "L3"}
    assert "L2" not in filtered


def test_listing_index_filter_with_empty_set_gives_empty_index(tmp_path):
    _write_item(tmp_path, "tgw001", {"ebay_listing": {"listing_id": "L1"}})
    full_index = pull.build_listing_index(tmp_path)

    filtered = {lid: p for lid, p in full_index.items()
                if p.parent.name in set()}

    assert filtered == {}


def test_listing_index_filter_with_none_passes_all(tmp_path):
    """When sku_filter is None (no filtering), full index is used unchanged."""
    _write_item(tmp_path, "tgw001", {"ebay_listing": {"listing_id": "L1"}})
    _write_item(tmp_path, "tgw002", {"ebay_listing": {"listing_id": "L2"}})

    full_index = pull.build_listing_index(tmp_path)
    sku_filter = None

    result = (
        {lid: p for lid, p in full_index.items() if p.parent.name in sku_filter}
        if sku_filter is not None
        else full_index
    )

    assert set(result.keys()) == {"L1", "L2"}


# ---------------------------------------------------------------------------
# Location / status scan logic (unit test for filter-building pattern)
# ---------------------------------------------------------------------------

def test_location_filter_selects_matching_items(tmp_path):
    """Items matching the location filter are included; others excluded."""
    _write_item(tmp_path, "tgw001", {"status": "available", "location": "A1"})
    _write_item(tmp_path, "tgw002", {"status": "available", "location": "B3"})
    _write_item(tmp_path, "tgw003", {"status": "available", "location": "A1"})

    location_filter = "A1"
    matched: Set[str] = set()
    for jp in tmp_path.glob("*/*.json"):
        doc = json.loads(jp.read_text(encoding="utf-8"))
        if doc.get("location") == location_filter:
            matched.add(jp.parent.name)

    assert matched == {"tgw001", "tgw003"}


def test_status_filter_selects_matching_items(tmp_path):
    _write_item(tmp_path, "tgw001", {"status": "available", "location": "A1"})
    _write_item(tmp_path, "tgw002", {"status": "sold",      "location": "A1"})
    _write_item(tmp_path, "tgw003", {"status": "available", "location": "B2"})

    status_filter = "available"
    matched: Set[str] = set()
    for jp in tmp_path.glob("*/*.json"):
        doc = json.loads(jp.read_text(encoding="utf-8"))
        if doc.get("status") == status_filter:
            matched.add(jp.parent.name)

    assert matched == {"tgw001", "tgw003"}


def test_location_and_status_filters_intersect(tmp_path):
    """Both filters applied together — only items matching both are selected."""
    _write_item(tmp_path, "tgw001", {"status": "available", "location": "A1"})
    _write_item(tmp_path, "tgw002", {"status": "sold",      "location": "A1"})
    _write_item(tmp_path, "tgw003", {"status": "available", "location": "B2"})

    location_filter = "A1"
    status_filter = "available"
    matched: Set[str] = set()
    for jp in tmp_path.glob("*/*.json"):
        doc = json.loads(jp.read_text(encoding="utf-8"))
        if doc.get("location") != location_filter:
            continue
        if doc.get("status") != status_filter:
            continue
        matched.add(jp.parent.name)

    assert matched == {"tgw001"}


def test_sku_and_location_filter_intersection(tmp_path):
    """--sku + --location: result is the intersection of both constraints."""
    _write_item(tmp_path, "tgw001", {"status": "available", "location": "A1"})
    _write_item(tmp_path, "tgw002", {"status": "available", "location": "B2"})
    _write_item(tmp_path, "tgw003", {"status": "available", "location": "A1"})

    explicit_skus: Set[str] = {"tgw001", "tgw002"}
    location_filter = "A1"

    scan_matched: Set[str] = set()
    for jp in tmp_path.glob("*/*.json"):
        doc = json.loads(jp.read_text(encoding="utf-8"))
        if doc.get("location") == location_filter:
            scan_matched.add(jp.parent.name)

    sku_filter = explicit_skus & scan_matched  # intersection
    assert sku_filter == {"tgw001"}
