"""
PP-FENCE-001 — Tests for fence endpoints + fence.py client.

Three new endpoints in http_server.py:
  POST /api/items/{sku}/append      — typed list append
  POST /api/items/{sku}/ebay-write  — deep-merge eBay blocks
  POST /api/items                   — item creation

Plus smoke tests for tgw.apis.fence client (mock HTTP via responses library,
or via FastAPI TestClient where appropriate).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

httpx = pytest.importorskip("httpx", reason="httpx required by TestClient")
from fastapi.testclient import TestClient  # noqa: E402

from tgw import http_server  # noqa: E402

API_KEY = "test-key-fence-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}

SKU = "tgw20260101120000001"


# ---------------------------------------------------------------------------
# Shared helpers from test_http_server pattern
# ---------------------------------------------------------------------------

class _FakeCursor:
    def __init__(self): self._rows = []
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, *a, **k): pass
    def fetchall(self): return []
    def fetchone(self): return (0,)

class _FakeConn:
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def cursor(self, *a, **k): return _FakeCursor()


def _write_item(itemdata_root: Path, sku: str, doc: dict) -> Path:
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc))
    return p


def _read_item(itemdata_root: Path, sku: str) -> dict:
    return json.loads((itemdata_root / sku / f"{sku}.json").read_text())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def enqueue_calls(monkeypatch):
    calls = []
    def _fake(*a, **k):
        calls.append(k)
        return "job-fence-0001"
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", _fake)
    return calls


@pytest.fixture
def env(tmp_path, monkeypatch, enqueue_calls):
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()

    cfg = {
        "itemdata_root": itemdata_root,
        "location_tree_root": tmp_path / "loctree",
        "thumbnail_root": tmp_path / "thumbs",
        "sqlite_catalog_path": tmp_path / "catalog.sqlite",
        "category_groups_path": str(tmp_path / "groups.json"),
        "plan_vault_path": tmp_path / "vault",
        "plan_inbox_path": tmp_path / "vault" / "inbox",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": False,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_key", "")
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _FakeConn())

    _write_item(itemdata_root, SKU, {
        "sku": SKU,
        "title": "Test Item",
        "location": "A1",
        "ebay_offer": {"price": "9.99", "price_comps": {"source": "browse"}},
        "ebay_listing": {"listing_id": "123", "photo_verify": True},
        "title_history": [],
        "vision_results": [],
        "price_history": [],
    })

    client = TestClient(http_server.app, raise_server_exceptions=True)
    return {"client": client, "itemdata_root": itemdata_root, "enqueue_calls": enqueue_calls}


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/append
# ---------------------------------------------------------------------------

class TestAppend:
    def test_append_vision_result(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "vision_result", "data": {"model": "qwen2.5-vl", "title": "Found"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True
        doc = _read_item(env["itemdata_root"], SKU)
        assert len(doc["vision_results"]) == 1
        assert doc["vision_results"][0]["title"] == "Found"
        assert "appended_at" in doc["vision_results"][0]

    def test_append_price_event(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "price_event", "data": {"price": "7.99", "stage": "move"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        doc = _read_item(env["itemdata_root"], SKU)
        assert doc["price_history"][0]["price"] == "7.99"

    def test_append_history_event_title(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "history_event", "data": {"type": "title", "old": "A", "new": "B"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        doc = _read_item(env["itemdata_root"], SKU)
        assert len(doc["title_history"]) == 1
        assert doc["title_history"][0]["new"] == "B"

    def test_append_invalid_op(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "bogus_op", "data": {}},
            headers=AUTH,
        )
        assert r.status_code == 400

    def test_append_history_event_missing_type(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "history_event", "data": {"old": "X"}},
            headers=AUTH,
        )
        assert r.status_code == 400

    def test_append_404(self, env):
        r = env["client"].post(
            "/api/items/tgw20260101999999999/append",
            json={"op": "photo", "data": {"url": "http://x"}},
            headers=AUTH,
        )
        assert r.status_code == 404

    def test_append_enqueues_catalog_rebuild(self, env):
        env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "price_event", "data": {"price": "5.00"}},
            headers=AUTH,
        )
        assert any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/ebay-write
# ---------------------------------------------------------------------------

class TestEbayWrite:
    def test_merge_offer(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={"ebay_offer": {"offer_id": "off001", "listing_id": "list001", "price": "9.99"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["changed_fields"] == ["ebay_offer"]
        doc = _read_item(env["itemdata_root"], SKU)
        assert doc["ebay_offer"]["offer_id"] == "off001"
        # price_comps must be preserved (protected sub-field)
        assert doc["ebay_offer"]["price_comps"] == {"source": "browse"}

    def test_merge_listing_preserves_photo_verify(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={"ebay_listing": {"listing_id": "list002", "listing_status": "ACTIVE"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        doc = _read_item(env["itemdata_root"], SKU)
        # photo_verify must be preserved
        assert doc["ebay_listing"]["photo_verify"] is True
        assert doc["ebay_listing"]["listing_status"] == "ACTIVE"

    def test_multiple_blocks(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={
                "ebay_offer": {"offer_id": "o1"},
                "ebay_listing": {"listing_id": "l1"},
            },
            headers=AUTH,
        )
        assert r.status_code == 200
        assert set(r.json()["changed_fields"]) == {"ebay_offer", "ebay_listing"}

    def test_no_blocks_rejected(self, env):
        r = env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={},
            headers=AUTH,
        )
        assert r.status_code == 400

    def test_404(self, env):
        r = env["client"].post(
            "/api/items/tgw20260101999999999/ebay-write",
            json={"ebay_offer": {"offer_id": "x"}},
            headers=AUTH,
        )
        assert r.status_code == 404

    def test_enqueues_catalog_rebuild(self, env):
        env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={"ebay_offer": {"price": "8.00"}},
            headers=AUTH,
        )
        assert any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])


# ---------------------------------------------------------------------------
# POST /api/items — item creation
# ---------------------------------------------------------------------------

NEW_SKU = "tgw20260628110000042"


class TestCreateItem:
    def test_create_success(self, env):
        r = env["client"].post(
            "/api/items",
            json={"sku": NEW_SKU, "data": {"title": "Brand New", "location": "C3"}},
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["sku"] == NEW_SKU
        doc = _read_item(env["itemdata_root"], NEW_SKU)
        assert doc["sku"] == NEW_SKU
        assert doc["title"] == "Brand New"

    def test_create_conflict(self, env):
        # SKU already seeded in fixture
        r = env["client"].post(
            "/api/items",
            json={"sku": SKU, "data": {"title": "Duplicate"}},
            headers=AUTH,
        )
        assert r.status_code == 409

    def test_create_invalid_sku(self, env):
        r = env["client"].post(
            "/api/items",
            json={"sku": "bad-sku-format", "data": {}},
            headers=AUTH,
        )
        assert r.status_code == 400

    def test_create_enqueues_catalog_rebuild(self, env):
        env["client"].post(
            "/api/items",
            json={"sku": NEW_SKU, "data": {"title": "X"}},
            headers=AUTH,
        )
        assert any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])

    def test_create_requires_auth(self, env):
        r = env["client"].post(
            "/api/items",
            json={"sku": NEW_SKU, "data": {}},
        )
        assert r.status_code in (401, 403)
