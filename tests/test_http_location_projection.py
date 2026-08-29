from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tgw import http_server, sqlite_catalog
from tgw.item_mutation import item_generation, operation_identity

API_KEY = "location-projection-test"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
SKU = "tgw20260101120000001"


@pytest.fixture
def location_env(tmp_path, monkeypatch):
    itemdata_root = tmp_path / "ItemData"
    item_dir = itemdata_root / SKU
    item_dir.mkdir(parents=True)
    item_path = item_dir / f"{SKU}.json"
    item_path.write_text(
        json.dumps({
            "sku": SKU,
            "title": "Location test item",
            "location": "old-bin",
            "catalog_verified": True,
        }),
        encoding="utf-8",
    )
    location_tree_root = tmp_path / "by-location"
    old_link = location_tree_root / "old-bin" / SKU
    old_link.parent.mkdir(parents=True)
    old_link.symlink_to(item_dir, target_is_directory=True)
    monkeypatch.setattr(
        http_server,
        "_cfg",
        {
            "itemdata_root": itemdata_root,
            "item_mutation_journal_root": tmp_path / "item-mutations",
            "location_tree_root": location_tree_root,
            "pretty": False,
        },
    )
    monkeypatch.setattr(http_server, "_api_key", API_KEY)
    monkeypatch.setattr(
        sqlite_catalog,
        "upsert_catalog_row",
        lambda config, document: {"ok": True},
    )
    return {
        "item_path": item_path,
        "old_link": old_link,
        "new_link": location_tree_root / "new-bin" / SKU,
    }


def test_primary_write_failure_leaves_json_and_location_links_unchanged(
    location_env,
    monkeypatch,
):
    item_path = location_env["item_path"]
    old_link = location_env["old_link"]
    new_link = location_env["new_link"]
    before_json = item_path.read_bytes()
    before_old_target = old_link.readlink()
    before_new_state = (new_link.exists(), new_link.is_symlink())
    expected_generation = item_generation(json.loads(before_json))

    def fail_primary_write(*args, **kwargs):
        raise OSError("injected canonical publication failure")

    monkeypatch.setattr(http_server, "atomic_write_json", fail_primary_write)
    client = TestClient(http_server.app, raise_server_exceptions=False)

    response = client.patch(
        f"/api/items/{SKU}",
        json={
            "fields": {"location": "new-bin"},
            "expected_generation": expected_generation,
        },
        headers=AUTH,
    )

    assert response.status_code == 500
    assert item_path.read_bytes() == before_json
    assert old_link.is_symlink()
    assert old_link.readlink() == before_old_target
    assert (new_link.exists(), new_link.is_symlink()) == before_new_state


def test_post_commit_projection_failure_is_truthful_and_generation_bound(
    location_env,
    monkeypatch,
):
    item_path = location_env["item_path"]
    old_link = location_env["old_link"]
    new_link = location_env["new_link"]
    original = json.loads(item_path.read_text(encoding="utf-8"))
    expected_generation = item_generation(original)
    monkeypatch.setattr(
        http_server,
        "sync_location_tree",
        lambda *args, **kwargs: {
            "ok": False,
            "sku": SKU,
            "error": "injected location projection failure",
        },
    )

    response = TestClient(http_server.app).patch(
        f"/api/items/{SKU}",
        json={
            "fields": {"location": "new-bin"},
            "expected_generation": expected_generation,
        },
        headers=AUTH,
    )

    assert response.status_code == 503, response.text
    body = response.json()["detail"]
    canonical_document = {**original, "location": "new-bin"}
    canonical_document.pop("catalog_verified", None)
    canonical_generation = item_generation(canonical_document)
    operation_id = operation_identity(
        sku=SKU,
        kind="location-tree-projection",
        expected_generation=canonical_generation,
        payload={"old_location": "old-bin", "new_location": "new-bin"},
    )
    stored = json.loads(item_path.read_text(encoding="utf-8"))
    finding = stored["pipeline_error"]

    assert body["ok"] is False
    assert body["code"] == "location_projection_repair_required"
    assert body["canonical_committed"] is True
    assert body["projection_updated"] is False
    assert "canonical item change committed" in body["detail"]
    assert body["canonical_generation"] == canonical_generation
    assert body["location_operation_id"] == operation_id
    assert body["finding_persisted"] is True
    assert body["resulting_generation"] == item_generation(stored)
    assert stored["location"] == "new-bin"
    assert finding["code"] == "location_update_failed"
    assert finding["source"] == "patch_item:location"
    assert finding["operation_id"] == operation_id
    assert finding["sku"] == SKU
    assert finding["object_generation"] == canonical_generation
    assert old_link.is_symlink()
    assert not new_link.exists()
    assert not new_link.is_symlink()
