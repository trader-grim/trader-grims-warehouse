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

import json
import sqlite3
from pathlib import Path

import pytest

# TestClient (starlette) requires httpx at import/use time. If httpx is not
# importable in the venv we skip the entire module with a clear reason rather
# than producing a misleading collection error.
httpx = pytest.importorskip(
    "httpx", reason="httpx is required by fastapi.testclient.TestClient"
)

from fastapi.testclient import TestClient  # noqa: E402

from tgw import http_server  # noqa: E402

API_KEY = "test-key-abc123"
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


# ---------------------------------------------------------------------------
# Fakes / stubs
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Minimal psycopg2-ish cursor that returns canned queue-job rows."""

    def __init__(self, rows):
        self._rows = rows

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
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._rows)


def _make_catalog(db_path: Path, rows):
    """Create a SQLite catalog matching http_server's expected columns."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        "CREATE TABLE catalog ("
        "sku TEXT, title TEXT, location TEXT, status TEXT, "
        "price REAL, qty INTEGER, image TEXT)"
    )
    con.executemany(
        "INSERT INTO catalog (sku, title, location, status, price, qty, image) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
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
        "postgres_dsn": "postgresql://fake/db",
        "pretty": True,
        "raw": {},
    }

    monkeypatch.setattr(http_server, "_cfg", cfg)
    monkeypatch.setattr(http_server, "_api_key", API_KEY)

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
    # Column set matches the SELECT in http_server.py:177.
    assert set(body["items"][0]) == {
        "sku", "title", "location", "status", "price", "qty", "image",
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
    r1 = client.get("/api/items", params={"limit": 1}, headers=AUTH_HEADERS)
    b1 = r1.json()
    assert b1["count"] == 1
    first_sku = b1["items"][0]["sku"]

    r2 = client.get(
        "/api/items", params={"limit": 1, "offset": 1}, headers=AUTH_HEADERS
    )
    b2 = r2.json()
    assert b2["count"] == 1
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


def test_patch_enqueues_coalesced_catalog_rebuild(env, enqueue_calls):
    r = env["client"].patch(
        f"/api/items/{SKU_A}",
        json={"fields": {"title": "Rebuild me"}},
        headers=AUTH_HEADERS,
    )
    assert r.status_code == 200
    assert len(enqueue_calls) == 1
    kw = enqueue_calls[0]["kwargs"]
    assert kw["queue_name"] == "catalog_rebuild"
    assert kw["dedupe_key"] == "catalog_rebuild:pending"
    assert kw["payload"] == {"reason": f"http_patch:{SKU_A}"}
    assert kw["max_attempts"] == 3


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
# PP-BULKEDIT-001 — /form/bulk + /api/bulk/preview + /api/bulk/apply
# ---------------------------------------------------------------------------

def test_bulk_form_html(client):
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
    # coalesced catalog_rebuild enqueued
    assert any(c["kwargs"].get("queue_name") == "catalog_rebuild" for c in enqueue_calls)


def test_bulk_apply_invalid_field(client):
    r = client.post("/api/bulk/apply", headers=AUTH_HEADERS,
                    json={"field": "price", "value": "9.99", "skus": [SKU_A]})
    # bulk_edit returns ok:False for a non-editable field (200 envelope)
    assert r.status_code == 200
    assert r.json()["ok"] is False


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
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: [])
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "All clear" in r.text


def test_todos_form_db_error_still_200(client, monkeypatch):
    import tgw.todo as todo

    def _boom(*a, **k):
        raise RuntimeError("pg down")

    monkeypatch.setattr(todo, "todo_list", _boom)
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "unavailable" in r.text.lower()


def test_todos_form_escapes_html(client, monkeypatch):
    import tgw.todo as todo
    rows = [{"id": 1, "agent": "claude", "priority": 50,
             "body": "<script>alert('x')</script>", "source": "s", "done_at": None}]
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: rows)
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert "<script>alert" not in r.text          # raw tag must not appear
    assert "&lt;script&gt;" in r.text             # escaped form present


# ---------------------------------------------------------------------------
# PP-CAPTURE-001 — GET/POST /form/suggest (Round 5 #44) — no Bearer (network trust)
# ---------------------------------------------------------------------------

def _suggestions_file(env):
    return env["cfg"]["plan_vault_path"] / "suggestions" / "SUGGESTIONS.md"


def test_suggest_form_html(client):
    r = client.get("/form/suggest")  # no auth header — network trust
    assert r.status_code == 200
    assert 'name="text"' in r.text
    assert 'action="/form/suggest"' in r.text


def test_suggest_post_appends_with_punctuation(env):
    tricky = 'add "quotes" & $(subshell) `backticks` | pipes; --flags \'single\''
    r = env["client"].post("/form/suggest", data={"text": tricky})
    assert r.status_code == 200
    assert "added:" in r.text
    content = _suggestions_file(env).read_text(encoding="utf-8")
    assert tricky in content                       # written verbatim
    assert content.startswith("- [ ] ")            # checklist format intact


def test_suggest_post_collapses_newlines_to_one_line(env):
    r = env["client"].post(
        "/form/suggest", data={"text": "line one\r\nline two\n\nline three"}
    )
    assert r.status_code == 200
    content = _suggestions_file(env).read_text(encoding="utf-8")
    lines = [ln for ln in content.splitlines() if ln.strip()]
    assert len(lines) == 1                         # one checklist line, not four
    assert "line one line two line three" in lines[0]


def test_suggest_post_empty_writes_nothing(env):
    r = env["client"].post("/form/suggest", data={"text": "   "})
    assert r.status_code == 200
    assert "nothing written" in r.text
    assert not _suggestions_file(env).exists()


def test_suggest_post_escapes_echo_but_writes_raw(env):
    payload = "<script>alert(1)</script>"
    r = env["client"].post("/form/suggest", data={"text": payload})
    assert r.status_code == 200
    assert payload not in r.text                   # echo is HTML-escaped
    assert "&lt;script&gt;" in r.text
    assert payload in _suggestions_file(env).read_text(encoding="utf-8")


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
    r = client.get("/form/items")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text
    # esc is now an alias for the shared escapeHtml
    assert 'const esc=escapeHtml' in r.text


def test_todos_form_uses_static_css(client, monkeypatch):
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
    import tgw.todo as todo
    monkeypatch.setattr(todo, "todo_list", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    r = client.get("/form/todos")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert 'font-family:system-ui' not in r.text


def test_suggest_form_uses_static_css(client):
    r = client.get("/form/suggest")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text


def test_item_detail_uses_static_css(env):
    r = env["client"].get(f"/form/items/{SKU_A}")
    assert r.status_code == 200
    assert '/static/tgw.css' in r.text
    assert '/static/nav.css' in r.text
    assert '/static/tgw.js' in r.text
    assert '/static/nav.js' in r.text
    assert 'font-family:system-ui' not in r.text
