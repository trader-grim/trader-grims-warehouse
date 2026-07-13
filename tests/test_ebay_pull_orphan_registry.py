"""
Tests for the orphaned-listing registry (invariant C11 — a skip/guard is a
finding, not a log line).

`sync_active_listings()` counts active eBay listings that have no
`custom_label`, or a `custom_label` with no matching local ItemData record,
as "orphans". Before this fix that list was discarded after being counted
(only a `log.warning`/`print` per hit) — an operator had no durable,
queryable way to find and act on the specific orphaned listings later.

These tests assert the findings are persisted to `pull.ORPHAN_REGISTRY`
(a JSON registry file, same pattern as workers/ebay_sku_migrate.py's
migrate-blocked.json), keyed by listing_id, and stay queryable across runs.

All tests run offline — no eBay API calls, no database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest

import tgw.ebay.pull as pull


def _write_item(root: Path, sku: str, doc: dict) -> Path:
    d = root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _listing(listing_id: str, custom_label: str, price: str = "9.99",
             title: str = "Some Item") -> Dict[str, Any]:
    return {
        "listing_id": listing_id,
        "custom_label": custom_label,
        "listing_url": f"https://ebay.com/itm/{listing_id}",
        "status": "Active",
        "live_price": price,
        "title": title,
    }


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    """Point the module-level registry path at a throwaway file per test."""
    registry_path = tmp_path / "ebay-orphan-listings.json"
    monkeypatch.setattr(pull, "ORPHAN_REGISTRY", registry_path)
    monkeypatch.setattr(pull.tgw_logging, "log_event", lambda *a, **k: None)
    from tests.conftest import make_fake_fence_write_tmp, make_fake_patch_item_tmp
    monkeypatch.setattr(pull, 'fence_ebay_write', make_fake_fence_write_tmp(tmp_path))
    monkeypatch.setattr(pull, 'fence_patch_item', make_fake_patch_item_tmp(tmp_path))
    return registry_path


def test_orphans_persisted_not_just_counted(tmp_path, _isolated_registry):
    """Two orphaned listings (no local ItemData, no custom_label) get written
    to the durable registry, not just counted in the returned stats dict."""
    listings = [
        _listing("L100", "tgwNOTFOUND", title="Missing Local Item"),
        _listing("L200", "", title="No SKU At All"),
    ]
    synced_at = "2026-07-13T00:00:00Z"

    with patch.object(pull, "get_my_ebay_selling", return_value=listings):
        stats = pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, synced_at,
        )

    assert stats["orphaned"] == 2
    # the finding must be queryable later — read it back from disk fresh,
    # not just trust the in-memory stats dict this call returned.
    assert _isolated_registry.exists()
    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))

    assert set(registry.keys()) == {"L100", "L200"}
    assert registry["L100"]["custom_label"] == "tgwNOTFOUND"
    assert registry["L100"]["title"] == "Missing Local Item"
    assert registry["L100"]["first_seen"] == synced_at
    assert registry["L100"]["last_seen"] == synced_at
    assert registry["L100"]["seen_count"] == 1
    assert registry["L200"]["custom_label"] == ""


def test_orphan_seen_again_updates_last_seen_and_count(tmp_path, _isolated_registry):
    """A still-unresolved orphan seen on a later run updates last_seen/seen_count
    but keeps its original first_seen — the registry tracks recurrence."""
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L100", "tgwNOTFOUND")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-01T00:00:00Z",
        )
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-13T00:00:00Z",
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    assert registry["L100"]["first_seen"] == "2026-07-01T00:00:00Z"
    assert registry["L100"]["last_seen"] == "2026-07-13T00:00:00Z"
    assert registry["L100"]["seen_count"] == 2


def test_resolved_orphan_pruned_on_full_scan(tmp_path, _isolated_registry):
    """Once a listing is no longer orphaned (e.g. the local item now exists),
    a full (unfiltered) run removes it from the registry rather than leaving
    a stale entry forever."""
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L100", "tgw001")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-01T00:00:00Z",
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    assert "L100" in registry

    # local item now exists — listing L100 is no longer an orphan
    _write_item(tmp_path, "tgw001", {"status": "available"})
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L100", "tgw001")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-13T00:00:00Z",
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    assert "L100" not in registry


def test_filtered_run_does_not_prune_unseen_entries(tmp_path, _isolated_registry):
    """A sku_filter'd (partial) run must not delete orphans it never looked
    at just because they weren't in this run's smaller listing set."""
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L100", "tgwNOTFOUND")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-01T00:00:00Z",
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    assert "L100" in registry

    # a filtered run for an unrelated SKU set shouldn't touch L100's entry
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L100", "tgwNOTFOUND")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-13T00:00:00Z",
            sku_filter={"tgw999"},
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    assert "L100" in registry


def test_registry_is_queryable_json_keyed_by_listing_id(tmp_path, _isolated_registry):
    """Registry shape matches migrate-blocked.json's convention: a flat JSON
    object keyed by a stable identifier, directly greppable/loadable — not a
    log line that rots in journald."""
    with patch.object(pull, "get_my_ebay_selling",
                       return_value=[_listing("L555", "tgwGONE", price="49.99",
                                               title="Vintage Widget")]):
        pull.sync_active_listings(
            {"pretty": False, "api_key": "test-api-key"}, tmp_path, "2026-07-13T00:00:00Z",
        )

    registry = json.loads(_isolated_registry.read_text(encoding="utf-8"))
    entry = registry["L555"]
    assert entry["listing_id"] == "L555"
    assert entry["live_price"] == "49.99"
    assert entry["title"] == "Vintage Widget"
    assert entry["listing_url"] == "https://ebay.com/itm/L555"
