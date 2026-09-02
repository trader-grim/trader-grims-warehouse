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
from tgw.item_mutation import item_generation  # noqa: E402

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
    monkeypatch.setattr(http_server, "_web_password", "")
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

    def test_append_no_longer_enqueues_catalog_rebuild(self, env):
        """PP-CATALOG-INCR-001 CI-4 (2026-07-18): enqueue_catalog_rebuild() is
        now a no-op — the fence's SQLite upsert (CI-2) keeps the catalog live
        instead of a per-write queue job."""
        env["client"].post(
            f"/api/items/{SKU}/append",
            json={"op": "price_event", "data": {"price": "5.00"}},
            headers=AUTH,
        )
        assert not any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])


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
        assert r.json()["resulting_generation"] == item_generation(doc)
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

    def test_no_longer_enqueues_catalog_rebuild(self, env):
        """PP-CATALOG-INCR-001 CI-4 (2026-07-18): see TestAppend's identical note."""
        env["client"].post(
            f"/api/items/{SKU}/ebay-write",
            json={"ebay_offer": {"price": "8.00"}},
            headers=AUTH,
        )
        assert not any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])


MACHINE_KEY = "test-machine-key-fence-001"
MACHINE_AUTH = {"Authorization": f"Bearer {MACHINE_KEY}"}


class TestSoldEvidence:
    """PP-SOLD-001 / Todo #1966 — the sanctioned machine sold-marking route."""

    def _seed_listed(self, env):
        _write_item(env["itemdata_root"], SKU, {
            "sku": SKU,
            "title": "Test Item",
            "status": "listed",
            "draft_listing": {"quantity": 2, "title": "Draft Title",
                              "description": "Draft body", "price": "19.99"},
            "ebay_listing": {"listing_id": "227407776039", "status": "Active"},
        })

    def test_sellout_marks_sold_without_touching_draft(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        self._seed_listed(env)
        sale = [{"order_id": "O-1", "buyer": "bob", "sale_price": 19.99,
                 "quantity": 2, "sale_date": "2026-08-25", "synced_at": "2026-08-25T00:00:00Z"}]
        r = env["client"].post(
            f"/api/items/{SKU}/sold-evidence",
            json={"ebay_sale": sale, "sold_out": True},
            headers=MACHINE_AUTH,
        )
        assert r.status_code == 200, r.text
        doc = _read_item(env["itemdata_root"], SKU)
        assert doc["status"] == "sold"
        assert doc["ebay_sale"] == sale
        assert doc["ebay_listing"]["status"] == "Sold"
        assert doc["ebay_listing"]["listing_id"] == "227407776039"
        assert doc["draft_listing"]["quantity"] == 0
        # draft content preserved verbatim
        assert doc["draft_listing"]["title"] == "Draft Title"
        assert doc["draft_listing"]["description"] == "Draft body"
        assert doc["draft_listing"]["price"] == "19.99"
        assert set(r.json()["changed_fields"]) == {"ebay_sale", "status", "ebay_listing", "draft_listing"}

    def test_partial_sale_only_decrements_quantity(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        self._seed_listed(env)
        sale = [{"order_id": "O-2", "quantity": 1}]
        r = env["client"].post(
            f"/api/items/{SKU}/sold-evidence",
            json={"ebay_sale": sale, "remaining_quantity": 1},
            headers=MACHINE_AUTH,
        )
        assert r.status_code == 200, r.text
        doc = _read_item(env["itemdata_root"], SKU)
        assert doc["status"] == "listed"
        assert doc["ebay_listing"]["status"] == "Active"
        assert doc["draft_listing"]["quantity"] == 1
        assert doc["draft_listing"]["title"] == "Draft Title"

    def test_oversold_records_only_ebay_sale(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        _write_item(env["itemdata_root"], SKU, {
            "sku": SKU, "status": "sold",
            "draft_listing": {"quantity": 0, "title": "Draft Title"},
            "ebay_listing": {"listing_id": "227407776039", "status": "Sold"},
            "ebay_sale": [{"order_id": "O-1"}],
        })
        sale = [{"order_id": "O-1"}, {"order_id": "O-3"}]
        r = env["client"].post(
            f"/api/items/{SKU}/sold-evidence",
            json={"ebay_sale": sale},
            headers=MACHINE_AUTH,
        )
        assert r.status_code == 200, r.text
        doc = _read_item(env["itemdata_root"], SKU)
        assert doc["ebay_sale"] == sale
        assert doc["draft_listing"]["quantity"] == 0
        assert r.json()["changed_fields"] == ["ebay_sale"]

    def test_rejects_non_machine_credential(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        self._seed_listed(env)
        r = env["client"].post(
            f"/api/items/{SKU}/sold-evidence",
            json={"ebay_sale": [], "sold_out": True},
            headers=AUTH,  # operator API key, not the machine credential
        )
        assert r.status_code == 403

    def test_generation_conflict_is_409(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        self._seed_listed(env)
        r = env["client"].post(
            f"/api/items/{SKU}/sold-evidence",
            json={"ebay_sale": [], "sold_out": True,
                  "expected_generation": "0" * 64},
            headers=MACHINE_AUTH,
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "generation_conflict"

    def test_404_for_unknown_sku(self, env, monkeypatch):
        monkeypatch.setattr(http_server, "_machine_api_key", MACHINE_KEY)
        r = env["client"].post(
            "/api/items/tgw20260101999999999/sold-evidence",
            json={"ebay_sale": [], "sold_out": True},
            headers=MACHINE_AUTH,
        )
        assert r.status_code == 404


class TestPatchCommittedGeneration:
    def test_response_binds_exact_committed_document(self, env):
        response = env["client"].patch(
            f"/api/items/{SKU}",
            json={"fields": {"title": "Committed title"}},
            headers=AUTH,
        )

        assert response.status_code == 200
        document = _read_item(env["itemdata_root"], SKU)
        assert response.json()["resulting_generation"] == item_generation(document)


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

    def test_create_no_longer_enqueues_catalog_rebuild(self, env):
        """PP-CATALOG-INCR-001 CI-4 (2026-07-18): enqueue_catalog_rebuild() is
        now a no-op — create_item_endpoint upserts the new SKU's SQLite
        catalog row directly instead."""
        env["client"].post(
            "/api/items",
            json={"sku": NEW_SKU, "data": {"title": "X"}},
            headers=AUTH,
        )
        assert not any(k.get("queue_name") == "catalog_rebuild" for k in env["enqueue_calls"])

    def test_create_requires_auth(self, env):
        r = env["client"].post(
            "/api/items",
            json={"sku": NEW_SKU, "data": {}},
        )
        assert r.status_code in (401, 403)
