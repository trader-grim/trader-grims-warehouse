"""
PP-EDITOR-001 — FastAPI TestClient suite for tgw.http_server.

These tests exercise the HTTP handlers against ACTUAL current behavior with
all external systems stubbed:
  * module-level ``_cfg`` points at a tmp_path itemdata_root + a tiny SQLite
    catalog matching the real column layout (sku,title,location,status,
    price,qty,image) used by ``list_items`` (http_server.py:177);
  * ``state_machine.enqueue_job`` is replaced with a recorder so the coalesced
    catalog_rebuild enqueue path runs without PostgreSQL;
  * ``psycopg2.connect`` is replaced with a fake connection so the queue-jobs
    fetch in ``get_item`` returns deterministic rows without a real database.

No real PostgreSQL, eBay, network, or secrets access occurs.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import subprocess
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2.errors
import pytest

# TestClient (starlette) requires httpx at import/use time. If httpx is not
# importable in the venv we skip the entire module with a clear reason rather
# than producing a misleading collection error.
httpx = pytest.importorskip(
    "httpx", reason="httpx is required by fastapi.testclient.TestClient"
)

from fastapi.testclient import TestClient  # noqa: E402

from tgw import http_server, inventory_record  # noqa: E402
from tgw.ebay import draft_specifics  # noqa: E402

API_KEY = "test-key-abc123"
WEB_KEY = "test-web-key-xyz"  # browser login password (checked against _web_password)
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


def _login(client):
    """Give a TestClient a valid /form/* browser session.

    session 42/43 added a login wall (`_session_guard` middleware + the
    `tgw_session` cookie) in front of every `/form/*` path — a bare Bearer
    header no longer gets past it, only a cookie the middleware recognizes
    in `http_server._sessions`. Injecting the session directly (rather than
    POSTing /login) keeps these tests fast and independent of the password
    hashing/comparison path, which has its own coverage elsewhere.
    """
    tok = f"test-session-{id(client)}"
    http_server._sessions[tok] = time.time() + 3600
    client.cookies.set(http_server._SESSION_COOKIE, tok)
    return client


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Minimal psycopg2-ish cursor that returns canned queue-job rows."""

    def __init__(self, rows, rowcount=1):
        self._rows = rows
        self.rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return None

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else (0,)


class _FakeConn:
    def __init__(self, rows, rowcount=1):
        self._rows = rows
        self._rowcount = rowcount

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._rows, rowcount=self._rowcount)


def _make_catalog(db_path: Path, rows):
    """Create a SQLite catalog matching http_server's expected columns.

    Each row is a 7-tuple: (sku, title, location, status, price, qty, image).
    A ``data`` column (TEXT, JSON) is required for json_extract in list_items;
    we seed it with a minimal ``{}`` payload unless the caller passes extra.
    """
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE catalog ("
        "sku TEXT, title TEXT, location TEXT, status TEXT, "
        "price REAL, qty INTEGER, image TEXT, attribute_set TEXT, data TEXT)"
    )
    con.executemany(
        "INSERT INTO catalog (sku, title, location, status, price, qty, image, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '{}')",
        rows,
    )
    con.commit()
    con.close()


def _write_item(itemdata_root: Path, sku: str, doc: dict) -> Path:
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sku}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# Two well-formed TGW SKUs (tgwYYYYMMDDHHMMSSmmm) for date-substr filtering.
SKU_A = "tgw20260101120000001"
SKU_B = "tgw20260315090000002"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def enqueue_calls(monkeypatch):
    """Record every state_machine.enqueue_job call; return canned job_id."""
    calls = []

    def _fake_enqueue(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return "job-fake-0001"

    monkeypatch.setattr(http_server.state_machine, "enqueue_job", _fake_enqueue)
    return calls


@pytest.fixture
def queue_rows():
    """Mutable container for the rows the fake psycopg2 connection returns."""
    return []


@pytest.fixture
def env(tmp_path, monkeypatch, queue_rows):
    """
    Build a self-contained config, stub psycopg2.connect, set the module API
    key, and seed the catalog + two items. Yields a dict of useful handles.
    """
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()
    location_tree_root = tmp_path / "loctree"
    location_tree_root.mkdir()
    thumbnail_root = tmp_path / "thumbs"
    thumbnail_root.mkdir()
    catalog_path = tmp_path / "catalog.sqlite"
    groups_path = tmp_path / "category-groups.json"

    # Catalog rows: (sku, title, location, status, price, qty, image)
    _make_catalog(
        catalog_path,
        [
            (SKU_A, "Red Widget", "A1", "In Stock", 9.99, 1, "a.jpg"),
            (SKU_B, "Blue Gadget", "B2", "Staged", 19.99, 2, "b.jpg"),
            ("tgw20260201000000003", "Empty Loc Item", "", "In Stock",
             None, 1, ""),
        ],
    )

    # Category-groups template table.
    groups_path.write_text(
        json.dumps({
            "groups": {
                "books": {
                    "name": "Books",
                    "size_class": "small",
                    "ai_hint": "printed book",
                    "ebay_categories": ["261186"],
                    "pricing": {"floor": 3.0, "typical_used": 8.0},
                },
                "vinyl": {
                    "name": "Vinyl Records",
                    "size_class": "medium",
                    "ai_hint": "12 inch record",
                    "ebay_categories": [],
                    "pricing": {},
                },
            }
        }),
        encoding="utf-8",
    )

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": itemdata_root,
        "location_tree_root": location_tree_root,
        "thumbnail_root": thumbnail_root,
        "category_groups_path": str(groups_path),
        "plan_vault_path": tmp_path / "vault",
        "plan_inbox_path": tmp_path / "vault" / "inbox",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
        "raw": {},
    }

    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)

    # No real PostgreSQL: psycopg2.connect returns our fake connection.
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(queue_rows),
    )

    # Seed two item JSON docs (one with media files).
    _write_item(itemdata_root, SKU_A, {
        "sku": SKU_A,
        "title": "Red Widget",
        "location": "A1",
        "condition": "Good",
        "catalog_verified": True,
    })
    item_b_dir = itemdata_root / SKU_B
    _write_item(itemdata_root, SKU_B, {
        "sku": SKU_B,
        "title": "Blue Gadget",
        "location": "B2",
    })
    # Media files for SKU_B for the media-list assembly in get_item.
    (item_b_dir / "front.jpg").write_bytes(b"fake")
    (item_b_dir / "back.PNG").write_bytes(b"fake")
    (item_b_dir / "clip.mp4").write_bytes(b"fake")
    (item_b_dir / "notes.txt").write_bytes(b"ignored")

    client = TestClient(http_server.app)

    return {
        "client": client,
        "cfg": cfg,
        "itemdata_root": itemdata_root,
        "location_tree_root": location_tree_root,
        "queue_rows": queue_rows,
        "groups_path": groups_path,
    }


@pytest.fixture
def client(env):
    return env["client"]


# ---------------------------------------------------------------------------
# GET /api/queue/daily_stats — PP-QUEUESTATS-001 date-scoped daily stats
# ---------------------------------------------------------------------------


def test_queue_daily_stats_requires_auth(client):
    r = client.get("/api/queue/daily_stats")
    assert r.status_code in (401, 403)


def test_queue_daily_stats_rejects_bad_date(client):
    r = client.get("/api/queue/daily_stats", params={"date": "not-a-date"},
                    headers=AUTH_HEADERS)
    assert r.status_code == 400


def test_queue_daily_stats_returns_date_scoped_per_queue_counts(client, queue_rows):
    import datetime as _dt

    hour = _dt.datetime(2026, 7, 17, 9, 0, 0)
    queue_rows.extend([
        {"queue_name": "ebay_upload", "stat_hour": hour, "state": "succeeded", "job_count": 12},
        {"queue_name": "ebay_upload", "stat_hour": hour, "state": "failed", "job_count": 1},
        {"queue_name": "ebay_upload", "stat_hour": hour, "state": "dead_letter", "job_count": 2},
        {"queue_name": "ebay_draft", "stat_hour": hour, "state": "succeeded", "job_count": 5},
    ])
    r = client.get("/api/queue/daily_stats", params={"date": "2026-07-17"},
                    headers=AUTH_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["date"] == "2026-07-17"
    assert data["tz"] == "America/Los_Angeles"
    assert data["queues"]["ebay_upload"]["succeeded"] == 12
    assert data["queues"]["ebay_upload"]["failed"] == 1
    assert data["queues"]["ebay_upload"]["dead_letter"] == 2
    # Per-hour breakdown is kept, not collapsed — groundwork for future
    # surge/anomaly detection per PP-QUEUESTATS-001.
    assert len(data["queues"]["ebay_upload"]["by_hour"]) == 3
    assert data["queues"]["ebay_draft"]["succeeded"] == 5
    assert data["queues"]["ebay_draft"]["failed"] == 0


def test_queue_daily_stats_defaults_to_today_when_no_date_given(client, queue_rows, monkeypatch):
    captured = {}

    class _CapturingCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            captured["params"] = params

        def fetchall(self):
            return []

    class _CapturingConn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self, *a, **k):
            return _CapturingCursor()

    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _CapturingConn())
    r = client.get("/api/queue/daily_stats", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert captured["params"][0] == http_server.datetime.now(http_server._DISPLAY_TZ).date()


def test_pipeline_page_shows_date_scoped_column_labels(client):
    """The old 'Done today'/'Failed/DL' pairing collapsed a lifetime count
    under a daily label (PP-QUEUESTATS-001). The rebuilt page must be
    explicit about which columns are date-scoped vs. lifetime."""
    _login(client)
    r = client.get("/form/pipeline")
    assert r.status_code == 200
    assert "Done today" in r.text
    assert "Failed today" in r.text
    assert "DL backlog" in r.text
    assert "/api/queue/daily_stats" in r.text


# ---------------------------------------------------------------------------
# Auth (HTTPBearer) — bad/missing token
# ---------------------------------------------------------------------------

def test_bad_token_rejected_401(client):
    r = client.get("/api/items", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid token"


def test_missing_token_rejected(client):
    # No Authorization header → rejected before the handler runs. This
    # Starlette/FastAPI build returns 401 for a missing bearer credential
    # (older versions returned 403); accept either to stay version-robust.
    r = client.get("/api/items")
    assert r.status_code in (401, 403)


def test_bearer_auth_uses_constant_time_compare(client, monkeypatch):
    """#1282 — bearer-token check must use secrets.compare_digest, not `==`,
    to close the timing side-channel the password check already avoided."""
    calls = []
    real_compare_digest = http_server.secrets.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real_compare_digest(a, b)

    monkeypatch.setattr(http_server.secrets, "compare_digest", spy)

    r = client.get("/api/items", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert calls, "secrets.compare_digest was never called for bearer auth"
    assert calls[0] == (API_KEY.encode(), API_KEY.encode())

    calls.clear()
    r = client.get("/api/items", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert calls == [(b"wrong", API_KEY.encode())]


# ---------------------------------------------------------------------------
# GET /api/items — search / location / status filters + limit/offset
# ---------------------------------------------------------------------------

def test_list_items_all(client):
    r = client.get("/api/items", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 3
    # ORDER BY sku DESC — highest SKU first.
    skus = [it["sku"] for it in body["items"]]
    assert skus == sorted(skus, reverse=True)
    # Column set matches the SELECT in http_server.py list_items.
    assert set(body["items"][0]) == {
        "sku", "title", "location", "status", "price", "qty", "image", "attribute_set",
        "ebay_listing_id", "ebay_offer_id", "ebay_ready_at", "has_draft",
        "ebay_listing_status",
    }


def test_list_items_search_filter(client):
    r = client.get("/api/items", params={"search": "Widget"}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["sku"] == SKU_A


def test_list_items_search_matches_sku(client):
    r = client.get("/api/items", params={"search": SKU_B}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["sku"] == SKU_B


def test_list_items_location_filter(client):
    r = client.get("/api/items", params={"location": "B2"}, headers=AUTH_HEADERS)
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["sku"] == SKU_B


def test_list_items_status_filter(client):
    r = client.get(
        "/api/items", params={"status_filter": "Staged"}, headers=AUTH_HEADERS
    )
    body = r.json()
    assert body["count"] == 1
    assert body["items"][0]["status"] == "Staged"


def test_list_items_limit_and_offset(client):
    # count is the TOTAL matching rows; items is the page slice.
    r1 = client.get("/api/items", params={"limit": 1}, headers=AUTH_HEADERS)
    b1 = r1.json()
    assert b1["count"] == 3        # 3 catalog rows total
    assert len(b1["items"]) == 1   # only 1 returned (limit=1)
    first_sku = b1["items"][0]["sku"]

    r2 = client.get(
        "/api/items", params={"limit": 1, "offset": 1}, headers=AUTH_HEADERS
    )
    b2 = r2.json()
    assert b2["count"] == 3        # total unchanged
    assert len(b2["items"]) == 1
    assert b2["items"][0]["sku"] != first_sku


def test_list_items_date_from_filter(client):
    # date_from compares substr(sku,4,8) >= prefix; SKU_B is 2026-03-15.
    r = client.get(
        "/api/items", params={"date_from": "20260301"}, headers=AUTH_HEADERS
    )
    skus = {it["sku"] for it in r.json()["items"]}
    assert SKU_B in skus
    assert SKU_A not in skus  # 2026-01-01 is before the lower bound


def test_list_items_503_when_catalog_missing(env, monkeypatch):
    cfg = dict(env["cfg"])
    cfg["sqlite_catalog_path"] = env["itemdata_root"] / "nope.sqlite"
    monkeypatch.setattr(http_server, "_cfg", cfg)
    r = env["client"].get("/api/items", headers=AUTH_HEADERS)
    assert r.status_code == 503


def test_list_items_ebay_fields(tmp_path, monkeypatch):
    """list_items returns eBay fields extracted via json_extract from data column."""
    catalog_path = tmp_path / "catalog.sqlite"
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()

    sku_listed = "tgw20260101000000001"
    sku_staged = "tgw20260102000000002"
    sku_draft = "tgw20260103000000003"
    sku_plain = "tgw20260104000000004"

    data_listed = json.dumps({
        "ebay_listing": {"listing_id": "123456789"},
        "ebay_offer": {"offer_id": "OFF1", "ready_at": None},
    })
    data_staged = json.dumps({
        "ebay_offer": {"offer_id": "OFF2"},
    })
    data_draft = json.dumps({
        "draft_listing": {"category_id": "261186", "title": "Widget"},
    })
    data_plain = json.dumps({})

    _make_catalog_with_data(catalog_path, [
        (sku_listed, "Listed Item", "A1", "Active", 19.99, 1, "a.jpg", data_listed),
        (sku_staged, "Staged Item", "B2", "Staged", 12.00, 1, "b.jpg", data_staged),
        (sku_draft, "Draft Item", "C3", "In Stock", 9.99, 1, "", data_draft),
        (sku_plain, "Plain Item", "D4", "In Stock", 5.00, 1, "d.jpg", data_plain),
    ])

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": itemdata_root,
        "location_tree_root": tmp_path / "loctree",
        "thumbnail_root": tmp_path / "thumbs",
        "category_groups_path": str(tmp_path / "cg.json"),
        "plan_vault_path": tmp_path / "vault",
        "plan_inbox_path": tmp_path / "vault" / "inbox",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )

    client = TestClient(http_server.app)
    r = client.get("/api/items", headers=AUTH_HEADERS)
    assert r.status_code == 200
    items = {it["sku"]: it for it in r.json()["items"]}

    assert items[sku_listed]["ebay_listing_id"] == "123456789"
    assert items[sku_listed]["has_draft"] == 0

    assert items[sku_staged]["ebay_offer_id"] == "OFF2"
    assert items[sku_staged]["ebay_listing_id"] is None
    assert items[sku_staged]["ebay_ready_at"] is None

    assert items[sku_draft]["has_draft"] == 1
    assert items[sku_draft]["ebay_offer_id"] is None

    assert items[sku_plain]["ebay_listing_id"] is None
    assert items[sku_plain]["ebay_offer_id"] is None
    assert items[sku_plain]["has_draft"] == 0


# ---------------------------------------------------------------------------
# status_filter=__eligible__ — "Eligible for listing" (Dave, s42, todo #1112)
# new/In Stock, NOT currently on eBay (no Active listing, no PUBLISHED offer).
# Ended listings qualify (relistable); sold/disposed excluded by the status
# allow-list itself (they're never 'new'/'in stock').
# ---------------------------------------------------------------------------

def test_eligible_filter_status_and_ebay_state(tmp_path, monkeypatch):
    catalog_path = tmp_path / "catalog.sqlite"
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()

    sku_new_never_listed = "tgw20260201000000001"
    sku_new_active = "tgw20260201000000002"
    sku_instock_published_offer = "tgw20260201000000003"
    sku_instock_ended_relistable = "tgw20260201000000004"
    sku_sold = "tgw20260201000000005"
    sku_staged_not_eligible_status = "tgw20260201000000006"
    sku_blank_never_listed = "tgw20260201000000007"
    sku_blank_active = "tgw20260201000000008"

    _make_catalog_with_data(catalog_path, [
        (sku_new_never_listed, "A", "A1", "new", 9.99, 1, "", "{}"),
        (sku_new_active, "B", "A2", "New", 9.99, 1, "",
         json.dumps({"ebay_listing": {"status": "Active"}})),
        (sku_instock_published_offer, "C", "A3", "In Stock", 9.99, 1, "",
         json.dumps({"ebay_offer": {"status": "PUBLISHED"}})),
        (sku_instock_ended_relistable, "D", "A4", "In Stock", 9.99, 1, "",
         json.dumps({"ebay_listing": {"status": "Ended"}})),
        (sku_sold, "E", "A5", "Sold", 9.99, 1, "", "{}"),
        (sku_staged_not_eligible_status, "F", "A6", "Staged", 9.99, 1, "", "{}"),
        # todo #1377: items the intake pipeline never stamped with a status
        # at all (blank/NULL) are a real, common case — not a data error —
        # and must count as eligible when not listed/published, matching
        # how the default "All" view already treats blank status as
        # active/non-terminal.
        (sku_blank_never_listed, "G", "A7", "", 9.99, 1, "", "{}"),
        (sku_blank_active, "H", "A8", "", 9.99, 1, "",
         json.dumps({"ebay_listing": {"status": "Active"}})),
    ])

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": itemdata_root,
        "location_tree_root": tmp_path / "loctree",
        "thumbnail_root": tmp_path / "thumbs",
        "category_groups_path": str(tmp_path / "cg.json"),
        "plan_vault_path": tmp_path / "vault",
        "plan_inbox_path": tmp_path / "vault" / "inbox",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _FakeConn([]))

    client = TestClient(http_server.app)
    r = client.get("/api/items", params={"status_filter": "__eligible__"},
                   headers=AUTH_HEADERS)
    assert r.status_code == 200
    skus = {it["sku"] for it in r.json()["items"]}

    assert sku_new_never_listed in skus
    assert sku_instock_ended_relistable in skus  # ended listings are relistable
    assert sku_new_active not in skus            # already Active on eBay
    assert sku_instock_published_offer not in skus  # PUBLISHED offer blocks it
    assert sku_sold not in skus                  # status excluded outright
    assert sku_staged_not_eligible_status not in skus  # not new/In Stock
    assert sku_blank_never_listed in skus        # blank status = never stamped, still eligible (#1377)
    assert sku_blank_active not in skus          # blank status but already Active still excluded


# ---------------------------------------------------------------------------
# GET /form/history/{sku_old} — historical-catalog lookup (todo #1054)
# ---------------------------------------------------------------------------

def _write_historical_catalogs(tmp_path, tgwcat_records=None, mastercat_records=None):
    catalog_root = tmp_path / "ItemCatalog"
    catalog_root.mkdir(exist_ok=True)
    if tgwcat_records is not None:
        (catalog_root / "historical-tgwcatalog.json").write_text(
            json.dumps(tgwcat_records), encoding="utf-8")
    if mastercat_records is not None:
        (catalog_root / "historical-master-catalog.json").write_text(
            json.dumps(mastercat_records), encoding="utf-8")
    return catalog_root


def _history_client(tmp_path, monkeypatch, catalog_root):
    cfg = {
        "itemdata_root": tmp_path / "ItemData",
        "catalog_root": catalog_root,
        "location_tree_root": tmp_path / "loctree",
        "thumbnail_root": tmp_path / "thumbs",
        "category_groups_path": str(tmp_path / "cg.json"),
        "plan_vault_path": tmp_path / "vault",
        "plan_inbox_path": tmp_path / "vault" / "inbox",
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_historical_index_by_sku_old", None)
    client = TestClient(http_server.app)
    _login(client)  # /form/* is gated by the session-cookie wall (s42/43)
    return client


def test_history_form_found_in_tgwcatalog(tmp_path, monkeypatch):
    catalog_root = _write_historical_catalogs(tmp_path, tgwcat_records={
        "tgw20140101144105453": {
            "sku": "tgw20140101144105453",
            "sku_old": "TGW20140101144105453",
            "tgw_name": "Mumm Champagne Bottle Stopper",
            "price": "4.99",
        },
    })
    client = _history_client(tmp_path, monkeypatch, catalog_root)
    r = client.get("/form/history/TGW20140101144105453")
    assert r.status_code == 200
    assert "Mumm Champagne Bottle Stopper" in r.text
    assert "TGW20140101144105453" in r.text


def test_history_form_found_in_mastercatalog_when_absent_from_tgwcatalog(tmp_path, monkeypatch):
    catalog_root = _write_historical_catalogs(
        tmp_path,
        tgwcat_records={},
        mastercat_records=[
            {"sku": "tgw20140101144105453", "sku_old": "TGW20140101144105453",
             "title": "Mumm Champagne Bottle Stopper (master)"},
        ],
    )
    client = _history_client(tmp_path, monkeypatch, catalog_root)
    r = client.get("/form/history/TGW20140101144105453")
    assert r.status_code == 200
    assert "Mumm Champagne Bottle Stopper (master)" in r.text


def test_history_form_not_found_renders_clean_message(tmp_path, monkeypatch):
    catalog_root = _write_historical_catalogs(tmp_path, tgwcat_records={}, mastercat_records=[])
    client = _history_client(tmp_path, monkeypatch, catalog_root)
    r = client.get("/form/history/TGW-DOES-NOT-EXIST")
    assert r.status_code == 200
    assert "No historical record found" in r.text


def test_item_detail_history_link_present_when_sku_old_set(env):
    sku = SKU_A
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    doc["sku_old"] = "TGW20140101144105453"
    (env["itemdata_root"] / sku / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")
    _login(env["client"])
    r = env["client"].get(f"/form/items/{sku}")
    assert "/form/history/TGW20140101144105453" in r.text
    assert "History" in r.text


# ---------------------------------------------------------------------------
# GET /api/items/{sku} — 404, media-list assembly, queue-jobs fetch
# ---------------------------------------------------------------------------

def test_get_item_404(client):
    r = client.get("/api/items/tgw99999999999999999", headers=AUTH_HEADERS)
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_get_item_media_lists(client):
    r = client.get(f"/api/items/{SKU_B}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    item = r.json()["item"]
    # Images sorted; .PNG matched case-insensitively; .txt ignored.
    assert item["_images"] == ["back.PNG", "front.jpg"]
    assert item["_videos"] == ["clip.mp4"]
    # No queue rows seeded -> empty list from the fake cursor.
    assert item["_queue_jobs"] == []


def test_get_item_queue_jobs_serialized(env):
    import datetime as _dt

    created = _dt.datetime(2026, 6, 1, 12, 0, 0, tzinfo=_dt.timezone.utc)
    env["queue_rows"].append({
        "job_id": "job-ai-1",
        "queue_name": "ai_identify",
        "state": "done",
        "attempt_count": 1,
        "created_at": created,
        "updated_at": created,
        "finished_at": None,
        "error_code": None,
        "error_detail": None,
    })
    r = env["client"].get(f"/api/items/{SKU_A}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    jobs = r.json()["item"]["_queue_jobs"]
    assert len(jobs) == 1
    j = jobs[0]
    # Datetimes are isoformatted; None finished_at stays None.
    assert j["created_at"] == created.isoformat()
    assert j["finished_at"] is None
    assert j["queue_name"] == "ai_identify"
    assert j["job_id"] == "job-ai-1"


def test_get_item_queue_fetch_failure_is_swallowed(env, monkeypatch):
    # If psycopg2.connect raises, get_item logs and returns empty job list.
    def _boom(*a, **k):
        raise RuntimeError("no db")

    monkeypatch.setattr(http_server.psycopg2, "connect", _boom)
    r = env["client"].get(f"/api/items/{SKU_A}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["item"]["_queue_jobs"] == []


# ---------------------------------------------------------------------------
# PATCH /api/items/{sku} — validation + merge (the prioritized core)
# ---------------------------------------------------------------------------

def test_patch_sku_immutable_400(client):
    r = client.patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"sku": "tgwX", "title": "new"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "sku field is immutable"


def test_patch_empty_fields_400(client):
    r = client.patch(
        f"/api/items/{SKU_A}", json={"fields": {}}, headers=AUTH_HEADERS
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "no fields provided"


def test_patch_unknown_sku_404(client):
    r = client.patch(
        "/api/items/tgw00000000000000000",
        json={"fields": {"title": "x"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404


def test_patch_rejects_invalid_condition_enum(env, enqueue_calls):
    """PP-CONDITION-ENUM-001 / todo #1562 — live incident regression test:
    a draft_listing.condition_enum PATCH set to a raw human label (not a
    real Inventory API enum) must be REJECTED with a field-tagged error,
    never silently written — this is the actual bug that dead-lettered
    tgw202605051124483 at ebay_stage ("Could not serialize field
    [condition]")."""
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"draft_listing": {"condition_enum": "Very Good"}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422
    body = r.json()
    assert body["ok"] is False
    assert body["field"] == "condition_enum"

    # The bad value must never have reached disk.
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text(encoding="utf-8")
    )
    assert (doc.get("draft_listing") or {}).get("condition_enum") != "Very Good"


def test_patch_accepts_valid_condition_enum(env, enqueue_calls):
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"draft_listing": {"condition_enum": "USED_VERY_GOOD"}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text(encoding="utf-8")
    )
    assert doc["draft_listing"]["condition_enum"] == "USED_VERY_GOOD"


def test_patch_multi_field_merge(env, enqueue_calls):
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"title": "Merged Title", "weight_oz": 4.5}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sku"] == SKU_A
    assert set(body["updated"]) == {"title", "weight_oz"}

    # Disk reflects the merge; pre-existing condition field is preserved.
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text(encoding="utf-8")
    )
    assert doc["title"] == "Merged Title"
    assert doc["weight_oz"] == 4.5
    assert doc["condition"] == "Good"


def test_patch_auto_pops_catalog_verified(env, enqueue_calls):
    # SKU_A was seeded with catalog_verified=True. A patch that does NOT include
    # catalog_verified should strip it (http_server.py:266-267).
    path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    assert json.loads(path.read_text())["catalog_verified"] is True

    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"title": "Touched"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert "catalog_verified" not in doc


def test_patch_keeps_catalog_verified_when_provided(env, enqueue_calls):
    path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"catalog_verified": False, "title": "Z"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["catalog_verified"] is False


def test_patch_location_syncs_tree(env, enqueue_calls):
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"location": "C9"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    # location is appended to updated_keys after the field-key list.
    assert "location" in body["updated"]

    # JSON now records the new location.
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text(encoding="utf-8")
    )
    assert doc["location"] == "C9"

    # Location tree got a symlink for the SKU under the new location dir.
    link = env["location_tree_root"] / "C9" / SKU_A
    assert link.is_symlink() or link.exists()


def test_patch_no_longer_enqueues_catalog_rebuild(env, enqueue_calls):
    """PP-CATALOG-INCR-001 CI-4 (2026-07-18): enqueue_catalog_rebuild() is
    now a no-op — CI-2's synchronous SQLite upsert (inside _apply_patch,
    called just before this point) keeps the catalog live instead. A title
    change doesn't touch image/photo_order, so CI-3's thumbnail trigger
    doesn't fire either — no enqueue calls at all for this write."""
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"title": "Rebuild me"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert enqueue_calls == []


def test_patch_succeeds_even_if_enqueue_raises(env, monkeypatch):
    # The enqueue is wrapped in try/except pass — patch still succeeds.
    def _boom(*a, **k):
        raise RuntimeError("queue down")

    monkeypatch.setattr(http_server.state_machine, "enqueue_job", _boom)
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"title": "Still ok"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# PATCH auto-enqueue on draft_listing edit (todo #1114) — push, never regen
# ---------------------------------------------------------------------------

def _seed_live_item(env, sku, extra_fields=None):
    doc = {
        "sku": sku, "title": "Live Widget", "location": "A1",
        "condition": "Good",
        "draft_listing": {"title": "Live Widget", "price": "9.99"},
        "ebay_offer": {"offer_id": "OFF-LIVE-1", "status": "PUBLISHED"},
    }
    doc.update(extra_fields or {})
    _write_item(env["itemdata_root"], sku, doc)


def test_operator_edit_to_live_draft_pushes_not_regenerates(env, enqueue_calls):
    sku = "tgw20260401000000009"
    _seed_live_item(env, sku)

    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Live Widget (fixed typo)"}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200

    queue_names = [c["kwargs"].get("queue_name") for c in enqueue_calls]
    assert "ebay_stage" in queue_names
    assert "ebay_draft" not in queue_names
    stage_call = next(c for c in enqueue_calls if c["kwargs"].get("queue_name") == "ebay_stage")
    assert stage_call["kwargs"]["payload"]["force"] is True
    assert stage_call["kwargs"]["payload"]["origin"] == "operator"

    # The operator's edit itself must survive on disk — the whole point.
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["draft_listing"]["title"] == "Live Widget (fixed typo)"


def test_worker_write_to_live_draft_does_not_auto_enqueue(env, enqueue_calls):
    """A machine write (X-TGW-Caller identifies it as a worker) must not
    trigger the push — only genuine operator edits should (s42 regression
    guard, still applies after the #1114 fix)."""
    sku = "tgw20260401000000010"
    _seed_live_item(env, sku)

    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Worker-written title"}}},
        headers={**AUTH_HEADERS, "X-TGW-Caller": "background:worker:ebay_draft"},
    )
    assert r.status_code == 200
    queue_names = [c["kwargs"].get("queue_name") for c in enqueue_calls]
    assert "ebay_stage" not in queue_names
    assert "ebay_draft" not in queue_names


def test_ebay_update_action_uses_same_dedupe_key_as_patch_auto_push(env, enqueue_calls):
    """Todo #1469 (live incident, 2026-07-16, Dave: 'why is every item
    staging twice... not even close to the same as ui'): the 'Update
    Listing' button calls saveEbayDraft() (PATCH draft_listing, which
    auto-enqueues ebay_stage under dedupe_key f"ebay_stage:{sku}") and then
    fires the ebay_update action as its own success callback — which used
    to enqueue a SECOND, unguarded ebay_stage job with no dedupe_key,
    racing the first. Both enqueue call sites must now share the exact
    same dedupe_key so the redundant attempt coalesces."""
    sku = "tgw20260401000000012"
    _seed_live_item(env, sku)

    patch_r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Operator edit"}}},
        headers=AUTH_HEADERS,
    )
    assert patch_r.status_code == 200

    action_r = env["client"].post(
        f"/api/items/{sku}/action",
        json={"action": "ebay_update"},
        headers=AUTH_HEADERS,
    )
    assert action_r.status_code == 200

    stage_calls = [c for c in enqueue_calls if c["kwargs"].get("queue_name") == "ebay_stage"]
    assert len(stage_calls) == 2  # one from the PATCH auto-push, one from the action
    dedupe_keys = {c["kwargs"].get("dedupe_key") for c in stage_calls}
    assert dedupe_keys == {f"ebay_stage:{sku}"}  # both share the SAME key — real Postgres would coalesce these


def test_ebay_update_action_survives_dedupe_collision(env, monkeypatch):
    """If the auto-push's job is still active when this action's own
    enqueue attempt hits the shared dedupe_key, Postgres raises
    UniqueViolation — the action must degrade to job_id=None, not a 500."""
    sku = "tgw20260401000000013"
    _seed_live_item(env, sku)

    def _raise_unique_violation(**kwargs):
        raise psycopg2.errors.UniqueViolation("duplicate dedupe_key")

    monkeypatch.setattr(http_server.state_machine, "enqueue_job", _raise_unique_violation)

    r = env["client"].post(
        f"/api/items/{sku}/action",
        json={"action": "ebay_update"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "sku": sku, "action": "ebay_update", "job_id": None}


def test_resync_photos_queues_upload_for_same_count_but_wrong_photo_set(
    env, enqueue_calls,
):
    """Count equality must not bypass upload when the identities differ.

    Two local photos and two hosted rows fooled the historical count check
    even when one hosted row belonged to a removed file.  The exact mapping
    fingerprint must wait and queue upload for the missing current photo.
    """
    sku = "tgw20260401000000014"
    item_dir = env["itemdata_root"] / sku
    item_dir.mkdir()
    first = item_dir / "001.jpg"
    second = item_dir / "002.jpg"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "photo_order": ["001.jpg", "002.jpg"],
        "ebay_photos": [
            {"local": str(first), "url": "https://i.ebayimg.com/001.jpg"},
            {"local": str(item_dir / "removed.jpg"),
             "url": "https://i.ebayimg.com/removed.jpg"},
        ],
        "draft_listing": {
            "imageUrls": [
                "https://i.ebayimg.com/001.jpg",
                "https://i.ebayimg.com/removed.jpg",
            ],
        },
    })

    response = env["client"].post(
        f"/api/items/{sku}/action",
        json={"action": "resync_photos"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["upload_queued"] is True
    assert body["local_photo_count"] == body["hosted_count"] == 2
    assert len(body["photo_fingerprint"]) == 64
    assert "1 missing EPS URL" in body["detail"]
    assert "1 stale hosted photo" in body["detail"]
    upload = next(
        call["kwargs"] for call in enqueue_calls
        if call["kwargs"].get("queue_name") == "ebay_upload"
    )
    assert upload["payload"]["sku"] == sku
    assert upload["dedupe_key"] == f"ebay_upload:{sku}"


def test_operator_edit_to_not_yet_live_draft_does_not_auto_enqueue(env, enqueue_calls):
    """No offer_id yet — nothing to push to, so no auto-enqueue at all
    (unchanged pre-#1114 behavior for pre-publish items)."""
    sku = "tgw20260401000000011"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Draft Widget", "location": "A1",
        "draft_listing": {"title": "Draft Widget"},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Draft Widget v2"}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    queue_names = [c["kwargs"].get("queue_name") for c in enqueue_calls]
    assert "ebay_stage" not in queue_names
    assert "ebay_draft" not in queue_names


def test_bare_top_level_title_edit_does_not_auto_enqueue(env, enqueue_calls):
    """Code-review fix: a PATCH to a bare top-level 'title' (not
    draft_listing.title) must NOT trigger the auto-push — ebay_stage only
    ever reads draft_listing's own title/description, so pushing here
    would silently send stale draft_listing content while claiming the
    edit was propagated. Only draft_listing.* edits should trigger."""
    sku = "tgw20260401000000012"
    _seed_live_item(env, sku)

    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"title": "Bare top-level title edit"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    queue_names = [c["kwargs"].get("queue_name") for c in enqueue_calls]
    assert "ebay_stage" not in queue_names
    assert "ebay_draft" not in queue_names


def test_saveebaydraft_shaped_patch_routes_item_specifics_through_accessor(env, enqueue_calls):
    """todo #1416 point 3: saveEbayDraft() now nests aspect edits inside
    draft_listing.item_specifics (matching every other Draft Editor
    field), matching the PATCH shape at http_server.py's saveEbayDraft().
    _apply_patch must route a bare partial item_specifics dict through
    the sanctioned tgw.ebay.draft_specifics accessor (preserving the
    envelope + provenance history), not shallow-merge it directly, and
    the existing auto-push-on-draft_listing-change behavior must still
    fire (no new trigger-set change needed, per the packet spec)."""
    sku = "tgw20260401000000014"
    _seed_live_item(env, sku, extra_fields={
        "draft_listing": {
            "title": "Live Widget", "price": "9.99",
            "item_specifics": {"Type": "Lapel Pin"},
        },
    })

    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"item_specifics": {"Type": "Brooch"}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200

    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert draft_specifics.get_ebay_aspects(doc)["Type"] == "Brooch"
    assert doc["draft_listing"]["item_specifics"]["_set"] == "ebay_draft"
    assert len(doc["draft_listing"]["item_specifics_history"]) == 1
    assert doc["draft_listing"]["item_specifics_history"][0]["previous_value"] == "Lapel Pin"
    # Sibling draft_listing fields (title, price) must survive the merge.
    assert doc["draft_listing"]["title"] == "Live Widget"
    assert doc["draft_listing"]["price"] == "9.99"
    # Auto-push must still fire — this is just a normal draft_listing edit.
    queue_names = [c["kwargs"].get("queue_name") for c in enqueue_calls]
    assert "ebay_stage" in queue_names


def test_operator_edit_to_live_draft_uses_dedupe_key(env, enqueue_calls):
    """Code-review fix: the auto-push must dedupe by SKU like the other
    ebay_stage enqueue call sites, so rapid successive edits collapse
    into one queued push instead of piling up duplicate live PUTs."""
    sku = "tgw20260401000000013"
    _seed_live_item(env, sku)

    env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Edit one"}}},
        headers=AUTH_HEADERS,
    )
    stage_call = next(c for c in enqueue_calls if c["kwargs"].get("queue_name") == "ebay_stage")
    assert stage_call["kwargs"]["dedupe_key"] == f"ebay_stage:{sku}"


# ---------------------------------------------------------------------------
# GET /api/locations
# ---------------------------------------------------------------------------

def test_list_locations(client):
    r = client.get("/api/locations", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Empty-string location excluded; sorted ascending.
    assert body["locations"] == ["A1", "B2"]


def test_list_locations_503_when_catalog_missing(env, monkeypatch):
    cfg = dict(env["cfg"])
    cfg["sqlite_catalog_path"] = env["itemdata_root"] / "nope.sqlite"
    monkeypatch.setattr(http_server, "_cfg", cfg)
    r = env["client"].get("/api/locations", headers=AUTH_HEADERS)
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# GET /api/category-groups
# ---------------------------------------------------------------------------

def test_list_category_groups(client):
    r = client.get("/api/category-groups", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    by_key = {g["key"]: g for g in body["groups"]}
    assert by_key["books"]["name"] == "Books"
    assert by_key["books"]["size_class"] == "small"
    assert by_key["books"]["ai_hint"] == "printed book"
    assert by_key["books"]["floor"] == 3.0
    assert by_key["books"]["typical_used"] == 8.0
    # vinyl has empty pricing dict -> floor/typical_used are None.
    assert by_key["vinyl"]["floor"] is None
    assert by_key["vinyl"]["typical_used"] is None


def test_category_groups_503_when_missing(env, monkeypatch):
    cfg = dict(env["cfg"])
    cfg["category_groups_path"] = str(env["itemdata_root"] / "no-groups.json")
    monkeypatch.setattr(http_server, "_cfg", cfg)
    r = env["client"].get("/api/category-groups", headers=AUTH_HEADERS)
    assert r.status_code == 503


def test_category_groups_requires_auth(client):
    r = client.get("/api/category-groups", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/items — create_item_endpoint (todo #1311: routed through the
# tgw.items.create_item fence instead of duplicating its path-construction
# logic inline)
# ---------------------------------------------------------------------------

NEW_SKU = "tgw20260401000000009"


def test_create_item_endpoint_writes_via_fence(env, enqueue_calls):
    client = env["client"]
    r = client.post(
        "/api/items", headers=AUTH_HEADERS,
        json={"sku": NEW_SKU, "data": {"title": "Fence-Created Item"}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sku"] == NEW_SKU
    doc = json.loads((env["itemdata_root"] / NEW_SKU / f"{NEW_SKU}.json").read_text())
    assert doc["title"] == "Fence-Created Item"
    assert doc["sku"] == NEW_SKU
    # PP-CATALOG-INCR-001 CI-4 (2026-07-18): catalog_rebuild's enqueue is now
    # a no-op — the endpoint upserts the new SKU's SQLite catalog row
    # directly instead, so no catalog_rebuild enqueue is expected here.
    assert not any(c["kwargs"].get("queue_name") == "catalog_rebuild" for c in enqueue_calls)


def test_create_item_endpoint_duplicate_sku_409(env):
    client = env["client"]
    r1 = client.post(
        "/api/items", headers=AUTH_HEADERS,
        json={"sku": NEW_SKU, "data": {"title": "First"}},
    )
    assert r1.status_code == 200
    r2 = client.post(
        "/api/items", headers=AUTH_HEADERS,
        json={"sku": NEW_SKU, "data": {"title": "Second"}},
    )
    assert r2.status_code == 409
    assert NEW_SKU in r2.json()["detail"]
    # original doc unchanged
    doc = json.loads((env["itemdata_root"] / NEW_SKU / f"{NEW_SKU}.json").read_text())
    assert doc["title"] == "First"


def test_create_item_endpoint_bad_sku_format_400(client):
    r = client.post(
        "/api/items", headers=AUTH_HEADERS,
        json={"sku": "not-a-sku", "data": {"title": "x"}},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# PP-BULKEDIT-001 — /form/bulk + /api/bulk/preview + /api/bulk/apply
# ---------------------------------------------------------------------------

def test_bulk_form_html(client):
    _login(client)
    r = client.get("/form/bulk")
    assert r.status_code == 200
    assert "Bulk Edit" in r.text
    assert "/api/bulk/preview" in r.text


def test_bulk_preview_requires_auth(client):
    r = client.post("/api/bulk/preview",
                    json={"field": "title", "value": "X", "location": "A1"})
    assert r.status_code in (401, 403)


def test_bulk_preview_by_search(client):
    # The shared fixture has a real (empty) location tree, so filter by search
    # (JSON scan) which matches only SKU_A's "Red Widget".
    r = client.post("/api/bulk/preview", headers=AUTH_HEADERS,
                    json={"field": "title", "value": "Renamed", "search": "Red"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] is False
    assert body["count"] == 1
    assert body["preview"][0]["sku"] == SKU_A
    assert body["preview"][0]["proposed"] == "Renamed"


def test_bulk_preview_no_selector_400(client):
    r = client.post("/api/bulk/preview", headers=AUTH_HEADERS,
                    json={"field": "title", "value": "X"})
    assert r.status_code == 400


def test_bulk_apply_writes_and_enqueues(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/apply", headers=AUTH_HEADERS,
                    json={"field": "title", "value": "Bulk Renamed", "skus": [SKU_A]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] is True
    assert body["count"] == 1
    # written to disk
    doc = json.loads((env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text())
    assert doc["title"] == "Bulk Renamed"
    # PP-CATALOG-INCR-001 CI-4 (2026-07-18): catalog_rebuild's enqueue is now
    # a no-op — items.py's bulk_edit -> _write_field upserts the SQLite
    # catalog row directly on write instead (CI-2/CI-4).
    assert not any(c["kwargs"].get("queue_name") == "catalog_rebuild" for c in enqueue_calls)


def test_bulk_apply_invalid_field(client):
    r = client.post("/api/bulk/apply", headers=AUTH_HEADERS,
                    json={"field": "price", "value": "9.99", "skus": [SKU_A]})
    # bulk_edit returns ok:False for a non-editable field (200 envelope)
    assert r.status_code == 200
    assert r.json()["ok"] is False


# ---------------------------------------------------------------------------
# POST /api/bulk/action — bulk selection actions (todo #881)
# ---------------------------------------------------------------------------

def test_bulk_action_requires_auth(client):
    r = client.post("/api/bulk/action", json={"skus": [SKU_A], "action": "ai_identify"})
    assert r.status_code in (401, 403)


def test_bulk_action_unknown_action_400(client):
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "bogus"})
    assert r.status_code == 400


def test_bulk_action_no_skus_400(client):
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [], "action": "ai_identify"})
    assert r.status_code == 400


def test_bulk_action_ai_identify_enqueues(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A, SKU_B], "action": "ai_identify"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    assert set(body["queued"]) == {SKU_A, SKU_B}
    # ai_reidentify flag written for each
    itemdata_root = env["itemdata_root"]
    for sku in (SKU_A, SKU_B):
        doc = json.loads((itemdata_root / sku / f"{sku}.json").read_text())
        assert doc.get("ai_reidentify") is True
    # jobs enqueued
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert queued_names.count("ai_identify") == 2


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/append — PP-INTAKE-004 Phase 1a incremental-ID trigger
# ---------------------------------------------------------------------------

def _append_photo(client, sku, name):
    return client.post(
        f"/api/items/{sku}/append", headers=AUTH_HEADERS,
        json={"op": "photo", "data": {"filename": name}},
    )


def test_append_photo_below_threshold_does_not_enqueue_ai_identify(env, enqueue_calls):
    client = env["client"]
    for i in range(5):  # below _MAX_PHOTOS_CLOUD (6)
        r = _append_photo(client, SKU_A, f"p{i}.jpg")
        assert r.status_code == 200
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert "ai_identify" not in queued_names


def test_append_photo_crossing_threshold_enqueues_ai_identify_once(env, enqueue_calls):
    client = env["client"]
    for i in range(6):  # crosses _MAX_PHOTOS_CLOUD (6) on the 6th append
        r = _append_photo(client, SKU_A, f"p{i}.jpg")
        assert r.status_code == 200
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert queued_names.count("ai_identify") == 1
    ai_calls = [c for c in enqueue_calls if c["kwargs"]["queue_name"] == "ai_identify"]
    assert ai_calls[0]["kwargs"]["payload"]["sku"] == SKU_A
    assert ai_calls[0]["kwargs"]["dedupe_key"] == f"ai_identify:{SKU_A}"


def test_append_photo_does_not_refire_once_already_identified(env, enqueue_calls):
    client = env["client"]
    itemdata_root = env["itemdata_root"]
    doc_path = itemdata_root / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["ai_identified"] = True
    doc_path.write_text(json.dumps(doc))

    for i in range(7):  # already past threshold, already identified
        r = _append_photo(client, SKU_A, f"p{i}.jpg")
        assert r.status_code == 200
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert "ai_identify" not in queued_names


def test_session_complete_sets_ai_reidentify_when_already_identified(env, enqueue_calls):
    client = env["client"]
    itemdata_root = env["itemdata_root"]
    doc_path = itemdata_root / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["ai_identified"] = True
    doc_path.write_text(json.dumps(doc))

    r = client.post(
        f"/api/items/{SKU_A}/append", headers=AUTH_HEADERS,
        json={"op": "photo", "data": {"filename": "last.jpg"}, "session_complete": True},
    )
    assert r.status_code == 200
    doc_after = json.loads(doc_path.read_text())
    assert doc_after.get("ai_reidentify") is True
    # refinement is a flag-set, not a direct re-enqueue of ai_identify itself
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert "ai_identify" not in queued_names


def test_session_complete_fallback_enqueues_ai_identify_when_never_run(env, enqueue_calls):
    client = env["client"]
    # only 2 photos — never crosses the early-fire threshold
    for i in range(2):
        r = _append_photo(client, SKU_A, f"p{i}.jpg")
        assert r.status_code == 200
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert "ai_identify" not in queued_names

    r = client.post(
        f"/api/items/{SKU_A}/append", headers=AUTH_HEADERS,
        json={"op": "photo", "data": {"filename": "last.jpg"}, "session_complete": True},
    )
    assert r.status_code == 200
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert queued_names.count("ai_identify") == 1
    itemdata_root = env["itemdata_root"]
    doc = json.loads((itemdata_root / SKU_A / f"{SKU_A}.json").read_text())
    assert doc.get("ai_reidentify") is not True  # fallback path is enqueue, not the reidentify flag


def test_bulk_action_ebay_price_enqueues(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "ebay_price"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert "ebay_price" in queued_names


def test_bulk_action_ebay_draft_enqueues(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A, SKU_B], "action": "ebay_draft"})
    assert r.status_code == 200
    assert r.json()["count"] == 2
    queued_names = [c["kwargs"]["queue_name"] for c in enqueue_calls]
    assert queued_names.count("ebay_draft") == 2


def test_bulk_action_mark_sold_writes_status(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "mark_sold"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    doc = json.loads((env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text())
    assert doc["status"] == "Sold"
    # PP-CATALOG-INCR-001 CI-4 (2026-07-18): catalog_rebuild's enqueue is now
    # a no-op — see test_bulk_apply_writes_and_enqueues's identical note.
    assert not any(c["kwargs"].get("queue_name") == "catalog_rebuild" for c in enqueue_calls)


def test_bulk_action_mark_sold_decrements_multi_qty_instead_of_sold(env, enqueue_calls):
    """audit#1143 #1190: a multi-qty item must decrement, not jump straight
    to status=Sold and hide the remaining unsold units."""
    client = env["client"]
    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["draft_listing"] = {"quantity": 3}
    doc_path.write_text(json.dumps(doc))

    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "mark_sold"})
    assert r.status_code == 200
    doc = json.loads(doc_path.read_text())
    assert doc.get("status") != "Sold"
    assert doc["draft_listing"]["quantity"] == 2


def test_bulk_action_mark_sold_reads_item_doc_exactly_once(env, enqueue_calls, monkeypatch):
    """code-review follow-up: the original mark_sold fix read the item doc
    once to compute `remaining`, then _apply_patch's own internal read
    merged that now-stale value -- a TOCTOU window a concurrent real-sale
    decrement (ebay/pull.py mark_item_sold) could land in. Closing it means
    exactly one read feeds the one write, no second independent read."""
    client = env["client"]
    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["draft_listing"] = {"quantity": 3}
    doc_path.write_text(json.dumps(doc))

    calls = []
    real_load = http_server.load_item_doc

    def _counting_load(path):
        calls.append(path)
        return real_load(path)

    monkeypatch.setattr(http_server, "load_item_doc", _counting_load)

    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "mark_sold"})
    assert r.status_code == 200
    assert len(calls) == 1


def test_ebay_sold_webhook_marks_item_sold(env, enqueue_calls, monkeypatch):
    """todo #1378: the sold-notification webhook imported two functions that
    don't exist in tgw.workers.ebay_legacy_sync (_mark_item_sold,
    _build_listing_index -- both live in tgw.ebay.pull under different
    names) -- every real eBay sold webhook call has 500'd since 2026-06-04
    (commit 429ebc5), before ever reaching the try/except. This drives the
    real endpoint end-to-end (signature check stubbed, everything else
    real) so an import regression fails loudly instead of silently."""
    import tgw.apis.ebay.notifications as notifications
    import tgw.ebay.pull as pull
    from tgw.http_server import _listing_index_built_at_reset

    monkeypatch.setattr(notifications, "verify_notification_signature", lambda body, cfg: True)
    _listing_index_built_at_reset()

    mark_calls = []

    def _fake_mark_item_sold(json_path, **kwargs):
        mark_calls.append((json_path, kwargs))
        return True

    monkeypatch.setattr(pull, "mark_item_sold", _fake_mark_item_sold)

    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["draft_listing"] = {"quantity": 1}
    doc["ebay_listing"] = {"listing_id": "998877"}
    doc_path.write_text(json.dumps(doc))

    ns = "urn:ebay:apis:eBLBaseComponents"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetItemTransactionsResponse xmlns="{ns}">
      <Timestamp>2026-07-13T00:00:00.000Z</Timestamp>
      <TransactionArray><Transaction>
        <Item><ItemID>998877</ItemID></Item>
        <TransactionPrice>9.99</TransactionPrice>
        <QuantityPurchased>1</QuantityPurchased>
        <CreatedDate>2026-07-13T00:00:00.000Z</CreatedDate>
        <Buyer><UserID>someone</UserID></Buyer>
        <TransactionID>tx-1</TransactionID>
      </Transaction></TransactionArray>
      <OrderID>order-1</OrderID>
    </GetItemTransactionsResponse>
  </soap:Body>
</soap:Envelope>"""

    client = env["client"]
    r = client.post("/webhooks/ebay/notification", headers=AUTH_HEADERS,
                    content=xml.encode("utf-8"))
    assert r.status_code == 200
    assert r.json() == {"ack": "Success"}

    assert len(mark_calls) == 1
    called_path, kwargs = mark_calls[0]
    assert called_path == doc_path
    assert kwargs["order_id"] == "order-1"
    assert kwargs["quantity"] == 1


def test_bulk_action_delete_writes_status(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "delete"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    doc = json.loads((env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text())
    assert doc["status"] == "deleted"
    assert "deleted_at" in doc


def test_bulk_action_missing_sku_reported_in_errors(env, enqueue_calls):
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": ["tgw_no_such_sku"], "action": "ebay_price"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert len(body["errors"]) == 1
    assert "not found" in body["errors"][0]


def test_ebay_write_protects_price_comps_from_worker_overwrite(env):
    """audit#1143 #1189: _apply_ebay_write's field-protection loop was a
    no-op -- it only restored the protected value when the incoming block
    didn't set it, which is exactly when nothing needed restoring. A worker
    write that tries to overwrite price_comps must be blocked outright."""
    client = env["client"]
    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["ebay_offer"] = {"offer_id": "o1", "price_comps": {"median": 12.5}}
    doc_path.write_text(json.dumps(doc))

    r = client.post(f"/api/items/{SKU_A}/ebay-write", headers=AUTH_HEADERS,
                     json={"ebay_offer": {"offer_id": "o1", "price_comps": {"median": 999}}})
    assert r.status_code == 200
    doc = json.loads(doc_path.read_text())
    assert doc["ebay_offer"]["price_comps"]["median"] == 12.5


def test_ebay_write_allow_protected_lets_owner_refresh_price_comps(env):
    """code-review follow-up: the #1189 fix must not block the ONE legitimate
    owner (ebay_price) from refreshing price_comps -- it opts in via
    allow_protected."""
    client = env["client"]
    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(doc_path.read_text())
    doc["ebay_offer"] = {"offer_id": "o1", "price_comps": {"median": 12.5}}
    doc_path.write_text(json.dumps(doc))

    r = client.post(f"/api/items/{SKU_A}/ebay-write", headers=AUTH_HEADERS,
                     json={"ebay_offer": {"offer_id": "o1", "price_comps": {"median": 999}},
                           "allow_protected": ["price_comps"]})
    assert r.status_code == 200
    doc = json.loads(doc_path.read_text())
    assert doc["ebay_offer"]["price_comps"]["median"] == 999


def test_remove_comp_uses_same_stats_formula_as_pricing_module(env):
    """audit#1143 #1193: remove_comp must use the same nearest-rank formula
    as ebay/pricing.py._compute_stats, not a separate linear-interpolation
    one, or stored comps stats silently shift on every operator edit."""
    from tgw.ebay.pricing import _compute_stats

    client = env["client"]
    doc_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    prices = [10.0, 12.0, 15.0, 20.0, 25.0]
    items = [{"url": f"http://x/{i}", "price": p} for i, p in enumerate(prices)]
    doc = json.loads(doc_path.read_text())
    doc["ebay_offer"] = {"price_comps": {"items": items + [
        {"url": "http://x/drop", "price": 1000.0},
    ]}}
    doc_path.write_text(json.dumps(doc))

    r = client.post(f"/api/items/{SKU_A}/remove-comp", headers=AUTH_HEADERS,
                     json={"url": "http://x/drop"})
    assert r.status_code == 200
    doc = json.loads(doc_path.read_text())
    comps = doc["ebay_offer"]["price_comps"]
    expected = _compute_stats(prices)
    assert comps["p25"] == expected["p25"]
    assert comps["median"] == expected["median"]
    assert comps["p75"] == expected["p75"]


def test_bulk_action_set_ready_missing_offer(env):
    """set_ready returns ok:False when items lack an offer_id."""
    client = env["client"]
    r = client.post("/api/bulk/action", headers=AUTH_HEADERS,
                    json={"skus": [SKU_A], "action": "set_ready"})
    assert r.status_code == 200
    body = r.json()
    # set_ready errors because SKU_A has no offer_id
    assert "errors" in body or "skipped" in body or body.get("ok") is False


# ---------------------------------------------------------------------------
# PP-TODO-001 — GET /form/todos (Round 4 #34) — no Bearer auth (network trust)
# ---------------------------------------------------------------------------

_TODO_ROWS = [
    {"id": 29, "agent": "claude", "priority": 10, "body": "Dead_letter triage flag",
     "source": "round4", "added_at": "2026-06-08", "done_at": None},
    {"id": 31, "agent": "claude", "priority": 30, "body": "Fingerprint index + tgw locate",
     "source": "round4", "added_at": "2026-06-08", "done_at": None},
    {"id": 12, "agent": "admin", "priority": 45, "body": "Fix 9 wrong-shipping listings",
     "source": "plan", "added_at": "2026-06-07", "done_at": None},
]


def test_todos_form_renders_grouped(client, monkeypatch):
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: list(_TODO_ROWS))
    r = client.get("/form/todos")  # no auth header — network trust
    assert r.status_code == 200
    text = r.text
    assert "Open Todos" in text
    assert "claude" in text and "admin" in text
    assert "Fingerprint index" in text          # a task body
    assert "#29" in text                          # id shown
    assert "3 open item(s)" in text               # total count


def test_todos_form_empty_all_clear(client, monkeypatch):
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "All clear" in r.text


def test_todos_form_db_error_still_200(client, monkeypatch):
    _login(client)
    import tgw.todo as todo

    def _boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(todo, "todo_list", _boom)
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "unavailable" in r.text.lower()


def test_todos_form_escapes_html(client, monkeypatch):
    _login(client)
    import tgw.todo as todo
    rows = [{"id": 1, "agent": "claude", "priority": 50,
             "body": "<script>alert('x')</script>", "source": "s", "done_at": None}]
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: rows)
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "<script>alert" not in r.text          # raw tag must not appear
    assert "&lt;script&gt;" in r.text             # escaped form present


# ---------------------------------------------------------------------------
# PP-AGENTTRACE-001 Phase 3 — GET /form/runs (#1582), no Bearer (network trust)
# ---------------------------------------------------------------------------

_RUN_ROWS = [
    {
        "run_id": "abcdef0123456789",
        "parent_run_id": None,
        "agent_type": "tgw-coder",
        "todo_id": 1580,
        "pp_ref": "PP-AGENTTRACE-001",
        "host": "tgw-prod",
        "git_branch": "todo/1580-agent-trace-phase1",
        "started_at": datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 7, 20, 10, 5, tzinfo=timezone.utc),
        "status": "completed",
        "summary": "Phase 1 built + merged",
        "transcript_path": "/opt/TGW/var/agent-traces/2026-07-20/abcdef0123456789.jsonl",
    },
    {
        "run_id": "fedcba9876543210",
        "parent_run_id": None,
        "agent_type": "claude-session",
        "todo_id": None,
        "pp_ref": None,
        "host": "tgw-prod",
        "git_branch": "catio-nix-0.0.1-alpha",
        "started_at": datetime(2026, 7, 20, 11, 0, tzinfo=timezone.utc),
        "ended_at": None,
        "status": "running",
        "summary": "",
        "transcript_path": None,
    },
    {
        "run_id": "1122334455667788",
        "parent_run_id": None,
        "agent_type": "aider",
        "todo_id": 1577,
        "pp_ref": "PP-SIMPLEJOBS-001",
        "host": "tgw-prod",
        "git_branch": "todo/1577-fix",
        "started_at": datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 7, 19, 9, 2, tzinfo=timezone.utc),
        "status": "failed",
        "summary": "<b>bad</b> exit",
        "transcript_path": "/opt/TGW/var/agent-traces/2026-07-19/1122334455667788.jsonl",
    },
]


def test_runs_form_renders_rows(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: list(_RUN_ROWS))
    r = client.get("/form/runs")  # no auth header — network trust
    assert r.status_code == 200
    text = r.text
    assert "Agent Runs" in text
    assert "tgw-coder" in text and "claude-session" in text and "aider" in text
    assert "3 run(s)" in text
    assert "PP-AGENTTRACE-001" in text
    assert "#1580" in text


def test_runs_form_empty_all_clear(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: [])
    r = client.get("/form/runs")
    assert r.status_code == 200
    assert "No agent runs recorded" in r.text


def test_runs_form_db_error_still_200(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine

    def _boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(state_machine, "list_agent_runs", _boom)
    r = client.get("/form/runs")
    assert r.status_code == 200
    assert "unavailable" in r.text.lower()


def test_runs_form_requires_session(client):
    r = client.get("/form/runs", follow_redirects=False)
    assert r.status_code == 303
    assert "/login" in r.headers["location"]


def test_runs_form_escapes_html(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    rows = [dict(_RUN_ROWS[2])]
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: rows)
    r = client.get("/form/runs")
    assert r.status_code == 200
    assert "<b>bad</b>" not in r.text
    assert "&lt;b&gt;bad&lt;/b&gt;" in r.text


def test_runs_form_status_color_coding(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: list(_RUN_ROWS))
    r = client.get("/form/runs")
    assert 'st-completed">completed' in r.text
    assert 'st-failed">failed' in r.text
    assert 'st-running">running' in r.text


def test_runs_form_has_filter_controls(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: list(_RUN_ROWS))
    r = client.get("/form/runs")
    assert 'id="runs-agent-sel"' in r.text
    assert 'id="runs-status-sel"' in r.text
    assert 'id="runs-search"' in r.text


def test_runs_form_transcript_path_shown_as_text(client, monkeypatch):
    """Transcript paths render as escaped text, not a clickable link — no
    file-serving route exists for /opt/TGW/var/agent-traces/ (packet spec
    item 4, flagged limitation)."""
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: list(_RUN_ROWS))
    r = client.get("/form/runs")
    assert "/opt/TGW/var/agent-traces/2026-07-20/abcdef0123456789.jsonl" in r.text
    assert '<a href="/opt/TGW/var/agent-traces' not in r.text


def test_runs_form_uses_static_css(client, monkeypatch):
    _login(client)
    from tgw.queue import state_machine
    monkeypatch.setattr(state_machine, "list_agent_runs", lambda *a, **k: list(_RUN_ROWS))
    r = client.get("/form/runs")
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text


def test_render_runs_html_pure():
    """Direct unit test of _render_runs_html() with synthetic rows — pure
    enough to test without the HTTP layer, per packet acceptance item 5."""
    from tgw.http_server import _render_runs_html

    html_out = _render_runs_html(list(_RUN_ROWS))
    assert "Agent Runs" in html_out
    assert "3 run(s)" in html_out
    assert "abcdef012345" in html_out  # truncated run id (12 chars)
    assert '<b>bad</b>' not in html_out
    assert '&lt;b&gt;bad&lt;/b&gt;' in html_out


def test_render_runs_html_empty():
    from tgw.http_server import _render_runs_html

    html_out = _render_runs_html([])
    assert "No agent runs recorded" in html_out


# ---------------------------------------------------------------------------
# PP-CAPTURE-001 — GET/POST /form/suggest (Round 5 #44) — no Bearer (network trust)
# ---------------------------------------------------------------------------

def _suggestions_file(env):
    return env["cfg"]["plan_vault_path"] / "suggestions" / "SUGGESTIONS.md"


def test_suggest_form_html(client):
    _login(client)
    r = client.get("/form/suggest")  # no auth header — network trust
    assert r.status_code == 200
    assert 'name="text"' in r.text
    assert 'action="/form/suggest"' in r.text


def test_suggest_post_appends_with_punctuation(env):
    _login(env["client"])
    tricky = 'add "quotes" & $(subshell) `backticks` | pipes; --flags \'single\''
    r = env["client"].post("/form/suggest", data={"text": tricky})
    assert r.status_code == 200
    assert "added:" in r.text
    content = _suggestions_file(env).read_text(encoding="utf-8")
    assert tricky in content                       # written verbatim
    assert content.startswith("- [ ] ")            # checklist format intact


def test_suggest_post_collapses_newlines_to_one_line(env):
    _login(env["client"])
    r = env["client"].post(
        "/form/suggest", data={"text": "line one\r\nline two\n\nline three"}
    )
    assert r.status_code == 200
    content = _suggestions_file(env).read_text(encoding="utf-8")
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1                         # one checklist line, not four
    assert "line one line two line three" in lines[0]


def test_suggest_post_empty_writes_nothing(env):
    _login(env["client"])
    r = env["client"].post("/form/suggest", data={"text": "   "})
    assert r.status_code == 200
    assert "nothing written" in r.text
    assert not _suggestions_file(env).exists()


def test_suggest_post_escapes_echo_but_writes_raw(env):
    _login(env["client"])
    payload = "<script>alert(1)</script>"
    r = env["client"].post("/form/suggest", data={"text": payload})
    assert r.status_code == 200
    assert payload not in r.text                   # echo is HTML-escaped
    assert "&lt;script&gt;" in r.text
    assert payload in _suggestions_file(env).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# POST /api/suggest — JSON suggest endpoint for nav popup (PP-CAPTURE-001 3n)
# ---------------------------------------------------------------------------

def test_api_suggest_json_writes_suggestion(env):
    """POST /api/suggest with JSON body appends to SUGGESTIONS.md, returns {ok}."""
    r = env["client"].post("/api/suggest", json={"text": "json suggest test"})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "written" in d
    content = _suggestions_file(env).read_text(encoding="utf-8")
    assert "json suggest test" in content


def test_api_suggest_json_collapses_whitespace(env):
    r = env["client"].post("/api/suggest", json={"text": "line one\nline two"})
    assert r.status_code == 200
    content = _suggestions_file(env).read_text(encoding="utf-8")
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "line one line two" in lines[0]


def test_api_suggest_json_empty_returns_400(env):
    r = env["client"].post("/api/suggest", json={"text": "   "})
    assert r.status_code == 400


def test_api_suggest_json_invalid_body_returns_400(env):
    r = env["client"].post("/api/suggest", content=b"not json",
                           headers={"Content-Type": "application/json"})
    assert r.status_code == 400


def test_api_suggest_no_auth_required(env):
    """Endpoint is network-trust — no Bearer token needed."""
    r = env["client"].post("/api/suggest", json={"text": "no auth test"})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# /form/todos enhancements — filter, expand, copy, PP-ref (PP-EDITOR-001 3n)
# ---------------------------------------------------------------------------

_TODO_ROWS_WITH_PPREF = [
    {"id": 55, "agent": "claude", "priority": 10,
     "body": "[PP-EDITOR-001] Fix the browse page", "source": "session", "done_at": None},
    {"id": 56, "agent": "claude", "priority": 20,
     "body": "PP-TODO-001/PP-CAPTURE-001: add filter", "source": "session", "done_at": None},
    {"id": 57, "agent": "admin", "priority": 30,
     "body": "Manual warehouse check", "source": "plan", "done_at": None},
]


def test_todos_form_has_agent_filter(client, monkeypatch):
    """Agent filter dropdown is present with each unique agent as an option."""
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: list(_TODO_ROWS_WITH_PPREF))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert 'id="agent-sel"' in r.text
    assert 'value="claude"' in r.text
    assert 'value="admin"' in r.text


def test_todos_form_pp_refs_extracted(client, monkeypatch):
    """PP-XXX-NNN references in task bodies render as pp-badge links."""
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: list(_TODO_ROWS_WITH_PPREF))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "pp-badge" in r.text
    assert "PP-EDITOR-001" in r.text
    assert "PP-TODO-001" in r.text


def test_todos_form_copy_btn_present(client, monkeypatch):
    """Each task row has a copy button with data-body attribute."""
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: list(_TODO_ROWS_WITH_PPREF))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert 'class="copy-btn"' in r.text
    assert "data-body=" in r.text


def test_todos_form_data_agent_attrs(client, monkeypatch):
    """Rows and groups have data-agent attributes for client-side filtering."""
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: list(_TODO_ROWS_WITH_PPREF))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert 'data-agent="claude"' in r.text
    assert 'data-agent="admin"' in r.text


# ---------------------------------------------------------------------------
# /form/items browse — load-more and action buttons (PP-EDITOR-001 3n)
# ---------------------------------------------------------------------------

def test_browse_has_load_more_js(client):
    """Browse page includes loadMore() function and card action controls."""
    _login(client)
    r = client.get("/form/items")
    assert r.status_code == 200
    assert "loadMore" in r.text
    assert "card-btns" in r.text
    assert "csel" in r.text   # action dropdown class
    assert "crun" in r.text   # run button class


def test_browse_uses_card_inner_link(client):
    """Cards use .card-inner anchor for navigation, not the outer .card."""
    _login(client)
    r = client.get("/form/items")
    assert r.status_code == 200
    assert "card-inner" in r.text
    # The old pattern (whole card = link) should not appear
    assert 'class="card"' not in r.text or 'card-inner' in r.text


# ---------------------------------------------------------------------------
# GET /api/health — platform health check
# ---------------------------------------------------------------------------

_HEALTH_OK = {
    "ok": True,
    "checks": [{"ok": True, "check": "tgw_api", "detail": "ok", "elapsed_ms": 1.0}],
    "failed": [],
    "elapsed_ms": 5.0,
}

_HEALTH_FAIL = {
    "ok": False,
    "checks": [{"ok": False, "check": "postgres", "detail": "conn refused", "elapsed_ms": 1.0}],
    "failed": ["postgres"],
    "elapsed_ms": 5.0,
}


def test_health_ok_200(client, monkeypatch):
    import tgw.health as health
    monkeypatch.setattr(health, "check_all", lambda cfg, **kw: dict(_HEALTH_OK))
    r = client.get("/api/health", headers=AUTH_HEADERS)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "dead_letter_count" in data
    assert isinstance(data["dead_letter_count"], int)


def test_health_fail_503(client, monkeypatch):
    import tgw.health as health
    monkeypatch.setattr(health, "check_all", lambda cfg, **kw: dict(_HEALTH_FAIL))
    r = client.get("/api/health", headers=AUTH_HEADERS)
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert detail["ok"] is False
    assert "postgres" in detail["failed"]


def test_health_dead_letter_count_in_response(client, monkeypatch, queue_rows):
    import tgw.health as health
    monkeypatch.setattr(health, "check_all", lambda cfg, **kw: dict(_HEALTH_OK))
    queue_rows.append((7,))   # fetchone() returns (7,) → dead_letter_count = 7
    r = client.get("/api/health", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["dead_letter_count"] == 7


def test_health_dead_letter_zero_on_postgres_error(client, monkeypatch):
    import tgw.health as health
    monkeypatch.setattr(health, "check_all", lambda cfg, **kw: dict(_HEALTH_OK))
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: (_ for _ in ()).throw(Exception("pg down")),
    )
    r = client.get("/api/health", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["dead_letter_count"] == 0


def test_health_requires_auth(client):
    r = client.get("/api/health")
    assert r.status_code in (401, 403)


def test_health_rejects_bad_token(client):
    r = client.get("/api/health", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/catalog/snapshot — PP-PORTABLE-CATALOG-001 Phase 2
# ---------------------------------------------------------------------------

def test_catalog_snapshot_returns_sqlite_bytes(client):
    r = client.get("/api/catalog/snapshot", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    # SQLite magic bytes
    assert r.content[:16] == b"SQLite format 3\x00"


def test_catalog_snapshot_requires_auth(client):
    r = client.get("/api/catalog/snapshot")
    assert r.status_code in (401, 403)


def test_catalog_snapshot_503_when_catalog_missing(env, monkeypatch):
    monkeypatch.setattr(
        http_server, "_cfg",
        {**env["cfg"], "sqlite_catalog_path": env["cfg"]["sqlite_catalog_path"].parent / "missing.db"},
    )
    r = env["client"].get("/api/catalog/snapshot", headers=AUTH_HEADERS)
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# PP-EDITOR-001 Phase 3a — static files + refactored /form/ pages
# ---------------------------------------------------------------------------

def test_static_tgw_css(client):
    r = client.get("/static/tgw.css")
    assert r.status_code == 200
    assert "font-family" in r.text
    assert "system-ui" in r.text


def test_static_nav_css(client):
    r = client.get("/static/nav.css")
    assert r.status_code == 200
    assert "tgw-nav" in r.text
    assert "nav-dropdown" in r.text


def test_static_tgw_js(client):
    r = client.get("/static/tgw.js")
    assert r.status_code == 200
    assert "escapeHtml" in r.text
    assert "initChips" in r.text
    assert "authHeaders" in r.text


def test_static_nav_js(client):
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "tgw-nav" in r.text
    assert "nav-dropdown" in r.text
    assert "/form/items" in r.text


def test_intake_form_uses_static_css(env):
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    # Base CSS must not be embedded inline
    assert 'font-family:system-ui' not in r.text
    # Page-specific JS still works (initChips call present)
    assert 'initChips' in r.text


def test_bulk_form_uses_static_css(client):
    _login(client)
    r = client.get("/form/bulk")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text
    # escapeHtml no longer defined inline — comes from tgw.js
    assert 'function escapeHtml' not in r.text
    # initChips used for field chip selector
    assert 'initChips' in r.text


def test_items_browse_uses_static_css(client):
    _login(client)
    r = client.get("/form/items")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text
    # esc is now an alias for the shared escapeHtml
    assert 'const esc=escapeHtml' in r.text


def test_items_browse_price_handles_empty_and_null():
    """ISS-011: _cardHtml price must not produce $NaN for null/empty/non-numeric price."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent / "src/tgw/http_server.py").read_text()
    # isNaN guard replaces null-check so empty strings don't produce NaN
    assert "isNaN(pf)" in src
    assert "parseFloat(it.price)" in src
    # old guard that failed for empty-string price must be gone
    assert "it.price!=null?'$'" not in src


def test_todos_form_uses_static_css(client, monkeypatch):
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text


def test_todos_form_error_uses_static_css(client, monkeypatch):
    _login(client)
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert 'font-family:system-ui' not in r.text


def test_suggest_form_uses_static_css(client):
    _login(client)
    r = client.get("/form/suggest")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text


def test_size_classes_form_lists_configured_ranges(env, tmp_path):
    config_path = tmp_path / "tgw-api-config.json"
    config_path.write_text(json.dumps({
        "unrelated": {"preserve": True},
        "size_class_ranges": {
            "packet": {
                "weight_oz": [2, 8],
                "dims_in": {"l": [4, 12], "w": [3, 9], "h": [None, 2]},
            }
        },
    }), encoding="utf-8")
    env["cfg"]["config_path"] = config_path
    _login(env["client"])

    r = env["client"].get("/form/size-classes")

    assert r.status_code == 200
    assert "packet" in r.text
    assert "Weight min" in r.text
    assert '/static/tgw.css' in r.text


def test_size_classes_form_adds_class_and_preserves_other_config(env, tmp_path):
    config_path = tmp_path / "tgw-api-config.json"
    config_path.write_text(json.dumps({"unrelated": [1, 2, 3]}), encoding="utf-8")
    env["cfg"]["config_path"] = config_path
    _login(env["client"])

    r = env["client"].post("/form/size-classes", data={
        "name": "medium_box", "weight_min": "4", "weight_max": "16.5",
        "length_min": "", "length_max": "14", "width_min": "3",
        "width_max": "10", "height_min": "2", "height_max": "8",
    })

    assert r.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["unrelated"] == [1, 2, 3]
    assert saved["size_class_ranges"]["medium_box"] == {
        "weight_oz": [4.0, 16.5],
        "dims_in": {"l": [None, 14.0], "w": [3.0, 10.0], "h": [2.0, 8.0]},
    }


def test_size_classes_form_rejects_reversed_range_without_writing(env, tmp_path):
    config_path = tmp_path / "tgw-api-config.json"
    original = {"size_class_ranges": {"flat": {"weight_oz": [None, None]}}}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    env["cfg"]["config_path"] = config_path
    _login(env["client"])

    r = env["client"].post("/form/size-classes", data={
        "name": "flat", "weight_min": "9", "weight_max": "2",
    })

    assert r.status_code == 400
    assert "minimum cannot exceed maximum" in r.text
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_item_detail_uses_static_css(env):
    _login(env["client"])
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text


def test_item_detail_no_stale_dole_cycle_claim(env):
    """audit#1143 #1113: approveForListing() was dead code (never called --
    the checkbox uses toggleApprove()) still shipping a confirm() dialog
    that falsely claimed items 'will go live at the next dole cycle' even
    though the ebay_dole worker was never installed. Removed."""
    _login(env["client"])
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert "next dole cycle" not in r.text
    assert "approveForListing" not in r.text


# ---------------------------------------------------------------------------
# GET /form/items/{sku} — eBay deep links (PP-EDITOR-001 Phase 3m)
# ---------------------------------------------------------------------------

def test_item_detail_ebay_deeplinks_active(env):
    """View on eBay, Seller Hub, and Messages links appear for an active listing."""
    _login(env["client"])
    sku = "tgw20260614110000010"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Deep Link Test Widget",
        "location": "X9",
        "ebay_listing": {
            "listing_id": "987654321",
            "listing_url": "https://www.ebay.com/itm/987654321",
            "status": "Active",
            "live_price": 24.99,
        },
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert "View on eBay" in r.text
    assert "https://www.ebay.com/itm/987654321" in r.text
    assert "Seller Hub" in r.text
    assert "https://www.ebay.com/sh/lst/active?keyword=987654321" in r.text
    assert "eBay Messages" in r.text
    assert "https://messages.ebay.com/" in r.text
    assert "offer-badge-wrap" in r.text
    # session-cookie auth only (s42/43 login wall) — no embedded API key
    assert API_KEY not in r.text


def test_item_detail_ebay_deeplinks_sold(env):
    """Messages link hidden for sold/inactive listing; View on eBay + Seller Hub still shown."""
    _login(env["client"])
    sku = "tgw20260614110000011"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Sold Widget",
        "location": "X9",
        "ebay_listing": {
            "listing_id": "111222333",
            "listing_url": "https://www.ebay.com/itm/111222333",
            "status": "Sold",
            "live_price": 15.00,
        },
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert "View on eBay" in r.text
    assert "Seller Hub" in r.text
    assert "eBay Messages" not in r.text


def test_item_detail_no_ebay_listing(env):
    """No eBay deep link buttons shown when ebay_listing is absent."""
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert "View on eBay" not in r.text
    assert "Seller Hub" not in r.text
    assert "eBay Messages" not in r.text
    assert "offer-badge-wrap" not in r.text


def test_item_detail_no_listing_url_only_id(env):
    """Seller Hub shown when listing_id present but no listing_url."""
    _login(env["client"])
    sku = "tgw20260614110000012"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "ID Only Widget",
        "location": "X9",
        "ebay_listing": {
            "listing_id": "444555666",
            "status": "Active",
        },
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert "View on eBay" not in r.text
    assert "Seller Hub" in r.text
    assert "eBay Messages" in r.text
    assert "offer-badge-wrap" in r.text


def test_item_detail_pipeline_tooltips(env, monkeypatch):
    """Worker queue names in pipeline jobs section carry hover tooltip text."""
    _login(env["client"])
    rows = [
        {
            "queue_name": "ai_identify",
            "state": "succeeded",
            "created_at": None,
            "updated_at": None,
            "finished_at": None,
            "error_code": None,
            "error_detail": None,
        }
    ]
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _FakeConn(rows))
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    # The ai_identify tooltip text should appear as a title attribute
    assert "Sends photo to Ollama" in r.text
    assert 'title="' in r.text


def test_item_detail_unknown_worker_no_tooltip(env, monkeypatch):
    """Unknown queue names render without a title attribute (no crash)."""
    _login(env["client"])
    rows = [
        {
            "queue_name": "some_future_worker",
            "state": "running",
            "created_at": None,
            "updated_at": None,
            "finished_at": None,
            "error_code": None,
            "error_detail": None,
        }
    ]
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _FakeConn(rows))
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert "some_future_worker" in r.text


def test_offers_form_sku_filter(env):
    """/form/offers?sku=X loads and contains SKU filter JS variables."""
    _login(env["client"])
    from unittest.mock import patch

    import tgw.http_server as _hs

    # Stub cmd_offers_list so the offers API call won't hit eBay
    with patch.object(_hs, "_cfg", env["cfg"]):
        r = env["client"].get("/form/offers?sku=tgw20260614110000010")
    assert r.status_code == 200
    assert "sku-filter-bar" in r.text
    assert "sku-filter-val" in r.text
    assert "_skuFilter" in r.text


# ---------------------------------------------------------------------------
# GET /api/dashboard — PP-EDITOR-001 Phase 3b
# ---------------------------------------------------------------------------

def _make_catalog_with_data(db_path: Path, rows_with_data):
    """Create a SQLite catalog including the full-JSON 'data' column."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE catalog ("
        "sku TEXT PRIMARY KEY, title TEXT, location TEXT, status TEXT, "
        "price REAL, qty INTEGER, image TEXT, attribute_set TEXT, data TEXT NOT NULL DEFAULT '{}')"
    )
    con.executemany(
        "INSERT INTO catalog (sku, title, location, status, price, qty, image, data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows_with_data,
    )
    con.commit()
    con.close()


@pytest.fixture
def dashboard_env(tmp_path, monkeypatch):
    """Fixture for dashboard tests: catalog with data column, stubbed Postgres + eBay + systemctl."""
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()
    catalog_path = tmp_path / "catalog.sqlite"

    # Items:
    #   SKU_A — has draft_listing, no offer_id  → needs_review
    #   SKU_B — has draft_listing + offer_id UNPUBLISHED + ready_at  → ready
    #   SKU_C — has revision_draft, no image  → has_revision_draft + needs_photos
    #   SKU_D — plain in-stock, has image  → no special state
    sku_a = "tgw20260101120000001"
    sku_b = "tgw20260201120000002"
    sku_c = "tgw20260301120000003"
    sku_d = "tgw20260401120000004"

    data_a = json.dumps({"draft_listing": {"title": "Foo"}, "ebay_offer": {}})
    data_b = json.dumps({
        "draft_listing": {"title": "Bar"},
        "ebay_offer": {
            "offer_id": "OFF1",
            "status": "UNPUBLISHED",
            "ready_at": "2026-06-01T00:00:00+00:00",
        },
    })
    data_c = json.dumps({"revision_draft": {"delta": {"title": "New"}}})
    data_d = json.dumps({})

    _make_catalog_with_data(catalog_path, [
        (sku_a, "Widget A", "A1", "In Stock", 9.99,  1, "",      data_a),
        (sku_b, "Gadget B", "B2", "Staged",  19.99,  1, "b.jpg", data_b),
        (sku_c, "Part C",   "C3", "In Stock",  5.00,  1, "",      data_c),
        (sku_d, "Box D",    "D4", "In Stock",  7.50,  1, "d.jpg", data_d),
    ])

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": itemdata_root,
        "postgres_dsn": "postgresql://fake/db",
        "thumbnail_root": tmp_path / "thumbs",
        "pretty": True,
        "raw": {},
    }

    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(http_server, "_pending_offers_cache", None)
    monkeypatch.setattr(http_server, "_pending_offers_cache_at", 0.0)

    # Postgres stub: fetchone returns (0,) → dead_letter_count = 0
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )

    client = TestClient(http_server.app)
    return {
        "client": client,
        "skus": (sku_a, sku_b, sku_c, sku_d),
    }


def test_dashboard_returns_counts(dashboard_env, monkeypatch):
    """Dashboard returns correct counts from SQLite and stubs."""
    # Mock eBay get_best_offers to return 2 pending offers
    import tgw.apis.ebay.trading as _trading
    monkeypatch.setattr(_trading, "get_best_offers", lambda cfg, status="All": [{"offer_id": "1"}, {"offer_id": "2"}])

    # Mock systemctl: 18 of 20 queues active
    from tgw.queue import WORKER_QUEUES
    total_q = len(WORKER_QUEUES)
    active_lines = "\n".join(["active"] * (total_q - 2) + ["inactive", "failed"])

    def _fake_run(cmd, **kwargs):
        class _R:
            stdout = active_lines
            returncode = 1
        return _R()

    monkeypatch.setattr(http_server.subprocess, "run", _fake_run)

    r = dashboard_env["client"].get("/api/dashboard", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()

    assert body["ok"] is True
    assert body["needs_review"] == 1        # only SKU_A (draft_listing + no offer_id)
    assert body["needs_photos"] == 2        # SKU_A and SKU_C have no image
    assert body["has_revision_draft"] == 1  # only SKU_C
    assert body["ready_count"] == 1         # only SKU_B (offer_id + UNPUBLISHED + ready_at)
    assert body["dead_letter_count"] == 0   # postgres stub returns 0
    assert body["pending_offers"] == 2
    assert body["worker_health"]["total"] == total_q
    assert body["worker_health"]["up"] == total_q - 2


def test_dashboard_requires_auth(dashboard_env):
    r = dashboard_env["client"].get("/api/dashboard")
    assert r.status_code in (401, 403)


def test_dashboard_ebay_failure_returns_none(dashboard_env, monkeypatch):
    """When eBay API fails, pending_offers is None (not an error)."""
    import tgw.apis.ebay.trading as _trading
    monkeypatch.setattr(_trading, "get_best_offers",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("timeout")))

    def _fake_run(cmd, **kwargs):
        class _R:
            stdout = ""
            returncode = 0
        return _R()
    monkeypatch.setattr(http_server.subprocess, "run", _fake_run)

    r = dashboard_env["client"].get("/api/dashboard", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["pending_offers"] is None


def test_dashboard_fallback_without_data_column(tmp_path, monkeypatch):
    """When the catalog has no 'data' column, json_extract counts return None."""
    catalog_path = tmp_path / "catalog.sqlite"
    # Deliberately build a catalog WITHOUT the data column to test dashboard fallback.
    _con = sqlite3.connect(str(catalog_path))
    _con.execute(
        "CREATE TABLE catalog ("
        "sku TEXT, title TEXT, location TEXT, status TEXT, "
        "price REAL, qty INTEGER, image TEXT)"
    )
    _con.executemany(
        "INSERT INTO catalog (sku, title, location, status, price, qty, image) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (SKU_A, "Widget", "A1", "In Stock", 9.99, 1, ""),
            (SKU_B, "Gadget", "B2", "Staged",  19.99, 1, "b.jpg"),
        ],
    )
    _con.commit()
    _con.close()

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": tmp_path / "ItemData",
        "postgres_dsn": "postgresql://fake/db",
        "thumbnail_root": tmp_path / "thumbs",
        "pretty": True,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(http_server, "_pending_offers_cache", None)
    monkeypatch.setattr(http_server, "_pending_offers_cache_at", 0.0)
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )

    import tgw.apis.ebay.trading as _trading
    monkeypatch.setattr(_trading, "get_best_offers", lambda *a, **k: [])

    def _fake_run(cmd, **kwargs):
        class _R:
            stdout = ""
            returncode = 0
        return _R()
    monkeypatch.setattr(http_server.subprocess, "run", _fake_run)

    client = TestClient(http_server.app)
    r = client.get("/api/dashboard", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["needs_review"] is None
    assert body["has_revision_draft"] is None
    assert body["ready_count"] is None
    assert body["needs_photos"] == 1   # SKU_A has empty image
    assert body["ok"] is True


# ---------------------------------------------------------------------------
# GET /api/activity — recent queue job completions
# ---------------------------------------------------------------------------

@pytest.fixture
def activity_env(tmp_path, monkeypatch):
    """Minimal env wired to return canned activity rows from psycopg2 stub."""
    itemdata_root = tmp_path / "ItemData"
    itemdata_root.mkdir()
    catalog_path = tmp_path / "catalog.sqlite"
    _make_catalog(catalog_path, [])

    cfg = {
        "sqlite_catalog_path": catalog_path,
        "itemdata_root": itemdata_root,
        "postgres_dsn": "postgresql://fake/db",
        "thumbnail_root": tmp_path / "thumbs",
        "pretty": True,
        "raw": {},
    }

    rows = [
        {
            "job_id": 1,
            "queue_name": "ebay_draft",
            "state": "succeeded",
            "sku": "tgw20260614120000001",
            "finished_at": "2026-06-14T12:00:00+00:00",
            "error_detail": None,
        },
        {
            "job_id": 2,
            "queue_name": "ebay_stage",
            "state": "failed",
            "sku": "tgw20260614110000002",
            "finished_at": "2026-06-14T11:00:00+00:00",
            "error_detail": "boom",
        },
        {
            "job_id": 3,
            "queue_name": "catalog_rebuild",
            "state": "succeeded",
            "sku": None,
            "finished_at": "2026-06-14T10:00:00+00:00",
            "error_detail": None,
        },
    ]

    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(rows),
    )
    return {"client": TestClient(http_server.app), "rows": rows}


def test_activity_returns_jobs(activity_env):
    r = activity_env["client"].get("/api/activity", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 3
    jobs = body["jobs"]
    assert jobs[0]["queue_name"] == "ebay_draft"
    assert jobs[0]["state"] == "succeeded"
    assert jobs[0]["sku"] == "tgw20260614120000001"
    assert jobs[1]["state"] == "failed"
    assert jobs[1]["error_detail"] == "boom"
    assert jobs[2]["sku"] is None  # catalog_rebuild has no sku


def test_activity_requires_auth(activity_env):
    r = activity_env["client"].get("/api/activity")
    assert r.status_code in (401, 403)


def test_activity_empty(tmp_path, monkeypatch):
    """Empty queue_jobs → ok=True, count=0, jobs=[]."""
    cfg = {
        "sqlite_catalog_path": tmp_path / "catalog.sqlite",
        "itemdata_root": tmp_path / "ItemData",
        "postgres_dsn": "postgresql://fake/db",
        "thumbnail_root": tmp_path / "thumbs",
        "pretty": True,
        "raw": {},
    }
    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(http_server, "_web_password", WEB_KEY)
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )
    client = TestClient(http_server.app)
    r = client.get("/api/activity", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["jobs"] == []


# ---------------------------------------------------------------------------
# GET /form/home — home dashboard page
# ---------------------------------------------------------------------------

def test_home_form_ok(client):
    """Home page returns 200 with all section anchors present."""
    _login(client)
    r = client.get("/form/home")
    assert r.status_code == 200
    assert "id=\"health-strip\"" in r.text
    assert "id=\"action-cards\"" in r.text
    assert "id=\"intake-sku\"" in r.text
    assert "id=\"activity\"" in r.text
    assert "id=\"pm-chat\"" in r.text


def test_home_form_no_auth_required(client):
    """Home page is served without Bearer token (network trust)."""
    r = client.get("/form/home")
    assert r.status_code == 200


def test_home_form_never_embeds_the_real_api_key(client):
    """Form pages authenticate purely via the httponly session cookie (s42/43
    login wall) — no bearer token of any kind is embedded in the rendered
    HTML for client-side JS to read (the old per-session-key-in-HTML pattern
    this test used to check for has been replaced outright, not swapped for
    a different token)."""
    _login(client)
    r = client.get("/form/home")
    assert r.status_code == 200
    assert API_KEY not in r.text


def test_home_form_uses_static_css(client):
    _login(client)
    r = client.get("/form/home")
    assert "/static/tgw.css" in r.text
    assert "/static/nav.css" in r.text
    assert "/static/tgw.js" in r.text
    assert "/static/nav.js" in r.text


def test_home_form_has_start_links(client):
    """Start-here section links to the key operational pages."""
    _login(client)
    r = client.get("/form/home")
    assert "/form/items" in r.text
    assert "/form/bulk" in r.text
    assert "/form/todos" in r.text
    assert "/form/suggest" in r.text


def test_nav_has_home_link():
    """nav.js updated to include a Home link."""
    nav_src = (
        __import__("pathlib").Path(__file__).parent.parent
        / "src/tgw/static/nav.js"
    ).read_text()
    assert "/form/home" in nav_src


def test_nav_has_pm_chat_link():
    """nav.js includes PM Chat link that opens the modal overlay."""
    p = __import__("pathlib").Path(__file__).parent.parent / "src/tgw/static/nav.js"
    nav_src = p.read_text()
    assert "/form/pm-chat" in nav_src
    assert "pm-overlay" in nav_src
    assert "pm-modal" in nav_src
    assert "_pmShow" in nav_src


def test_nav_css_has_pm_overlay_styles():
    """nav.css includes the PM chat overlay, modal, and toast styles."""
    p = __import__("pathlib").Path(__file__).parent.parent / "src/tgw/static/nav.css"
    css = p.read_text()
    assert ".pm-overlay" in css
    assert ".pm-modal" in css
    assert ".pm-modal-messages" in css
    assert ".pm-modal-input-row" in css
    assert ".pm-toast" in css


def test_nav_pm_storage_key_distinct_from_home():
    """nav.js uses a different sessionStorage key than the home page widget."""
    import pathlib
    root = pathlib.Path(__file__).parent.parent
    nav_src = (root / "src/tgw/static/nav.js").read_text()
    home_src = (root / "src/tgw/http_server.py").read_text()
    # nav.js must use the -nav suffix key
    assert "tgw-pm-h-nav" in nav_src
    # home page uses the base key
    assert "'tgw-pm-h'" in home_src
    # nav.js must NOT use the bare base key (would collide on /form/home)
    assert "'tgw-pm-h'" not in nav_src


def test_nav_pm_chat_intercepts_link():
    """nav.js intercepts the /form/pm-chat link click to open modal, not navigate."""
    p = __import__("pathlib").Path(__file__).parent.parent / "src/tgw/static/nav.js"
    nav_src = p.read_text()
    # The event listener must preventDefault on the pm-chat link
    assert "_pmLink" in nav_src
    assert "preventDefault" in nav_src


# ---------------------------------------------------------------------------
# POST /api/pm/chat — PM chat (PP-EDITOR-001 Phase 3d)
# ---------------------------------------------------------------------------

def test_pm_chat_no_auth_rejected(client):
    r = client.post("/api/pm/chat", json={"message": "hi"})
    assert r.status_code in (401, 403)


def test_pm_chat_openrouter_called(env, monkeypatch):
    """pm_chat calls OpenRouter and returns {ok, message, actions}."""
    client = env["client"]

    fake_response_text = (
        "There are 2 open todos and the queue is idle.\n"
        "ACTIONS: [{\"type\": \"none\"}]"
    )

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["messages"] = json.get("messages", [])
        captured["model"] = json.get("model")

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self_inner):
                return {
                    "choices": [{"message": {"content": fake_response_text}}]
                }

        return _FakeResp()

    import tgw.http_server as _hs
    from tgw.apis import llm as _llm
    monkeypatch.setattr(_hs, "_build_pm_context", lambda: "todos: 2")
    monkeypatch.setattr(_llm.requests, "post", fake_post)
    monkeypatch.setattr(_llm, "get_task_model", lambda cfg, task: ("openrouter", "test/model"))
    monkeypatch.setattr(_llm, "_load_openrouter_key", lambda cfg: "test-or-key")

    r = client.post(
        "/api/pm/chat",
        json={"message": "how many todos?", "history": []},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "todos" in body["message"].lower()
    assert isinstance(body["actions"], list)
    assert body["actions"][0]["type"] == "none"

    # System message includes LIVE SYSTEM STATUS
    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert "LIVE SYSTEM STATUS" in sys_msg["content"]

    # User message is last
    user_msg = captured["messages"][-1]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "how many todos?"


def test_pm_chat_history_threaded(env, monkeypatch):
    """History messages are prepended between system and user turn."""
    client = env["client"]

    import tgw.http_server as _hs
    from tgw.apis import llm as _llm
    monkeypatch.setattr(_hs, "_build_pm_context", lambda: "idle")

    captured_msgs = []

    def fake_post(url, headers, json, timeout):
        captured_msgs.extend(json.get("messages", []))

        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self_inner):
                return {"choices": [{"message": {"content": "ok\nACTIONS: [{\"type\":\"none\"}]"}}]}

        return _R()

    monkeypatch.setattr(_llm.requests, "post", fake_post)
    monkeypatch.setattr(_llm, "get_task_model", lambda cfg, task: ("openrouter", "m"))
    monkeypatch.setattr(_llm, "_load_openrouter_key", lambda cfg: "k")

    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
    ]
    client.post(
        "/api/pm/chat",
        json={"message": "second question", "history": history},
        headers=AUTH_HEADERS,
    )
    roles = [m["role"] for m in captured_msgs]
    assert roles == ["system", "user", "assistant", "user"]


def test_pm_chat_actions_parsed(env, monkeypatch):
    """add_todo action in ACTIONS block is returned in the response."""
    client = env["client"]

    import tgw.http_server as _hs
    from tgw.apis import llm as _llm
    monkeypatch.setattr(_hs, "_build_pm_context", lambda: "idle")

    action_payload = [{"type": "add_todo", "agent": "claude", "body": "Fix it", "priority": 30}]
    resp_text = "You have dead letters.\nACTIONS: " + json.dumps(action_payload)

    def fake_post(url, headers, json, timeout):
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self_inner):
                return {"choices": [{"message": {"content": resp_text}}]}
        return _R()

    monkeypatch.setattr(_llm.requests, "post", fake_post)
    monkeypatch.setattr(_llm, "get_task_model", lambda cfg, task: ("openrouter", "m"))
    monkeypatch.setattr(_llm, "_load_openrouter_key", lambda cfg: "k")

    r = client.post("/api/pm/chat", json={"message": "any issues?"}, headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["actions"][0]["type"] == "add_todo"
    assert body["actions"][0]["body"] == "Fix it"


# ---------------------------------------------------------------------------
# POST /api/pm/action — execute a PM-proposed action
# ---------------------------------------------------------------------------

def test_pm_action_add_todo(env, monkeypatch):
    """add_todo action calls todo_add and returns ok."""
    client = env["client"]

    added = {}

    def fake_todo_add(agent, body, priority, source):
        added.update({"agent": agent, "body": body, "priority": priority})
        return {"ok": True, "id": 999, "agent": agent, "body": body, "priority": priority,
                "pp_ref": None, "depends_on": [], "plan_anchor": None}

    import tgw.todo as _todo
    monkeypatch.setattr(_todo, "todo_add", fake_todo_add)

    r = client.post(
        "/api/pm/action",
        json={"type": "add_todo", "agent": "claude", "body": "Check dead letters", "priority": 20},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "999" in body["message"]
    assert added["agent"] == "claude"
    assert added["body"] == "Check dead letters"


def test_pm_action_add_suggestion(env, monkeypatch):
    """add_suggestion action calls cmd_suggest and returns ok."""
    client = env["client"]

    suggested = {}

    def fake_suggest(cfg, text):
        suggested["text"] = text
        return {"ok": True, "written": f"- [ ] 2026-06-14T00:00 :: {text}"}

    from tgw import api as _api
    monkeypatch.setattr(_api, "cmd_suggest", fake_suggest)

    # monkey-patch _cfg to have a plan_vault_path (already set in env)
    # The pm_action endpoint calls cmd_suggest(_cfg, text) so it goes through the monkeypatch
    r = client.post(
        "/api/pm/action",
        json={"type": "add_suggestion", "text": "Try a new pricing strategy"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert suggested["text"] == "Try a new pricing strategy"


def test_pm_action_unknown_type(client):
    r = client.post(
        "/api/pm/action",
        json={"type": "destroy_everything"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 400


def test_pm_action_add_todo_missing_body(client):
    r = client.post(
        "/api/pm/action",
        json={"type": "add_todo", "agent": "claude"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 400


def test_home_has_pm_chat_widget(client):
    """Home page includes the PM chat widget, not the old stub."""
    _login(client)
    r = client.get("/form/home")
    assert "pm-wrap" in r.text
    assert "pm-messages" in r.text
    assert "pm-input" in r.text
    assert "pmSend" in r.text
    # Old stub should be gone
    assert "coming soon" not in r.text


# ---------------------------------------------------------------------------
# GET /docs  /docs/{path}  (PP-EDITOR-001 Phase 3f)
# ---------------------------------------------------------------------------

def _seed_vault(vault_root: Path) -> None:
    """Write a minimal approved standalone Plan materialization for docs tests."""
    (vault_root / "reference" / "runbooks").mkdir(parents=True)
    (vault_root / "plan").mkdir(parents=True)
    (vault_root / "reference" / "runbooks" / "INDEX.md").write_text(
        "# Runbooks Index\n\nList of runbooks.\n", encoding="utf-8"
    )
    (vault_root / "reference" / "runbooks" / "dead-letter-triage.md").write_text(
        "# Dead Letter Triage\n\nSteps to handle dead letters.\n", encoding="utf-8"
    )
    (vault_root / "reference" / "ISSUES.md").write_text(
        "# Issues\n\nKnown issues go here.\n", encoding="utf-8"
    )
    (vault_root / "plan" / "handoff.md").write_text(
        "# Handoff\n\nSession handoff notes.\n", encoding="utf-8"
    )


def _seed_approved_docs(env) -> Path:
    """Bind docs tests to a separate, exact Plan checkout, never source vault."""
    root = env["cfg"].get("standalone_plan_root")
    if root is None:
        root = env["cfg"]["plan_vault_path"].parent / "approved-plan"
        env["cfg"]["standalone_plan_root"] = root
    root = Path(root)
    _seed_vault(root)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Fixture"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "approved"], check=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    env["cfg"].update(
        plan_approved_commit=commit,
        plan_approved_solution_hash="sha256:" + "a" * 64,
    )
    return root


def test_docs_redirect(env):
    """GET /docs redirects to the runbook index."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("/docs/")


def test_docs_renders_markdown(env):
    """GET /docs/{path} renders a markdown file as HTML."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/runbooks/INDEX.md", follow_redirects=True)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Runbooks Index" in r.text
    assert "docs-content" in r.text
    assert "docs-sidebar" in r.text


def test_docs_ignore_same_named_legacy_vault_content(env):
    """A mutable source-vault document cannot shadow the pinned Plan view."""
    approved = _seed_approved_docs(env)
    legacy = env["cfg"]["plan_vault_path"]
    (legacy / "reference" / "runbooks").mkdir(parents=True)
    (legacy / "reference" / "runbooks" / "INDEX.md").write_text("# Legacy poison\n")

    r = env["client"].get("/docs/reference/runbooks/INDEX.md")
    assert r.status_code == 200
    assert "Runbooks Index" in r.text
    assert "Legacy poison" not in r.text
    assert env["cfg"]["plan_approved_commit"] in r.text
    assert approved != legacy


def test_docs_sidebar_lists_docs(env):
    """Sidebar contains links to other vault docs."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/runbooks/INDEX.md", follow_redirects=True)
    assert r.status_code == 200
    assert "dead-letter-triage" in r.text
    assert "ISSUES" in r.text or "Issues" in r.text
    assert "handoff" in r.text or "Handoff" in r.text


def test_docs_active_link_marked(env):
    """The current doc's sidebar link has the active class."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/runbooks/INDEX.md", follow_redirects=True)
    assert r.status_code == 200
    assert 'class="docs-link active"' in r.text


def test_docs_non_md_rejected(env):
    """Non-.md paths return 404."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/runbooks/INDEX.txt")
    assert r.status_code == 404


def test_docs_path_traversal_rejected(env):
    """../  traversal outside the vault root returns 403."""
    client = env["client"]
    vault = _seed_approved_docs(env)
    # Write a file outside the vault to confirm it would exist if served.
    outside = vault.parent / "secret.md"
    outside.write_text("secret", encoding="utf-8")

    r = client.get("/docs/../secret.md")
    assert r.status_code in (403, 404)


def test_docs_missing_file_404(env):
    """Missing file returns 404."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/does-not-exist.md")
    assert r.status_code == 404


def test_docs_uses_static_css(env):
    """Rendered page links the shared tgw.css."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/reference/ISSUES.md", follow_redirects=True)
    assert r.status_code == 200
    assert "tgw.css" in r.text


def test_docs_plan_handoff(env):
    """plan/handoff.md can be fetched via /docs/plan/handoff.md."""
    client = env["client"]
    _seed_approved_docs(env)

    r = client.get("/docs/plan/handoff.md", follow_redirects=True)
    assert r.status_code == 200
    assert "Handoff" in r.text


def test_nav_includes_docs_link(client):
    """nav.js ships a /docs link so every page can reach the doc renderer."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/docs" in r.text


# ---------------------------------------------------------------------------
# GET /api/offers — Best Offers API (PP-EDITOR-001 Phase 3g)
# POST /api/offers/{offer_id}/respond
# GET /form/offers
# ---------------------------------------------------------------------------

def test_get_offers_requires_auth(client):
    r = client.get("/api/offers")
    assert r.status_code in (401, 403)


def test_get_offers_returns_pending(env, monkeypatch):
    """GET /api/offers returns pending offers enriched with location + pct_of_ask."""
    import tgw.http_server as hmod
    import tgw.offers as offers_mod

    client = env["client"]
    fake_offers = [
        {
            "offer_id": "99001",
            "listing_id": "12345678",
            "title": "Red Widget",
            "sku": SKU_A,
            "buyer": "buyer123",
            "offer_price": 9.0,
            "listing_price": 10.0,
            "status": "Pending",
            "expiry": "2026-06-20T00:00:00.000Z",
        }
    ]

    monkeypatch.setattr(hmod, "_offer_location", lambda sku: "A1" if sku == SKU_A else "")
    monkeypatch.setattr(
        offers_mod, "cmd_offers_list",
        lambda cfg, **kw: {"ok": True, "offers": list(fake_offers), "auto_accepted": [], "count": 1},
    )

    r = client.get("/api/offers", headers=AUTH_HEADERS)

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert len(body["offers"]) == 1
    o = body["offers"][0]
    assert o["offer_id"] == "99001"
    assert o["pct_of_ask"] == pytest.approx(90.0)
    assert o["location"] == "A1"


def test_get_offers_error_propagated(env, monkeypatch):
    """If cmd_offers_list returns ok=False, the API mirrors it."""
    import tgw.offers as offers_mod

    monkeypatch.setattr(offers_mod, "cmd_offers_list",
                        lambda cfg, **kw: {"ok": False, "error": "token expired"})

    client = env["client"]
    r = client.get("/api/offers", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "token expired" in body["error"]


def test_respond_offer_dry_run(env, monkeypatch):
    """POST /api/offers/{id}/respond with dry_run=True returns preview without eBay call."""
    import tgw.offers as offers_mod

    client = env["client"]

    def fake_respond(cfg, offer_id, listing_id, action, counter_price=None, *, dry_run=True, by="claude"):
        return {
            "ok": True, "dry_run": dry_run, "offer_id": offer_id,
            "listing_id": listing_id, "action": action,
            "counter_price": counter_price, "by": by, "at": "2026-06-14T12:00:00Z",
            "note": "dry-run: no eBay API call made; add --live to submit",
        }

    monkeypatch.setattr(offers_mod, "cmd_offers_respond", fake_respond)

    r = client.post(
        "/api/offers/99001/respond",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        json={"listing_id": "12345678", "action": "Accept", "dry_run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["action"] == "Accept"
    assert body["offer_id"] == "99001"


def test_respond_offer_counter_price(env, monkeypatch):
    """POST respond with Counter action passes counter_price."""
    import tgw.offers as offers_mod

    received = {}

    def fake_respond(cfg, offer_id, listing_id, action, counter_price=None, *, dry_run=True, by="claude"):
        received.update(action=action, counter_price=counter_price, dry_run=dry_run)
        return {"ok": True, "dry_run": dry_run, "offer_id": offer_id,
                "listing_id": listing_id, "action": action,
                "counter_price": counter_price, "by": by, "at": "2026-06-14T12:00:00Z"}

    monkeypatch.setattr(offers_mod, "cmd_offers_respond", fake_respond)

    client = env["client"]
    r = client.post(
        "/api/offers/99001/respond",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        json={"listing_id": "12345678", "action": "Counter", "counter_price": 27.50, "dry_run": True},
    )
    assert r.status_code == 200
    assert received["action"] == "Counter"
    assert received["counter_price"] == pytest.approx(27.50)


def test_respond_offer_requires_auth(client):
    r = client.post(
        "/api/offers/99001/respond",
        json={"listing_id": "12345678", "action": "Accept"},
    )
    assert r.status_code in (401, 403)


def test_form_offers_renders(client):
    """/form/offers returns HTML with expected structure."""
    _login(client)
    r = client.get("/form/offers")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Best Offers" in r.text
    assert "dry-badge" in r.text
    assert "Go Live" in r.text
    assert "/api/offers" in r.text


def test_form_offers_no_auth_required(client):
    """/form/offers is accessible without Bearer token (network trust)."""
    r = client.get("/form/offers")
    assert r.status_code == 200


def test_nav_includes_offers_link(client):
    """nav.js includes a link to /form/offers."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/offers" in r.text


def test_offers_pct_none_when_prices_missing(env, monkeypatch):
    """pct_of_ask is None when listing_price is absent."""
    import tgw.offers as offers_mod

    offers = [{"offer_id": "1", "listing_id": "x", "title": "t", "sku": SKU_A,
               "buyer": "b", "offer_price": 10.0, "listing_price": None,
               "status": "Pending", "expiry": ""}]

    monkeypatch.setattr(offers_mod, "cmd_offers_list",
                        lambda cfg, **kw: {"ok": True, "offers": offers, "auto_accepted": [], "count": 1})

    client = env["client"]
    r = client.get("/api/offers", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["offers"][0]["pct_of_ask"] is None


# ---------------------------------------------------------------------------
# GET /api/items/pending-revision — revision queue API (PP-EDITOR-001 Phase 3h)
# POST /api/items/{sku}/revision/apply
# ---------------------------------------------------------------------------
# DELETE /api/items/{sku} — soft-delete item
# ---------------------------------------------------------------------------


def test_delete_item_sets_status(client, env):
    """DELETE /api/items/{sku} sets status=deleted in item JSON."""
    r = client.delete(f"/api/items/{SKU_A}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["sku"] == SKU_A

    item_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    import json as _json
    doc = _json.loads(item_path.read_text())
    assert doc["status"] == "deleted"
    assert "deleted_at" in doc


def test_delete_item_requires_auth(client):
    r = client.delete(f"/api/items/{SKU_A}")
    assert r.status_code == 401


def test_delete_item_not_found(client):
    r = client.delete("/api/items/tgw99999999999999999", headers=AUTH_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/items/{sku}/photo-order
# ---------------------------------------------------------------------------


def test_photo_order_saves_to_json(client, env):
    """POST /api/items/{sku}/photo-order persists order list to item JSON."""
    order = ["c.jpg", "a.jpg", "b.jpg"]
    r = client.post(
        f"/api/items/{SKU_A}/photo-order",
        json={"order": order},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["order"] == order

    import json as _json
    doc = _json.loads((env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text())
    assert doc["photo_order"] == order


def test_photo_order_requires_auth(client):
    r = client.post(f"/api/items/{SKU_A}/photo-order", json={"order": ["a.jpg"]})
    assert r.status_code == 401


def test_photo_order_not_found(client):
    r = client.post(
        "/api/items/tgw99999999999999999/photo-order",
        json={"order": ["a.jpg"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404


def test_photo_order_enqueues_via_shared_helper(client, enqueue_calls):
    """audit#1143 (#1198): set_photo_order used to duplicate the
    catalog_rebuild enqueue inline instead of calling
    _enqueue_catalog_rebuild(); verify it now goes through the shared
    helper (same dedupe key, same coalescing behavior).

    PP-CATALOG-INCR-001 CI-3 (2026-07-18): a photo_order write also enqueues
    thumbnail_gen via _apply_patch's new conditional trigger (only fires
    when the write touches image/photo_order).

    PP-CATALOG-INCR-001 CI-4 (2026-07-18): catalog_rebuild's enqueue is now
    a no-op (SQLite catalog stays live via CI-2's synchronous upsert
    instead), so only the thumbnail_gen call remains."""
    r = client.post(
        f"/api/items/{SKU_A}/photo-order",
        json={"order": ["a.jpg"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert len(enqueue_calls) == 1
    tg_kwargs = enqueue_calls[0]["kwargs"]
    assert tg_kwargs["queue_name"] == "thumbnail_gen"
    assert tg_kwargs["dedupe_key"] == f"thumbnail_gen:{SKU_A}"
    assert tg_kwargs["payload"]["sku"] == SKU_A


# ---------------------------------------------------------------------------
# Browse page — per-page selector and view toggle
# ---------------------------------------------------------------------------


def test_browse_has_page_selector(client):
    """Inventory browse page includes items-per-page selector."""
    _login(client)
    r = client.get("/form/items")
    assert r.status_code == 200
    assert 'id="pg-sel"' in r.text
    assert "30/page" in r.text
    assert "60/page" in r.text


def test_browse_has_view_toggle(client):
    """Inventory browse page includes card/list view toggle buttons."""
    _login(client)
    r = client.get("/form/items")
    assert r.status_code == 200
    assert "setView('card')" in r.text
    assert "setView('list')" in r.text
    assert "_rowHtml" in r.text


def test_browse_no_cache_header(client):
    """Form pages return Cache-Control: no-store so stale API keys don't persist."""
    _login(client)
    r = client.get("/form/items")
    assert "no-store" in r.headers.get("cache-control", "")


def test_home_no_cache_header(client):
    _login(client)
    r = client.get("/form/home")
    assert "no-store" in r.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# Item detail — inline editing UI, photo reorder UI
# ---------------------------------------------------------------------------


def test_item_detail_inline_editing_ui(client, env):
    """Item detail page marks editable fields with data-field attribute."""
    _login(client)
    r = client.get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert 'data-field="title"' in r.text
    assert 'data-field="ai_hint"' in r.text
    assert 'data-field="location"' in r.text
    assert "fv-edit" in r.text
    assert "dblclick" in r.text


def test_item_detail_no_cache_header(client, env):
    _login(client)
    r = client.get(f"/form/items/{SKU_A}")
    assert "no-store" in r.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# DELETE /api/items/{sku}/revision
# GET /form/revisions
# ---------------------------------------------------------------------------

_REVISION_DRAFT = {
    "delta": {"title": "New Title"},
    "baseline": {
        "hash": "abcd1234abcd1234",
        "snapshot": {"title": "Old Title"},
    },
    "created_at": "2026-06-14T12:00:00Z",
    "by": "claude",
}


def _write_item_with_revision(itemdata_root, sku, draft=None):
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, "title": "Old Title", "location": "A1"}
    if draft:
        doc["revision_draft"] = draft
    (d / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def _seed_catalog_with_revision(db_path, sku, doc):
    import sqlite3
    with sqlite3.connect(str(db_path)) as con:
        # Ensure catalog table has data column (may differ from http_server fixture)
        try:
            con.execute("ALTER TABLE catalog ADD COLUMN data TEXT")
        except Exception:
            pass
        con.execute(
            "UPDATE catalog SET data = ? WHERE sku = ?",
            (json.dumps(doc), sku),
        )


def test_pending_revision_requires_auth(client):
    r = client.get("/api/items/pending-revision")
    assert r.status_code in (401, 403)


def test_pending_revision_empty_when_no_drafts(env):
    """No items have revision_draft → empty list."""
    client = env["client"]
    r = client.get("/api/items/pending-revision", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["items"] == []


def test_pending_revision_returns_items_with_drafts(env):
    """Items with revision_draft appear in the pending list."""
    doc = _write_item_with_revision(env["itemdata_root"], SKU_A, _REVISION_DRAFT)
    _seed_catalog_with_revision(env["cfg"]["sqlite_catalog_path"], SKU_A, doc)

    client = env["client"]
    r = client.get("/api/items/pending-revision", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert any(i["sku"] == SKU_A for i in body["items"])
    item = next(i for i in body["items"] if i["sku"] == SKU_A)
    assert item["draft"]["delta"]["title"] == "New Title"
    assert item["location"] == "A1"


def test_apply_revision_dry_run(env, monkeypatch):
    """POST /revision/apply with dry_run=True calls cmd_revise_apply."""
    import tgw.revision as rev_mod

    _write_item_with_revision(env["itemdata_root"], SKU_A, _REVISION_DRAFT)

    def fake_apply(cfg, sku, *, dry_run=True, by="claude"):
        return {"ok": True, "sku": sku, "dry_run": dry_run, "applied": False,
                "delta": {}, "diff_lines": ["=== apply diff ==="],
                "blocking_drift": [], "non_blocking_drift": [],
                "composed": {}, "baseline_hash": "x", "current_hash": "x", "hash_match": True}

    monkeypatch.setattr(rev_mod, "cmd_revise_apply", fake_apply)

    client = env["client"]
    r = client.post(
        f"/api/items/{SKU_A}/revision/apply",
        headers={**AUTH_HEADERS, "Content-Type": "application/json"},
        json={"dry_run": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["dry_run"] is True
    assert body["sku"] == SKU_A


def test_apply_revision_requires_auth(client):
    r = client.post(f"/api/items/{SKU_A}/revision/apply",
                    json={"dry_run": True})
    assert r.status_code in (401, 403)


def test_discard_revision_removes_draft(env):
    """DELETE /revision removes revision_draft from the item JSON."""
    _write_item_with_revision(env["itemdata_root"], SKU_A, _REVISION_DRAFT)
    client = env["client"]

    r = client.delete(f"/api/items/{SKU_A}/revision", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["discarded"] is True

    saved = json.loads((env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text())
    assert "revision_draft" not in saved


# test_discard_revision_persists_finding_when_rebuild_enqueue_fails removed
# 2026-07-18 (PP-CATALOG-INCR-001 CI-4): the catalog_rebuild-enqueue + C11
# not-queued guard it exercised was deleted from discard_revision — CI-2's
# synchronous SQLite upsert (already run by the _apply_patch just above it)
# makes that guard dead weight, so there's no longer a queue-enqueue-failure
# path in this endpoint to test.


def test_discard_revision_noop_when_absent(env):
    """DELETE /revision on item without a draft returns ok with a note."""
    _write_item_with_revision(env["itemdata_root"], SKU_A)  # no draft
    client = env["client"]

    r = client.delete(f"/api/items/{SKU_A}/revision", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "note" in body


def test_discard_revision_404_on_missing_sku(client):
    r = client.delete("/api/items/tgwNOPE/revision", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_discard_revision_requires_auth(client):
    r = client.delete(f"/api/items/{SKU_A}/revision")
    assert r.status_code in (401, 403)


def test_form_revisions_renders(client):
    """/form/revisions returns HTML with expected structure."""
    _login(client)
    r = client.get("/form/revisions")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Revisions" in r.text
    assert "dry-badge" in r.text
    assert "Go Live" in r.text
    assert "/api/items/pending-revision" in r.text


def test_form_revisions_no_auth_required(client):
    """/form/revisions is accessible without Bearer token."""
    r = client.get("/form/revisions")
    assert r.status_code == 200


def test_nav_includes_revisions_link(client):
    """nav.js includes a link to /form/revisions."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/revisions" in r.text


# ---------------------------------------------------------------------------
# PP-EDITOR-001 Phase 3i — /form/review post-draft review queue
# ---------------------------------------------------------------------------

_DRAFT_LISTING = {
    "title": "AI-Enhanced Widget Title",
    "category_id": "12345",
    "category_name": "Widgets",
    "condition": "Used - Good",
    "condition_id": 3000,
    "condition_label": "Used",
    "description": "Minor scuff on the base, otherwise excellent.",
    "shipping_profile": "standard",
    "price": 14.99,
    "aspects_required_total": 3,
    "aspects_required_filled": 3,
    "quality": {"score": 82},
}


def _write_item_with_draft(itemdata_root, sku, draft_listing, extra=None):
    d = itemdata_root / sku
    d.mkdir(parents=True, exist_ok=True)
    doc = {"sku": sku, "title": "Widget", "location": "C3", "condition": "Good"}
    doc["draft_listing"] = draft_listing
    if extra:
        doc.update(extra)
    (d / f"{sku}.json").write_text(json.dumps(doc), encoding="utf-8")
    return doc


def _seed_catalog_with_data(db_path, sku, doc):
    import sqlite3
    with sqlite3.connect(str(db_path)) as con:
        try:
            con.execute("ALTER TABLE catalog ADD COLUMN data TEXT")
        except Exception:
            pass
        con.execute(
            "UPDATE catalog SET data = ? WHERE sku = ?",
            (json.dumps(doc), sku),
        )


def test_review_queue_requires_auth(client):
    r = client.get("/api/items/review-queue")
    assert r.status_code in (401, 403)


def test_review_queue_empty_when_no_drafts(env):
    """No items with draft_listing → empty list."""
    client = env["client"]
    r = client.get("/api/items/review-queue", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["items"] == []


def test_review_queue_returns_items_with_draft_listing(env):
    """Items with draft_listing and no offer_id appear in the review queue."""
    doc = _write_item_with_draft(env["itemdata_root"], SKU_A, _DRAFT_LISTING)
    _seed_catalog_with_data(env["cfg"]["sqlite_catalog_path"], SKU_A, doc)

    client = env["client"]
    r = client.get("/api/items/review-queue", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1
    item = body["items"][0]
    assert item["sku"] == SKU_A
    assert item["title"] == "AI-Enhanced Widget Title"
    assert item["price"] == 14.99
    assert item["condition"] == "Used - Good"
    assert item["category_name"] == "Widgets"
    assert item["location"] == "A1"  # from the catalog row seeded by env fixture


def test_review_queue_excludes_staged_items(env):
    """Items that already have an offer_id are not in the review queue."""
    doc = _write_item_with_draft(
        env["itemdata_root"], SKU_A, _DRAFT_LISTING,
        extra={"ebay_offer": {"offer_id": "offer-abc-123", "status": "UNPUBLISHED"}},
    )
    _seed_catalog_with_data(env["cfg"]["sqlite_catalog_path"], SKU_A, doc)

    client = env["client"]
    r = client.get("/api/items/review-queue", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0


def test_approve_action_sets_status_ready(env, monkeypatch):
    """POST /api/items/{sku}/action with action=approve sets status=Ready on the item JSON."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    _write_item_with_draft(env["itemdata_root"], SKU_A, _DRAFT_LISTING)

    client = env["client"]
    r = client.post(
        f"/api/items/{SKU_A}/action",
        json={"action": "approve"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "Ready"

    # Verify the JSON file was updated
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["status"] == "Ready"


def test_approve_action_404_on_missing_sku(client):
    """POST /api/items/{sku}/action approve returns 404 when sku does not exist."""
    r = client.post(
        "/api/items/tgw99999999999999999/action",
        json={"action": "approve"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 404


def test_approve_action_requires_auth(client):
    r = client.post(f"/api/items/{SKU_A}/action", json={"action": "approve"})
    assert r.status_code in (401, 403)


def test_ebay_price_action_enqueues_canonical_item_job(env, enqueue_calls):
    _write_item_with_draft(
        env["itemdata_root"], SKU_A,
        {**_DRAFT_LISTING, "price": 14.99, "price_confidence": "old"},
        extra={"ebay_offer": {"price": 14.99, "target_price": 12.99}},
    )

    response = env["client"].post(
        f"/api/items/{SKU_A}/action",
        json={"action": "ebay_price"},
        headers=AUTH_HEADERS,
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    price_call = next(
        call["kwargs"] for call in reversed(enqueue_calls)
        if call["kwargs"].get("queue_name") == "ebay_price"
    )
    assert price_call["entity_type"] == "item"
    assert price_call["entity_id"] == SKU_A
    assert price_call["payload"] == {"sku": SKU_A, "origin": "operator"}
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text()
    )
    assert doc["draft_listing"]["price"] is None
    assert doc["draft_listing"]["price_confidence"] is None
    assert doc["ebay_offer"]["price"] is None
    assert doc["ebay_offer"]["target_price"] is None


def test_accept_proposals_persists_item_specifics_edit(env, monkeypatch):
    """POST action=accept_proposals actually writes accepted item_specifics
    into draft_listing.item_specifics (Set B) on disk when item_specifics
    already existed.

    todo #1416 point 4: this used to write into item_attributes (Set A) —
    a boundary bug, since sync.py's actual eBay push (_build_offer_bodies)
    reads ONLY draft_listing.item_specifics. Regression test for #1291
    (an identity-comparison bug on a mutated-in-place dict silently
    discarded this edit) is preserved by asserting the write actually
    lands, now against the corrected target set."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    draft = dict(_DRAFT_LISTING)
    draft["item_specifics"] = {"Brand": "OldBrand", "Color": "Red"}
    _write_item_with_draft(
        env["itemdata_root"], SKU_A, draft,
        extra={
            "revision_draft": {
                "delta": {"item_specifics": {"Brand": "NewBrand", "Size": "Large"}}
            },
        },
    )

    client = env["client"]
    r = client.post(
        f"/api/items/{SKU_A}/action",
        json={"action": "accept_proposals"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True

    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    # todo #1416: Set B envelope — read via the sanctioned accessor, not
    # the bare dict directly.
    isp_fields = draft_specifics.get_ebay_aspects(doc)
    assert isp_fields["Brand"] == "NewBrand"
    assert isp_fields["Color"] == "Red"
    assert isp_fields["Size"] == "Large"
    assert doc["draft_listing"]["item_specifics"]["_set"] == "ebay_draft"
    assert len(doc["draft_listing"]["item_specifics_history"]) >= 1
    # Padlock auto-sync (Dave, 2026-07-18) supersedes this test's original
    # assertion: any draft_listing save now pushes unlocked keys into
    # item_attributes by design (see inventory_record.sync_from_draft),
    # so the accepted values SHOULD land there too — nothing here is locked.
    ia_fields = inventory_record.get_inventory_fields(doc)
    assert ia_fields["Brand"] == "NewBrand"
    assert ia_fields["Size"] == "Large"
    assert doc.get("revision_draft") is None


def test_accept_proposals_persists_draft_listing_title_description(env, monkeypatch):
    """Same regression as above, for draft_listing title/description edits."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    _write_item_with_draft(
        env["itemdata_root"], SKU_A, dict(_DRAFT_LISTING),
        extra={
            "revision_draft": {
                "delta": {
                    "title": "Corrected Title",
                    "description": "Corrected description text.",
                }
            },
        },
    )

    client = env["client"]
    r = client.post(
        f"/api/items/{SKU_A}/action",
        json={"action": "accept_proposals"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["draft_listing"]["title"] == "Corrected Title"
    assert doc["draft_listing"]["description"] == "Corrected description text."
    # Runner-review regression (todo #1416): accept_proposals is a separate
    # endpoint from patch_item() — the #1415 fix (regenerate
    # listing_description whenever description changes) lives only in
    # patch_item(), so accept_proposals must regenerate it independently or
    # the exact stale-push bug #1415 fixed comes back through this door.
    assert "Corrected description text." in doc["draft_listing"]["listing_description"]
    # Original draft_listing fields untouched by the merge
    assert doc["draft_listing"]["price"] == _DRAFT_LISTING["price"]
    assert doc.get("revision_draft") is None


def test_accept_proposals_item_specifics_absent_before(env, monkeypatch):
    """When draft_listing.item_specifics did not exist before, accept_proposals
    still creates it correctly (Set B, not Set A)."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    _write_item_with_draft(
        env["itemdata_root"], SKU_A, _DRAFT_LISTING,
        extra={
            "revision_draft": {
                "delta": {"item_specifics": {"Brand": "FreshBrand"}}
            },
        },
    )

    client = env["client"]
    r = client.post(
        f"/api/items/{SKU_A}/action",
        json={"action": "accept_proposals"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True

    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert draft_specifics.get_ebay_aspects(doc)["Brand"] == "FreshBrand"
    # Padlock auto-sync (Dave, 2026-07-18): unlocked keys now follow the
    # draft into item_attributes on every save — supersedes this test's
    # original "must not land in Set A" assertion.
    assert inventory_record.get_inventory_fields(doc)["Brand"] == "FreshBrand"


def test_item_detail_inventory_record_panel_shows_set_a_unblended(env):
    """todo #1416 point 5: the Inventory Record panel ("Canonical TGW
    data — never overwritten by marketplace sync") must show Set A
    (item_attributes) values unblended with Set B — no single displayed
    value may be ambiguous about which set it came from."""
    _login(env["client"])
    sku = "tgw20260615110000020"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Drift Test Widget",
        "location": "X9",
        "item_attributes": {"Type": "Lapel Pin", "Brand": "Unbranded"},
        "draft_listing": {
            "item_specifics": {"Type": "Brooch", "Brand": "Unbranded"},
        },
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    # Set A's own value ("Lapel Pin") is shown as the primary value.
    assert "Lapel Pin" in r.text
    # The differing Set B value is shown as a clearly-labeled secondary
    # line, never silently overwritten or blended into the same key.
    assert "eBay value: Brooch" in r.text
    # An overlapping key that AGREES between the two sets must not print
    # a redundant "eBay value:" line (only genuine drift is called out).
    assert "eBay value: Unbranded" not in r.text


def test_item_detail_inventory_record_panel_offers_add_to_listing_for_set_a_only_keys(env):
    """Todo #1475 (Dave, 2026-07-16): "the initial item draft view after
    import should show all filled fields, not just the ones the eBay
    category requires or recommends... gives the operator all the data we
    have to choose from." A Set A key with no Set B counterpart gets a
    "+ Add to listing" button wired to addFromInventory(); a key already
    present in Set B (whether it agrees or differs) does not."""
    _login(env["client"])
    sku = "tgw20260615110000057"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Add To Listing Button Test",
        "location": "X9",
        "item_attributes": {"Type": "Lapel Pin", "Brand": "Unbranded", "Era": "1980s"},
        "draft_listing": {
            "item_specifics": {"Type": "Brooch", "Brand": "Unbranded"},
        },
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    # "Era" has no Set B counterpart -> gets the add-to-listing button.
    assert 'addFromInventory("Era","1980s",this)' in r.text
    # "Type"/"Brand" already exist in Set B (whether differing or agreeing)
    # -> no button for either.
    assert 'addFromInventory("Type"' not in r.text
    assert 'addFromInventory("Brand"' not in r.text
    assert "function addFromInventory(" in r.text
    assert "function buildAspectRow(" in r.text


def test_item_detail_aspects_form_prefills_from_set_b_not_set_a(env):
    """todo #1416 point 3: the eBay Draft Editor's aspects form
    (window._DL_PREFILL) must prefill from draft_listing.item_specifics
    (Set B) — NOT item_attributes (Set A). Before this fix, an operator's
    typed edit in this form silently wrote to item_attributes and never
    reached the eBay push path."""
    _login(env["client"])
    sku = "tgw20260615110000021"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Prefill Test Widget",
        "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert 'window._DL_PREFILL = {"Type": "Brooch"}' in r.text
    assert "Lapel Pin" not in r.text.split("window._DL_PREFILL")[1][:200]


def test_item_detail_js_renders_stored_aspects_outside_category_list(env):
    """Todo #1470: the aspects form used to render inputs ONLY for the
    fields the CURRENT category's official aspect list defines — any
    stored draft_listing.item_specifics field NOT in that list was
    completely invisible/uneditable, even though _build_offer_bodies()
    pushes it to eBay unconditionally regardless of category-schema
    membership (confirmed live: 18 of 20 real live aspects on a real item
    were hidden this way, following a category change that left
    now-mismatched aspects stranded). This locks in that the served page's
    JS contains the "render every remaining prefill key too" loop.

    Revised per Dave's correction, same day: these fields are eBay's own
    legitimate "custom aspect" capability, not just category-mismatch
    debris — badge reads "CUSTOM ASPECT", not an error-sounding label, and
    a real "add custom aspect" control exists so an operator can create one
    on purpose going forward, not just inherit stray ones by accident."""
    _login(env["client"])
    sku = "tgw20260615110000044"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Extra Aspects Widget", "location": "X9",
        "draft_listing": {"item_specifics": {"Material": "Porcelain", "Color": "Orange"}},
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert "CUSTOM ASPECT" in r.text
    assert "NOT IN CATEGORY" not in r.text
    assert "Object.keys(prefill).forEach(function(name){" in r.text
    assert "if(covered[name])return;" in r.text
    assert 'id="new-aspect-name"' in r.text
    assert 'id="new-aspect-value"' in r.text
    assert "function addCustomAspect(){" in r.text


def test_item_detail_save_ebay_draft_js_sends_cleared_aspects(env):
    """Todo #1461: saveEbayDraft() used to gate aspect inclusion on
    `if(v)` — clearing a field's value produced v==='' which was silently
    dropped from the save payload, so the backend never saw the attempted
    change and the old value stuck forever (Dave, live: "I have repeatedly
    deleted material... that field reverts every time"). Fix compares each
    input's current value against its `data-initial` (rendered value) and
    sends the key whenever it changed, including a change to empty. This
    locks in that the served page no longer contains the old silent-drop
    pattern and does contain the fixed comparison."""
    _login(env["client"])
    sku = "tgw20260615110000032"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Aspect Clear Test Widget", "location": "X9",
        "draft_listing": {"item_specifics": {"Material": "Silver"}},
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert "if(v!==init)attrs[k]=v;" in r.text
    assert "if(v)attrs[k]=v;" not in r.text
    assert "data-initial=" in r.text  # rendered by loadCatCtx(), not this response, but the setter must exist


def test_category_context_js_shows_off_list_selection_value():
    """Todo #1467 (Dave, 2026-07-16): a filled-in, normal ('same' layer,
    not custom/orphaned) aspect that uses a SELECTION_ONLY control (a
    `<select>`) rendered with NO `<option>` marked selected whenever its
    stored value wasn't among the CURRENT category's allowed_values (e.g.
    after a category change narrowed the option list) — real live case:
    Material="Cloisonne" on tgw202605051207245, category "Porcelain" only
    allows Material="Porcelain". With no <option selected>, the browser
    silently falls back to the first/blank option, so a genuinely filled
    field rendered indistinguishable from an empty one ("Dave initially
    didn't see it was filled"). Not a data bug — the stored value was
    correct throughout, only the <select>'s rendering discarded it.

    Fix mirrors the pre-existing `dl-condition-select` "not valid for this
    category, please fix" fallback-option pattern a few lines above this
    exact code in `_CATEGORY_CONTEXT_IIFE`: when the current value isn't in
    allowed_values, prepend an extra selected <option> carrying the real
    value (with a "not in this category's list" hint) instead of silently
    dropping it. This locks in that the served JS contains that fallback."""
    src = http_server._CATEGORY_CONTEXT_IIFE
    assert "SELECTION_ONLY" in src
    assert "var offList=cur&&asp.allowed_values.indexOf(cur)===-1;" in src
    assert "if(offList)opts=" in src
    assert "not in this category" in src


# ---------------------------------------------------------------------------
# Todo #1464 (Tigwa's field-set-boundary audit, invariant C12/C14): the
# generic PATCH endpoint must not accept a caller-supplied full Set A/Set B
# envelope from a non-machine caller — that bypasses the sanctioned
# accessor's own diff/provenance logic entirely. A bare partial dict must
# keep working exactly as before (that's #1461's aspects-form save path).
# ---------------------------------------------------------------------------


def test_patch_rejects_bare_envelope_item_attributes_from_operator(env):
    sku = "tgw20260615110000040"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Envelope Guard Widget", "location": "X9",
        "item_attributes": {"Type": "Brooch"},
    })
    forged_envelope = {
        "_set": "inventory_record", "version": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "fields": {"Type": "SOMETHING ELSE ENTIRELY"},
    }
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"item_attributes": forged_envelope}},
        headers=AUTH_HEADERS,  # no X-TGW-Caller — operator/browser-shaped request
    )
    assert r.status_code == 422
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["item_attributes"] == {"Type": "Brooch"}  # untouched


def test_patch_rejects_bare_envelope_item_specifics_from_operator(env):
    sku = "tgw20260615110000041"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Envelope Guard Widget B", "location": "X9",
        "draft_listing": {"item_specifics": {"Material": "Silver"}},
    })
    forged_envelope = {
        "_set": "ebay_draft", "version": 1,
        "updated_at": "2026-01-01T00:00:00+00:00",
        "fields": {"Material": "SOMETHING ELSE ENTIRELY"},
    }
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"item_specifics": forged_envelope}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 422
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["draft_listing"]["item_specifics"] == {"Material": "Silver"}  # untouched


def test_patch_allows_envelope_item_attributes_from_machine_caller(env):
    """ai_identify.py builds the envelope itself via set_inventory_fields()
    before ever reaching HTTP — the fence PATCH is just transport for an
    already-sanctioned write. Must keep working."""
    sku = "tgw20260615110000042"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Machine Envelope Widget", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
    })
    real_envelope = {
        "_set": "inventory_record", "version": 1,
        "updated_at": "2026-07-16T00:00:00+00:00",
        "updated_at_backfilled": False,
        "fields": {"Type": "Brooch"},
    }
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"item_attributes": real_envelope}},
        headers={**AUTH_HEADERS, "X-TGW-Caller": "background:worker:ai_identify"},
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["item_attributes"]["fields"] == {"Type": "Brooch"}


def test_patch_still_allows_bare_partial_item_specifics_from_operator(env):
    """The eBay Draft Editor's normal save path (#1461) sends a bare
    partial dict, not an envelope — must be completely unaffected by the
    new envelope gate."""
    sku = "tgw20260615110000043"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Bare Dict Still Works Widget", "location": "X9",
        "draft_listing": {"item_specifics": {"Material": "Silver"}},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"item_specifics": {"Material": ""}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["draft_listing"]["item_specifics"]["fields"]["Material"] == ""


# ---------------------------------------------------------------------------
# todo #1417: eBay Draft -> Inventory Record reverse-flow endpoints. A
# DIFFERENT code path from accept_proposals above (different data source
# Set B -> different destination Set A, own button, no shared action name
# — spec point 6/3) — verified as real, separate HTTP endpoints, not a
# unit test of internal functions in isolation.
# ---------------------------------------------------------------------------

def test_inventory_diff_get_surfaces_mismatch(env):
    sku = "tgw20260615110000030"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Diff Test Widget", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin", "Brand": "Unbranded"},
        "draft_listing": {
            "item_specifics": {"Type": "Brooch", "Brand": "Unbranded", "Metal": "Silver"},
        },
    })
    r = env["client"].get(f"/api/items/{sku}/inventory-diff", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    keys = {d["key"] for d in body["diffs"]}
    assert keys == {"Type", "Metal"}
    type_diff = next(d for d in body["diffs"] if d["key"] == "Type")
    assert type_diff["inventory_value"] == "Lapel Pin"
    assert type_diff["ebay_value"] == "Brooch"
    metal_diff = next(d for d in body["diffs"] if d["key"] == "Metal")
    assert metal_diff["inventory_value"] is None  # new fact, not a correction


def test_inventory_diff_get_requires_auth(client):
    sku = "tgw20260615110000031"
    r = client.get(f"/api/items/{sku}/inventory-diff")
    assert r.status_code in (401, 403)


def test_inventory_diff_get_404_unknown_sku(env):
    r = env["client"].get("/api/items/tgw99999999999999999/inventory-diff", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_inventory_diff_get_read_only_no_mutation(env):
    """Spec point 2: the GET endpoint never mutates anything, callable any
    time."""
    sku = "tgw20260615110000032"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Read Only Test", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
    })
    json_path = env["itemdata_root"] / sku / f"{sku}.json"
    before = json_path.read_text(encoding="utf-8")
    r = env["client"].get(f"/api/items/{sku}/inventory-diff", headers=AUTH_HEADERS)
    assert r.status_code == 200
    after = json_path.read_text(encoding="utf-8")
    assert before == after


def test_inventory_diff_apply_writes_only_checked_keys(env, monkeypatch):
    """Acceptance item 2: uncheck one field, submit, confirm only the
    checked field landed in item_attributes with provenance, and the
    unchecked one still shows as an open diff afterward."""
    monkeypatch.setattr(http_server, "_enqueue_catalog_rebuild", lambda *a, **k: None)
    sku = "tgw20260615110000033"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Apply Test Widget", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
        "draft_listing": {
            "item_specifics": {"Type": "Brooch", "Metal": "Silver"},
            "item_specifics_history": [
                {"ts": "2026-07-01T00:00:00+00:00", "key": "Type",
                 "value": "Brooch", "previous_value": "Lapel Pin",
                 "source": "ebay_draft", "applied_by": "system"},
                {"ts": "2026-07-01T00:00:00+00:00", "key": "Metal",
                 "value": "Silver", "previous_value": None,
                 "source": "ebay_draft", "applied_by": "system"},
            ],
        },
    })

    client = env["client"]
    # Operator unchecked "Metal" in the UI — only "Type" submitted.
    r = client.post(
        f"/api/items/{sku}/inventory-diff/apply",
        json={"keys": ["Type"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] == ["Type"]

    json_path = env["itemdata_root"] / sku / f"{sku}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    ia_fields = inventory_record.get_inventory_fields(doc)
    assert ia_fields["Type"] == "Brooch"
    assert "Metal" not in ia_fields  # not checked, not applied
    hist = doc["item_attributes_history"]
    assert hist[-1]["key"] == "Type"
    assert hist[-1]["source"] == "ebay_draft"
    assert hist[-1]["applied_by"] == "operator"
    assert hist[-1]["detected_at"] == "2026-07-01T00:00:00+00:00"

    # The un-checked/skipped key still shows as an open diff — no sticky
    # dismissed state (spec point 5).
    r2 = client.get(f"/api/items/{sku}/inventory-diff", headers=AUTH_HEADERS)
    remaining_keys = {d["key"] for d in r2.json()["diffs"]}
    assert remaining_keys == {"Metal"}


def test_inventory_diff_apply_does_not_touch_draft_listing_or_revision_draft(env, monkeypatch):
    """Spec point 6: this packet's apply action shares no write path with
    accept_proposals/revision_draft — draft_listing must be untouched."""
    monkeypatch.setattr(http_server, "_enqueue_catalog_rebuild", lambda *a, **k: None)
    sku = "tgw20260615110000034"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Isolation Test Widget", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}, "price": 14.99},
        "revision_draft": {"delta": {"title": "Unrelated pipeline proposal"}},
    })
    client = env["client"]
    r = client.post(
        f"/api/items/{sku}/inventory-diff/apply",
        json={"keys": ["Type"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    json_path = env["itemdata_root"] / sku / f"{sku}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["draft_listing"]["price"] == 14.99
    assert draft_specifics.get_ebay_aspects(doc)["Type"] == "Brooch"  # Set B untouched
    assert doc["revision_draft"]["delta"]["title"] == "Unrelated pipeline proposal"


def test_inventory_diff_apply_idempotent_no_op_when_stale(env, monkeypatch):
    """A key requested that is no longer an active diff (Set A/Set B
    already agree by the time of the call) is a silent no-op, not an
    error — re-diffing live, never trusting caller-supplied values."""
    monkeypatch.setattr(http_server, "_enqueue_catalog_rebuild", lambda *a, **k: None)
    sku = "tgw20260615110000035"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Stale Apply Test", "location": "X9",
        "item_attributes": {"Type": "Brooch"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
    })
    client = env["client"]
    r = client.post(
        f"/api/items/{sku}/inventory-diff/apply",
        json={"keys": ["Type"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["applied"] == []


def test_inventory_diff_apply_requires_auth(client):
    sku = "tgw20260615110000036"
    r = client.post(f"/api/items/{sku}/inventory-diff/apply", json={"keys": ["Type"]})
    assert r.status_code in (401, 403)


def test_draft_save_auto_syncs_unlocked_key_to_inventory_record(env, enqueue_calls):
    """Padlock design (Dave, 2026-07-18): saving a draft_listing edit
    pushes unlocked keys into item_attributes automatically — no separate
    diff-review step needed for the common case."""
    sku = "tgw20260615110000040"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Elephant Widget", "location": "X9",
        "item_attributes": {"_set": "inventory_record", "version": 1,
                             "updated_at": "2026-07-01T00:00:00+00:00",
                             "updated_at_backfilled": False,
                             "fields": {"Type": "Elephant Figurine"}},
        "draft_listing": {"title": "Elephant Widget",
                           "item_specifics": {"Type": "Elephant Figurine"}},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"item_specifics": {"Type": "Mouse Figurine"}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    ia = inventory_record.get_inventory_fields(doc)
    assert ia["Type"] == "Mouse Figurine"


def test_draft_save_auto_syncs_title_and_description_top_level(env, enqueue_calls):
    """Second live bug (Dave, 2026-07-18): the padlock sync only reached
    item_attributes aspects, not the top-level title/description fields
    the page header and fields panel actually display. Both now sync the
    same way, keyed by "title"/"description" in the shared locked_keys list."""
    sku = "tgw20260615110000044"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Elephant Thimble", "description": "An elephant.",
        "location": "X9",
        "draft_listing": {"title": "Elephant Thimble", "description": "An elephant."},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {
            "title": "Mouse Thimble", "description": "A mouse.",
        }}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["title"] == "Mouse Thimble"
    assert doc["description"] == "A mouse."


def test_locked_title_does_not_auto_sync_from_draft_save(env, enqueue_calls):
    """Locking "title" freezes the top-level field even though it isn't
    an item_attributes aspect — same shared locked_keys mechanism."""
    sku = "tgw20260615110000045"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Elephant Thimble", "location": "X9",
        "item_attributes": {"_set": "inventory_record", "version": 1,
                             "updated_at": "2026-07-01T00:00:00+00:00",
                             "updated_at_backfilled": False,
                             "fields": {}, "locked_keys": ["title"]},
        "draft_listing": {"title": "Elephant Thimble"},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"title": "Mouse Thimble"}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert doc["draft_listing"]["title"] == "Mouse Thimble"
    assert doc["title"] == "Elephant Thimble"


def test_locked_key_does_not_auto_sync_from_draft_save(env, enqueue_calls):
    """A locked key stays frozen at its current value even when the draft
    changes — the padlock is the only way a key stops following eBay."""
    sku = "tgw20260615110000041"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Widget", "location": "X9",
        "item_attributes": {"_set": "inventory_record", "version": 1,
                             "updated_at": "2026-07-01T00:00:00+00:00",
                             "updated_at_backfilled": False,
                             "fields": {"Type": "Brooch"},
                             "locked_keys": ["Type"]},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
    })
    r = env["client"].patch(
        f"/api/items/{sku}",
        json={"fields": {"draft_listing": {"item_specifics": {"Type": "Pendant"}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    # Set B (the actual eBay draft) still updates normally...
    assert draft_specifics.get_ebay_aspects(doc)["Type"] == "Pendant"
    # ...but the locked Set A key stays frozen at its old value.
    assert inventory_record.get_inventory_fields(doc)["Type"] == "Brooch"


def test_inventory_lock_toggle_endpoint(env):
    sku = "tgw20260615110000042"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Widget", "location": "X9",
        "item_attributes": {"_set": "inventory_record", "version": 1,
                             "updated_at": "2026-07-01T00:00:00+00:00",
                             "updated_at_backfilled": False,
                             "fields": {"Type": "Brooch"}},
    })
    r = env["client"].post(
        f"/api/items/{sku}/inventory-lock",
        json={"key": "Type", "locked": True},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    doc = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert inventory_record.is_locked(doc, "Type") is True
    # Locking never appends to item_attributes_history — it's metadata
    # about sync behavior, not a fact about the item.
    assert not doc.get("item_attributes_history")

    r2 = env["client"].post(
        f"/api/items/{sku}/inventory-lock",
        json={"key": "Type", "locked": False},
        headers=AUTH_HEADERS,
    )
    assert r2.status_code == 200
    doc2 = json.loads((env["itemdata_root"] / sku / f"{sku}.json").read_text())
    assert inventory_record.is_locked(doc2, "Type") is False


def test_inventory_lock_requires_auth(client):
    sku = "tgw20260615110000043"
    r = client.post(f"/api/items/{sku}/inventory-lock", json={"key": "Type", "locked": True})
    assert r.status_code in (401, 403)


def test_item_detail_page_renders_inv_diff_panel_container(env):
    """Acceptance item 3: the reverse-flow panel is a distinct, separately
    labeled panel from the forward "Pipeline proposed changes" banner —
    both present on the same test item, never sharing an action name."""
    _login(env["client"])
    sku = "tgw20260615110000037"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Both Panels Test Widget", "location": "X9",
        "item_attributes": {"Type": "Lapel Pin"},
        "draft_listing": {"item_specifics": {"Type": "Brooch"}},
        "revision_draft": {"delta": {"title": "Some pipeline title proposal"}},
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    # The reverse-flow panel container + its own distinct button/label.
    assert 'id="inv-diff-panel"' in r.text
    assert "eBay &rarr; Inventory Record sync" in r.text or "eBay → Inventory Record sync" in r.text
    assert "applyInventoryDiff()" in r.text
    assert "Apply Checked to Inventory Record" in r.text
    # The forward banner is also present and uses a DIFFERENT button/action.
    assert "Pipeline proposed changes" in r.text
    assert "acceptProposals()" in r.text
    assert "Accept All Proposals" in r.text
    # No shared action name between the two.
    assert "applyInventoryDiff" != "acceptProposals"


# ---------------------------------------------------------------------------
# todo #1471: category-aspect migration endpoints. A category change can
# strand Set B aspects the CURRENT category no longer recognizes — eBay's
# own Seller Hub discards those; TGW's push doesn't (todo #1470's companion
# finding). This matches eBay's discard-as-default WITHOUT deleting data —
# checked keys move to item_attributes (Set A), unchecked stay on eBay.
# Deliberately its own code path from inventory-diff above (spec point 6).
# ---------------------------------------------------------------------------

def _mock_category_aspects(monkeypatch, names):
    import tgw.ebay.category_aspect_migration as cam_mod
    monkeypatch.setattr(cam_mod, "get_aspects",
                        lambda cfg, category_id: [{"name": n} for n in names])


def test_category_aspect_migration_get_surfaces_orphans(env, monkeypatch):
    _mock_category_aspects(monkeypatch, ["Material"])
    sku = "tgw20260615110000050"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Orphan Test Widget", "location": "X9",
        "draft_listing": {
            "category_id": "38064",
            "item_specifics": {"Material": "Porcelain", "Color": "Orange", "Type": "Figurine"},
        },
    })
    r = env["client"].get(f"/api/items/{sku}/category-aspect-migration", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert {o["key"] for o in body["orphaned"]} == {"Color", "Type"}


def test_category_aspect_migration_get_requires_auth(client):
    sku = "tgw20260615110000051"
    r = client.get(f"/api/items/{sku}/category-aspect-migration")
    assert r.status_code in (401, 403)


def test_category_aspect_migration_get_404_unknown_sku(env):
    r = env["client"].get(
        "/api/items/tgw99999999999999999/category-aspect-migration", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_category_aspect_migration_get_read_only_no_mutation(env, monkeypatch):
    _mock_category_aspects(monkeypatch, ["Material"])
    sku = "tgw20260615110000052"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Read Only Migration Test", "location": "X9",
        "draft_listing": {"category_id": "38064", "item_specifics": {"Color": "Orange"}},
    })
    json_path = env["itemdata_root"] / sku / f"{sku}.json"
    before = json_path.read_text(encoding="utf-8")
    r = env["client"].get(f"/api/items/{sku}/category-aspect-migration", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert json_path.read_text(encoding="utf-8") == before


def test_category_aspect_migration_apply_moves_checked_keys(env, monkeypatch):
    """Move Color to Set A, leave Type on eBay (not checked) — same
    checked-subset discipline as the inventory-diff apply endpoint."""
    monkeypatch.setattr(http_server, "_enqueue_catalog_rebuild", lambda *a, **k: None)
    _mock_category_aspects(monkeypatch, ["Material"])
    sku = "tgw20260615110000053"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Apply Migration Test Widget", "location": "X9",
        "draft_listing": {
            "category_id": "38064",
            "item_specifics": {"Material": "Porcelain", "Color": "Orange", "Type": "Figurine"},
        },
    })
    client = env["client"]
    r = client.post(
        f"/api/items/{sku}/category-aspect-migration/apply",
        json={"keys": ["Color"]},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["migrated"] == ["Color"]

    json_path = env["itemdata_root"] / sku / f"{sku}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    ia_fields = inventory_record.get_inventory_fields(doc)
    assert ia_fields["Color"] == "Orange"
    ebay_fields = draft_specifics.get_ebay_aspects(doc)
    assert "Color" not in ebay_fields  # removed from Set B
    assert ebay_fields == {"Material": "Porcelain", "Type": "Figurine"}  # untouched otherwise

    # Removed key no longer shows as an orphan afterward — re-detected live.
    r2 = client.get(f"/api/items/{sku}/category-aspect-migration", headers=AUTH_HEADERS)
    remaining = {o["key"] for o in r2.json()["orphaned"]}
    assert remaining == {"Type"}


def test_category_aspect_migration_apply_idempotent_no_op_when_stale(env, monkeypatch):
    monkeypatch.setattr(http_server, "_enqueue_catalog_rebuild", lambda *a, **k: None)
    _mock_category_aspects(monkeypatch, ["Material", "Color"])
    sku = "tgw20260615110000054"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Stale Migration Test", "location": "X9",
        "draft_listing": {
            "category_id": "38064",
            "item_specifics": {"Material": "Porcelain", "Color": "Orange"},
        },
    })
    r = env["client"].post(
        f"/api/items/{sku}/category-aspect-migration/apply",
        json={"keys": ["Color"]},  # Color is NOT actually orphaned (in the mocked list)
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["migrated"] == []


def test_category_aspect_migration_apply_requires_auth(client):
    sku = "tgw20260615110000055"
    r = client.post(f"/api/items/{sku}/category-aspect-migration/apply", json={"keys": ["Color"]})
    assert r.status_code in (401, 403)


def test_item_detail_page_renders_inline_aspect_keep_checkbox_wiring(env):
    """Todo #1472 (Dave, 2026-07-16): the old standalone migration panel is
    retired — checkbox-to-discard for non-official aspects now lives inline
    in #aspects-form and is driven by one Save Draft click, not a separate
    always-checked confirm()+immediate-apply panel."""
    _login(env["client"])
    sku = "tgw20260615110000056"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku, "title": "Migration Panel Container Test", "location": "X9",
        "draft_listing": {"category_id": "38064", "item_specifics": {"Color": "Orange"}},
    })
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    assert 'id="cat-aspect-migration-panel"' not in r.text
    assert "applyCategoryAspectMigration" not in r.text
    assert "loadCategoryAspectMigration" not in r.text
    assert "aspect-keep-cb" in r.text
    assert "/category-aspect-migration/apply" in r.text


def test_form_review_renders(client):
    """/form/drafts returns HTML with expected structure; /form/review redirects."""
    _login(client)
    r = client.get("/form/drafts")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Review Queue" in r.text
    assert "/api/items/review-queue" in r.text
    assert "Approve All" in r.text
    # Old URL redirects
    r2 = client.get("/form/review", follow_redirects=False)
    assert r2.status_code == 301


def test_form_review_no_auth_required(client):
    """/form/drafts is accessible without Bearer token."""
    r = client.get("/form/drafts")
    assert r.status_code == 200


def test_nav_includes_review_link(client):
    """nav.js includes links to /form/drafts and /form/needs-review."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/drafts" in r.text
    assert "/form/needs-review" in r.text


def test_nav_review_badge_span_present(client):
    """nav.js includes the nav-review-count badge span."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "nav-review-count" in r.text


def test_review_queue_returns_condition_label_and_description(env):
    """Review-queue items include condition_label, condition_description, shipping_profile."""
    doc = _write_item_with_draft(env["itemdata_root"], SKU_A, _DRAFT_LISTING)
    _seed_catalog_with_data(env["cfg"]["sqlite_catalog_path"], SKU_A, doc)

    client = env["client"]
    r = client.get("/api/items/review-queue", headers=AUTH_HEADERS)
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["condition_label"] == "Used"
    assert item["condition_description"] == "Minor scuff on the base, otherwise excellent."
    assert item["shipping_profile"] == "standard"


def test_bulk_approve_sets_status_ready(env, monkeypatch):
    """POST /api/bulk/action approve sets status=Ready on each SKU."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    _write_item_with_draft(env["itemdata_root"], SKU_A, _DRAFT_LISTING)
    _write_item_with_draft(env["itemdata_root"], SKU_B, _DRAFT_LISTING)

    client = env["client"]
    r = client.post(
        "/api/bulk/action",
        json={"skus": [SKU_A, SKU_B], "action": "approve"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2

    for sku in (SKU_A, SKU_B):
        json_path = env["itemdata_root"] / sku / f"{sku}.json"
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        assert doc["status"] == "Ready"


def test_bulk_list_now_sets_ready_and_enqueues_stage(env, monkeypatch):
    """POST /api/bulk/action list_now sets status=Ready and enqueues ebay_stage."""
    enqueued = []
    monkeypatch.setattr(
        http_server.state_machine,
        "enqueue_job",
        lambda queue_name, **kw: enqueued.append(queue_name) or "job-fake",
    )

    _write_item_with_draft(env["itemdata_root"], SKU_A, _DRAFT_LISTING)

    client = env["client"]
    r = client.post(
        "/api/bulk/action",
        json={"skus": [SKU_A], "action": "list_now"},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 1

    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["status"] == "Ready"
    assert "ebay_stage" in enqueued


# ---------------------------------------------------------------------------
# PP-EDITOR-001 Phase 3j — pipeline monitor + dead-letter manager
# ---------------------------------------------------------------------------

def _fake_systemctl_output(units):
    """Build fake `systemctl show --property=Id,ActiveState,SubState,MainPID` output."""
    blocks = []
    for i, u in enumerate(units):
        active = "active" if i % 3 != 2 else "inactive"
        sub = "running" if active == "active" else "dead"
        pid = str(1000 + i) if active == "active" else "0"
        blocks.append(f"Id={u}\nActiveState={active}\nSubState={sub}\nMainPID={pid}\n")
    return "\n".join(blocks)


def test_system_workers_requires_auth(client):
    r = client.get("/api/system/workers")
    assert r.status_code in (401, 403)


def test_system_workers_returns_unit_list(env, monkeypatch):
    """GET /api/system/workers returns a worker list with active/sub state."""
    from tgw.queue import WORKER_QUEUES

    all_units = [f"tgw-worker@{q}.service" for q in WORKER_QUEUES] + ["tgw-http.service"]

    def _fake_run(cmd, **kwargs):
        class _R:
            stdout = _fake_systemctl_output(all_units)
            returncode = 0
        return _R()

    monkeypatch.setattr(http_server.subprocess, "run", _fake_run)

    r = env["client"].get("/api/system/workers", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["total"] == len(all_units)
    assert body["up"] > 0
    workers = body["workers"]
    assert len(workers) == len(all_units)
    # First worker should be active
    assert workers[0]["active"] in ("active", "inactive", "unknown")
    assert "unit" in workers[0]
    assert "sub" in workers[0]


def test_system_workers_fallback_on_subprocess_failure(env, monkeypatch):
    """When systemctl fails, workers are returned as 'unknown'."""
    def _fail(*a, **k):
        raise OSError("systemctl not found")

    monkeypatch.setattr(http_server.subprocess, "run", _fail)

    r = env["client"].get("/api/system/workers", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert all(w["active"] == "unknown" for w in body["workers"])


def test_pipeline_jobs_requires_auth(client):
    r = client.get("/api/pipeline/jobs")
    assert r.status_code in (401, 403)


def test_pipeline_jobs_returns_active_and_dead(env, monkeypatch):
    """GET /api/pipeline/jobs returns running and dead_letter jobs."""
    import datetime

    rows = [
        {
            "job_id": "aaaaaaaa-0000-0000-0000-000000000001",
            "queue_name": "ebay_draft",
            "state": "running",
            "sku": SKU_A,
            "started_at": datetime.datetime(2026, 6, 14, 12, 0, 0),
            "finished_at": None,
            "created_at": datetime.datetime(2026, 6, 14, 11, 59, 0),
            "error_detail": None,
            "attempt_count": 1,
            "max_attempts": 5,
        },
        {
            "job_id": "bbbbbbbb-0000-0000-0000-000000000002",
            "queue_name": "ebay_stage",
            "state": "dead_letter",
            "sku": SKU_B,
            "started_at": datetime.datetime(2026, 6, 14, 10, 0, 0),
            "finished_at": datetime.datetime(2026, 6, 14, 10, 1, 0),
            "created_at": datetime.datetime(2026, 6, 14, 10, 0, 0),
            "error_detail": "eBay API timeout",
            "attempt_count": 5,
            "max_attempts": 5,
        },
    ]

    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(rows),
    )

    r = env["client"].get("/api/pipeline/jobs", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 2
    jobs = body["jobs"]
    assert jobs[0]["state"] == "running"
    assert jobs[0]["sku"] == SKU_A
    assert jobs[1]["state"] == "dead_letter"
    assert jobs[1]["error_detail"] == "eBay API timeout"
    # Timestamps serialised as ISO strings
    assert isinstance(jobs[0]["started_at"], str)


def test_pipeline_jobs_empty(env, monkeypatch):
    """No active/dead jobs → ok=True, count=0."""
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )
    r = env["client"].get("/api/pipeline/jobs", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["jobs"] == []


def test_requeue_job_requires_auth(client):
    r = client.post("/api/jobs/some-job-id/requeue")
    assert r.status_code in (401, 403)


def test_requeue_job_not_found(env, monkeypatch):
    """Requeue returns 404 when job_id does not exist."""
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )
    r = env["client"].post("/api/jobs/nonexistent-job/requeue", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_requeue_job_wrong_state(env, monkeypatch):
    """Requeue returns 400 when job is not in dead_letter state."""
    rows = [{
        "job_id": "aaa",
        "queue_name": "ebay_draft",
        "payload_json": {"sku": SKU_A},
        "state": "succeeded",
        "max_attempts": 3,
    }]
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(rows),
    )
    r = env["client"].post("/api/jobs/aaa/requeue", headers=AUTH_HEADERS)
    assert r.status_code == 400
    assert "dead_letter" in r.json()["detail"]


def test_requeue_job_success(env, monkeypatch):
    """Requeue re-enters the current item dispatcher, never a stale envelope."""
    rows = [{
        "job_id": "dead-job-id-001",
        "queue_name": "ebay_draft",
        "payload_json": {"sku": SKU_A},
        "state": "dead_letter",
        "max_attempts": 3,
        "entity_type": "generic",
        "entity_id": "ebay_draft",
        "operation": "run",
        "handler_family": "ebay_draft",
        "priority": 30,
    }]
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(rows),
    )
    enqueued = {}

    def _enqueue(**kwargs):
        enqueued.update(kwargs)
        return "new-job-id-999"

    monkeypatch.setattr(http_server.state_machine, "enqueue_job", _enqueue)

    r = env["client"].post("/api/jobs/dead-job-id-001/requeue", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["new_job_id"] == "new-job-id-999"
    assert body["queue"] == "ebay_draft"
    assert body["retry_mode"] == "current_item_action"
    assert enqueued["entity_type"] == "item"
    assert enqueued["entity_id"] == SKU_A
    assert enqueued["queue_name"] == "ebay_draft"
    assert enqueued["payload"] == {"sku": SKU_A, "origin": "operator"}
    doc = json.loads(
        (env["itemdata_root"] / SKU_A / f"{SKU_A}.json").read_text()
    )
    assert doc["ai_redraft_requested"] is True


def test_every_http_sku_enqueue_uses_canonical_item_identity():
    """Every literal per-SKU HTTP enqueue obeys the queue manifest."""
    source_path = Path(http_server.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    missing = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "enqueue_job":
            continue
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        payload = keywords.get("payload")
        if not isinstance(payload, ast.Dict):
            continue
        payload_keys = {
            key.value for key in payload.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if "sku" not in payload_keys:
            continue
        if "entity_type" not in keywords or "entity_id" not in keywords:
            missing.append(call.lineno)
            continue
        entity_type = keywords["entity_type"]
        entity_id = keywords["entity_id"]
        if not (
            isinstance(entity_type, ast.Constant)
            and entity_type.value == "item"
            and isinstance(entity_id, ast.Name)
            and entity_id.id == "sku"
        ):
            missing.append(call.lineno)
    assert missing == []


def test_provider_effect_item_queues_have_one_http_dispatch_authority():
    """No bulk, retry, or patch endpoint may bypass item_action authority."""
    source_path = Path(http_server.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    direct = []

    class Visitor(ast.NodeVisitor):
        function = "<module>"

        def visit_FunctionDef(self, node):
            previous = self.function
            self.function = node.name
            self.generic_visit(node)
            self.function = previous

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "enqueue_job":
                keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
                queue = keywords.get("queue_name")
                if isinstance(queue, ast.Constant) and queue.value in {
                    "ebay_stage", "ebay_publish",
                }:
                    direct.append((self.function, queue.value, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    assert direct
    assert {function for function, _, _ in direct} == {"item_action"}


def test_cancel_job_requires_auth(client):
    r = client.post("/api/jobs/some-job/cancel")
    assert r.status_code in (401, 403)


def test_cancel_job_not_found(env, monkeypatch):
    """Cancel returns 404 when job_id does not exist."""
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([]),
    )
    r = env["client"].post("/api/jobs/nonexistent/cancel", headers=AUTH_HEADERS)
    assert r.status_code == 404


def test_cancel_job_wrong_state(env, monkeypatch):
    """Cancel returns 400 when job is running (not cancellable via this endpoint)."""
    rows = [{"state": "running"}]
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn(rows),
    )
    r = env["client"].post("/api/jobs/aaa/cancel", headers=AUTH_HEADERS)
    assert r.status_code == 400


def test_cancel_job_success(env, monkeypatch):
    """Cancel a dead-letter job — returns ok=True."""
    # First call (SELECT) returns dead_letter row; second call (UPDATE) is a no-op fake.
    call_count = [0]
    def _connect(*a, **k):
        call_count[0] += 1
        rows = [{"state": "dead_letter"}] if call_count[0] == 1 else []
        return _FakeConn(rows)

    monkeypatch.setattr(http_server.psycopg2, "connect", _connect)

    r = env["client"].post("/api/jobs/dead-letter-job-123/cancel", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["cancelled"] is True


def test_cancel_job_race_with_worker_lease_returns_409(env, monkeypatch):
    """audit#1143 #1197: SELECT sees dead_letter, but a worker leases the job
    before the UPDATE runs (WHERE state=ANY(...) matches zero rows) -- this
    must surface as a 409, not a silent 200 'cancelled'."""
    call_count = [0]
    def _connect(*a, **k):
        call_count[0] += 1
        if call_count[0] == 1:
            return _FakeConn([{"state": "dead_letter"}])
        return _FakeConn([], rowcount=0)

    monkeypatch.setattr(http_server.psycopg2, "connect", _connect)

    r = env["client"].post("/api/jobs/raced-job-1/cancel", headers=AUTH_HEADERS)
    assert r.status_code == 409


def test_form_pipeline_renders(client):
    """/form/pipeline returns HTML with pipeline monitor structure."""
    _login(client)
    r = client.get("/form/pipeline")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Pipeline Monitor" in r.text
    assert "/api/queue/status" in r.text
    assert "/api/pipeline/jobs" in r.text
    assert "/api/system/workers" in r.text
    assert "Dead-Letter" in r.text


def test_form_pipeline_no_auth_required(client):
    """/form/pipeline is accessible without Bearer token."""
    r = client.get("/form/pipeline")
    assert r.status_code == 200


def test_nav_includes_pipeline_link(client):
    """nav.js includes a link to /form/pipeline."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/pipeline" in r.text


# ---------------------------------------------------------------------------
# PP-EDITOR-001 Phase 3k — /form/system + supporting API endpoints
# ---------------------------------------------------------------------------

def test_system_info_requires_auth(client):
    r = client.get("/api/system/info")
    assert r.status_code in (401, 403)


def test_system_info_returns_disk_token_sync_states(env, monkeypatch):
    """GET /api/system/info returns all four sub-sections."""
    import shutil

    # Stub shutil.disk_usage to avoid touching real filesystem
    FakeUsage = type("FakeUsage", (), {"total": 100_000_000_000, "used": 40_000_000_000, "free": 60_000_000_000})()
    monkeypatch.setattr(shutil, "disk_usage", lambda *a, **k: FakeUsage)

    # Stub psycopg2 for job state counts
    monkeypatch.setattr(
        http_server.psycopg2, "connect",
        lambda *a, **k: _FakeConn([("succeeded", 42), ("dead_letter", 3)]),
    )

    # Put a fake eBay token in the cfg
    token_path = env["cfg"]["itemdata_root"].parent / "ebay-token.json"
    token_path.write_text(
        __import__("json").dumps({"access_token": "tok", "expires_at": str(int(__import__("time").time()) + 7200)}),
        encoding="utf-8",
    )
    env["cfg"]["ebay_token_path"] = token_path

    r = env["client"].get("/api/system/info", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "disk" in body
    assert "itemdata" in body["disk"]
    assert body["disk"]["itemdata"]["pct"] == 40.0
    assert "ebay_token" in body
    assert body["ebay_token"]["exists"] is True
    assert body["ebay_token"]["remaining_seconds"] > 0
    assert "sync" in body
    assert body["sync"]["catalog_mtime"] is not None  # catalog db exists via env fixture
    assert "job_states" in body
    assert body["job_states"].get("succeeded") == 42
    assert body["job_states"].get("dead_letter") == 3


def test_system_info_token_missing(env, monkeypatch):
    """system_info handles missing token file gracefully."""
    import shutil
    FakeUsage = type("FakeUsage", (), {"total": 1_000_000, "used": 100_000, "free": 900_000})()
    monkeypatch.setattr(shutil, "disk_usage", lambda *a, **k: FakeUsage)
    monkeypatch.setattr(http_server.psycopg2, "connect", lambda *a, **k: _FakeConn([]))
    nonexistent = env["cfg"]["itemdata_root"].parent / "no-such-token.json"
    env["cfg"]["ebay_token_path"] = nonexistent

    r = env["client"].get("/api/system/info", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["ebay_token"]["exists"] is False
    assert body["ebay_token"]["ok"] is False


def test_restart_worker_requires_auth(client):
    r = client.post("/api/system/workers/tgw-http.service/restart")
    assert r.status_code in (401, 403)


def test_restart_worker_invalid_unit(env):
    """Restart refuses units not in the allowed set."""
    r = env["client"].post(
        "/api/system/workers/evil-unit.service/restart",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 400
    assert "allowed" in r.json()["detail"]


def test_restart_worker_success(env, monkeypatch):
    """Restart calls systemctl and returns ok=True on zero exit."""
    class _R:
        returncode = 0
        stderr = ""
    monkeypatch.setattr(http_server.subprocess, "run", lambda *a, **k: _R())
    r = env["client"].post(
        "/api/system/workers/tgw-http.service/restart",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["unit"] == "tgw-http.service"


def test_restart_worker_failure(env, monkeypatch):
    """Restart returns ok=False when systemctl exits non-zero."""
    class _R:
        returncode = 1
        stderr = "Access denied"
    monkeypatch.setattr(http_server.subprocess, "run", lambda *a, **k: _R())
    r = env["client"].post(
        "/api/system/workers/tgw-http.service/restart",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "Access denied" in body["stderr"]


def test_restart_worker_subprocess_error(env, monkeypatch):
    """Restart returns ok=False when subprocess.run raises."""
    def _fail(*a, **k):
        raise OSError("sudo not found")
    monkeypatch.setattr(http_server.subprocess, "run", _fail)
    r = env["client"].post(
        "/api/system/workers/tgw-http.service/restart",
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "sudo not found" in body["error"]


def test_form_system_renders(client):
    """/form/system returns HTML with all key sections."""
    _login(client)
    r = client.get("/form/system")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "System Health" in r.text
    assert "/api/health" in r.text
    assert "/api/system/workers" in r.text
    assert "/api/system/info" in r.text
    assert "eBay Token" in r.text
    assert "Disk Usage" in r.text
    assert "Workers" in r.text


def test_form_system_no_auth_required(client):
    """/form/system is accessible without Bearer token."""
    r = client.get("/form/system")
    assert r.status_code == 200


def test_nav_includes_system_link(client):
    """nav.js includes a link to /form/system."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/system" in r.text


# ---------------------------------------------------------------------------
# GET /form/intake — intake landing page (PP-EDITOR-001 Phase 3l)
# ---------------------------------------------------------------------------

def test_intake_landing_returns_200(client):
    r = client.get("/form/intake")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_intake_landing_no_auth_required(client):
    r = client.get("/form/intake")
    assert r.status_code == 200


def test_intake_landing_key_elements(client):
    """Landing page has SKU input, recent list placeholder, and inventory link."""
    _login(client)
    r = client.get("/form/intake")
    assert 'id="sku-input"' in r.text
    assert 'id="recent-list"' in r.text
    assert "goIntake" in r.text
    assert "/form/items" in r.text
    assert "/static/tgw.css" in r.text
    assert "/static/nav.css" in r.text


def test_intake_landing_never_embeds_the_real_api_key(client):
    """Landing page relies on the session cookie, not an embedded token."""
    _login(client)
    r = client.get("/form/intake")
    assert r.status_code == 200
    assert API_KEY not in r.text


def test_intake_landing_scan_hint(client):
    """Landing page shows the barcode scan hint text."""
    _login(client)
    r = client.get("/form/intake")
    assert "scan" in r.text.lower() or "barcode" in r.text.lower()


# ---------------------------------------------------------------------------
# GET /form/intake/{sku} — enhanced intake form (PP-EDITOR-001 Phase 3l)
# ---------------------------------------------------------------------------

def test_intake_form_has_photo_badge(env):
    """Intake form shows photo count badge; 0-photo badge has warning class."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert r.status_code == 200
    assert 'id="photo-badge"' in r.text
    assert "0 photos" in r.text
    assert "badge-photo-warn" in r.text


def test_intake_form_photo_count_with_images(env):
    """Intake form shows correct photo count when images exist (SKU_B has 2)."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_B}")
    assert r.status_code == 200
    assert "2 photos" in r.text
    # Badge element should use non-warn class — check the element class attribute directly
    assert 'class="badge badge-photo">' in r.text


def test_intake_form_has_status_badge(env):
    """Intake form shows an item-status badge."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert r.status_code == 200
    assert 'id="status-badge"' in r.text
    assert 'badge-status' in r.text


def test_intake_form_has_action_buttons(env):
    """Intake form shows identify and re-draft action buttons."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert r.status_code == 200
    assert "triggerAction" in r.text
    assert "btn-identify" in r.text
    assert "Re-draft" in r.text


def test_intake_form_start_identify_label(env):
    """Intake form shows 'Start Identify' when ai_identified is not set."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert "Start Identify" in r.text


def test_intake_form_reidentify_label(env):
    """Intake form shows 'Re-identify' when ai_identified is True."""
    _login(env["client"])
    _write_item(env["itemdata_root"], "tgw20260401000000010", {
        "sku": "tgw20260401000000010",
        "ai_identified": True,
    })
    r = env["client"].get("/form/intake/tgw20260401000000010")
    assert r.status_code == 200
    assert "Re-identify" in r.text


def test_intake_form_has_polling_js(env):
    """Intake form includes live polling JS for queue job status."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert "startPolling" in r.text
    assert "pollTimer" in r.text
    assert "TERMINAL" in r.text
    assert "setInterval" in r.text


def test_intake_form_has_view_detail_link(env):
    """Intake form has a 'View detail' link to /form/items/{sku}."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert f"/form/items/{SKU_A}" in r.text
    assert "detail-link" in r.text


def test_intake_form_never_embeds_the_real_api_key(env):
    """Intake form relies on the session cookie, not an embedded token."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert r.status_code == 200
    assert API_KEY not in r.text


def test_intake_form_has_job_badge_placeholder(env):
    """Intake form includes hidden job-badge element for live updates."""
    _login(env["client"])
    r = env["client"].get(f"/form/intake/{SKU_A}")
    assert 'id="job-badge"' in r.text


def test_nav_includes_intake_link(client):
    """nav.js includes a link to /form/intake."""
    r = client.get("/static/nav.js")
    assert r.status_code == 200
    assert "/form/intake" in r.text


# ---------------------------------------------------------------------------
# POST /api/inbox/upload — Flutter app inbox file upload (PP-EDITOR-001 3o)
# ---------------------------------------------------------------------------

def test_inbox_upload_md_file_saved_with_timestamp(env, tmp_path):
    """Uploading a .md file saves it under plan_inbox_path with a timestamp prefix."""
    inbox_dir = env["cfg"]["plan_vault_path"] / "inbox"
    content = b"# Test Note\n\nThis is a test inbox upload."
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("note.md", content, "text/markdown")},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    filename = body["filename"]
    # Filename must be timestamp-prefixed: YYYYMMDDTHHMMSSoriginal.md
    assert filename.endswith("_note.md")
    assert "T" in filename.split("_")[0]
    saved = inbox_dir / filename
    assert saved.exists()
    assert saved.read_bytes() == content


def test_inbox_upload_non_md_file_accepted(env):
    """Non-.md files are accepted — they land in inbox but pm_intake ignores them."""
    content = b"plain text note"
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("info.txt", content, "text/plain")},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_inbox_upload_requires_auth(env):
    """Endpoint requires Bearer auth — no token → 401."""
    content = b"# Note"
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("note.md", content, "text/markdown")},
    )
    assert r.status_code == 401


def test_inbox_upload_rejects_path_traversal(env):
    """Filename with path traversal component is rejected with 400."""
    content = b"attack"
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("../evil.md", content, "text/markdown")},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 400


def test_inbox_upload_rejects_oversized_file(env):
    """Files over 512 KB are rejected with 413."""
    big = b"x" * (512 * 1024 + 1)
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("big.md", big, "text/markdown")},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 413


def test_inbox_upload_creates_inbox_dir_if_missing(env, monkeypatch):
    """Endpoint creates the inbox dir if it doesn't exist yet."""
    new_inbox = env["cfg"]["plan_vault_path"] / "inbox_new"
    cfg = dict(env["cfg"])
    cfg["plan_inbox_path"] = new_inbox
    monkeypatch.setattr(http_server, "_cfg", cfg)
    assert not new_inbox.exists()
    content = b"# Fresh dir test"
    r = env["client"].post(
        "/api/inbox/upload",
        files={"file": ("fresh.md", content, "text/markdown")},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert new_inbox.exists()


# ---------------------------------------------------------------------------
# audit#1143 todo #1237 — http_server.py unescaped/unsafe output sweep
# ---------------------------------------------------------------------------


def test_login_get_escapes_next_param(client):
    """/login reflects `next` into a hidden input; must escape HTML metachars."""
    r = client.get('/login', params={"next": '"><script>alert(1)</script>'})
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_login_post_escapes_next_on_failure(client):
    """On a failed login the form re-renders with `next` — must stay escaped."""
    r = client.post(
        "/login",
        data={"next": '"><script>alert(1)</script>', "key": "wrong-password"},
    )
    assert r.status_code == 401
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_login_post_rejects_protocol_relative_redirect(env):
    """Open-redirect guard must reject //evil.com, not just check a leading '/'."""
    r = env["client"].post(
        "/login",
        data={"next": "//evil.com/phish", "key": WEB_KEY},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/form/home"


def test_login_post_allows_safe_relative_redirect(env):
    """A genuine same-origin relative path still redirects normally."""
    r = env["client"].post(
        "/login",
        data={"next": "/form/items/tgw123", "key": WEB_KEY},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/form/items/tgw123"


def test_intake_form_404_escapes_unknown_sku(env):
    """Unknown-SKU 404 page reflects the path segment; must escape it."""
    _login(env["client"])
    # No slash in the payload: the {sku} route param is a single path
    # segment and won't match across an encoded "/".
    r = env["client"].get("/form/intake/%3Cimg%20onerror%3Dalert(1)%3E")
    assert r.status_code == 404
    assert "<img onerror=alert(1)>" not in r.text
    assert "&lt;img" in r.text


def test_intake_form_escapes_stored_fields(env):
    """weight_oz/barcode/ai_hint render into value=\"...\" attrs; must escape quotes/tags."""
    _login(env["client"])
    sku = "tgw20260401000000099"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "barcode": '"><script>alert(1)</script>',
        "ai_hint": '" onmouseover="alert(2)',
        "weight_oz": '4.5"><b>x</b>',
    })
    r = env["client"].get(f"/form/intake/{sku}")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert 'onmouseover="alert(2)' not in r.text
    assert "<b>x</b>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_intake_form_rejects_path_traversal_sku(env):
    """audit#1143 (#1198): intake_form built the ItemData path from a raw
    sku with no '..' guard, unlike sibling media routes (get_media,
    get_thumb_noauth). A sku containing '..' must be rejected before any
    filesystem access."""
    _login(env["client"])
    r = env["client"].get("/form/intake/..sneaky")
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/items/{sku}/hint-trail
# ---------------------------------------------------------------------------


def test_hint_trail_returns_history(client, env):
    _write_item(env["itemdata_root"], SKU_A, {
        "sku": SKU_A,
        "identification_history": [{"round": 1, "at": "2026-07-01T00:00:00Z"}],
    })
    r = client.get(f"/api/items/{SKU_A}/hint-trail", headers=AUTH_HEADERS)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 1


def test_hint_trail_rejects_path_traversal_sku(client):
    """audit#1143 (#1198): get_hint_trail built the ItemData path from a
    raw sku with no '..' guard, unlike sibling media routes."""
    r = client.get("/api/items/..sneaky/hint-trail", headers=AUTH_HEADERS)
    assert r.status_code == 400


def test_docs_page_escapes_raw_html_in_markdown(env):
    """/docs renders pinned Plan Markdown; raw HTML/script must not execute."""
    vault = _seed_approved_docs(env)
    (vault / "evil.md").write_text(
        "# Title\n\n<script>alert(1)</script>\n", encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(vault), "add", "evil.md"], check=True)
    subprocess.run(["git", "-C", str(vault), "commit", "-qm", "evil approved"], check=True)
    env["cfg"]["plan_approved_commit"] = subprocess.check_output(
        ["git", "-C", str(vault), "rev-parse", "HEAD"], text=True,
    ).strip()
    r = env["client"].get("/docs/evil.md")
    assert r.status_code == 200
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# ---------------------------------------------------------------------------
# GET /form/items/{sku} — store category dropdowns (primary + secondary)
# ---------------------------------------------------------------------------


def test_item_detail_store_category_dropdowns_populate_and_select(env):
    """audit#1143 (#1198): the secondary store-category dropdown used to be
    built from a plain rebuild block that silently relied on the primary
    block's _sc_list surviving a shared try/except -- if the primary lookup
    ever failed partway, the secondary block's NameError would be silently
    swallowed too. Both dropdowns now share one _sc_list + a single builder
    function, so both should populate correctly and mark the right option
    selected."""
    groups_path = env["groups_path"]
    groups_path.write_text(
        json.dumps({
            "groups": {
                "books": {
                    "name": "Books",
                    "store_category": "Books & Media",
                    "store_category_id": "111",
                },
                "electronics": {
                    "name": "Electronics",
                    "store_category": "Electronics",
                    "store_category_id": "222",
                },
            }
        }),
        encoding="utf-8",
    )
    sku = "tgw20260701000000077"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Store Cat Test Item",
        "draft_listing": {
            "store_category_id": "111",
            "secondary_store_category_id": "222",
        },
    })
    _login(env["client"])
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    text = r.text
    # both known store categories appear in both dropdowns
    assert 'value="111" data-name="Books &amp; Media"' in text
    assert 'value="222" data-name="Electronics"' in text
    # primary dropdown selects 111, secondary selects 222
    assert 'value="111" data-name="Books &amp; Media" selected' in text
    assert 'value="222" data-name="Electronics" selected' in text


def test_item_detail_store_categories_do_not_fetch_ebay_during_render(env, monkeypatch):
    """A normal item page must retain locally configured category choices even
    when GetStore is unavailable; rendering must not call the live adapter."""
    groups_path = env["groups_path"]
    groups_path.write_text(
        json.dumps({"groups": {
            "books": {
                "name": "Books",
                "store_category": "Books & Media",
                "store_category_id": "111",
            },
            "electronics": {
                "name": "Electronics",
                "store_category": "Electronics",
                "store_category_id": "222",
            },
        }}),
        encoding="utf-8",
    )
    sku = "tgw20260701000000079"
    _write_item(env["itemdata_root"], sku, {"sku": sku, "title": "No Live Store Fetch"})
    monkeypatch.setattr(
        http_server,
        "_live_store_categories",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("item rendering called GetStore")),
    )
    _login(env["client"])
    response = env["client"].get(f"/form/items/{sku}")
    assert response.status_code == 200
    assert 'value="111" data-name="Books &amp; Media"' in response.text
    assert 'value="222" data-name="Electronics"' in response.text


def test_item_detail_store_categories_render_complete_ebay_snapshot_without_live_fetch(env, monkeypatch):
    """Routine rendering uses the complete last-known-good eBay snapshot, not
    the smaller category-group mapping and not a synchronous GetStore call."""
    env["groups_path"].write_text(
        json.dumps({"groups": {
            "mapped": {"store_category": "Mapped only", "store_category_id": "111"},
        }}),
        encoding="utf-8",
    )
    catalog_root = env["groups_path"].parent / "catalog"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    snapshot = catalog_root / "ebay-store-categories.json"
    snapshot.write_text(json.dumps({
        "source": "ebay_get_store",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "results": [
            {"id": "111", "name": "Books"},
            {"id": "222", "name": "Electronics"},
            {"id": "333", "name": "Unmapped but real"},
        ],
    }), encoding="utf-8")
    sku = "tgw20260701000000081"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Snapshot-backed categories",
        "draft_listing": {"store_category_id": "333"},
    })
    monkeypatch.setattr(
        http_server,
        "_live_store_categories",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("item rendering called GetStore")),
    )
    _login(env["client"])
    response = env["client"].get(f"/form/items/{sku}")
    assert response.status_code == 200
    assert 'value="111" data-name="Books"' in response.text
    assert 'value="222" data-name="Electronics"' in response.text
    assert 'value="333" data-name="Unmapped but real" selected' in response.text
    assert "eBay snapshot: 3 categories" in response.text


def test_store_category_refresh_persists_and_preserves_last_known_good(env, monkeypatch):
    """A successful explicit refresh atomically replaces the snapshot; a later
    provider failure returns and preserves that same last-known-good data."""
    from tgw.apis.ebay import trading

    catalog_root = env["groups_path"].parent / "catalog-refresh"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    expected = [
        {"id": "111", "name": "Books"},
        {"id": "222", "name": "Electronics"},
    ]
    monkeypatch.setattr(trading, "get_store_categories", lambda _cfg: expected)
    http_server._LIVE_STORE_CATS_CACHE.update({"data": None, "at": 0.0})

    refreshed, fallback = http_server._live_store_categories(env["cfg"])
    assert refreshed == expected
    assert fallback is False
    snapshot = catalog_root / "ebay-store-categories.json"
    before = json.loads(snapshot.read_text())
    assert before["results"] == expected
    assert before["source"] == "ebay_get_store"
    assert before["refreshed_at"]

    monkeypatch.setattr(trading, "get_store_categories", lambda _cfg: [{"id": "333", "name": ""}])
    http_server._LIVE_STORE_CATS_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_store_categories(env["cfg"])
    assert retained == expected
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before

    monkeypatch.setattr(trading, "get_store_categories", lambda _cfg: [{"id": 333, "name": "Books"}])
    http_server._LIVE_STORE_CATS_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_store_categories(env["cfg"])
    assert retained == expected
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before

    monkeypatch.setattr(trading, "get_store_categories", lambda _cfg: (_ for _ in ()).throw(RuntimeError("offline")))
    http_server._LIVE_STORE_CATS_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_store_categories(env["cfg"])
    assert retained == expected
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before


def test_item_detail_fulfillment_policies_render_snapshot_without_live_fetch(env, monkeypatch):
    """Routine item rendering reads the last-known-good policy snapshot and
    never synchronously calls the eBay Account API."""
    catalog_root = env["groups_path"].parent / "policy-catalog"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    (catalog_root / "ebay-fulfillment-policies.json").write_text(json.dumps({
        "source": "ebay_account_api",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "fulfillment": {"POLICY-1": "Small parcel", "POLICY-2": "Large parcel"},
        "return": {},
    }), encoding="utf-8")
    sku = "tgw20260701000000082"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Snapshot-backed shipping",
        "draft_listing": {"shipping_profile": "POLICY-2"},
    })
    monkeypatch.setattr(
        http_server,
        "_live_fulfillment_policies",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("item rendering called Account API")),
    )
    _login(env["client"])
    response = env["client"].get(f"/form/items/{sku}")
    assert response.status_code == 200
    assert 'value="POLICY-1"' in response.text
    assert 'value="POLICY-2" selected' in response.text
    assert "eBay snapshot: 2 fulfillment policies" in response.text

    (catalog_root / "ebay-fulfillment-policies.json").write_text(json.dumps({
        "source": "ebay_account_api",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "fulfillment": {"POLICY-1": ["not", "a", "name"]},
    }), encoding="utf-8")
    policies, refreshed_at, error = http_server._fulfillment_policies_snapshot(env["cfg"])
    assert policies == {}
    assert refreshed_at is None
    assert "valid" in error


def test_fulfillment_refresh_persists_and_preserves_last_known_good(env, monkeypatch):
    """Policy refresh updates only the fulfillment section and never destroys a
    usable snapshot when the Account API later fails."""
    from tgw.ebay import sync

    catalog_root = env["groups_path"].parent / "policy-refresh"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    snapshot = catalog_root / "ebay-fulfillment-policies.json"
    snapshot.write_text(json.dumps({"return": {"RETURN-1": "Returns"}}), encoding="utf-8")
    expected_rows = [
        {"id": "POLICY-1", "name": "Small parcel"},
        {"id": "POLICY-2", "name": "Large parcel"},
    ]
    monkeypatch.setattr(sync, "get_fulfillment_policies_full", lambda _cfg: expected_rows)
    http_server._LIVE_FULFILLMENT_POLICIES_CACHE.update({"data": None, "at": 0.0})

    refreshed, fallback = http_server._live_fulfillment_policies(env["cfg"])
    assert refreshed == {"POLICY-1": "Small parcel", "POLICY-2": "Large parcel"}
    assert fallback is False
    before = json.loads(snapshot.read_text())
    assert before["return"] == {"RETURN-1": "Returns"}
    assert before["fulfillment"] == refreshed
    assert before["refreshed_at"]

    monkeypatch.setattr(sync, "get_fulfillment_policies_full", lambda _cfg: [{"id": "POLICY-3", "name": ""}])
    http_server._LIVE_FULFILLMENT_POLICIES_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_fulfillment_policies(env["cfg"])
    assert retained == refreshed
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before

    monkeypatch.setattr(sync, "get_fulfillment_policies_full", lambda _cfg: [{"id": ["POLICY-3"], "name": "Bad"}])
    http_server._LIVE_FULFILLMENT_POLICIES_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_fulfillment_policies(env["cfg"])
    assert retained == refreshed
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before

    monkeypatch.setattr(sync, "get_fulfillment_policies_full", lambda _cfg: (_ for _ in ()).throw(RuntimeError("offline")))
    http_server._LIVE_FULFILLMENT_POLICIES_CACHE.update({"data": None, "at": 0.0})
    retained, fallback = http_server._live_fulfillment_policies(env["cfg"])
    assert retained == refreshed
    assert fallback is True
    assert json.loads(snapshot.read_text()) == before


def test_store_categories_get_reads_snapshot_without_live_fetch(env, monkeypatch):
    catalog_root = env["groups_path"].parent / "store-get-catalog"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    (catalog_root / "ebay-store-categories.json").write_text(json.dumps({
        "source": "ebay_get_store",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "results": [{"id": "111", "name": "Books"}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        http_server,
        "_live_store_categories",
        lambda _cfg: (_ for _ in ()).throw(AssertionError("GET crossed eBay boundary")),
    )
    _login(env["client"])
    response = env["client"].get("/api/ebay/store-categories")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "results": [{"id": "111", "name": "Books"}],
        "source": "snapshot",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "error": None,
    }

    (catalog_root / "ebay-store-categories.json").write_text(json.dumps({
        "source": "ebay_get_store",
        "refreshed_at": "2026-07-28T12:00:00Z",
        "results": [{"id": 111, "name": "Books"}],
    }), encoding="utf-8")
    response = env["client"].get("/api/ebay/store-categories")
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["source"] == "local_mapping"
    assert "valid" in response.json()["error"]


def test_reference_data_refresh_endpoint_reconciles_both_snapshots_once(env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        http_server,
        "_live_store_categories",
        lambda _cfg: (calls.append("store") or ([{"id": "111", "name": "Books"}], False)),
    )
    monkeypatch.setattr(
        http_server,
        "_live_fulfillment_policies",
        lambda _cfg: (calls.append("fulfillment") or ({"POLICY-1": "Small parcel"}, False)),
    )
    _login(env["client"])
    response = env["client"].post("/api/ebay/reference-data/refresh")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "store_categories": {"count": 1, "preserved_snapshot": False},
        "fulfillment_policies": {"count": 1, "preserved_snapshot": False},
    }
    assert calls == ["store", "fulfillment"]


def test_item_detail_preserves_stored_selector_ids_when_snapshot_unavailable(env):
    """An unavailable snapshot must not make an ordinary Save silently clear
    stored primary/secondary Store IDs or the shipping-policy ID."""
    catalog_root = env["groups_path"].parent / "missing-reference-data"
    catalog_root.mkdir()
    env["cfg"]["catalog_root"] = catalog_root
    env["groups_path"].write_text(json.dumps({"groups": {}}), encoding="utf-8")
    sku = "tgw20260701000000083"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Preserve stored IDs",
        "draft_listing": {
            "store_category_id": "STORE-1",
            "secondary_store_category_id": "STORE-2",
            "shipping_profile": "POLICY-9",
        },
    })
    _login(env["client"])
    response = env["client"].get(f"/form/items/{sku}")
    assert response.status_code == 200
    assert 'value="STORE-1" data-name="Stored category STORE-1" selected' in response.text
    assert 'value="STORE-2" data-name="Stored category STORE-2" selected' in response.text
    assert 'value="POLICY-9" selected' in response.text
    assert "not in current snapshot" in response.text


def test_item_detail_store_categories_keep_distinct_ids_and_drop_invalid_values(env):
    """Configured groups may reuse a display name; preserve their distinct
    stored IDs and never expose a value that can overwrite a draft as None."""
    env["groups_path"].write_text(
        json.dumps({"groups": {
            "first": {"store_category": "Books", "store_category_id": "111"},
            "second": {"store_category": "Books", "store_category_id": "222"},
            "missing": {"store_category": "Broken", "store_category_id": None},
            "blank": {"store_category": "Also Broken", "store_category_id": "  "},
        }}),
        encoding="utf-8",
    )
    sku = "tgw20260701000000080"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Keep Stored Category IDs",
        "draft_listing": {"store_category_id": "222", "secondary_store_category_id": "111"},
    })
    _login(env["client"])
    response = env["client"].get(f"/form/items/{sku}")
    assert response.status_code == 200
    assert 'value="111" data-name="Books" selected' in response.text
    assert 'value="222" data-name="Books" selected' in response.text
    assert 'value="None"' not in response.text
    assert 'value="  "' not in response.text


def test_item_detail_best_offer_control_reflects_state(env):
    """todo #1256 (+ code-review follow-up): per-item Best Offer control
    (offer.listingPolicies.bestOfferTerms) -- tri-state select (not a
    checkbox: "not set" must be a real, distinct, selectable option, not
    conflated with "disabled") reflects draft_listing.best_offer_enabled;
    auto-accept/decline prices prefill from draft_listing."""
    sku = "tgw20260701000000078"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Best Offer Test Item",
        "draft_listing": {
            "best_offer_enabled": True,
            "best_offer_auto_accept_price": 45.0,
            "best_offer_auto_decline_price": 20,
        },
    })
    _login(env["client"])
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    text = r.text
    assert '<option value="true" selected>Enabled</option>' in text
    assert 'id="dl-best-offer-accept" placeholder="auto-accept $" value="45.0"' in text
    assert 'id="dl-best-offer-decline" placeholder="auto-decline $" value="20"' in text


def test_item_detail_best_offer_control_not_set_when_unset(env):
    """The unset state must render as its own selected option ("not set"),
    never silently coerced to the "Disabled" option -- that's exactly the
    bug where saving any unrelated field forced best_offer_enabled=false."""
    sku = "tgw20260701000000079"
    _write_item(env["itemdata_root"], sku, {"sku": sku, "title": "No Best Offer Item"})
    _login(env["client"])
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    text = r.text
    assert '<option value="" selected>' in text
    assert '<option value="false" selected>' not in text
    assert '<option value="true" selected>' not in text


def test_item_detail_best_offer_control_disabled_when_explicitly_false(env):
    """False is a real, meaningful, distinct choice from unset -- must
    render as the "Disabled" option selected, not fall back to "not set"."""
    sku = "tgw20260701000000080"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Best Offer Disabled Item",
        "draft_listing": {"best_offer_enabled": False},
    })
    _login(env["client"])
    r = env["client"].get(f"/form/items/{sku}")
    assert r.status_code == 200
    text = r.text
    assert '<option value="false" selected>' in text
    assert '<option value="" selected>' not in text


# ---------------------------------------------------------------------------
# DELETE /api/items/{sku}/assets/{filename} — archive-before-delete (#1310,
# invariant E5, PP-COHESION-001)
# ---------------------------------------------------------------------------

def test_delete_asset_archives_before_unlink(env):
    """When archive_root is configured, deleting a photo must zip its bytes
    into archive_root before the file is unlinked from disk."""
    sku = SKU_B
    item_dir = env["itemdata_root"] / sku
    photo = item_dir / "front.jpg"
    original_bytes = photo.read_bytes()

    archive_root = env["cfg"]["itemdata_root"].parent / "archive"
    env["cfg"]["archive_root"] = archive_root

    r = env["client"].delete(f"/api/items/{sku}/assets/front.jpg", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "sku": sku, "deleted": "front.jpg"}

    # (a) the photo file is gone from the SKU dir
    assert not photo.exists()

    # (b) a zip matching the photo's stem exists in archive_root and
    # contains the original photo bytes
    zpath = archive_root / "front.zip"
    assert zpath.exists()
    with zipfile.ZipFile(zpath, "r") as zf:
        names = zf.namelist()
        assert len(names) == 1
        assert names[0].startswith("front.jpg.")
        assert zf.read(names[0]) == original_bytes


def test_delete_asset_no_archive_root_still_deletes(env):
    """With archive_root unset/None, delete must still succeed with no
    exception and no archive step attempted (null-safe guard, matching
    atomic_write_json's own pattern)."""
    sku = SKU_B
    item_dir = env["itemdata_root"] / sku
    photo = item_dir / "back.PNG"
    assert photo.exists()
    assert "archive_root" not in env["cfg"]

    r = env["client"].delete(f"/api/items/{sku}/assets/back.PNG", headers=AUTH_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True, "sku": sku, "deleted": "back.PNG"}

    assert not photo.exists()
    # No archive directory should have been created anywhere under tmp_path
    # as a side effect of this delete.
    assert not (env["itemdata_root"].parent / "archive").exists()


def test_item_detail_pipeline_log_distinguishes_retry_wait_from_dead_letter():
    """Dave, 2026-07-14: retry_wait (transient, will retry itself) was
    rendered in the same red color as dead_letter/failed (fatal, needs a
    human) in the item-detail page's pipeline-jobs log -- the CSS class
    names didn't even match the real queue_jobs.state values, and the
    error-detail text color was hardcoded red regardless of state. Fixed
    to a distinct warning color for retry_wait."""
    from tgw.http_server import _render_item_detail_html

    item = {"sku": "tgw1", "title": "Test Item"}
    jobs = [
        {
            "queue_name": "ebay_draft",
            "state": "retry_wait",
            "error_detail": "rate limited, will retry",
            "job_id": "j1",
            "updated_at": None,
            "finished_at": None,
            "created_at": None,
        },
        {
            "queue_name": "ebay_upload",
            "state": "dead_letter",
            "error_detail": "fatal upload error",
            "job_id": "j2",
            "updated_at": None,
            "finished_at": None,
            "created_at": None,
        },
    ]

    html = _render_item_detail_html("tgw1", item, [], [], jobs)

    # CSS class names match the real state strings (previously js-done/
    # js-pending never matched any actual queue_jobs.state value).
    assert 'class="js-retry-wait"' in html
    assert 'class="js-dead-letter"' in html

    # retry_wait's error text uses the warning color, not the critical one.
    retry_wait_pos = html.index("rate limited, will retry")
    dead_letter_pos = html.index("fatal upload error")
    retry_wait_row = html[:retry_wait_pos]
    dead_letter_row = html[retry_wait_pos:dead_letter_pos]

    assert "#fd8" in retry_wait_row[-600:]
    assert "#f99" in dead_letter_row


def test_item_detail_top_retry_requeues_exact_retryable_dead_letter():
    item = {"sku": "tgw1", "title": "Failed item", "status": "In Stock"}
    jobs = [{
        "queue_name": "ebay_publish",
        "state": "dead_letter",
        "job_id": "failed-publish-job",
        "retry_allowed": True,
        "error_detail": "provider rejected request",
    }]

    html = http_server._render_item_detail_html("tgw1", item, [], [], jobs)

    assert 'onclick="retryJob(&quot;failed-publish-job&quot;)"' in html
    assert 'onclick="retryPipeline()"' not in html


def test_item_detail_receipt_identity_dead_letter_offers_new_current_graph_attempt():
    """A stale receipt identity cannot be blind-retried, but it also must not
    strand a valid draft after the receipt producer has been corrected.

    The replacement List action launches a new operator-authorized evaluation;
    it does not mutate or requeue the historical dead-letter job.
    """
    item = {
        "sku": "tgw202510161310076",
        "title": "Roland R-MIX",
        "status": "In Stock",
        "draft_listing": {"title": "Roland R-MIX", "price": 19.99},
    }
    jobs = [{
        "queue_name": "ebay_upload",
        "state": "dead_letter",
        "job_id": "stale-receipt-job",
        "retry_allowed": False,
        "error_detail": "receipt binding rejected: INVALID_RECEIPT_IDENTITY",
        # Production queue rows retain worker results inside payload_json.
        "payload_json": {
            "sku": "tgw202510161310076",
            "result": {
                "outcome": "failed",
                "evidence": {"reason_code": "INVALID_RECEIPT_IDENTITY"},
            },
        },
    }]

    html = http_server._render_item_detail_html(
        "tgw202510161310076", item, [], [], jobs,
    )
    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )

    assert action_line is not None
    assert '>List on eBay</button>' in action_line.group(1)
    assert 'onclick="listOnEbay()"' in action_line.group(1)
    assert '>Retry</button>' not in action_line.group(1)
    assert '>Needs attention</button>' not in action_line.group(1)


def test_item_detail_other_nonretryable_dead_letter_still_needs_attention():
    item = {
        "sku": "tgw1",
        "title": "Hard failure",
        "status": "In Stock",
        "draft_listing": {"title": "Hard failure", "price": 19.99},
    }
    jobs = [{
        "queue_name": "ebay_upload",
        "state": "dead_letter",
        "job_id": "hard-failure-job",
        "retry_allowed": False,
        "result": {
            "outcome": "failed",
            "evidence": {"reason_code": "PROVIDER_REJECTED"},
        },
    }]

    html = http_server._render_item_detail_html("tgw1", item, [], [], jobs)
    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )

    assert action_line is not None
    assert '>Needs attention</button>' in action_line.group(1)
    assert '>List on eBay</button>' not in action_line.group(1)


def test_item_detail_sold_out_item_has_no_relist_action():
    item = {
        "sku": "tgw-sold",
        "title": "Sold item",
        "status": "sold",
        "draft_listing": {
            "title": "Sold item",
            "price": 33.99,
            "quantity": 0,
        },
        "ebay_listing": {
            "listing_id": "227446147105",
            "status": "Sold",
        },
        "ebay_offer": {
            "offer_id": "266460592018",
            "status": "UNPUBLISHED",
        },
    }

    html = http_server._render_item_detail_html("tgw-sold", item, [], [], [])
    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )

    assert action_line is not None
    assert '>Sold</button>' in action_line.group(1)
    assert ' disabled' in action_line.group(1)
    assert '>Relist</button>' not in action_line.group(1)
    assert '>List on eBay</button>' not in action_line.group(1)


def test_item_detail_positive_stale_quantity_does_not_override_sold_status():
    item = {
        "sku": "tgw-restocked",
        "title": "Restocked item",
        "status": "sold",
        "draft_listing": {
            "title": "Restocked item",
            "price": 33.99,
            "quantity": 1,
        },
    }

    html = http_server._render_item_detail_html(
        "tgw-restocked", item, [], [], [],
    )
    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )

    assert action_line is not None
    assert '>Sold</button>' in action_line.group(1)
    assert ' disabled' in action_line.group(1)
    assert '>Relist</button>' not in action_line.group(1)
    assert '>List on eBay</button>' not in action_line.group(1)


def test_item_detail_missing_draft_price_shows_set_price_not_retry():
    """A pricing worker may correctly refuse to invent a price when it has no
    positive market evidence.  The empty draft price must still lead the
    operator to the editor, even when no structured pipeline_error was saved.
    """
    item = {
        "sku": "tgw1",
        "title": "Identified item",
        "ai_identified": True,
        "draft_listing": {"title": "Draft title", "category_id": "177027", "price": None},
    }
    jobs = [{
        "queue_name": "ebay_price",
        "state": "dead_letter",
        "job_id": "failed-price-job",
        "retry_allowed": True,
        "error_detail": "ebay-price produced no positive price",
    }]

    html = http_server._render_item_detail_html("tgw1", item, [], [], jobs)

    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )
    assert action_line is not None
    assert ">Set Price</button>" in action_line.group(1)
    assert ">Retry</button>" not in action_line.group(1)
    assert ">Needs attention</button>" not in action_line.group(1)
    assert "AI Reidentify" in html


def test_item_detail_global_ai_action_is_always_available():
    first = http_server._render_item_detail_html(
        "tgw1", {"sku": "tgw1", "title": "New item"}, [], [], [],
    )
    identified = http_server._render_item_detail_html(
        "tgw2", {"sku": "tgw2", "title": "Known item", "ai_identified": True},
        [], [], [],
    )

    assert "AI Identify" in first
    assert "AI Reidentify" in identified
    assert first.count("triggerAction('ai_identify')") >= 1
    assert identified.count("triggerAction('ai_identify')") >= 1


@pytest.mark.parametrize(
    ("result", "reason", "expected"),
    (
        ("true", "photo sync complete: 5/5 local photos", "Photo sync ready"),
        ("false", "photo sync waiting: 3/5 local photos", "Waiting for photo sync"),
    ),
)
def test_item_detail_workflow_card_shows_photo_sync_fingerprint(
    result, reason, expected,
):
    html = http_server._render_item_detail_html(
        "tgw1", {"sku": "tgw1", "title": "Photo item"}, [], [], [],
        workflow_card={
            "goal": {"id": "tgw.ebay_listable", "version": "1"},
            "object_generation": "generation-1",
            "graph_id": "graph-1",
            "fingerprints": [{
                "condition_id": "photos_uploaded",
                "result": result,
                "reasons": [reason],
                "evidence": [{"identity": "photo-sync:abc"}],
            }],
        },
    )

    assert 'id="photo-sync-fingerprint"' in html
    assert expected in html
    assert reason in html


def test_item_detail_photo_sync_warning_offers_resync_not_stale_retry():
    """Current condition evidence outranks an older failed attempt.

    A draft can have every local photo represented in ``ebay_photos`` while its
    draft image order is still unsynchronized.  In that state the bounded repair
    is ``resync_photos``/``ebay_upload``.  Blindly retrying a historical stage or
    evaluator dead letter cannot establish ``photos_uploaded`` and may repeat the
    wrong treatment.
    """
    item = {
        "sku": "tgw202604121416554",
        "title": "What Neat Feet!",
        "status": "In Stock",
        "draft_listing": {
            "title": "What Neat Feet!",
            "category_id": "261186",
            "price": 11.99,
        },
    }
    jobs = [{
        "queue_name": "ebay_stage",
        "state": "dead_letter",
        "job_id": "historical-stage-failure",
        "retry_allowed": True,
        "error_detail": "eBay rejected staging",
    }]
    workflow_card = {
        "goal": {"id": "tgw.ebay_listable", "version": "1"},
        "object_generation": "generation-current",
        "graph_id": "graph-current",
        "fingerprints": [{
            "condition_id": "photos_uploaded",
            "result": "false",
            "reasons": [
                "photo sync waiting: 7/7 local photos; "
                "draft image order is not synchronized",
            ],
            "evidence": [{"identity": "photo-sync:current"}],
        }, {
            "condition_id": "item_has_photos",
            "result": "true",
            "reasons": ["photos present"],
            "evidence": [{"identity": "item-data:current"}],
        }],
    }

    html = http_server._render_item_detail_html(
        item["sku"], item, [], [], jobs, workflow_card=workflow_card,
    )
    action_line = re.search(
        r'<div class="act-row" id="action-line">(.*?)</div>', html,
    )

    assert action_line is not None
    assert '>Resync Photos</button>' in action_line.group(1)
    assert 'onclick="resyncPhotos()"' in action_line.group(1)
    assert '>List on eBay</button>' in action_line.group(1)
    assert 'onclick="listOnEbay()"' in action_line.group(1)
    assert '>Retry</button>' not in action_line.group(1)
    assert '>Needs attention</button>' not in action_line.group(1)


def test_item_detail_uses_ai_category_before_draft_exists(env):
    _login(env["client"])
    sku = "tgw-ai-category-fallback"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Identified Book",
        "ai_identified": True,
        "ebay_category_id": "261186",
        "ebay_category_name": "Books",
    })

    response = env["client"].get(f"/form/items/{sku}")

    assert response.status_code == 200
    assert 'id="dl-cat-id" value="261186"' in response.text
    assert "261186&nbsp;·&nbsp;Books" in response.text
    assert 'window._DL_CAT_ID = "261186"' in response.text


def test_item_detail_visibly_flags_missing_primary_category(env):
    _login(env["client"])
    sku = "tgw-missing-category"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Uncategorized Item",
        "ai_identified": True,
        "draft_listing": {"title": "Uncategorized Item"},
    })

    response = env["client"].get(f"/form/items/{sku}")

    assert response.status_code == 200
    assert 'id="dl-cat-search"' in response.text
    assert 'aria-invalid="true"' in response.text
    assert "Required — choose an eBay category before staging" in response.text
    assert "category required before aspects can be checked" in response.text


def test_required_aspect_controls_have_machine_visible_invalid_state(env):
    _login(env["client"])
    sku = "tgw-required-aspect-state"
    _write_item(env["itemdata_root"], sku, {
        "sku": sku,
        "title": "Book",
        "draft_listing": {"title": "Book", "category_id": "261186"},
    })

    response = env["client"].get(f"/form/items/{sku}")

    assert response.status_code == 200
    assert "data-required=\"'+(asp.required?'true':'false')+'\"" in response.text
    assert "aria-invalid=\"'+(reqEmpty?'true':'false')+'\"" in response.text
    assert "if(reqEmpty)missingReq++" in response.text


@pytest.mark.parametrize(
    "item",
    (
        {"sku": "tgw1", "title": "New"},
        {"sku": "tgw1", "title": "Known", "ai_identified": True},
        {
            "sku": "tgw1", "title": "Draft", "ai_identified": True,
            "draft_listing": {"title": "Draft", "category_id": "123"},
        },
        {
            "sku": "tgw1", "title": "Live", "ai_identified": True,
            "draft_listing": {"title": "Draft", "category_id": "123"},
            "ebay_offer": {"offer_id": "offer-1"},
            "ebay_listing": {"listing_id": "listing-1", "status": "Active"},
        },
    ),
)
def test_every_rendered_item_onclick_function_is_defined(item):
    html = http_server._render_item_detail_html("tgw1", item, [], [], [])
    definitions = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", html))
    onclick_calls = set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\s*\(', html))
    assert onclick_calls - definitions == set()


def test_async_item_actions_wait_for_terminal_state_before_reload():
    html = http_server._render_item_detail_html(
        "tgw1",
        {"sku": "tgw1", "title": "Known", "ai_identified": True},
        [], [], [],
    )

    assert "function waitForAction(action,jobId)" in html
    assert "item.ai_redraft_requested===true" in html
    assert "j.job_id===jobId" in html
    assert "waitForAction(action,d.job_id||'')" in html


# ---------------------------------------------------------------------------
# Invariant C14 fleet-wide clear-value round-trip detector (todo #1468,
# PP-LISTEDITOR-001).
#
# The 2026-07-16 Material incident (see reference/invariants.md C14) found
# ONE save path (the aspects form) silently dropping an operator's CLEARED
# value. This section is the general check: for every operator-facing save
# path this file exposes, prove the round trip set -> save -> clear -> save
# -> re-read actually reflects the clear, not just a changed value. A save
# path with no test here either doesn't accept operator field corrections
# (read-only / pipeline-action endpoints) or moves data between sets rather
# than clearing an operator-supplied value (inventory-diff/apply,
# category-aspect-migration/apply, set-template) — see this todo's result
# manifest for the full path inventory and why each one is or isn't covered.
# ---------------------------------------------------------------------------

def test_c14_patch_item_top_level_field_clear_roundtrip(env):
    """Item-detail direct-edit path (the dblclick-to-edit fields panel,
    saveInlineField's commit()) — PATCHes {fields: {field: newVal}}
    unconditionally, including newVal==''. Confirms the simple case: no
    other save intervenes between the clear and the re-read."""
    client = env["client"]
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"

    r = client.patch(f"/api/items/{SKU_A}", json={"fields": {"brand": "Acme"}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["brand"] == "Acme"

    r = client.patch(f"/api/items/{SKU_A}", json={"fields": {"brand": ""}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["brand"] == "", (
        "operator cleared 'brand' via the item-detail field editor and the "
        "save reported ok, but the cleared value was not actually persisted "
        "— invariant C14"
    )


def test_c14_patch_item_aspects_form_clear_roundtrip(env):
    """eBay Draft Editor aspects form (saveEbayDraft()) — bare
    draft_listing.item_specifics dict PATCH, routed through
    set_ebay_aspects(). This is the exact HTTP-level path #1461 fixed; a
    prior test (test_draft_specifics.py) covers the accessor directly, this
    covers the full endpoint round trip an operator's browser actually
    exercises."""
    client = env["client"]
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"

    r = client.patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"draft_listing": {"item_specifics": {"Material": "Silver"}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert draft_specifics.get_ebay_aspects(doc)["Material"] == "Silver"

    r = client.patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"draft_listing": {"item_specifics": {"Material": ""}}}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert draft_specifics.get_ebay_aspects(doc)["Material"] == "", (
        "operator cleared the Material aspect via the aspects form and the "
        "save reported ok, but the cleared value was not actually persisted "
        "— the exact 2026-07-16 live incident, invariant C14"
    )


def test_c14_bulk_apply_title_clear_roundtrip(env, enqueue_calls):
    """Bulk editor (/api/bulk/apply) — BulkBody.value is a plain str, so an
    empty string is a legitimate 'clear this field across N items' request.
    Confirms the same round trip for the fleet-edit path, not just the
    single-item editor."""
    client = env["client"]
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"

    r = client.post("/api/bulk/apply", headers=AUTH_HEADERS,
                    json={"field": "ai_hint", "value": "vintage brooch", "skus": [SKU_A]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["ai_hint"] == "vintage brooch"

    r = client.post("/api/bulk/apply", headers=AUTH_HEADERS,
                    json={"field": "ai_hint", "value": "", "skus": [SKU_A]})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["ai_hint"] == "", (
        "bulk-apply cleared 'ai_hint' to '' and reported ok, but the "
        "cleared value was not actually persisted — invariant C14"
    )


def test_c14_accept_proposals_clear_roundtrip(env, monkeypatch):
    """POST /api/items/{sku}/action action=accept_proposals — a revision
    proposal delta can itself propose clearing an aspect (e.g. an AI-drafted
    correction that a previously-set aspect is wrong and should be blank).
    Confirms the accept path actually writes the empty value into Set B,
    not just non-empty proposed values (the existing #1416-era tests here
    only ever assert non-empty accepted values)."""
    monkeypatch.setattr(http_server.state_machine, "enqueue_job", lambda *a, **k: "job-fake")

    draft = dict(_DRAFT_LISTING)
    draft["item_specifics"] = {"Material": "Silver"}
    _write_item_with_draft(
        env["itemdata_root"], SKU_A, draft,
        extra={"revision_draft": {"delta": {"item_specifics": {"Material": ""}}}},
    )

    client = env["client"]
    r = client.post(f"/api/items/{SKU_A}/action", json={"action": "accept_proposals"},
                    headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert draft_specifics.get_ebay_aspects(doc)["Material"] == "", (
        "an accepted revision proposal cleared 'Material' but the cleared "
        "value was not actually persisted into draft_listing.item_specifics "
        "— invariant C14"
    )


def test_c14_locked_top_level_field_clear_survives_unrelated_draft_save(env):
    """Control case: the padlock lock (set_locked / toggleInventoryLock)
    is the documented mitigation for the base-data auto-sync below — a
    LOCKED field's clear must survive an unrelated draft_listing save.
    Passes today; kept alongside the unlocked-case regression below so a
    future change to the lock check itself gets caught."""
    client = env["client"]
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"

    _write_item(env["itemdata_root"], SKU_A, {
        "sku": SKU_A, "title": "Widget", "location": "A1",
        "description": "Old description text",
        "draft_listing": {"description": "Old description text"},
    })
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    lock_patch = inventory_record.set_locked(doc, "description", True)
    http_server._apply_patch(json_path, lock_patch)

    r = client.patch(f"/api/items/{SKU_A}", json={"fields": {"description": ""}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["description"] == ""

    # Unrelated draft_listing save (e.g. a price edit via the aspects form).
    r = client.patch(f"/api/items/{SKU_A}",
                     json={"fields": {"draft_listing": {"price": 12.34}}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["description"] == "", (
        "a LOCKED top-level field's clear should survive an unrelated "
        "draft_listing save — if this fails, the padlock mitigation itself "
        "has regressed"
    )


def test_c14_unlocked_description_clear_reverted_by_unrelated_draft_save(env):
    client = env["client"]
    json_path = env["itemdata_root"] / SKU_A / f"{SKU_A}.json"

    _write_item(env["itemdata_root"], SKU_A, {
        "sku": SKU_A, "title": "Widget", "location": "A1",
        "description": "Old description text",
        "draft_listing": {"description": "Old description text"},
    })

    # Operator clears the top-level description via the item-detail
    # dblclick-to-edit field (unlocked — the default state).
    r = client.patch(f"/api/items/{SKU_A}", json={"fields": {"description": ""}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["description"] == ""

    # Later, the operator saves an unrelated field on the aspects form
    # (e.g. price) — any draft_listing PATCH triggers the padlock sync.
    r = client.patch(f"/api/items/{SKU_A}",
                     json={"fields": {"draft_listing": {"price": 12.34}}},
                     headers=AUTH_HEADERS)
    assert r.status_code == 200
    doc = json.loads(json_path.read_text(encoding="utf-8"))
    assert doc["description"] == "", (
        "operator's cleared 'description' was silently restored to the "
        "stale draft_listing.description value by the next unrelated "
        "draft_listing save — invariant C14"
    )


def test_item_detail_shows_list_action_after_successful_stage_supersedes_dead_letter():
    """A successfully re-staged UNPUBLISHED offer must remain operator-listable.

    The failed stage stays in the audit ledger, but cannot mask the normal
    List on eBay action after a later stage job for the same offer succeeds.
    """
    from tgw.http_server import _render_item_detail_html

    item = {
        "sku": "tgw1",
        "title": "Recovered staged item",
        "status": "In Stock",
        "draft_listing": {"title": "Recovered staged item", "price": 16.99},
        "ebay_offer": {"status": "UNPUBLISHED", "price": 16.99},
    }
    jobs = [
        {
            "queue_name": "ebay_stage",
            "state": "dead_letter",
            "job_id": "failed-stage",
            "error_detail": "Offer entity already exists.",
            "created_at": "2026-07-25T00:28:45+00:00",
            "updated_at": "2026-07-25T00:29:18+00:00",
            "finished_at": "2026-07-25T00:29:18+00:00",
        },
        {
            "queue_name": "ebay_stage",
            "state": "succeeded",
            "job_id": "recovered-stage",
            "created_at": "2026-07-25T00:32:21+00:00",
            "updated_at": "2026-07-25T00:32:25+00:00",
            "finished_at": "2026-07-25T00:32:25+00:00",
        },
    ]

    html = _render_item_detail_html("tgw1", item, [], [], jobs)

    assert "List on eBay" in html
    assert 'onclick="retryPipeline()"' not in html


def test_item_detail_handles_mixed_tz_aware_dead_letter_naive_succeeded():
    """Regression: aware dead_letter timestamp + naive succeeded timestamp
    must not raise TypeError when compared (todo #1683)."""
    from tgw.http_server import _render_item_detail_html

    item = {
        "sku": "tgw1",
        "title": "Recovered staged item",
        "status": "In Stock",
        "draft_listing": {"title": "Recovered staged item", "price": 16.99},
        "ebay_offer": {"status": "UNPUBLISHED", "price": 16.99},
    }
    jobs = [
        {
            "queue_name": "ebay_stage",
            "state": "dead_letter",
            "job_id": "failed-stage",
            "error_detail": "Offer entity already exists.",
            "finished_at": "2026-07-25T00:29:18+00:00",  # aware
        },
        {
            "queue_name": "ebay_stage",
            "state": "succeeded",
            "job_id": "recovered-stage",
            "finished_at": "2026-07-25T00:32:25",  # naive
        },
    ]

    html = _render_item_detail_html("tgw1", item, [], [], jobs)

    assert "List on eBay" in html
    assert 'onclick="retryPipeline()"' not in html


def test_item_detail_handles_mixed_tz_naive_dead_letter_aware_succeeded():
    """Regression: naive dead_letter timestamp + aware succeeded timestamp
    must not raise TypeError when compared (todo #1683)."""
    from tgw.http_server import _render_item_detail_html

    item = {
        "sku": "tgw1",
        "title": "Recovered staged item",
        "status": "In Stock",
        "draft_listing": {"title": "Recovered staged item", "price": 16.99},
        "ebay_offer": {"status": "UNPUBLISHED", "price": 16.99},
    }
    jobs = [
        {
            "queue_name": "ebay_stage",
            "state": "dead_letter",
            "job_id": "failed-stage",
            "error_detail": "Offer entity already exists.",
            "finished_at": "2026-07-25T00:29:18",  # naive
        },
        {
            "queue_name": "ebay_stage",
            "state": "succeeded",
            "job_id": "recovered-stage",
            "finished_at": "2026-07-25T00:32:25+00:00",  # aware
        },
    ]

    html = _render_item_detail_html("tgw1", item, [], [], jobs)

    assert "List on eBay" in html
    assert 'onclick="retryPipeline()"' not in html


def test_item_detail_handles_mixed_tz_baseline_at_and_job_timestamp():
    """Regression: _after_baseline()'s own fromisoformat comparison must also
    tolerate mixed aware/naive timestamps between item['baseline_at'] and a
    job's finished_at (todo #1683)."""
    from tgw.http_server import _render_item_detail_html

    item = {
        "sku": "tgw1",
        "title": "Recovered staged item",
        "status": "In Stock",
        "baseline_at": "2026-07-25T00:00:00",  # naive baseline
        "draft_listing": {"title": "Recovered staged item", "price": 16.99},
        "ebay_offer": {"status": "UNPUBLISHED", "price": 16.99},
    }
    jobs = [
        {
            "queue_name": "ebay_stage",
            "state": "dead_letter",
            "job_id": "failed-stage",
            "error_detail": "Offer entity already exists.",
            "finished_at": "2026-07-25T00:29:18+00:00",  # aware, after baseline
        },
    ]

    # Must not raise; a dead_letter after baseline with no later success is a
    # real actionable error, so it should surface (not "List on eBay").
    html = _render_item_detail_html("tgw1", item, [], [], jobs)
    assert html
