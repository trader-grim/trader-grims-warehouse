"""PP-BULKEDIT-001 — unit tests for the shared bulk_edit core + `tgw bulk` CLI."""

import json

import pytest

import tgw.api as api
from tgw.items import bulk_edit


@pytest.fixture
def cfg(tmp_path):
    itemdata = tmp_path / "ItemData"
    itemdata.mkdir()
    # Leave location_tree_root absent so resolve() uses its JSON-scan fallback
    # for the `location` selector; locationupdate creates the tree on demand.
    return {
        "itemdata_root": itemdata,
        "location_tree_root": tmp_path / "loctree",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
    }


def _item(cfg, sku, doc):
    d = cfg["itemdata_root"] / sku
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sku}.json").write_text(json.dumps({"sku": sku, **doc}), encoding="utf-8")


def test_invalid_field_rejected(cfg):
    out = bulk_edit(cfg, {"location": "A1"}, "price", "9.99")
    assert out["ok"] is False
    assert "not editable" in out["error"]


def test_preview_does_not_write(cfg):
    _item(cfg, "tgw20260101120000001", {"title": "Old", "location": "A1"})
    out = bulk_edit(cfg, {"location": "A1"}, "title", "New Title")
    assert out["applied"] is False
    assert out["count"] == 1
    assert out["preview"][0]["current"] == "Old"
    assert out["preview"][0]["proposed"] == "New Title"
    # unchanged on disk
    doc = json.loads((cfg["itemdata_root"] / "tgw20260101120000001"
                      / "tgw20260101120000001.json").read_text())
    assert doc["title"] == "Old"


def test_apply_writes_title(cfg):
    sku = "tgw20260101120000002"
    _item(cfg, sku, {"title": "Old", "location": "A1"})
    out = bulk_edit(cfg, {"location": "A1"}, "title", "Brand New", apply=True)
    assert out["applied"] is True
    assert out["count"] == 1
    assert out["updated"] == [sku]
    doc = json.loads((cfg["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["title"] == "Brand New"


def test_apply_status_uses_legacy_key(cfg):
    sku = "tgw20260101120000003"
    _item(cfg, sku, {"title": "X Item Title", "location": "A1", "#STATUS": "In Stock"})
    bulk_edit(cfg, {"location": "A1"}, "status", "Out of Stock", apply=True)
    doc = json.loads((cfg["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["#STATUS"] == "Out of Stock"


def test_apply_location_moves_all(cfg):
    a = "tgw20260101120000004"
    b = "tgw20260101120000005"
    _item(cfg, a, {"title": "Item A Title Here", "location": "A1"})
    _item(cfg, b, {"title": "Item B Title Here", "location": "A1"})
    out = bulk_edit(cfg, {"location": "A1"}, "location", "SHELF9", apply=True)
    assert out["count"] == 2
    for sku in (a, b):
        doc = json.loads((cfg["itemdata_root"] / sku / f"{sku}.json").read_text())
        assert doc["location"] == "SHELF9"


def test_limit_caps_matches(cfg):
    for i in range(5):
        _item(cfg, f"tgw2026010112000001{i}", {"title": f"T{i} long enough", "location": "A1"})
    out = bulk_edit(cfg, {"location": "A1"}, "title", "Z", limit=2)
    assert out["count"] == 2


def test_explicit_skus_selector(cfg):
    a = "tgw20260101120000020"
    b = "tgw20260101120000021"
    _item(cfg, a, {"title": "A title here", "location": "A1"})
    _item(cfg, b, {"title": "B title here", "location": "B2"})
    out = bulk_edit(cfg, {"skus": [a]}, "title", "X")
    assert out["count"] == 1
    assert out["preview"][0]["sku"] == a


# ---------------------------------------------------------------------------
# cmd_bulk CLI wrapper
# ---------------------------------------------------------------------------

def test_cmd_bulk_requires_selector(cfg):
    out = api.cmd_bulk(cfg, field="title", value="X")
    assert out["ok"] is False
    assert "no selector" in out["error"]


def test_cmd_bulk_preview(cfg):
    _item(cfg, "tgw20260101120000030", {"title": "Old", "location": "A1"})
    out = api.cmd_bulk(cfg, field="title", value="New", location="A1")
    assert out["ok"] is True
    assert out["applied"] is False
    assert out["count"] == 1


def test_cmd_bulk_apply_enqueues_rebuild(cfg, monkeypatch):
    from tgw.queue import state_machine as sm
    sku = "tgw20260101120000031"
    _item(cfg, sku, {"title": "Old", "location": "A1"})
    calls = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "enqueue_job", lambda **kw: calls.append(kw) or "job-1")
    out = api.cmd_bulk(cfg, field="title", value="New", location="A1", apply=True)
    assert out["applied"] is True
    assert out["count"] == 1
    assert calls and calls[0]["queue_name"] == "catalog_rebuild"


# --- bug #007: partial-success must still write + rebuild ---

def test_bulk_partial_success_contract(cfg, monkeypatch):
    """One of two writes fails → ok=False but count reflects the successes."""
    a = "tgw20260101120000040"
    b = "tgw20260101120000041"
    _item(cfg, a, {"title": "A title here", "location": "A1"})
    _item(cfg, b, {"title": "B title here", "location": "A1"})
    import tgw.items as items

    real = items.update_item

    def flaky(cfg_, sku, field, value, **k):
        if sku == b:
            return {"ok": False, "sku": sku, "field": field, "error": "boom"}
        return real(cfg_, sku, field, value, **k)

    monkeypatch.setattr(items, "update_item", flaky)
    out = bulk_edit(cfg, {"location": "A1"}, "title", "X", apply=True)
    assert out["ok"] is False           # a failure occurred
    assert out["count"] == 1            # but one write succeeded
    assert out["updated"] == [a]
    assert len(out["failed"]) == 1


def test_cmd_bulk_enqueues_rebuild_on_partial_success(cfg, monkeypatch):
    """Rebuild must be gated on count, not ok — partial success still changed disk."""
    from tgw.queue import state_machine as sm
    calls = []
    monkeypatch.setattr(sm, "init", lambda *a, **k: None)
    monkeypatch.setattr(sm, "enqueue_job", lambda **kw: calls.append(kw) or "job-1")
    monkeypatch.setattr(
        "tgw.items.bulk_edit",
        lambda *a, **k: {"ok": False, "count": 5, "updated": ["s1"] * 5,
                         "failed": [{"sku": "sx", "error": "boom"}], "applied": True},
    )
    out = api.cmd_bulk(cfg, field="title", value="X", location="A1", apply=True)
    assert out["count"] == 5
    assert calls and calls[0]["queue_name"] == "catalog_rebuild"


# --- bug #008: negative limit must not slice from the end ---

def test_negative_limit_is_not_end_slice(cfg):
    for i in range(3):
        _item(cfg, f"tgw2026010112000005{i}", {"title": f"T{i} long enough", "location": "A1"})
    out = bulk_edit(cfg, {"location": "A1"}, "title", "Z", limit=-5)
    assert out["count"] == 3   # all matched, NOT skus[:-5] == []
