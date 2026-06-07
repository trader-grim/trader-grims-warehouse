"""PP-FULFILLMENT-001 — tests for the real `tgw picklist` CLI.

cmd_picklist groups/sorts items by warehouse location (unlocated last) and
resolves the best eBay id per item. list_items is stubbed so the test is pure.
"""

import tgw.api as api

_ITEMS = [
    {"sku": "tgw003", "title": "Gamma", "location": "B2",
     "ebay_listing": {"listing_id": "333"}},
    {"sku": "tgw001", "title": "Alpha", "location": "A1", "Item number": "111"},
    {"sku": "tgw002", "title": "Beta", "location": "A1"},
    {"sku": "tgw004", "title": "Unlocated thing", "location": ""},
]


def test_picklist_groups_and_sorts(monkeypatch):
    monkeypatch.setattr(api, "list_items",
                        lambda cfg, **kw: {"ok": True, "count": len(_ITEMS), "items": _ITEMS})
    out = api.cmd_picklist({})
    assert out["ok"] is True
    assert out["count"] == 4
    assert out["locations"] == 3  # A1, B2, '' (unlocated)

    order = [(r["location"], r["sku"]) for r in out["picklist"]]
    # location-sorted ascending; unlocated ('') sorts LAST; sku-sorted within loc.
    assert order == [("A1", "tgw001"), ("A1", "tgw002"),
                     ("B2", "tgw003"), ("", "tgw004")]


def test_picklist_resolves_ebay_id(monkeypatch):
    monkeypatch.setattr(api, "list_items",
                        lambda cfg, **kw: {"ok": True, "count": len(_ITEMS), "items": _ITEMS})
    by_sku = {r["sku"]: r["ebay_id"] for r in api.cmd_picklist({})["picklist"]}
    assert by_sku["tgw003"] == "333"   # pipeline listing_id
    assert by_sku["tgw001"] == "111"   # legacy "Item number"
    assert by_sku["tgw002"] == ""      # neither


def test_picklist_passes_filters_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(api, "list_items",
                        lambda cfg, **kw: seen.update(kw) or {"ok": True, "items": []})
    api.cmd_picklist({}, status="Sold", location="A1", search="widget")
    assert seen["status"] == "Sold"
    assert seen["location"] == "A1"
    assert seen["search"] == "widget"


def test_picklist_empty(monkeypatch):
    monkeypatch.setattr(api, "list_items",
                        lambda cfg, **kw: {"ok": True, "count": 0, "items": []})
    out = api.cmd_picklist({})
    assert out["ok"] is True
    assert out["count"] == 0
    assert out["picklist"] == []
